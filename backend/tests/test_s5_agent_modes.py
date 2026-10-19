"""S5 (#139 / ADR 0008): one loop, plan/build modes, parking and continuation.

Pins the acceptance criteria at the seams the slice actually moved:

* switching the mode switches the tool face (plan = the declared ``explore``
  tier, build = everything mounted) — and the dispatch guard, not just the
  advertised schemas;
* a round with no tool call parks the task (there is no COMPLETED);
* a parked task continues from the same conversation, checkpoints included;
* the step budget is per round, so a long conversation cannot brick itself;
* the plan is editable while parked, and the edit lands in the next round.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, cast

import pytest
from fastapi.testclient import TestClient

from backend.api.schemas import TaskStatus
from backend.core.agent.nodes import AgentRuntime
from backend.core.agent.state import AgentState
from backend.core.agent.subagent import EXPLORE_TIER, tier_face_names
from backend.core.llm.client import LLMResponse, MockLLMClient
from backend.core.tools.registry import build_tools
from backend.main import app
from backend.services.event_bus import EventBus
from backend.services.persistence import Persistence
from backend.tests.conftest import make_manager, make_settings


# ── helpers ──────────────────────────────────────────────────────────────────
class _RecordingLLM(MockLLMClient):
    """Scripted mock that records the tool face offered on every call."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.offered: List[List[str]] = []
        self.systems: List[str] = []

    def complete(self, messages, tools=None, **kwargs):
        self.offered.append(sorted(t["function"]["name"] for t in (tools or [])))
        self.systems.append(str((messages or [{}])[0].get("content") or ""))
        return super().complete(messages, tools, **kwargs)


def _runtime(settings, llm, tools) -> AgentRuntime:
    tm = SimpleNamespace(
        settings=settings,
        event_bus=EventBus(),
        add_artifact=lambda *a, **k: None,
        is_stop_flagged=lambda _tid: False,
        _active_states={},
    )
    return AgentRuntime(
        "t1",
        tm,
        llm=llm,
        tools=tools,
        tool_schemas=[t.to_openai_schema() for t in tools],
    )


def _state(**kw: Any) -> AgentState:
    base: Dict[str, Any] = {
        "task_id": "t1",
        "mode": "build",
        "messages": [{"role": "user", "content": "do the thing"}],
        "plan": [],
        "steps": [],
        "artifacts": [],
        "status": "RUNNING",
        "stop_requested": False,
        "step_index": 0,
        "round_steps": 0,
        "final_answer": "",
        "error": "",
        "_last_action": "",
        "_current_tool_calls": [],
        "subtasks": [],
        "_is_subtask": False,
    }
    base.update(kw)
    # Partial/extra keys by design: these tests drive one or two nodes.
    return cast(AgentState, base)


