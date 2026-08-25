"""Tests for the LangGraph orchestration kernel.

Exercises ``build_graph`` + :class:`AgentRuntime` end-to-end with a scripted
:class:`MockLLMClient` (no LLM key, no network):

* the graph compiles;
* a full task produces a ``final_answer`` and a persisted artifact;
* ``stop`` interrupts the loop within 2 seconds (P0-9);
* the ``human_confirm`` node pauses *before* a dangerous (requires_confirm) tool
  and only proceeds after approval (and skips after rejection) (P1-2).

Also wraps the engineer's offline smoke test so it runs under pytest.
"""

from __future__ import annotations

import threading
import time

from backend.core.agent.graph import build_graph
from backend.core.agent.nodes import AgentRuntime
from backend.core.llm.client import MockLLMClient
from backend.tests.conftest import make_manager
from backend.tests.test_smoke import main as smoke_main


def _run_until_done(tm, task_id, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = tm.get_task(task_id)
        if task and task.status.value in ("COMPLETED", "FAILED", "INTERRUPTED"):
            return task
        time.sleep(0.05)
    return tm.get_task(task_id)


def _auto_confirm_in_background(tm, event_bus, task_id, approved, timeout=10):
    """Poll the event buffer and confirm/reject any pending confirmation."""

    def _watcher():
        deadline = time.time() + timeout
        while time.time() < deadline:
            for ev in event_bus.replay(task_id):
                if ev["type"] == "human_confirm_required":
                    tm.confirm(task_id, ev["data"]["tool_call_id"], approved)
                    return
            time.sleep(0.01)

    t = threading.Thread(target=_watcher, daemon=True)
    t.start()
    return t


# ── build / compile ──
def test_build_graph_compiles(settings):
    runtime = AgentRuntime(
        task_id="t", task_manager=None, llm=None, tools=[], tool_schemas=[], max_steps=5
    )
    graph = build_graph(runtime)
    assert graph is not None


def test_build_graph_runs_and_produces_final_answer(settings, event_bus):
    mock = MockLLMClient(
        plan=["Plan and act"],
        tool_calls=[
            {
                "id": "c1",
                "name": "file_io",
                "arguments": {"action": "write", "path": "out.txt", "content": "result"},
            }
        ],
        final_answer="I wrote out.txt.",
    )
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="demo", user_input="write a file")
    task = _run_until_done(tm, task_id)

    assert task is not None
    assert task.status.value == "COMPLETED"
    assert "out.txt" in task.final_answer
    assert any(a.filename == "out.txt" for a in task.artifacts)

    events = {e["type"] for e in event_bus.replay(task_id)}
    assert "plan_update" in events
    assert "tool_call" in events
    assert "tool_result" in events
    assert "artifact_created" in events
    assert "final_answer" in events
    assert "task_completed" in events


# ── stop within 2s (P0-9) ──
class _SlowMockLLMClient(MockLLMClient):
    """Mock client that sleeps a little each call so the run stays RUNNING
    long enough for us to exercise the stop signal."""

    def complete(self, messages, tools=None, **kwargs):
        time.sleep(0.15)
        return super().complete(messages, tools, **kwargs)


def test_stop_interrupts_loop_within_two_seconds(settings, event_bus):
    mock = _SlowMockLLMClient(
        plan=["p"],
        tool_calls=[
            {"id": f"c{i}", "name": "file_io", "arguments": {"action": "write", "path": f"f{i}.txt", "content": "x"}}
            for i in range(8)
        ],
        final_answer="never reached",
    )
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="slow", user_input="do many steps")

    # Wait until the task is actually RUNNING, then stop.
    stop_at = None
    deadline = time.time() + 10
    while time.time() < deadline:
        task = tm.get_task(task_id)
        if task and task.status.value == "RUNNING":
            stop_at = time.time()
            tm.stop(task_id)
            break
        if task and task.status.value not in ("PENDING", "RUNNING"):
            break
        time.sleep(0.02)

    task = _run_until_done(tm, task_id)
    done_at = time.time()

    assert stop_at is not None, "task never entered RUNNING state for stop test"
    assert task.status.value == "INTERRUPTED"
    # The interrupt must take effect well within the 2s requirement.
    assert (done_at - stop_at) <= 2.0

    events = {e["type"] for e in event_bus.replay(task_id)}
    assert "task_interrupted" in events


# ── human confirmation (P1-2) ──
def test_human_confirm_pauses_then_proceeds_on_approval(settings, event_bus):
    mock = MockLLMClient(
        plan=["p"],
        tool_calls=[
            {
                "id": "cc1",
                "name": "code_exec",
                "arguments": {"language": "python", "code": "print(1 + 1)"},
            }
        ],
        final_answer="Ran the code.",
    )
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="code", user_input="run python")
    _auto_confirm_in_background(tm, event_bus, task_id, approved=True)

    task = _run_until_done(tm, task_id)

    assert task.status.value == "COMPLETED"
    events = event_bus.replay(task_id)
    assert "human_confirm_required" in {e["type"] for e in events}
    # The code_exec tool actually executed (approval granted).
    tool_results = [e["data"] for e in events if e["type"] == "tool_result"]
    assert any(
        r.get("tool_name") == "code_exec" and r.get("status") == "success"
        for r in tool_results
    )


def test_human_confirm_skips_tool_on_rejection(settings, event_bus):
    mock = MockLLMClient(
        plan=["p"],
        tool_calls=[
            {
                "id": "cc1",
                "name": "code_exec",
                "arguments": {"language": "python", "code": "print(1 + 1)"},
            }
        ],
        final_answer="Skipped the code.",
    )
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="code", user_input="run python")
    _auto_confirm_in_background(tm, event_bus, task_id, approved=False)

    task = _run_until_done(tm, task_id)

    assert task.status.value == "COMPLETED"
    events = event_bus.replay(task_id)
    assert "human_confirm_required" in {e["type"] for e in events}
    tool_results = [e["data"] for e in events if e["type"] == "tool_result"]
    assert any(
        r.get("tool_name") == "code_exec" and r.get("status") == "skipped"
        for r in tool_results
    )


# ── engineer smoke (offline) included in the unified run ──
def test_engineer_smoke_passes():
    assert smoke_main() == 0
