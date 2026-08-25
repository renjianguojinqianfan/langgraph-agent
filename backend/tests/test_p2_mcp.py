"""P2 item 1 — MCP client integration tests (offline).

Uses a self-written JSON-RPC MCP echo server (``mcp_echo_server.py``) launched
with ``sys.executable`` (absolute path — Windows-safe; no ``npx`` dependency).

Covers:
* zero-regression: ``mcp_servers=[]`` / ``mcp_enabled=false`` / invalid JSON;
* connection + tool discovery (name/args_schema match the server inputSchema);
* call forwarding (``call_tool``) and ToolResult mapping;
* failure isolation: bad command, killed server -> ``success=False`` not crash;
* circuit breaker reuse via ``ToolExecutor.dispatch``;
* per-call confirmation (write-like heuristic + ``mcp_force_confirm`` override);
* idempotent cleanup (no leaked child processes).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from backend.config import Settings
from backend.core.mcp.client import McpClientManager, McpServerConfig
from backend.core.tools.base import ToolResult
from backend.core.tools.mcp_tool import McpTool, sanitize_name
from backend.core.tools.resilience import ToolExecutor

ECHO_SERVER = str(Path(__file__).resolve().parent / "mcp_echo_server.py")


def _echo_settings(tmp_path: Path, **overrides) -> Settings:
    """Settings wired to the local echo MCP server."""
    base = dict(
        data_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        mcp_enabled=True,
        mcp_connect_timeout_sec=15,
        mcp_timeout_sec=10,
        mcp_servers=json.dumps(
            [
                {
                    "name": "echo",
                    "command": sys.executable,
                    "args": [ECHO_SERVER],
                    "enabled": True,
                }
            ]
        ),
    )
    base.update(overrides)
    return Settings(**base)


def _kill_pid(pid: int) -> None:
    if os.name == "nt":  # pragma: no cover - Windows
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True,
            timeout=10,
        )
    else:  # pragma: no cover - POSIX
        os.kill(pid, signal.SIGKILL)


# ─────────────────── zero regression ───────────────────
def test_mcp_empty_servers_yields_no_tools(tmp_path):
    s = _echo_settings(tmp_path, mcp_servers="[]")
    mgr = McpClientManager(s)
    tools = mgr.connect_all()
    assert tools == []
    assert mgr.status_list() == []
    mgr.cleanup()


def test_mcp_disabled_yields_no_tools(tmp_path):
    s = _echo_settings(tmp_path, mcp_enabled=False, mcp_servers=json.dumps(
        [{"name": "echo", "command": sys.executable, "args": [ECHO_SERVER], "enabled": True}]
    ))
    mgr = McpClientManager(s)
    tools = mgr.connect_all()
    assert tools == []
    mgr.cleanup()


def test_mcp_invalid_json_continues(tmp_path):
    s = _echo_settings(tmp_path, mcp_servers="{not json}")
    mgr = McpClientManager(s)
    tools = mgr.connect_all()
    assert tools == []
    mgr.cleanup()


def test_mcp_disabled_server_skipped(tmp_path):
    """A server with enabled=false is reported disabled and never connected."""
    s = _echo_settings(
        tmp_path,
        mcp_servers=json.dumps(
            [
                {"name": "off", "command": sys.executable, "args": [ECHO_SERVER], "enabled": False},
                {"name": "on", "command": sys.executable, "args": [ECHO_SERVER], "enabled": True},
            ]
        ),
    )
    mgr = McpClientManager(s)
    tools = mgr.connect_all()
    names = {t["server"] for t in tools}
    assert names == {"on"}
    statuses = {st.name: st.to_dict() for st in mgr.status_list()}
    assert statuses["off"]["status"] == "disabled"
    assert statuses["on"]["status"] == "connected"
    mgr.cleanup()


# ─────────────────── connect + discovery ───────────────────
def test_mcp_connect_lists_tools_with_schema(tmp_path):
    s = _echo_settings(tmp_path)
    mgr = McpClientManager(s)
    tools = mgr.connect_all()
    assert len(tools) == 2
    by_name = {t["name"]: t for t in tools}
    assert "echo" in by_name and "write_file" in by_name
    # args_schema matches the server's inputSchema (with required preserved).
    schema = by_name["echo"]["input_schema"]
    assert schema["type"] == "object"
    assert "text" in schema["properties"]
    assert "text" in schema["required"]
    status = mgr.status_list()[0]
    assert status.status == "connected"
    assert status.tools_count == 2
    mgr.cleanup()


def test_mcp_connect_failure_isolated(tmp_path):
    """A bad command only marks that server error — startup continues."""
    s = _echo_settings(
        tmp_path,
        mcp_servers=json.dumps(
            [
                {"name": "bad", "command": "definitely-not-a-real-cmd-xyz", "args": [], "enabled": True},
                {"name": "echo", "command": sys.executable, "args": [ECHO_SERVER], "enabled": True},
            ]
        ),
    )
    mgr = McpClientManager(s)
    tools = mgr.connect_all()
    assert {t["server"] for t in tools} == {"echo"}
    statuses = {st.name: st.to_dict() for st in mgr.status_list()}
    assert statuses["bad"]["status"] == "error"
    assert statuses["echo"]["status"] == "connected"
    mgr.cleanup()


def test_mcp_sanitize_name():
    assert sanitize_name("my-server/fs") == "my_server_fs"
    assert sanitize_name("read_text") == "read_text"
    assert sanitize_name("a b.c") == "a_b_c"


# ─────────────────── call forwarding + ToolResult mapping ───────────────────
def test_mcp_call_tool_success(tmp_path):
    s = _echo_settings(tmp_path)
    mgr = McpClientManager(s)
    mgr.connect_all()
    tool = McpTool(
        server_name="echo",
        tool_name="echo",
        description="Echo the given text back.",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        manager=mgr,
        settings=s,
    )
    res = tool.run(text="hello")
    assert res.success is True
    assert res.data["server"] == "echo"
    assert res.data["tool"] == "echo"
    assert res.data["text"] == "echo:hello"
    mgr.cleanup()


def test_mcp_killed_server_returns_failure_not_crash(tmp_path):
    pid_file = tmp_path / "echo.pid"
    s = _echo_settings(
        tmp_path,
        mcp_timeout_sec=3,
        mcp_servers=json.dumps(
            [
                {
                    "name": "echo",
                    "command": sys.executable,
                    "args": [ECHO_SERVER],
                    "env": {"ECHO_PID_FILE": str(pid_file)},
                    "enabled": True,
                }
            ]
        ),
    )
    mgr = McpClientManager(s)
    mgr.connect_all()
    tool = McpTool(
        server_name="echo",
        tool_name="echo",
        description="echo",
        input_schema={"type": "object", "properties": {}, "required": []},
        manager=mgr,
        settings=s,
    )
    # Ensure the child is up and callable once.
    assert tool.run(text="pre").success is True

    # Kill the server process; the next call must fail cleanly (no crash).
    deadline = time.time() + 10
    pid = None
    while time.time() < deadline:
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text(encoding="utf-8").strip())
                break
            except ValueError:
                pass
        time.sleep(0.1)
    assert pid, "echo server PID file was not written"
    _kill_pid(pid)

    res = tool.run(text="after")
    assert res.success is False
    assert res.error  # explicit error message
    mgr.cleanup()


def test_mcp_circuit_breaker_opens_after_failures(tmp_path):
    pid_file = tmp_path / "echo.pid"
    s = _echo_settings(
        tmp_path,
        tool_failure_threshold=2,
        tool_max_retries=0,
        mcp_timeout_sec=3,
        mcp_servers=json.dumps(
            [
                {
                    "name": "echo",
                    "command": sys.executable,
                    "args": [ECHO_SERVER],
                    "env": {"ECHO_PID_FILE": str(pid_file)},
                    "enabled": True,
                }
            ]
        ),
    )
    mgr = McpClientManager(s)
    mgr.connect_all()
    tool = McpTool(
        server_name="echo",
        tool_name="write_file",
        description="Write a file (write-like).",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        manager=mgr,
        settings=s,
    )
    # write-like tools must not retry (retryable=False) so each dispatch is a
    # single attempt — deterministic circuit counting.
    assert tool.retryable is False
    assert tool.max_retries == 0

    events: list = []
    executor = ToolExecutor(s, publish_fn=lambda et, d: events.append((et, d)))

    deadline = time.time() + 10
    pid = None
    while time.time() < deadline:
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text(encoding="utf-8").strip())
                break
            except ValueError:
                pass
        time.sleep(0.1)
    assert pid
    _kill_pid(pid)

    r1 = executor.dispatch(tool, path="a.txt")
    r2 = executor.dispatch(tool, path="b.txt")
    assert r1.success is False and r2.success is False
    # Third dispatch is short-circuited by the open circuit.
    r3 = executor.dispatch(tool, path="c.txt")
    assert r3.success is False
    assert r3.circuit_open is True
    assert any(et == "tool_circuit_open" for et, _ in events)
    mgr.cleanup()


# ─────────────────── confirmation ───────────────────
def test_mcp_write_like_tool_requires_confirm(tmp_path):
    s = _echo_settings(tmp_path)
    mgr = McpClientManager(s)
    mgr.connect_all()
    read_tool = McpTool(
        server_name="echo", tool_name="echo", description="Echo text.",
        input_schema={}, manager=mgr, settings=s,
    )
    write_tool = McpTool(
        server_name="echo", tool_name="write_file", description="Write a file.",
        input_schema={}, manager=mgr, settings=s,
    )
    assert read_tool.needs_per_call_confirm is True
    assert read_tool._needs_confirm({}) is False
    assert write_tool._needs_confirm({}) is True
    mgr.cleanup()


def test_mcp_force_confirm_overrides_read_tool(tmp_path):
    s = _echo_settings(
        tmp_path,
        mcp_force_confirm=json.dumps(["mcp__echo__echo"]),
    )
    mgr = McpClientManager(s)
    mgr.connect_all()
    tool = McpTool(
        server_name="echo", tool_name="echo", description="Echo text.",
        input_schema={}, manager=mgr, settings=s,
    )
    assert tool._needs_confirm({}) is True
    mgr.cleanup()


def test_mcp_confirm_judgement_never_blocks(tmp_path):
    """A broken _needs_confirm in the executor branch only warns — never blocks."""
    from types import SimpleNamespace

    from backend.core.agent.nodes import AgentRuntime
    from backend.core.llm.client import MockLLMClient
    from backend.services.event_bus import EventBus

    s = _echo_settings(tmp_path)
    mgr = McpClientManager(s)
    mgr.connect_all()
    tool = McpTool(
        server_name="echo", tool_name="echo", description="Echo text.",
        input_schema={}, manager=mgr, settings=s,
    )

    def boom(_args):
        raise RuntimeError("judgement exploded")

    tool._needs_confirm = boom  # type: ignore[method-assign]

    mock = MockLLMClient(
        tool_calls=[{"id": "c1", "name": tool.name, "arguments": {"text": "x"}}]
    )
    bus = EventBus()
    tm = SimpleNamespace(settings=s, event_bus=bus, add_artifact=lambda *a: None)
    rt = AgentRuntime(
        "t_mcp", tm, llm=mock, tools=[tool], tool_schemas=[tool.to_openai_schema()],
        confirm_enabled=True,
    )
    state = {
        "step_index": 1,
        "steps": [{"index": 1, "thought": "", "tool_calls": [], "status": "running"}],
        "messages": [{"role": "user", "content": "hi"}],
        "plan": [], "artifacts": [], "status": "RUNNING", "stop_requested": False,
        "pending_confirm": {}, "final_answer": "", "error": "",
        "_last_action": "", "_current_tool_calls": [], "_confirmed_ids": [],
        "_rejected_ids": [], "_needs_confirm": False, "risk_report": [],
        "_risk_blocked": False, "subtasks": [], "_is_subtask": False,
    }
    rt.executor(state)  # must NOT raise
    recs = state["_current_tool_calls"]
    assert len(recs) == 1
    # The judgement exception was swallowed before need_confirm could be set.
    assert recs[0]["need_confirm"] is False
    mgr.cleanup()


# ─────────────────── cleanup ───────────────────
def test_mcp_cleanup_idempotent(tmp_path):
    s = _echo_settings(tmp_path)
    mgr = McpClientManager(s)
    mgr.connect_all()
    mgr.cleanup()
    mgr.cleanup()  # second call safe
    assert mgr._closed is True
    # Calls after cleanup fail cleanly.
    with pytest.raises(RuntimeError):
        mgr.call_tool("echo", "echo", {"text": "x"})


def test_mcp_manager_config_validation():
    with pytest.raises(Exception):
        McpServerConfig(name="", command="x")
    with pytest.raises(Exception):
        McpServerConfig(name="ok", command="")
