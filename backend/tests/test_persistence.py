"""Tests for JSON-file persistence (save / load / list / current contract).

ADR 0013 decision 1: ``tasks.json`` is read under the CURRENT contract only.
A record carrying a retired shape is refused readably and the file is left
byte-for-byte untouched — there is no read-time backfill and no migration.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from backend.api.schemas import ChatTurn, StepRecord, Task, TaskStatus, ToolCallRecord
from backend.config import Settings
from backend.services.persistence import Persistence, UnsupportedTasksFileError


def _current_record(task_id: str = "abc", workspace: str = "/work/demo") -> dict:
    """One record in the current contract (every field a new task writes)."""
    return {
        "id": task_id,
        "title": "demo",
        "user_input": "do something",
        "workspace": workspace,
        "model": "default",
        "status": "INTERRUPTED",
        "mode": "build",
        "steps": [
            {
                "index": 1,
                "round": 1,
                "thought": "plan",
                "tool_calls": [
                    {
                        "id": "c1",
                        "tool_name": "read",
                        "input": {"path": "a.txt"},
                        "output": None,
                        "status": "success",
                        "error": None,
                    }
                ],
                "status": "done",
            }
        ],
        "plan_version": 0,
        "plan_hash": "",
        "conversation": [
            {"role": "user", "content": "do something"},
            {"role": "assistant", "content": "done"},
        ],
        "final_answer": "done",
        "error": None,
        "created_at": "2025-01-01T00:00:00+00:00",
        "updated_at": "2025-01-01T00:00:01+00:00",
        "subtasks": [],
    }


def _write_tasks_file(settings: Settings, records: list[dict]) -> bytes:
    settings.data_path.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(records, ensure_ascii=False).encode("utf-8")
    (settings.data_path / "tasks.json").write_bytes(payload)
    return payload


def _sample_task(task_id="abc") -> Task:
    return Task(
        id=task_id,
        title="demo",
        user_input="do something",
        workspace="/work/demo",
        status=TaskStatus.INTERRUPTED,
        conversation=[ChatTurn(role="user", content="do something")],
        steps=[StepRecord(index=1, round=1, thought="plan", tool_calls=[], status="done")],
        final_answer="done",
        error=None,
        created_at="2025-01-01T00:00:00+00:00",
        updated_at="2025-01-01T00:00:01+00:00",
    )


def test_save_and_load_round_trip(settings):
    p = Persistence(settings)
    task = _sample_task()
    assert p.save_task(task) is True

    loaded = p.load_task(task.id)
    assert loaded is not None
    assert loaded.id == task.id
    assert loaded.status == TaskStatus.INTERRUPTED
    assert loaded.final_answer == "done"
    assert loaded.steps[0].index == 1
    assert loaded.steps[0].round == 1


def test_load_missing_returns_none(settings):
    p = Persistence(settings)
    assert p.load_task("does-not-exist") is None


def test_list_tasks_returns_newest_first(settings):
    p = Persistence(settings)
    p.save_task(_sample_task("t1"))
    p.save_task(_sample_task("t2"))
    tasks = p.list_tasks()
    assert len(tasks) == 2
    # list is sorted by created_at desc; both share the same timestamp here so
    # just assert both ids are present.
    assert {t.id for t in tasks} == {"t1", "t2"}


def test_tasks_persisted_to_disk_and_reloadable(settings):
    p = Persistence(settings)
    p.save_task(_sample_task("disk1"))
    # A brand new Persistence instance should read it back from tasks.json.
    p2 = Persistence(settings)
    assert p2.load_task("disk1") is not None


def test_failed_dump_is_reported_and_new_record_is_not_kept(settings, monkeypatch):
    """REL-02: a failed dump must not look like a successful save."""
    p = Persistence(settings)
    task = _sample_task("t-undurable")
    monkeypatch.setattr(p, "_dump", lambda: False)

    assert p.save_task(task) is False
    assert p.load_task(task.id) is None


def test_failed_dump_restores_the_previous_record(settings, monkeypatch):
    p = Persistence(settings)
    original = _sample_task("t-keep")
    assert p.save_task(original)

    replacement = original.model_copy(deep=True)
    replacement.status = TaskStatus.FAILED
    monkeypatch.setattr(p, "_dump", lambda: False)

    assert p.save_task(replacement) is False
    assert p.load_task("t-keep") is original
    assert p.load_task("t-keep").status == TaskStatus.INTERRUPTED


# ── ADR 0013 decision 1: the current format is the only readable one ────────
def test_current_record_round_trips_through_tasks_json(settings):
    """The current format reads back field for field and re-dumps identically."""
    _write_tasks_file(settings, [_current_record()])

    p = Persistence(settings)
    loaded = p.load_task("abc")
    assert loaded is not None
    assert loaded.workspace == "/work/demo"
    assert [t.role for t in loaded.conversation] == ["user", "assistant"]
    assert loaded.steps[0].round == 1
    assert loaded.steps[0].tool_calls[0].input == {"path": "a.txt"}

    assert p.save_task(loaded) is True
    rebuilt = Persistence(settings).load_task("abc")
    assert rebuilt is not None
    assert rebuilt.model_dump(mode="json") == loaded.model_dump(mode="json")
    assert not list(settings.data_path.glob("tasks.json.corrupt-*"))


@pytest.mark.parametrize(
    "mutate, marker",
    [
        (lambda r: r.update(status="COMPLETED"), "COMPLETED"),
        (lambda r: r.update(workspace=""), "workspace"),
        (lambda r: r.update(conversation=[]), "conversation"),
        (
            lambda r: r["steps"][0].pop("round"),
            "round",
        ),
        (
            lambda r: r["steps"][0]["tool_calls"][0].update(input=["a", "b"]),
            "input",
        ),
    ],
    ids=["completed-status", "missing-workspace", "missing-conversation", "step-without-round", "non-object-input"],
)
def test_retired_record_is_refused_and_the_file_is_untouched(settings, mutate, marker):
    record = _current_record()
    mutate(record)
    original = _write_tasks_file(settings, [record])

    with pytest.raises(UnsupportedTasksFileError) as excinfo:
        Persistence(settings)

    message = str(excinfo.value)
    assert "tasks.json" in message
    assert marker in message
    # Refusal never rewrites, renames or backs up the unsupported file.
    assert (settings.data_path / "tasks.json").read_bytes() == original
    assert not list(settings.data_path.glob("tasks.json.corrupt-*"))


def test_a_current_record_before_the_retired_one_is_not_a_partial_load(settings):
    """Refusal is whole-file: no half history comes back before the raise."""
    _write_tasks_file(settings, [_current_record("good"), {"id": "old", "status": "COMPLETED"}])

    with pytest.raises(UnsupportedTasksFileError):
        Persistence(settings)


# ── schema face: the write path can only produce the current shape ──────────
def test_dict_tool_call_input_is_unchanged() -> None:
    """A current record's object input is neither wrapped nor rewritten."""
    call = ToolCallRecord(id="c1", tool_name="read", input={"path": "a.txt"})

    assert call.input == {"path": "a.txt"}


@pytest.mark.parametrize("retired", [["a", "b"], "ls -la", 42, None])
def test_non_object_tool_call_input_is_rejected_at_the_schema(retired) -> None:
    with pytest.raises(ValidationError):
        ToolCallRecord(id="c1", tool_name="read", input=retired)


def test_step_round_is_required_and_one_based() -> None:
    with pytest.raises(ValidationError):
        StepRecord.model_validate({"index": 1})
    with pytest.raises(ValidationError):
        StepRecord.model_validate({"index": 1, "round": 0})


def test_task_requires_a_workspace_and_a_conversation() -> None:
    with pytest.raises(ValidationError):
        Task.model_validate({"id": "x"})
    with pytest.raises(ValidationError):
        Task.model_validate({"id": "x", "workspace": ""})
    with pytest.raises(ValidationError):
        Task.model_validate({"id": "x", "workspace": "/work/demo", "conversation": []})
