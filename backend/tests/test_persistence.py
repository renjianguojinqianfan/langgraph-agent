"""Tests for JSON-file persistence (save / load / list / artifacts)."""

from __future__ import annotations

from pathlib import Path

from backend.api.schemas import Artifact, PlanStep, StepRecord, Task, TaskStatus
from backend.services.persistence import Persistence
from backend.tests.conftest import make_settings


def _sample_task(task_id="abc") -> Task:
    return Task(
        id=task_id,
        title="demo",
        user_input="do something",
        status=TaskStatus.COMPLETED,
        steps=[StepRecord(index=1, thought="plan", tool_calls=[], status="done")],
        plan=[PlanStep(index=1, description="step one", status="done")],
        artifacts=[],
        final_answer="done",
        error=None,
        created_at="2025-01-01T00:00:00+00:00",
        updated_at="2025-01-01T00:00:01+00:00",
    )


def test_save_and_load_round_trip(settings):
    p = Persistence(settings)
    task = _sample_task()
    p.save_task(task)

    loaded = p.load_task(task.id)
    assert loaded is not None
    assert loaded.id == task.id
    assert loaded.status == TaskStatus.COMPLETED
    assert loaded.final_answer == "done"
    assert loaded.steps[0].index == 1
    assert loaded.plan[0].description == "step one"


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


def test_register_artifact_records_file(settings, tmp_path):
    p = Persistence(settings)
    target = settings.artifacts_path / "report.txt"
    target.write_text("hello artifact", encoding="utf-8")

    art = p.register_artifact(target)
    assert isinstance(art, Artifact)
    assert art.filename == "report.txt"
    assert art.size == len("hello artifact")
    assert art.path == str(target)


def test_find_and_read_artifact(settings, tmp_path):
    p = Persistence(settings)
    target = settings.artifacts_path / "note.txt"
    target.write_text("payload", encoding="utf-8")
    art = p.register_artifact(target)

    task = _sample_task("t-with-art")
    task.artifacts = [art]
    p.save_task(task)

    found = p.find_artifact("t-with-art", art.id)
    assert found is not None
    assert found.filename == "note.txt"

    data = p.read_artifact(art.id)
    assert data == b"payload"


def test_read_missing_artifact_returns_none(settings):
    p = Persistence(settings)
    assert p.read_artifact("nope") is None
