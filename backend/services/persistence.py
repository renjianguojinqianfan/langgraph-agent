"""Persistence: in-memory store + JSON file on disk (P1-3).

Tasks are kept in memory and periodically flushed to
``<data_dir>/tasks.json``. The interface is intentionally narrow so a
SQLite-backed implementation can replace it later without touching the service
layer.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..api.schemas import Task
from ..config import Settings
from ..core.runtime_context import normalize_workspace, workspace_key
from ..utils.logging import get_logger

logger = get_logger("persistence")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PersistenceError(OSError):
    """A mutation could not be written to tasks.json and was rolled back."""


class Persistence:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.data_path = settings.data_path
        self.data_path.mkdir(parents=True, exist_ok=True)
        # The historical/default workspace for tasks without an explicit dir.
        settings.artifacts_path.mkdir(parents=True, exist_ok=True)
        self._tasks_file = self.data_path / "tasks.json"
        # Worker threads run save_task concurrently with the request thread;
        # every mutation of ``self._tasks`` and the dump below is serialized.
        self._lock = threading.Lock()
        self._tasks: Dict[str, Task] = {}
        self._load()

    def _load(self) -> None:
        if not self._tasks_file.exists():
            return
        try:
            raw = json.loads(self._tasks_file.read_text(encoding="utf-8"))
            for item in raw:
                try:
                    # ADR 0008 decision 5 / slice S5: ``COMPLETED`` is retired —
                    # "完成" is just another "停下". Records written before S5
                    # still carry it, so map it on load instead of dropping the
                    # user's history as a corrupt record.
                    if item.get("status") == "COMPLETED":
                        item["status"] = "INTERRUPTED"
                    task = Task(**item)
                    # ADR 0010: pre-workspace records lived in the old sandbox root.
                    task.workspace = normalize_workspace(
                        task.workspace or self.settings.artifacts_path
                    )
                except Exception:
                    logger.warning("Skipping corrupt task record %s", item.get("id"))
                    continue
                self._tasks[task.id] = task
        except Exception:
            # Spec Issue #4: a torn/partial tasks.json must never silently wipe
            # history. Preserve it for forensics, then start fresh.
            backup = self.data_path / f"tasks.json.corrupt-{_now().replace(':', '')}"
            try:
                os.replace(self._tasks_file, backup)
                logger.error(
                    "tasks.json unreadable; preserved as %s and starting fresh.",
                    backup.name,
                )
            except OSError:
                logger.exception("tasks.json unreadable and could not be archived.")
            else:
                return  # file moved away; do not fall through to re-read it

    def _dump(self) -> bool:
        # Atomic replace so a crash mid-write can never tear tasks.json.
        # Returns False when the disk was left untouched, so callers whose
        # success promise depends on durability (deletion) can roll back.
        tmp_file = self._tasks_file.with_suffix(".json.tmp")
        try:
            data = [
                t.model_dump(mode="json") for t in list(self._tasks.values())
            ]
            tmp_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            try:
                os.replace(tmp_file, self._tasks_file)
            except PermissionError:
                # Windows: AV scanners / editors can hold the target briefly.
                # Short backoff, then one retry before giving up this round.
                time.sleep(0.05)
                os.replace(tmp_file, self._tasks_file)
        except Exception:
            logger.exception("Failed to persist tasks")
            try:
                tmp_file.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        return True

    # ── tasks ──
    def save_task(self, task: Task) -> None:
        with self._lock:
            self._tasks[task.id] = task
            self._dump()

    def load_task(self, task_id: str) -> Optional[Task]:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(
        self, limit: int = 50, workspace: Optional[str] = None
    ) -> List[Task]:
        with self._lock:
            ordered = sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)
        if workspace is not None:
            key = workspace_key(workspace)
            ordered = [t for t in ordered if workspace_key(t.workspace) == key]
        return ordered[:limit]

    def list_all(self) -> List[Task]:
        """Every persisted task, newest first (no limit).

        Exists for startup-time reconciliation sweeps that must observe all
        records regardless of the interactive listing limit.
        """
        with self._lock:
            return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)

    # ── deletion (#241): history only, never the workspace files ──
    def _delete_locked(self, task_ids: List[str]) -> int:
        """Remove records and persist; roll the memory back if the dump fails.

        Callers must hold ``self._lock``. A deletion is only reported as done
        when the disk agrees: otherwise the record would reappear after a
        restart while its checkpoint/trace had already been cleaned up.
        """
        removed = {
            task_id: self._tasks.pop(task_id)
            for task_id in task_ids
            if task_id in self._tasks
        }
        if not removed:
            return 0
        if not self._dump():
            self._tasks.update(removed)
            raise PersistenceError(
                "could not persist the history deletion; tasks.json was left unchanged"
            )
        return len(removed)

    def delete_task(self, task_id: str) -> bool:
        with self._lock:
            return self._delete_locked([task_id]) == 1

    def delete_tasks(self, task_ids: List[str]) -> int:
        with self._lock:
            return self._delete_locked(task_ids)

