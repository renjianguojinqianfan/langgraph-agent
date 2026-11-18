"""S3 (#330): the three lifecycle entries converge on one execution core.

The acceptance shape (issue #330): whichever door a round comes through —
new task (``run``), resumed task (``_resume_run``), continued conversation
(``_continue_run``) — the same events come out from the first ``step_start``
on, the same parked record is written (no dangling step or tool call), and a
crash takes the same FAILED + ``task_failed`` path. Admission (status +
checkpoint + trace attach) and trace order stay on the request side and are
pinned by ``test_lifecycle_failures.py`` / ``test_resume.py``.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import pytest

from backend.api.schemas import ChatTurn, Task, TaskStatus
from backend.core.llm.client import MockLLMClient
from backend.services.persistence import Persistence
from backend.services.task_manager import TaskManager
from backend.tests.conftest import assert_no_in_flight, make_manager, make_settings

AGENT_DIR = Path(__file__).resolve().parents[1] / "core" / "agent"
PARKED = (TaskStatus.INTERRUPTED, TaskStatus.FAILED)


def _wait_parked(tm, task_id: str, timeout: float = 15.0) -> Task:
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = tm.get_task(task_id)
        if task is not None and task.status in PARKED:
            return task
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} never parked")


def _event_types(tm, task_id: str) -> List[str]:
    return [e["type"] for e in tm.event_bus.replay(task_id)]


def _round_types(tm, task_id: str, start: int, end: int) -> List[str]:
    """One round's event types, from its first ``step_start`` on."""
    types = _event_types(tm, task_id)[start:end]
    if "step_start" not in types:
        return []
    return types[types.index("step_start"):]


def _parked_record(settings, task_id: str = "t-parked") -> Task:
    now = datetime.now(timezone.utc).isoformat()
    task = Task(
        id=task_id,
        title="parked",
        user_input="hi",
        workspace=str(settings.artifacts_path),
        status=TaskStatus.INTERRUPTED,
        conversation=[ChatTurn(role="user", content="hi")],
        created_at=now,
        updated_at=now,
    )
    assert Persistence(settings).save_task(task)
    return task


class _AliveThread(threading.Thread):
    """A worker that refuses to finish: ``join`` returns, ``is_alive`` stays."""

    def is_alive(self) -> bool:
        return True

    def join(self, timeout: float | None = None) -> None:
        return None


# ── success path: one sequence, one terminal record ─────────────────────────
def test_new_resumed_and_continued_rounds_emit_the_same_sequence(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, checkpoint_enabled=True)
    tm = make_manager(settings, MockLLMClient(final_answer="answer"))
    task_id = tm.create_task(title="s3", user_input="hello")
    first = _wait_parked(tm, task_id)
    marks = [len(_event_types(tm, task_id))]
    steps = [len(first.steps)]

    tm.resume(task_id)
    second = _wait_parked(tm, task_id)
    marks.append(len(_event_types(tm, task_id)))
    steps.append(len(second.steps))

    tm.send_message(task_id, "again")
    third = _wait_parked(tm, task_id)
    marks.append(len(_event_types(tm, task_id)))
    steps.append(len(third.steps))

    rounds = [
        _round_types(tm, task_id, 0, marks[0]),
        _round_types(tm, task_id, marks[0], marks[1]),
        _round_types(tm, task_id, marks[1], marks[2]),
    ]
    # One answer, no tools: step_start → final_answer → park. All three doors.
    assert rounds == [
        ["step_start", "final_answer", "task_interrupted"],
        ["step_start", "final_answer", "task_interrupted"],
        ["step_start", "final_answer", "task_interrupted"],
    ]
    assert [task.status for task in (first, second, third)] == [TaskStatus.INTERRUPTED] * 3
    assert steps == [1, 2, 3]
    # The persisted record (not the in-memory object) carries no in-flight work.
    assert_no_in_flight(tm.get_task(task_id))