def _wait_parked(tm, task_id: str, timeout: float = 15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = tm.get_task(task_id)
        if task and task.status in (TaskStatus.INTERRUPTED, TaskStatus.FAILED):
            return task
        time.sleep(0.05)
    return tm.get_task(task_id)


class _LoopingLLM(MockLLMClient):
    """Always asks for another write; only the step budget can stop it."""

    def __init__(self) -> None:
        super().__init__(plan=["p"], final_answer="never reached")
        self.calls = 0

    def complete(self, messages, tools=None, **kwargs):
        self.calls += 1
        return LLMResponse(
            content="",
            tool_calls=[
                {
                    "id": f"c{self.calls}",
                    "name": "write",
                    "arguments": {"path": f"loop_{self.calls}.txt", "content": "x"},
                }
            ],
        )


# ── mode -> tool face ───────────────────────────────────────────────────────
def test_plan_mode_advertises_only_the_explore_tier(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    tools = build_tools(settings)
    mock = _RecordingLLM(final_answer="done")
    rt = _runtime(settings, mock, tools)

    rt.agent(_state(mode="plan"))
    expected = tier_face_names([t.name for t in tools], EXPLORE_TIER)
    assert mock.offered[-1] == sorted(expected)
    assert "write" not in mock.offered[-1]
    assert "bash" not in mock.offered[-1]  # C3: shell access stays out of plan mode
    assert "spawn_subagent" not in mock.offered[-1]

    rt.agent(_state(mode="build"))
    assert "write" in mock.offered[-1]
    assert set(mock.offered[-1]) == {t.name for t in tools}


def test_plan_mode_refuses_to_dispatch_a_tool_outside_its_face(tmp_path: Path) -> None:
    """The face is enforced at dispatch, not only advertised in the schema list."""
    settings = make_settings(tmp_path)
    tools = build_tools(settings)
    rt = _runtime(settings, MockLLMClient(final_answer="done"), tools)
    state = _state(
        mode="plan",
        steps=[{"index": 1, "thought": "", "status": "running", "tool_calls": []}],
        _current_tool_calls=[
            {
                "id": "c1",
                "tool_name": "write",
                "input": {"path": "should_not_exist.txt", "content": "x"},
                "output": None,
                "status": "pending",
                "error": "",
            }
        ],
    )

    rt.tool_node(state)

    rec = state["_current_tool_calls"][0]
    assert rec["status"] == "failed"
    assert "not available in plan mode" in rec["error"]
    assert not (Path(settings.artifacts_path) / "should_not_exist.txt").exists()


# ── a round ends = no tool call; the task can continue ──────────────────────
def test_a_round_without_tool_calls_parks_and_the_next_message_continues(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path, checkpoint_enabled=True)
    tm = make_manager(settings, MockLLMClient(final_answer="answer one"))

    task_id = tm.create_task(title="chat", user_input="hello")
    first = _wait_parked(tm, task_id)
    assert first.status == TaskStatus.INTERRUPTED
    assert [t.role for t in first.conversation] == ["user", "assistant"]
    assert first.final_answer == "answer one"

    claimed = tm.send_message(task_id, "and one more thing")
    assert claimed["status"] == "RUNNING"
    # The user's turn is visible immediately, before the round finishes.
    assert claimed["task"]["conversation"][-1] == {
        "role": "user",
        "content": "and one more thing",
    }

    second = _wait_parked(tm, task_id)
    assert second.status == TaskStatus.INTERRUPTED
    assert [t.role for t in second.conversation] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    # Two rounds: cumulative step indices, not a reset record.
    assert [s.index for s in second.steps] == [1, 2]
    events = [e["type"] for e in tm.event_bus.replay(task_id)]
    assert events.count("task_resumed") == 1
    assert events.count("task_interrupted") == 2  # one per parked round


def test_stop_on_a_parked_task_does_not_poison_the_next_round(tmp_path: Path) -> None:
    """A second 停止 click (or a stop against a parked task) must be a no-op.

    The stop flag is authoritative and only popped by a run's teardown; setting
    it on a parked task would make the next round abort at its first node entry,
    losing the user's message.
    """
    settings = make_settings(tmp_path, checkpoint_enabled=True)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    task_id = tm.create_task(title="stop", user_input="hi")
    parked = _wait_parked(tm, task_id)
    assert parked is not None
    before = len(parked.steps)

    tm.stop(task_id)
    tm.stop(task_id)  # double click

    tm.send_message(task_id, "continue")
    after = _wait_parked(tm, task_id)
    assert after is not None
    assert len(after.steps) == before + 1
    assert after.conversation[-1].role == "assistant"
    assert after.conversation[-1].content == "ok"


def test_resume_uses_the_edited_plan(tmp_path: Path) -> None:
    """The no-new-message `resume` path must seed the persisted plan too.

    Otherwise a plan saved while parked is overwritten by the checkpoint's old
    copy at finalize, i.e. the API said 200 and the edit disappeared.
    """
    settings = make_settings(tmp_path, checkpoint_enabled=True)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    task_id = tm.create_task(title="resume-plan", user_input="hi")
    _wait_parked(tm, task_id)
    tm.set_plan(task_id, [{"description": "edited step", "status": "active"}])

    tm.resume(task_id)
    after = _wait_parked(tm, task_id)
    assert after is not None
    assert [(p.description, p.status) for p in after.plan] == [("edited step", "active")]


class _ProsePlanLLM:
    """Plan mode replies with a question instead of a JSON plan."""

    def complete(self, messages, tools=None, **kwargs):
        return LLMResponse(
            content="Which folder should the report go in?",
            reasoning_content="我需要先确认路径",
        )


def test_plan_mode_keeps_the_plan_when_the_model_asks_back(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    rt = _runtime(settings, _ProsePlanLLM(), [])
    old = [{"index": 1, "description": "previous plan step", "status": "pending"}]
    state = _state(mode="plan", plan=old)

    rt.agent(state)

    # The question reaches the user; the plan is not replaced by a fake step.
    assert state["plan"] == old
    assert state["final_answer"] == "Which folder should the report go in?"
    assert state["messages"][-1]["reasoning_content"] == "我需要先确认路径"


def test_plan_mode_parses_a_fenced_json_plan(tmp_path: Path) -> None:
    class _FencedLLM:
        def complete(self, messages, tools=None, **kwargs):
            return LLMResponse(
                content='```json\n[{"description": "one"}, {"description": "two"}]\n```'
            )

    settings = make_settings(tmp_path)
    rt = _runtime(settings, _FencedLLM(), [])
    state = _state(mode="plan")

    rt.agent(state)

    assert [p["description"] for p in state["plan"]] == ["one", "two"]


def test_plan_edit_lands_in_the_next_round(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, checkpoint_enabled=True)
    tm = make_manager(
        settings, MockLLMClient(plan=["old step"], final_answer="built")
    )

    task_id = tm.create_task(title="p", user_input="plan then build", mode="plan")
    parked = _wait_parked(tm, task_id)
    assert [p.description for p in parked.plan] == ["old step"]

    tm.set_plan(task_id, [{"description": "edited step", "status": "active"}])
    tm.set_mode(task_id, "build")
    tm.send_message(task_id, "go")

    after = _wait_parked(tm, task_id)
    # The persisted plan is the source of truth: the edit seeded the next round
    # and came back through finalize instead of the checkpoint's old copy.
    assert [(p.description, p.status) for p in after.plan] == [("edited step", "active")]
    events = tm.event_bus.replay(task_id)
    plan_events = [e["data"]["plan"] for e in events if e["type"] == "plan_update"]
    assert any(p and p[0]["description"] == "edited step" for p in plan_events)


def test_step_budget_is_per_round_not_per_task(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, checkpoint_enabled=True, max_steps=2)
    tm = make_manager(settings, _LoopingLLM())

    task_id = tm.create_task(title="budget", user_input="loop")
    first = _wait_parked(tm, task_id)
    assert first.status == TaskStatus.INTERRUPTED
    assert len(first.steps) == 2  # the budget stopped the round

    tm.send_message(task_id, "keep going")
    second = _wait_parked(tm, task_id)
    assert len(second.steps) == 4  # a fresh round gets a fresh budget
    assert second.status == TaskStatus.INTERRUPTED


def test_continuation_survives_a_process_restart(tmp_path: Path) -> None:
    """「停下后接着说能接着跑，含进程重启后」— on-disk record + checkpoint.

    The first manager is shut down the way a process exit does; the second one
    sees only the persisted record and the sqlite store.
    """
    settings = make_settings(tmp_path, checkpoint_enabled=True)
    tm1 = make_manager(settings, MockLLMClient(final_answer="first"))
    task_id = tm1.create_task(title="restart", user_input="hi")
    first = _wait_parked(tm1, task_id)
    assert first is not None
    tm1.shutdown()

    tm2 = make_manager(settings, MockLLMClient(final_answer="second"))
    restored = tm2.get_task(task_id)
    assert restored is not None
    assert restored.mode.value == "build"  # sticky across restart

    tm2.send_message(task_id, "after restart")
    after = _wait_parked(tm2, task_id)
    assert after is not None
    assert [t.content for t in after.conversation][-2:] == ["after restart", "second"]
    assert after.status == TaskStatus.INTERRUPTED


def test_default_mode_is_build_and_sticky(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, checkpoint_enabled=True)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    task_id = tm.create_task(title="m", user_input="hi")
    parked = _wait_parked(tm, task_id)
    assert parked is not None
    assert parked.mode.value == "build"

    tm.set_mode(task_id, "plan")
    switched = tm.get_task(task_id)
    assert switched is not None
    assert switched.mode.value == "plan"
    tm.send_message(task_id, "plan please")
    _wait_parked(tm, task_id)
    again = tm.get_task(task_id)
    assert again is not None
    assert again.mode.value == "plan"  # sticky


# ── REST wiring ─────────────────────────────────────────────────────────────
def _api_client(tmp_path: Path):
    settings = make_settings(tmp_path, checkpoint_enabled=True)
    eb = EventBus()
    persistence = Persistence(settings)
    tm = make_manager(
        settings,
        MockLLMClient(plan=["step"], final_answer="ok"),
        event_bus=eb,
        persistence=persistence,
    )
    # The manager under test is injected on the app the TestClient drives.
    app.state.settings = settings
    app.state.event_bus = eb
    app.state.persistence = persistence
    app.state.task_manager = tm
    return TestClient(app, base_url="http://localhost"), tm


def test_rest_mode_plan_messages(tmp_path: Path) -> None:
    client, tm = _api_client(tmp_path)
    task_id = tm.create_task(title="api", user_input="hi")
    _wait_parked(tm, task_id)

    assert client.put("/api/tasks/nope/mode", json={"mode": "plan"}).status_code == 404
    assert client.put(f"/api/tasks/{task_id}/mode", json={"mode": "bogus"}).status_code == 422
    r = client.put(f"/api/tasks/{task_id}/mode", json={"mode": "plan"})
    assert r.status_code == 200
    assert r.json()["data"]["mode"] == "plan"

    r = client.put(
        f"/api/tasks/{task_id}/plan",
        json={"plan": [{"description": "hand written"}]},
    )
    assert r.status_code == 200
    assert r.json()["data"]["plan"][0]["description"] == "hand written"

    r = client.post(f"/api/tasks/{task_id}/messages", json={"input": "continue"})
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "RUNNING"
    parked = _wait_parked(tm, task_id)
    assert parked is not None
    assert parked.mode.value == "plan"  # the switch was set to plan earlier
    assert [p.description for p in parked.plan] == ["step"]


@pytest.mark.parametrize(
    "status", [TaskStatus.RUNNING, TaskStatus.PENDING, TaskStatus.FAILED]
)
def test_rest_plan_edit_is_refused_when_not_parked(
    tmp_path: Path, status: TaskStatus
) -> None:
    """Only a parked task is editable (the route and the UI say the same)."""
    client, tm = _api_client(tmp_path)
    task_id = tm.create_task(title="api2", user_input="hi")
    _wait_parked(tm, task_id)  # no live worker: the status alone is the gate
    task = tm.get_task(task_id)
    assert task is not None
    task.status = status
    tm.persistence.save_task(task)

    r = client.put(f"/api/tasks/{task_id}/plan", json={"plan": [{"description": "x"}]})
    assert r.status_code == 409


# ── retired terminal value ──────────────────────────────────────────────────
def test_completed_is_not_a_live_status(tmp_path: Path) -> None:
    assert "COMPLETED" not in TaskStatus.__members__

    # Pre-S5 records still load, mapped to the one "停下" (ADR 0008 decision 5).
    settings = make_settings(tmp_path)
    (tmp_path / "tasks.json").write_text(
        json.dumps([{"id": "legacy", "status": "COMPLETED", "user_input": "old"}]),
        encoding="utf-8",
    )
    rebuilt = Persistence(settings)
    loaded = rebuilt.load_task("legacy")
    assert loaded is not None
    assert loaded.status == TaskStatus.INTERRUPTED
