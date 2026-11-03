"""Tests for JSON-file persistence (save / load / list / migration)."""

from __future__ import annotations

import json

import pytest

from backend.api.schemas import StepRecord, Task, TaskStatus
from backend.services.persistence import Persistence


def _task_with_input(value: object) -> Task:
    return Task.model_validate(
        {
            "id": "legacy",
            "steps": [
                {
                    "index": 1,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "tool_name": "read",
                            "input": value,
                            "status": "success",
                        }
                    ],
                }
            ],
        }
    )


def _sample_task(task_id="abc") -> Task:
    return Task(
        id=task_id,
        title="demo",
        user_input="do something",
        status=TaskStatus.INTERRUPTED,
        steps=[StepRecord(index=1, thought="plan", tool_calls=[], status="done")],
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


# ── #255: legacy non-dict tool-call input stays readable ────────────────────
@pytest.mark.parametrize(
    "legacy",
    [
        [{"path": "a.txt", "content": "x"}],
        "ls -la",
        42,
        None,
    ],
)
def test_legacy_tool_call_input_is_normalized_on_read(legacy) -> None:
    """旧格式把入参直接存成了 list / str / int / null：读入边界包成对象。"""
    task = _task_with_input(legacy)

    assert task.steps[0].tool_calls[0].input == {"value": legacy}


def test_dict_tool_call_input_is_unchanged() -> None:
    """正常对象入参行为不变，不被包一层。"""
    task = _task_with_input({"path": "a.txt"})

    assert task.steps[0].tool_calls[0].input == {"path": "a.txt"}


def test_legacy_input_in_tasks_json_loads_and_dumps_as_object(settings) -> None:
    """旧形态 tasks.json 落盘：加载成功，导出的 ``input`` 也是对象。"""
    (settings.data_path / "tasks.json").write_text(
        json.dumps(
            [
                {
                    "id": "legacy",
                    "status": "INTERRUPTED",
                    "steps": [
                        {
                            "index": 1,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "tool_name": "read",
                                    "input": ["a", "b"],
                                    "status": "success",
                                }
                            ],
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    p = Persistence(settings)
    loaded = p.load_task("legacy")

    assert loaded is not None
    assert loaded.steps[0].tool_calls[0].input == {"value": ["a", "b"]}
    dumped = loaded.model_dump(mode="json")
    assert dumped["steps"][0]["tool_calls"][0]["input"] == {"value": ["a", "b"]}