# ── one core: every entry delegates to the single round executor ───────────
def test_every_entry_hands_the_round_to_one_executor(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path, checkpoint_enabled=True)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    entries: List[str] = []
    original = TaskManager._execute_round

    def spy(self, task_id, prepare, **kwargs):
        entries.append(kwargs["entry"])
        return original(self, task_id, prepare, **kwargs)

    monkeypatch.setattr(TaskManager, "_execute_round", spy)

    task_id = tm.create_task(title="s3-core", user_input="hi")
    _wait_parked(tm, task_id)
    tm.resume(task_id)
    _wait_parked(tm, task_id)
    tm.send_message(task_id, "more")
    _wait_parked(tm, task_id)

    assert entries == ["run", "resume", "continuation"]


# ── crash path: FAILED + task_failed + no dangling work ────────────────────
@pytest.mark.parametrize("entry", ["run", "resume", "continuation"])
def test_a_crashed_round_is_failed_and_announced_the_same_way(
    tmp_path: Path, monkeypatch, entry: str
) -> None:
    settings = make_settings(tmp_path, checkpoint_enabled=True)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))

    def boom(task, final):
        raise RuntimeError("boom after the round")

    if entry == "run":
        monkeypatch.setattr(tm, "_finalize_terminal", boom)
        task_id = tm.create_task(title="s3-fail", user_input="hi")
    else:
        task_id = tm.create_task(title="s3-fail", user_input="hi")
        _wait_parked(tm, task_id)
        monkeypatch.setattr(tm, "_finalize_terminal", boom)
        if entry == "resume":
            tm.resume(task_id)
        else:
            tm.send_message(task_id, "again")

    task = _wait_parked(tm, task_id)
    assert task.status == TaskStatus.FAILED
    assert "boom after the round" in (task.error or "")
    assert "task_failed" in _event_types(tm, task_id)
    assert_no_in_flight(tm.get_task(task_id))


# ── admission: join a worker that will not quit ────────────────────────────
@pytest.mark.parametrize("entry", ["resume", "send_message"])
def test_an_unwinding_worker_keeps_the_next_round_out(tmp_path: Path, entry: str) -> None:
    settings = make_settings(tmp_path)
    _parked_record(settings)
    tm = make_manager(settings, MockLLMClient())
    tm._threads["t-parked"] = _AliveThread()

    with pytest.raises(RuntimeError, match="still shutting down"):
        if entry == "resume":
            tm.resume("t-parked")
        else:
            tm.send_message("t-parked", "next")

    parked = tm.get_task("t-parked")
    assert parked is not None and parked.status == TaskStatus.INTERRUPTED


# ── superseded worker still closes its trace ───────────────────────────────
@pytest.mark.parametrize("worker", ["_resume_run", "_continue_run"])
def test_a_superseded_worker_still_closes_its_trace(tmp_path: Path, worker: str) -> None:
    settings = make_settings(tmp_path)
    _parked_record(settings)
    tm = make_manager(settings, MockLLMClient())
    # The request side attached the trace before flipping the record; a stop()
    # in that window leaves the worker superseded — the file must still end.
    tm._attach_trace("t-parked", strict=True)

    if worker == "_resume_run":
        tm._resume_run("t-parked")
    else:
        tm._continue_run("t-parked", "next")

    assert tm._trace is not None
    trace_file = tm._trace.file_path("t-parked")
    lines = [
        json.loads(line)
        for line in trace_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert lines and lines[-1]["type"] == "trace_end"
    parked = tm.get_task("t-parked")
    assert parked is not None and parked.status == TaskStatus.INTERRUPTED


# ── host surface: a Protocol, not getattr fallbacks for doubles ────────────
def test_the_agent_core_has_no_host_getattr_fallbacks() -> None:
    """A double must provide what the nodes read (#330 AC3).

    The retired fallbacks papered over test fakes that lacked ``settings`` /
    ``is_stop_flagged`` / ``redact``; they also made a real wiring mistake
    silent in production. The two remaining ``getattr(self.llm, "model", "")``
    reads probe the LLM double, not the host.
    """
    for name in ("nodes.py", "subagent.py"):
        source = (AGENT_DIR / name).read_text(encoding="utf-8")
        assert "getattr(getattr(" not in source, name
        assert 'getattr(self, "tm"' not in source, name
        assert "getattr(self.tm" not in source, name
        assert 'getattr(tm, "is_stop_flagged"' not in source, name
        assert "getattr(settings," not in source, name
        assert "._stop_flags" not in source, name
        assert "._tools" not in source, name
