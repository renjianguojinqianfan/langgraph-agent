"""P2 item 1 — MCP client integration tests (offline).

Uses a self-written JSON-RPC MCP echo server (``mcp_echo_server.py``) launched
with ``sys.executable`` (absolute path — Windows-safe; no ``npx`` dependency).

Covers:
* zero-regression: ``mcp_servers=[]`` / ``mcp_enabled=false`` / invalid JSON;
* connection + tool discovery (name/args_schema match the server inputSchema);
* call forwarding (``call_tool``) and ToolResult mapping;
* failure isolation: bad command, killed server -> ``success=False`` not crash;
* danger classification from the server's self-reported ``destructiveHint`` /
  ``readOnlyHint`` — issue #57; inert metadata since #138 removed the gate;
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
from typing import Any

import pytest

from backend.config import Settings
from backend.core.mcp.client import McpClientManager, McpServerConfig
from backend.core.tools.mcp_tool import McpTool, sanitize_name
from backend.utils.sandbox import MCP_ENV_ALLOWLIST

ECHO_SERVER = str(Path(__file__).resolve().parent / "mcp_echo_server.py")


def _echo_settings(tmp_path: Path, **overrides) -> Settings:
    """Settings wired to the local echo MCP server."""
    base: dict[str, Any] = dict(
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
        # getattr 而不是直接 signal.SIGKILL：mypy 按当前平台解析 signal 常量，
        # 在 Windows 上 SIGKILL 不存在会让类型闸变红（而这条分支只在 POSIX 跑）。
        # 两个平台上运行时语义完全相同。
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))


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


def test_mcp_tool_reports_exception_type_when_message_is_empty(tmp_path):
    """A bare pipe-reset exception must still surface a non-empty reason.

    GitHub's Windows runner can raise exceptions whose ``str()`` is empty;
    ``McpTool.run`` falls back to the exception type name instead of handing a
    blank ``error`` to the model / trace.
    """

    class _Manager:
        def call_tool(self, server, tool, arguments):
            raise ConnectionResetError()

    s = _echo_settings(tmp_path, mcp_servers="[]")
    tool = McpTool(
        server_name="echo",
        tool_name="echo",
        description="echo",
        input_schema={},
        manager=_Manager(),
        settings=s,
    )
    res = tool.run(text="after")
    assert res.success is False
    assert res.error == "ConnectionResetError"


def test_mcp_tool_reports_exception_type_when_timeout_message_is_empty(tmp_path):
    """A bare TimeoutError must surface a non-empty reason too (#212)."""

    class _Manager:
        def call_tool(self, server, tool, arguments):
            raise TimeoutError()

    s = _echo_settings(tmp_path, mcp_servers="[]")
    tool = McpTool(
        server_name="echo",
        tool_name="echo",
        description="echo",
        input_schema={},
        manager=_Manager(),
        settings=s,
    )
    res = tool.run(text="after")
    assert res.success is False
    assert res.error == "TimeoutError"


# ─────────────────── danger classification ───────────────────
def test_mcp_self_reported_annotations_drive_the_danger_signal(tmp_path):
    """The server's self-reported annotations (via ``connect_all``) reach McpTool.

    #57 replaced the verb table with the protocol's own hints; #138 later retired
    the confirmation gate, so the classification is inert metadata now.
    """
    s = _echo_settings(tmp_path)
    mgr = McpClientManager(s)
    discovered = {t["name"]: t for t in mgr.connect_all()}
    read_tool = McpTool(
        server_name="echo", tool_name="echo", description="Echo text.",
        input_schema={}, manager=mgr, settings=s,
        annotations=discovered["echo"]["annotations"],
    )
    write_tool = McpTool(
        server_name="echo", tool_name="write_file", description="Write a file.",
        input_schema={}, manager=mgr, settings=s,
        annotations=discovered["write_file"]["annotations"],
    )
    assert read_tool.danger_signal == "safe"
    assert write_tool.danger_signal == "dangerous"
    mgr.cleanup()


def test_mcp_unreported_tool_reads_as_unknown(tmp_path):
    """AC2：server 两个 hint 都没给 → ``unknown``，不当作 safe。"""
    s = _echo_settings(tmp_path)
    mgr = McpClientManager(s)
    mgr.connect_all()
    unreported = McpTool(
        server_name="echo", tool_name="quiet", description="Nobody knows.",
        input_schema={}, manager=mgr, settings=s,
    )
    assert unreported.danger_signal == "unknown"
    mgr.cleanup()


# ─────────────────── child environment (#254) ───────────────────
def test_mcp_child_env_is_allowlisted_only(tmp_path, monkeypatch):
    """A third-party MCP server gets the allowlist, not the parent environment.

    The audit for #254 found ``_connect`` passed ``dict(os.environ)`` to the
    stdio child, so every MCP server spawned from user config could read the
    LLM / cloud keys exported in the parent. The child now gets the bash
    allowlist plus npm's Windows config dirs, plus its own explicit ``env``
    mapping. Asserted as "the received set is a subset of the declared policy",
    so a denylist implementation that leaks any other variable would fail.
    """
    dump = tmp_path / "echo-env.json"
    secrets = ("LLM_API_KEY", "DASHSCOPE_API_KEY", "GITHUB_TOKEN", "EXA_API_KEY")
    for name in secrets:
        monkeypatch.setenv(name, "secret-sentinel-do-not-leak")
    monkeypatch.setenv("MY_TEST_ONLY_VAR", "not-on-any-allowlist")
    s = _echo_settings(
        tmp_path,
        mcp_servers=json.dumps(
            [
                {
                    "name": "echo",
                    "command": sys.executable,
                    "args": [ECHO_SERVER],
                    "env": {"ECHO_ENV_DUMP_FILE": str(dump)},
                    "enabled": True,
                }
            ]
        ),
    )
    mgr = McpClientManager(s)
    mgr.connect_all()
    try:
        assert dump.exists(), "echo server did not dump its environment"
        child_env = json.loads(dump.read_text(encoding="utf-8"))
    finally:
        mgr.cleanup()

    received = {key.upper() for key in child_env}
    # PATH is inherited unchanged, so `npx` / `node` resolve inside the child.
    path_key = next(key for key in child_env if key.upper() == "PATH")
    assert child_env[path_key] == os.environ["PATH"]
    for name in (*secrets, "MY_TEST_ONLY_VAR"):
        assert name not in received, f"{name} leaked into the MCP child env"
    # "Only the allowlist": nothing beyond the declared policy + explicit env.
    # The MCP SDK's stdio transport also merges its documented-safe defaults
    # (DEFAULT_INHERITED_ENV_VARS) on top of the env we pass, so they belong to
    # the effective policy; anything else is an unexplained leak.
    from mcp.client.stdio import DEFAULT_INHERITED_ENV_VARS

    allowed = (
        {name.upper() for name in MCP_ENV_ALLOWLIST}
        | {name.upper() for name in DEFAULT_INHERITED_ENV_VARS}
        | {"PYTHONUNBUFFERED", "ECHO_ENV_DUMP_FILE"}
    )
    assert received <= allowed, f"unexpected inherited variables: {received - allowed}"
    # The server's own explicit env is the intended channel for extras.
    assert child_env["ECHO_ENV_DUMP_FILE"] == str(dump)


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
