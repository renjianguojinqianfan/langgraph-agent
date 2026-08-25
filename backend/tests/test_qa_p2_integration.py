"""QA independent integration tests — P2 cross-cutting concerns.

Verifies the integration points that span both P2 features and the existing
kernel:

* ``import backend.main`` succeeds (P1 circular-import fix regression guard —
  previously ``subagent.py`` imported lazily so the FastAPI app could be built);
* smoke race regression: the terminal event is published BEFORE the terminal
  status is persisted, so once ``get_task()`` reports a terminal status the
  ``task_completed`` event is always already available in the replay buffer —
  run repeatedly to catch the pre-fix race;
* MCP + Git tools coexist when both switches are enabled;
* MCP read tools flow through ``ToolExecutor.dispatch`` (P0 breaker reuse) with
  a successful result.

All tests are offline.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from backend.config import Settings
from backend.core.llm.client import MockLLMClient
from backend.core.mcp.client import McpClientManager
from backend.core.tools.mcp_tool import McpTool
from backend.core.tools.resilience import ToolExecutor
from backend.core.tools.registry import build_tools
from backend.services.event_bus import EventBus
from backend.services.persistence import Persistence
from backend.services.task_manager import TaskManager
from backend.tests.conftest import make_settings

ECHO_SERVER = str(Path(__file__).resolve().parent / "mcp_echo_server.py")


def test_import_backend_main_succeeds():
    """The FastAPI app module imports without circular-import errors."""
    import backend.main  # noqa: F401

    assert backend.main.app is not None
    assert backend.main.app.title == "LangGraph Autonomous Task Agent"


def _run_task_once(tmp_path, index: int) -> tuple[str, list]:
    """Run one full mock task and return (final_status, event_types)."""
    settings = make_settings(tmp_path)
    eb = EventBus()
    persistence = Persistence(settings)
    mock = MockLLMClient(
        plan=["Write a file"],
        tool_calls=[
            {
                "id": f"c{index}",
                "name": "file_io",
                "arguments": {"action": "write", "path": f"out_{index}.txt", "content": f"content {index}"},
            }
        ],
        final_answer="done",
    )
    tm = TaskManager(settings, eb, persistence, llm_client=mock, tools=build_tools(settings))
    task_id = tm.create_task(title=f"smoke {index}", user_input=f"write out_{index}.txt")
    deadline = time.time() + 15
    status = "RUNNING"
    while time.time() < deadline:
        task = tm.get_task(task_id)
        if task and task.status.value in ("COMPLETED", "FAILED", "INTERRUPTED"):
            status = task.status.value
            break
        time.sleep(0.05)
    events = eb.replay(task_id)
    event_types = {e["type"] for e in events}
    # Pre-fix race: the terminal status could be persisted before the event was
    # published, so an observer would see COMPLETED but no task_completed event.
    if status == "COMPLETED":
        assert "task_completed" in event_types, (
            "terminal status visible but task_completed event missing (publish-before-save race)"
        )
    tm.shutdown()
    return status, event_types


def test_smoke_race_regression_multiple_runs(tmp_path):
    """Run the full mock task several times — no flaky terminal-event loss."""
    statuses = []
    for i in range(3):
        status, _ = _run_task_once(tmp_path, i)
        statuses.append(status)
    assert statuses == ["COMPLETED", "COMPLETED", "COMPLETED"]


def test_mcp_and_git_coexist(tmp_path):
    """Both switches enabled: MCP tools and Git tools appear together."""
    settings = Settings(
        data_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        mcp_enabled=True,
        mcp_servers=json.dumps(
            [{"name": "echo", "command": sys.executable, "args": [ECHO_SERVER], "enabled": True}]
        ),
        git_enabled=True,
        git_repo_dir=str(tmp_path / "repos"),
        git_timeout_sec=10,
        mcp_connect_timeout_sec=15,
        mcp_timeout_sec=10,
    )
    eb = EventBus()
    persistence = Persistence(settings)
    mock = MockLLMClient(final_answer="ok")
    tm = TaskManager(settings, eb, persistence, llm_client=mock, tools=build_tools(settings))
    names = {t.name for t in tm._tools}
    assert "mcp__echo__echo" in names
    assert "mcp__echo__write_file" in names
    assert "git_status" in names and "git_commit" in names
    tm.shutdown()


def test_mcp_read_tool_through_tool_executor(tmp_path):
    """A read-like MCP tool dispatched via ToolExecutor succeeds (breaker reuse)."""
    settings = Settings(
        data_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        mcp_enabled=True,
        mcp_servers=json.dumps(
            [{"name": "echo", "command": sys.executable, "args": [ECHO_SERVER], "enabled": True}]
        ),
        mcp_connect_timeout_sec=15,
        mcp_timeout_sec=10,
    )
    mgr = McpClientManager(settings)
    mgr.connect_all()
    tool = McpTool(
        server_name="echo", tool_name="echo", description="Echo text.",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        manager=mgr, settings=settings,
    )
    events: list = []
    executor = ToolExecutor(settings, publish_fn=lambda et, d: events.append((et, d)))
    res = executor.dispatch(tool, text="via-executor")
    assert res.success is True
    assert res.data["text"] == "echo:via-executor"
    # A successful dispatch resets the breaker (no tool_circuit_open).
    assert not any(et == "tool_circuit_open" for et, _ in events)
    mgr.cleanup()


def test_task_manager_shutdown_without_mcp_safe(tmp_path):
    """shutdown() is a no-op when MCP was never enabled (no attribute crash)."""
    settings = make_settings(tmp_path)  # mcp_enabled=False, git_enabled=False
    eb = EventBus()
    persistence = Persistence(settings)
    mock = MockLLMClient(final_answer="ok")
    tm = TaskManager(settings, eb, persistence, llm_client=mock, tools=build_tools(settings))
    tm.shutdown()  # must not raise
    tm.shutdown()
