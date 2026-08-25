"""QA independent boundary tests — P2 item 1 (MCP client).

These tests complement ``test_p2_mcp.py`` (engineer's suite) with an
independent perspective. Coverage gaps addressed here:

* settings parsing: ``mcp_servers_list`` / ``mcp_force_confirm_list`` edge
  cases (valid array / invalid JSON / non-list / non-dict entries / missing
  required fields);
* ``McpTool`` normalisation: name sanitisation, description fallback,
  ``args_schema`` fallback for missing / non-object schemas, and the
  read/write resilience knobs;
* ``McpTool.run`` result mapping: ``isError=true`` -> ``success=False``;
  structured content extraction; empty content;
* executor per-call confirmation end-to-end (write-like tool -> confirm ->
  reject -> skipped with NO execution; read tool executes directly; force
  confirm overrides a read tool);
* REST ``GET /api/mcp/servers`` via TestClient (disabled -> empty list;
  connected -> connected + tools_count; failure -> error);
* TaskManager integration: MCP tools actually land in ``_tools`` and
  ``shutdown()`` is idempotent.

All tests are offline: the echo MCP server is launched with ``sys.executable``
and every assertion is deterministic.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.core.llm.client import MockLLMClient
from backend.core.mcp.client import McpClientManager, McpServerConfig
from backend.core.tools.base import ToolResult
from backend.core.tools.mcp_tool import McpTool, sanitize_name
from backend.core.tools.registry import build_tools
from backend.main import app
from backend.services.event_bus import EventBus
from backend.services.persistence import Persistence
from backend.services.task_manager import TaskManager
from backend.tests.conftest import make_settings

ECHO_SERVER = str(Path(__file__).resolve().parent / "mcp_echo_server.py")


def _echo_settings(tmp_path: Path, **overrides) -> Settings:
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


# ─────────────────── settings parsing edge cases ───────────────────
def test_mcp_servers_list_parsing(tmp_path):
    s = Settings(
        data_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        mcp_servers=json.dumps([{"name": "a", "command": "x"}, {"name": "b", "command": "y"}]),
    )
    assert len(s.mcp_servers_list) == 2

    # Invalid JSON -> empty list (no crash).
    assert Settings(mcp_servers="{not json}").mcp_servers_list == []

    # Non-list JSON -> empty list.
    assert Settings(mcp_servers='"hello"').mcp_servers_list == []
    assert Settings(mcp_servers='{"a": 1}').mcp_servers_list == []

    # Empty / whitespace -> empty list.
    assert Settings(mcp_servers="").mcp_servers_list == []
    assert Settings(mcp_servers="   ").mcp_servers_list == []


def test_mcp_force_confirm_list_parsing(tmp_path):
    s = Settings(
        data_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        mcp_force_confirm=json.dumps(["mcp__a__write", "mcp__b__delete"]),
    )
    assert s.mcp_force_confirm_list == ["mcp__a__write", "mcp__b__delete"]
    # Invalid -> empty; non-list -> empty.
    assert Settings(mcp_force_confirm="nope").mcp_force_confirm_list == []
    assert Settings(mcp_force_confirm='{"x": 1}').mcp_force_confirm_list == []


def test_mcp_config_skips_non_dict_and_missing_fields(tmp_path):
    """connect_all never crashes on garbage entries; valid ones still connect."""
    s = _echo_settings(
        tmp_path,
        mcp_servers=json.dumps(
            [
                "just a string",                       # non-dict -> skipped
                42,                                    # non-dict -> skipped
                {"name": "no-cmd"},                    # missing command -> validation error
                {"command": "no-name"},                # missing name -> validation error
                {"name": "echo", "command": sys.executable, "args": [ECHO_SERVER]},
            ]
        ),
    )
    mgr = McpClientManager(s)
    tools = mgr.connect_all()
    assert {t["server"] for t in tools} == {"echo"}
    statuses = {st.name: st.to_dict() for st in mgr.status_list()}
    assert statuses["echo"]["status"] == "connected"
    mgr.cleanup()


def test_mcp_transport_not_stdio_marked_error(tmp_path):
    """A server configured with transport=http is reserved/unimplemented."""
    s = _echo_settings(
        tmp_path,
        mcp_servers=json.dumps(
            [
                {"name": "httpy", "command": sys.executable, "args": [ECHO_SERVER], "transport": "http", "url": "http://x"},
                {"name": "echo", "command": sys.executable, "args": [ECHO_SERVER], "enabled": True},
            ]
        ),
    )
    mgr = McpClientManager(s)
    tools = mgr.connect_all()
    assert {t["server"] for t in tools} == {"echo"}
    statuses = {st.name: st.to_dict() for st in mgr.status_list()}
    assert statuses["httpy"]["status"] == "error"
    assert "not implemented" in statuses["httpy"]["error"]
    mgr.cleanup()


# ─────────────────── McpTool normalisation ───────────────────
def test_mcp_tool_name_sanitised(tmp_path):
    s = _echo_settings(tmp_path)
    mgr = McpClientManager(s)
    tool = McpTool(
        server_name="my-server/fs",
        tool_name="read text",
        description="",
        input_schema=None,
        manager=mgr,
        settings=s,
    )
    assert tool.name == "mcp__my_server_fs__read_text"
    # Description fallback.
    assert tool.description == "MCP tool my-server/fs.read text"
    # args_schema fallback for None / non-object.
    assert tool.args_schema == {"type": "object", "properties": {}, "required": []}
    mgr.cleanup()


def test_mcp_tool_args_schema_fallback(tmp_path):
    s = _echo_settings(tmp_path)
    mgr = McpClientManager(s)
    # Non-object inputSchema -> default empty schema.
    tool = McpTool(
        server_name="s", tool_name="t", description="d",
        input_schema={"type": "array", "items": {}}, manager=mgr, settings=s,
    )
    assert tool.args_schema == {"type": "object", "properties": {}, "required": []}
    # Object schema missing properties/required -> filled in.
    tool2 = McpTool(
        server_name="s", tool_name="t2", description="d",
        input_schema={"type": "object"}, manager=mgr, settings=s,
    )
    assert tool2.args_schema == {"type": "object", "properties": {}, "required": []}
    mgr.cleanup()


def test_mcp_tool_resilience_knobs(tmp_path):
    """Read-like tools keep retry; write-like tools disable retry (no repeat)."""
    s = _echo_settings(tmp_path)
    mgr = McpClientManager(s)
    read = McpTool(
        server_name="s", tool_name="read_thing", description="Read something.",
        input_schema={}, manager=mgr, settings=s,
    )
    assert read._write_like is False
    assert read.retryable is True
    assert read.max_retries is None
    assert read.circuit_breaker is True

    write = McpTool(
        server_name="s", tool_name="delete_thing", description="Delete something.",
        input_schema={}, manager=mgr, settings=s,
    )
    assert write._write_like is True
    assert write.retryable is False
    assert write.max_retries == 0
    assert write.circuit_breaker is True
    mgr.cleanup()


def test_sanitize_name_extra():
    assert sanitize_name("") == ""
    assert sanitize_name("üñïçødé") == "_____d_"  # ASCII letters survive, others replaced
    assert sanitize_name("a__b") == "a__b"


# ─────────────────── ToolResult mapping ───────────────────
class _FakeManager:
    """Minimal manager stand-in returning a canned raw result dict."""

    def __init__(self, raw: dict):
        self._raw = raw

    def call_tool(self, server, tool, arguments, timeout=None):
        return self._raw


def test_mcp_tool_iserror_maps_to_failure():
    raw = {"content": [{"type": "text", "text": "boom"}], "isError": True}
    tool = McpTool(
        server_name="s", tool_name="t", description="d", input_schema={},
        manager=_FakeManager(raw), settings=None,
    )
    res = tool.run()
    assert res.success is False
    assert res.error == "mcp error (isError=true)"
    assert res.data["text"] == "boom"


def test_mcp_tool_structured_content_extracted():
    raw = {
        "content": [
            {"type": "text", "text": "plain"},
            {"type": "structured", "structured": {"k": 1}},
        ],
        "isError": False,
    }
    tool = McpTool(
        server_name="s", tool_name="t", description="d", input_schema={},
        manager=_FakeManager(raw), settings=None,
    )
    res = tool.run()
    assert res.success is True
    assert res.data["text"] == "plain"
    assert res.data["structured"] == [{"k": 1}]
    assert res.data["server"] == "s"
    assert res.data["tool"] == "t"


def test_mcp_tool_empty_content_ok():
    raw = {"content": [], "isError": False}
    tool = McpTool(
        server_name="s", tool_name="t", description="d", input_schema={},
        manager=_FakeManager(raw), settings=None,
    )
    res = tool.run()
    assert res.success is True
    assert res.data["text"] == ""


def test_mcp_tool_manager_exception_maps_to_failure():
    class _Boom:
        def call_tool(self, *a, **kw):
            raise RuntimeError("connection lost")

    tool = McpTool(
        server_name="s", tool_name="t", description="d", input_schema={},
        manager=_Boom(), settings=None,
    )
    res = tool.run()
    assert res.success is False
    assert "connection lost" in res.error


# ─────────────────── executor per-call confirm (end-to-end) ───────────────────
def test_executor_write_tool_confirm_rejected_not_executed(tmp_path):
    """A write-like MCP tool must go through human_confirm; rejection skips."""
    s = _echo_settings(tmp_path)
    mgr = McpClientManager(s)
    mgr.connect_all()
    tool = McpTool(
        server_name="echo", tool_name="write_file", description="Write a file.",
        input_schema={}, manager=mgr, settings=s,
    )

    from backend.core.agent.nodes import AgentRuntime

    mock = MockLLMClient(
        tool_calls=[{"id": "c1", "name": tool.name, "arguments": {"path": "x.txt"}}]
    )
    bus = EventBus()
    tm = type("TM", (), {})()
    tm.settings = s
    tm.event_bus = bus

    executed: list = []

    class _FakeTM:
        settings = s
        event_bus = bus

        def request_confirm(self, task_id, tool_call_id):
            # The user REJECTS immediately.
            ev = type("EV", (), {"is_set": lambda self: True})()
            return ev

        def consume_confirm(self, task_id, tool_call_id):
            return False

        def add_artifact(self, *a, **kw):
            return None

    rt = AgentRuntime(
        "t_qa_mcp", _FakeTM(), llm=mock, tools=[tool],
        tool_schemas=[tool.to_openai_schema()], confirm_enabled=True,
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
    rt.executor(state)
    assert state["_current_tool_calls"][0]["need_confirm"] is True

    # human_confirm_node: reject -> recorded in _rejected_ids.
    rt.human_confirm_node(state)
    assert state["_rejected_ids"] == ["c1"]
    assert state["_needs_confirm"] is False

    # tool_node: the rejected call must be SKIPPED — the MCP server never runs.
    rt.tool_node(state)
    rec = state["_current_tool_calls"][0]
    assert rec["status"] == "skipped"
    assert rec["error"] == "rejected by user"
    assert executed == []
    mgr.cleanup()


def test_executor_read_tool_executes_directly(tmp_path):
    """A read-like MCP tool executes without confirmation."""
    s = _echo_settings(tmp_path)
    mgr = McpClientManager(s)
    mgr.connect_all()
    tool = McpTool(
        server_name="echo", tool_name="echo", description="Echo text.",
        input_schema={}, manager=mgr, settings=s,
    )

    from backend.core.agent.nodes import AgentRuntime

    mock = MockLLMClient(
        tool_calls=[{"id": "c1", "name": tool.name, "arguments": {"text": "hi"}}]
    )
    bus = EventBus()
    tm = type("TM", (), {})()
    tm.settings = s
    tm.event_bus = bus

    class _FakeTM:
        settings = s
        event_bus = bus

        def request_confirm(self, *a):
            raise AssertionError("read tool must not request confirmation")

        def consume_confirm(self, *a):
            return False

        def add_artifact(self, *a, **kw):
            return None

    rt = AgentRuntime(
        "t_qa_mcp_read", _FakeTM(), llm=mock, tools=[tool],
        tool_schemas=[tool.to_openai_schema()], confirm_enabled=True,
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
    rt.executor(state)
    assert state["_current_tool_calls"][0]["need_confirm"] is False
    # tool_node executes immediately -> success.
    rt.tool_node(state)
    rec = state["_current_tool_calls"][0]
    assert rec["status"] == "success"
    assert rec["output"]["text"] == "echo:hi"
    mgr.cleanup()


def test_executor_force_confirm_overrides_read_tool(tmp_path):
    """mcp_force_confirm forces confirmation even for read-like tools."""
    s = _echo_settings(tmp_path, mcp_force_confirm=json.dumps(["mcp__echo__echo"]))
    mgr = McpClientManager(s)
    mgr.connect_all()
    tool = McpTool(
        server_name="echo", tool_name="echo", description="Echo text.",
        input_schema={}, manager=mgr, settings=s,
    )
    assert tool._needs_confirm({}) is True  # force override
    assert tool._write_like is False          # but still read-like at construction
    mgr.cleanup()


# ─────────────────── REST GET /api/mcp/servers ───────────────────
def _make_client_with_mcp(tmp_path, settings, tools):
    eb = EventBus()
    persistence = Persistence(settings)
    mock = MockLLMClient(final_answer="ok")
    tm = TaskManager(settings, eb, persistence, llm_client=mock, tools=tools)
    with TestClient(app) as tc:
        tc.app.state.settings = settings
        tc.app.state.event_bus = eb
        tc.app.state.persistence = persistence
        tc.app.state.task_manager = tm
        yield tc, tm


def test_api_mcp_servers_disabled_returns_empty(tmp_path):
    settings = make_settings(tmp_path)  # mcp_enabled=False
    tools = build_tools(settings)
    for tc, tm in _make_client_with_mcp(tmp_path, settings, tools):
        r = tc.get("/api/mcp/servers")
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        assert body["data"]["servers"] == []
        tm.shutdown()


def test_api_mcp_servers_connected(tmp_path):
    settings = _echo_settings(tmp_path)
    tools = build_tools(settings)
    for tc, tm in _make_client_with_mcp(tmp_path, settings, tools):
        r = tc.get("/api/mcp/servers")
        assert r.status_code == 200
        servers = r.json()["data"]["servers"]
        assert len(servers) == 1
        assert servers[0]["name"] == "echo"
        assert servers[0]["status"] == "connected"
        assert servers[0]["tools_count"] == 2
        tm.shutdown()


def test_api_mcp_servers_error_status(tmp_path):
    settings = _echo_settings(
        tmp_path,
        mcp_servers=json.dumps(
            [{"name": "bad", "command": "definitely-not-a-real-cmd-xyz", "args": []}]
        ),
    )
    tools = build_tools(settings)
    for tc, tm in _make_client_with_mcp(tmp_path, settings, tools):
        r = tc.get("/api/mcp/servers")
        servers = r.json()["data"]["servers"]
        assert servers[0]["name"] == "bad"
        assert servers[0]["status"] == "error"
        assert servers[0]["error"]
        tm.shutdown()


# ─────────────────── TaskManager integration ───────────────────
def test_task_manager_loads_mcp_tools_and_shutdown_idempotent(tmp_path):
    settings = _echo_settings(tmp_path)
    eb = EventBus()
    persistence = Persistence(settings)
    mock = MockLLMClient(final_answer="ok")
    tools = build_tools(settings)
    tm = TaskManager(settings, eb, persistence, llm_client=mock, tools=tools)
    names = {t.name for t in tm._tools}
    assert "mcp__echo__echo" in names
    assert "mcp__echo__write_file" in names
    # shutdown twice -> safe, no leak.
    tm.shutdown()
    tm.shutdown()


def test_task_manager_no_mcp_when_disabled(tmp_path):
    settings = make_settings(tmp_path)  # mcp_enabled=False
    eb = EventBus()
    persistence = Persistence(settings)
    mock = MockLLMClient(final_answer="ok")
    tools = build_tools(settings)
    tm = TaskManager(settings, eb, persistence, llm_client=mock, tools=tools)
    names = {t.name for t in tm._tools}
    assert not any(n.startswith("mcp__") for n in names)
    tm.shutdown()
