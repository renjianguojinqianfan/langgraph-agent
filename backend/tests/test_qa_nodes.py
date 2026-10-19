"""QA integration tests — ``tool_node`` dispatch and its conversation face.

Verifies that one tool call is executed exactly once (#157: no retry, no
circuit breaker) and that the failure reason lands in the tool message the
model reads, not only in the events a human reads (#95).

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

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self.calls = 0

    def run(self, **kwargs) -> ToolResult:
        self.calls += 1
        return ToolResult(success=False, error="boom")


class _SilentFailTool(_FailTool):
    """A failure that carries no message at all — the case the fallback wording
    exists for, since an empty ``error`` tells the model as little as ``null`` did."""

    name = "qa_silent_fail"

    def run(self, **kwargs) -> ToolResult:
        self.calls += 1
        return ToolResult(success=False)


class _EchoTool(_FailTool):
    """A success carrying a real payload — the shape the failure fix must not touch."""

    name = "qa_echo"

    def run(self, **kwargs) -> ToolResult:
        self.calls += 1
        return ToolResult(success=True, data={"note": "ok 中文", "n": 3})


def _state_with_call(tool_name: str = "qa_fail") -> AgentState:
    return {
        "step_index": 1,
        "steps": [{"index": 1, "thought": "", "tool_calls": [], "status": "running"}],
        "messages": [{"role": "user", "content": "hi"}],
        "_current_tool_calls": [
            {
                "id": "call_1",
                "tool_name": tool_name,
                "input": {},
                "output": None,
                "status": "pending",
                "error": "",
            }
        ],
    }


def _tool_messages(state: AgentState) -> list:
    """The conversation face: what the model reads on its next turn."""
    return [m for m in state.get("messages", []) if m.get("role") == "tool"]


def test_tool_node_executes_each_call_once():
    """One dispatch = one execution, and no retry/breaker fields (#157).

    The old executor retried a failed tool twice behind the model's back, so a
    deterministic failure ran three times. The model must instead see the single
    failure and decide what to do next.
    """
    settings = Settings()
    tool = _FailTool(settings)
    bus = EventBus()
    events = []
    bus.subscribe("t1", lambda e: events.append(e))
    tm = SimpleNamespace(settings=settings, event_bus=bus, add_artifact=lambda *a: None)
    rt = AgentRuntime("t1", tm, llm=SimpleNamespace(), tools=[tool], tool_schemas=[])

    state = _state_with_call()
    rt.tool_node(state)
    rec = state["_current_tool_calls"][0]
    assert tool.calls == 1
    assert rec["status"] == "failed"
    assert rec["error"] == "boom"
    assert "retries" not in rec
    assert "circuit_open" not in rec
    assert not any(e["type"] == "tool_circuit_open" for e in events)


def test_tool_node_unknown_tool_does_not_crash():
    settings = Settings()
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
            }
        ],
    }
    rt.tool_node(state)
    rec = state["_current_tool_calls"][0]
    assert rec["status"] == "failed"
    assert "unknown tool" in rec["error"]
    assert any(e["type"] == "tool_result" for e in events)


# ──────────── artifact extraction: only a real file is a product ────────────
class _PathResultTool(BaseTool):
    """Succeeds and advertises ``path`` — the shape write tools return, and the
    shape the retired ``file_io`` list returned for a *directory*.

    Declares ``registers_artifact`` like a write tool does: the point of these
    two tests is the file/directory guard, not the registration source (#72
    keeps both axes covered by separate cases).
    """

    name = "qa_path_result"
    description = "d"
    args_schema = {}
    registers_artifact = True

    def __init__(self, settings: Settings | None = None, target: Path | None = None) -> None:
        super().__init__(settings)
        self._target = target

    def run(self, **kwargs) -> ToolResult:
        return ToolResult(success=True, data={"path": str(self._target)})


class _ReadLikePathTool(_PathResultTool):
    """Same result shape, but a read tool: advertises the file it looked at
    without writing it, so it is not a source of products (#72)."""

    name = "qa_read_like"
    registers_artifact = False


def _run_tool_node(tmp_path, target: Path, tool: BaseTool | None = None) -> list:
    settings = make_settings(tmp_path)
    bus = EventBus()
    registered: list = []
    tm = SimpleNamespace(
        settings=settings,
        event_bus=bus,
        add_artifact=lambda *a: registered.append(a[1]),
    )
    subject = tool or _PathResultTool(settings, target)
    rt = AgentRuntime("t9", tm, llm=SimpleNamespace(), tools=[subject], tool_schemas=[])
    state = _state_with_call(subject.name)
    rt.tool_node(state)
    assert state["_current_tool_calls"][0]["status"] == "success"
    return registered


def test_directory_result_registers_no_artifact(tmp_path):
    """A directory under ``path`` must not become a task artifact — a product is a
    file (ADR 0004). Before #72 it registered, and the 0-byte「产物」that reached
    the then-live verification layer looped the task back (PR #69 live, run 1)."""
    target = tmp_path / "sandbox-dir"
    target.mkdir()
    assert _run_tool_node(tmp_path, target) == []


def test_file_result_registers_the_artifact(tmp_path):
    target = tmp_path / "produced.txt"
    target.write_text("body", encoding="utf-8")
    assert _run_tool_node(tmp_path, target) == [target]


def test_read_like_result_on_a_real_file_registers_nothing(tmp_path):
    """A tool that only looked at a file is not a product of the task (#72):
    the path it advertises must not reach ``add_artifact``, even though the
    file exists and would pass the directory guard."""
    target = tmp_path / "read-back.txt"
    target.write_text("body", encoding="utf-8")
    settings = make_settings(tmp_path)
    assert _run_tool_node(tmp_path, target, _ReadLikePathTool(settings, target)) == []


# ─────────── #95: the reason a tool failed reaches the model ───────────
#
# Seam: ``AgentRuntime.tool_node``. Observed on the conversation face
# (``state["messages"]``) — what the executor LLM reads on its next turn — and
# on the event face (``tool_result``), which must keep carrying the same fields.


def _bus_runtime(tools: list, task_id: str = "t95") -> tuple:
    """An ``AgentRuntime`` plus the list its events land in."""
    settings = Settings()
    bus = EventBus()
    events: list = []
    bus.subscribe(task_id, lambda e: events.append(e))
    tm = SimpleNamespace(settings=settings, event_bus=bus, add_artifact=lambda *a: None)
    rt = AgentRuntime(task_id, tm, llm=SimpleNamespace(), tools=tools, tool_schemas=[])
    return rt, events


def test_failed_tool_message_carries_the_reason():
    """The model used to read the literal ``null``: ``ToolResult.error`` went to
    ``rec["error"]`` (human-facing events only), never into ``messages`` (#95)."""
    rt, events = _bus_runtime([_FailTool()])
    state = _state_with_call()
    rt.tool_node(state)

    assert _tool_messages(state) == [
        {"role": "tool", "tool_call_id": "call_1", "content": '{"ok": false, "error": "boom"}'}
    ]
    # The event face keeps every field it had: the model is a NEW reader of the
    # reason, not a replacement for the human one.
    rec = state["_current_tool_calls"][0]
    assert rec["status"] == "failed"
    assert rec["error"] == "boom"
    assert [e["data"]["error"] for e in events if e["type"] == "tool_result"] == ["boom"]


def test_failed_tool_without_a_reason_still_says_it_failed():
    """``ToolResult(success=False)`` with no message at all: the model must not
    fall back to reading nothing — the wording says 「failed」, that is the floor."""
    rt, _events = _bus_runtime([_SilentFailTool()])
    state = _state_with_call("qa_silent_fail")
    rt.tool_node(state)

    assert _tool_messages(state) == [
        {"role": "tool", "tool_call_id": "call_1", "content": '{"ok": false, "error": "tool failed"}'}
    ]


def test_unknown_tool_answers_the_model():
    """The other branch that answered nothing: the name is not in the registry,
    and the conversation kept a silent ``tool_call_id`` (#95)."""
    rt, events = _bus_runtime([])
    state = _state_with_call("ghost_tool")
    rt.tool_node(state)

    assert _tool_messages(state) == [
        {"role": "tool", "tool_call_id": "call_1", "content": '{"ok": false, "error": "unknown tool: ghost_tool"}'}
    ]
    rec = state["_current_tool_calls"][0]
    assert rec["status"] == "failed"
    assert [e["data"]["error"] for e in events if e["type"] == "tool_result"] == [
        "unknown tool: ghost_tool"
    ]


def test_success_content_stays_the_payload_verbatim():
    """Only the failure branches changed: a successful payload is dumped exactly
    as before — same bytes, no ``ok`` / ``error`` wrapper keys (#95)."""
    rt, _events = _bus_runtime([_EchoTool()])
    state = _state_with_call("qa_echo")
    rt.tool_node(state)

    assert _tool_messages(state) == [
        {"role": "tool", "tool_call_id": "call_1", "content": '{"note": "ok 中文", "n": 3}'}
    ]
