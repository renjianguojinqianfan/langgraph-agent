"""S8 (#334): every runtime event carries an explicit revision.

The web store must decide "is this snapshot / event / continuation answer
newer?" from `(round, step, plan_version)` — never from a step-array length.
The backend publishes those fields with each event so the decision is made
against an explicit sequence:

* round: the 1-based conversation round (#265);
* step: the cumulative step index inside that round;
* plan_version: the plan file version the event was produced under.

Task-level events (`task_created` / `task_resumed` / `task_interrupted` /
`task_failed`) carry `round` and `plan_version`; step-level events also carry
`step`. Missing fields on pre-S8 trace lines are tolerated by the reader.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List

from backend.api.schemas import TaskStatus
from backend.core.llm.client import MockLLMClient
from backend.tests.conftest import make_manager, make_settings


def _wait_parked(tm, task_id: str, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = tm.get_task(task_id)
        if task is not None and task.status in (
            TaskStatus.INTERRUPTED,
            TaskStatus.FAILED,
        ):
            return task
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} never parked")


def _replay(tm, task_id: str) -> List[Dict[str, Any]]:
    return tm.event_bus.replay(task_id)


def test_step_and_task_events_carry_round_step_and_plan_version(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(final_answer="answer"))
    task_id = tm.create_task(title="revision", user_input="hello")
    _wait_parked(tm, task_id)

    events = _replay(tm, task_id)
    by_type: Dict[str, Dict[str, Any]] = {}
    for event in events:
        by_type[event["type"]] = event["data"]
        data = event["data"]
        assert data["round"] == 1, event
        assert data["plan_version"] == 0, event

    assert by_type["step_start"]["step"] == 1
    assert by_type["llm_call"]["step"] == 1
    assert by_type["final_answer"]["step"] == 1
    # A task-level park still states the round/version and the highest step it
    # had settled, so a later replay can be told apart from the live stream.
    assert by_type["task_interrupted"]["step"] == 1
    assert by_type["task_created"]["round"] == 1
    assert by_type["task_created"]["step"] == 0


def test_a_resumed_round_publishes_the_next_round_number(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, checkpoint_enabled=True)
    tm = make_manager(settings, MockLLMClient(final_answer="answer"))
    task_id = tm.create_task(title="revision", user_input="hello")
    _wait_parked(tm, task_id)

    # resume() continues the same conversation round (no new user turn).
    tm.resume(task_id)
    _wait_parked(tm, task_id)
    resumed = [e for e in _replay(tm, task_id) if e["type"] == "task_resumed"]
    assert len(resumed) == 1
    assert resumed[0]["data"]["round"] == 1
    assert resumed[0]["data"]["plan_version"] == 0

    # A continuation adds a user turn: the new round is 2.
    tm.send_message(task_id, "again")
    _wait_parked(tm, task_id)
    resumed = [e for e in _replay(tm, task_id) if e["type"] == "task_resumed"]
    assert len(resumed) == 2
    assert resumed[1]["data"]["round"] == 2
    second_round = [e for e in _replay(tm, task_id) if e["data"].get("round") == 2]
    assert second_round
    assert second_round[-1]["type"] == "task_interrupted"
    assert second_round[-1]["data"]["plan_version"] == 0


def test_a_stop_during_a_new_round_reports_the_live_round(tmp_path: Path) -> None:
    """The persisted record can lag the live round while steps are in flight.

    `stop()` must stamp the event with the round the running state owns, not
    the last round saved to disk — otherwise the web store treats the real
    stop as a replay and leaves the new round RUNNING.
    """
    from datetime import datetime, timezone
    from typing import cast

    from backend.api.schemas import ChatTurn, StepRecord, Task
    from backend.core.agent.state import AgentState

    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(final_answer="answer"))
    now = datetime.now(timezone.utc).isoformat()
    task = Task(
        id="t-live",
        title="live",
        user_input="hello",
        workspace=str(settings.artifacts_path),
        status=TaskStatus.RUNNING,
        steps=[StepRecord(index=1, round=1, status="done")],
        conversation=[ChatTurn(role="user", content="hello")],
        created_at=now,
        updated_at=now,
    )
    assert tm.persistence.save_task(task)
    tm._active_states[task.id] = cast(AgentState, {"round_no": 2, "step_index": 7})

    tm.stop(task.id)

    stops = [e for e in _replay(tm, task.id) if e["type"] == "task_interrupted"]
    assert stops
    assert stops[-1]["data"]["round"] == 2
    assert stops[-1]["data"]["step"] == 7
