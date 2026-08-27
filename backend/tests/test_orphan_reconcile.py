"""Orphan-task reconciliation on TaskManager startup (spec Issue #4, D5).

A crash leaves tasks stuck in RUNNING (or PENDING, before the worker thread
flipped the state) in ``tasks.json`` even though their execution threads are
gone. On construction, TaskManager must reconcile such orphans into
INTERRUPTED (the resumable state) exactly once, and never touch tasks that
already have a terminal status.

Also pins the persistence durability contract the resume feature rests on:
a torn ``tasks.json`` must never silently wipe task history.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.api.schemas import Task, TaskStatus
from backend.core.llm.client import MockLLMClient
from backend.services.persistence import Persistence
from backend.tests.conftest import make_manager, make_settings


def _make_task_record(settings, task_id: str, status: str):
    """Write a raw task record into persistence without going through run()."""
    now = datetime.now(timezone.utc).isoformat()
    persistence = Persistence(settings)
    task = Task(
        id=task_id,
        title=f"orphan {task_id}",
        user_input="demo input",
        status=TaskStatus(status),
        created_at=now,
        updated_at=now,
    )
    persistence.save_task(task)


class TestOrphanReconciliation:
    def test_orphan_running_task_becomes_interrupted_on_startup(self, tmp_path):
        settings = make_settings(tmp_path)
        _make_task_record(settings, "t-orphan", "RUNNING")

        # Rebuild the manager: the fresh process owns no live threads.
        rebuilt = make_manager(settings, MockLLMClient())
        task = rebuilt.get_task("t-orphan")
        assert task is not None
        assert task.status == TaskStatus.INTERRUPTED

    def test_orphan_pending_task_becomes_interrupted_on_startup(self, tmp_path):
        """Crash window between create_task() persist and thread start leaves
        PENDING records; they are equally dead and must be reconciled."""
        settings = make_settings(tmp_path)
        _make_task_record(settings, "t-pending", "PENDING")

        rebuilt = make_manager(settings, MockLLMClient())
        task = rebuilt.get_task("t-pending")
        assert task is not None
        assert task.status == TaskStatus.INTERRUPTED

    def test_terminal_statuses_are_untouched(self, tmp_path):
        settings = make_settings(tmp_path)
        for tid, status in [
            ("t-done", "COMPLETED"),
            ("t-fail", "FAILED"),
            ("t-int", "INTERRUPTED"),
        ]:
            _make_task_record(settings, tid, status)

        rebuilt = make_manager(settings, MockLLMClient())
        assert rebuilt.get_task("t-done").status == TaskStatus.COMPLETED
        assert rebuilt.get_task("t-fail").status == TaskStatus.FAILED
        assert rebuilt.get_task("t-int").status == TaskStatus.INTERRUPTED


class TestPersistenceDurability:
    """The disk contract under review finding #2: a torn write must never
    erase history, and concurrent saves must not corrupt the dump."""

    def test_corrupt_tasks_file_is_preserved_and_start_fresh(self, tmp_path):
        settings = make_settings(tmp_path)
        _make_task_record(settings, "t-keep", "COMPLETED")

        # Corrupt the file the way a torn write would.
        tasks_file = settings.data_path / "tasks.json"
        tasks_file.write_text('{"id": "t-keep"', encoding="utf-8")

        fresh = Persistence(settings)
        assert fresh.load_task("t-keep") is None  # starts clean...
        # ...but the corrupted original is kept for forensics, not deleted.
        backups = list(settings.data_path.glob("tasks.json.corrupt-*"))
        assert backups, "corrupt tasks.json was silently destroyed"

    def test_concurrent_saves_keep_all_records(self, tmp_path):
        import threading

        settings = make_settings(tmp_path)
        persistence = Persistence(settings)
        errors: list[Exception] = []

        def save(i: int) -> None:
            try:
                for j in range(20):
                    now = datetime.now(timezone.utc).isoformat()
                    t = Task(
                        id=f"t-{i}-{j}",
                        title="conc",
                        status=TaskStatus.COMPLETED,
                        created_at=now,
                        updated_at=now,
                    )
                    persistence.save_task(t)
            except Exception as exc:  # pragma: no cover - diagnostic
                errors.append(exc)

        threads = [threading.Thread(target=save, args=(i,)) for i in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert not errors
        assert len(persistence.list_all()) == 160
        reread = Persistence(settings)
        assert len(reread.list_all()) == 160

    def test_list_all_returns_every_task(self, tmp_path):
        settings = make_settings(tmp_path)
        for i in range(60):
            _make_task_record(settings, f"t-{i}", "COMPLETED")
        persistence = Persistence(settings)
        assert len(persistence.list_tasks(limit=50)) == 50  # existing API unchanged
        assert len(persistence.list_all()) == 60
