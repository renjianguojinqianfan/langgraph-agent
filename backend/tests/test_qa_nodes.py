"""QA integration tests — tool_node resilience pass-through (P0 item 2).

Verifies that the kernel's ``tool_node`` writes ``circuit_open`` / ``retries``
into the tool-call record and the ``tool_result`` event, and that a
short-circuited dispatch publishes ``tool_circuit_open`` — end to end through
:class:`~backend.core.agent.nodes.AgentRuntime` with a real
:class:`~backend.core.tools.resilience.ToolExecutor`.

Also covers the node's artifact-extraction guard: a tool result whose ``path``
is a directory registers no artifact (#17).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from backend.config import Settings
from backend.core.agent.nodes import AgentRuntime
from backend.core.agent.state import AgentState
from backend.core.tools.base import BaseTool, ToolResult
from backend.services.event_bus import EventBus
from backend.tests.conftest import make_settings


class _FailTool(BaseTool):
    name = "qa_fail"
    description = "d"
    args_schema = {}
    retryable = True
    max_retries = 0
    circuit_breaker = True

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self.calls = 0

    def run(self, **kwargs) -> ToolResult:
        self.calls += 1
        return ToolResult(success=False, error="boom")


def _state_with_call(tool_name: str = "qa_fail") -> AgentState:
    return {
        "step_index": 1,
        "steps": [{"index": 1, "thought": "", "tool_calls": [], "status": "running"}],
        "_current_tool_calls": [
            {
                "id": "call_1",
                "tool_name": tool_name,
                "input": {},
                "output": None,
                "status": "pending",
                "error": "",
                "need_confirm": False,
                "confirmed": False,
            }
        ],
        "_confirmed_ids": [],
        "_rejected_ids": [],
    }


def test_tool_node_writes_circuit_open_and_retries():
    settings = Settings(tool_failure_threshold=1, tool_max_retries=0, tool_cooldown_sec=30)
    tool = _FailTool(settings)
    bus = EventBus()
    events = []
    bus.subscribe("t1", lambda e: events.append(e))
    tm = SimpleNamespace(settings=settings, event_bus=bus, add_artifact=lambda *a: None)
    rt = AgentRuntime("t1", tm, llm=SimpleNamespace(), tools=[tool], tool_schemas=[])

    # First dispatch: executes, fails -> opens the breaker.
    state = _state_with_call()
    rt.tool_node(state)
    rec = state["_current_tool_calls"][0]
    assert rec["status"] == "failed"
    assert rec["circuit_open"] is False
    assert rec["retries"] == 0
    assert tool.calls == 1

    # Second dispatch: short-circuited by the breaker; tool NOT executed.
    state = _state_with_call()
    rt.tool_node(state)
    rec2 = state["_current_tool_calls"][0]
    assert rec2["status"] == "failed"
    assert rec2["circuit_open"] is True
    assert rec2["retries"] == 0
    assert tool.calls == 1  # unchanged

    # The tool_result event carries the flags; tool_circuit_open was published.
    tr = [e for e in events if e["type"] == "tool_result"]
    assert any(e["data"].get("circuit_open") is True for e in tr)
    assert any(e["type"] == "tool_circuit_open" for e in events)


def test_tool_node_unknown_tool_does_not_crash():
    settings = Settings(tool_failure_threshold=3)
    bus = EventBus()
    events = []
    bus.subscribe("t2", lambda e: events.append(e))
    tm = SimpleNamespace(settings=settings, event_bus=bus, add_artifact=lambda *a: None)
    rt = AgentRuntime("t2", tm, llm=SimpleNamespace(), tools=[], tool_schemas=[])

    state = {
        "step_index": 1,
        "steps": [{"index": 1, "thought": "", "tool_calls": [], "status": "running"}],
        "_current_tool_calls": [
            {
                "id": "c1",
                "tool_name": "ghost_tool",
                "input": {},
                "output": None,
                "status": "pending",
                "error": "",
                "need_confirm": False,
                "confirmed": False,
            }
        ],
        "_confirmed_ids": [],
        "_rejected_ids": [],
    }
    rt.tool_node(state)
    rec = state["_current_tool_calls"][0]
    assert rec["status"] == "failed"
    assert "unknown tool" in rec["error"]
    assert any(e["type"] == "tool_result" for e in events)


# ──────────── artifact extraction: only a real file is a product ────────────
class _PathResultTool(BaseTool):
    """Succeeds and advertises ``path`` — the shape write tools return, and the
    shape the retired ``file_io`` list returned for a *directory*."""

    name = "qa_path_result"
    description = "d"
    args_schema = {}
    retryable = False
    max_retries = 0
    circuit_breaker = False

    def __init__(self, settings: Settings | None = None, target: Path | None = None) -> None:
        super().__init__(settings)
        self._target = target

    def run(self, **kwargs) -> ToolResult:
        return ToolResult(success=True, data={"path": str(self._target)})


def _run_tool_node(tmp_path, target: Path) -> list:
    settings = make_settings(tmp_path)
    bus = EventBus()
    registered: list = []
    tm = SimpleNamespace(
        settings=settings,
        event_bus=bus,
        add_artifact=lambda *a: registered.append(a[1]),
    )
    rt = AgentRuntime(
        "t9", tm, llm=SimpleNamespace(), tools=[_PathResultTool(settings, target)], tool_schemas=[]
    )
    state = _state_with_call("qa_path_result")
    rt.tool_node(state)
    assert state["_current_tool_calls"][0]["status"] == "success"
    return registered


def test_directory_result_registers_no_artifact(tmp_path):
    """A directory under ``path`` used to become a task artifact; verification
    then saw a 0-byte「产物」and looped the task back (PR #69 live, run 1)."""
    target = tmp_path / "sandbox-dir"
    target.mkdir()
    assert _run_tool_node(tmp_path, target) == []


def test_file_result_registers_the_artifact(tmp_path):
    target = tmp_path / "produced.txt"
    target.write_text("body", encoding="utf-8")
    assert _run_tool_node(tmp_path, target) == [target]
