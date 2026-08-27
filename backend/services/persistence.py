"""Persistence: in-memory store + JSON file on disk (P1-3).

Tasks and artifacts are kept in memory and periodically flushed to
``<data_dir>/tasks.json``. Artifact *files* live under ``artifacts_dir``. The
interface is intentionally narrow so a SQLite-backed implementation can replace
it later without touching the service layer.
"""

from __future__ import annotations

import json
import mimetypes
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import Settings
from ..utils.logging import get_logger
from ..api.schemas import Artifact, Task

logger = get_logger("persistence")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Persistence:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.data_path = settings.data_path
        self.artifacts_path = settings.artifacts_path
        self.data_path.mkdir(parents=True, exist_ok=True)
        self.artifacts_path.mkdir(parents=True, exist_ok=True)
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
                    task = Task(**item)
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

    def _dump(self) -> None:
        # Atomic replace so a crash mid-write can never tear tasks.json.
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

    # ── tasks ──
    def save_task(self, task: Task) -> None:
        with self._lock:
            self._tasks[task.id] = task
            self._dump()

    def load_task(self, task_id: str) -> Optional[Task]:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self, limit: int = 50) -> List[Task]:
        with self._lock:
            ordered = sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)
        return ordered[:limit]

    def list_all(self) -> List[Task]:
        """Every persisted task, newest first (no limit).

        Exists for startup-time reconciliation sweeps that must observe all
        records regardless of the interactive listing limit.
        """
        with self._lock:
            return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)

    # ── artifacts ──
    def register_artifact(self, path: Path) -> Artifact:
        """Record an existing file on disk as an :class:`Artifact`."""
        path = Path(path)
        stat = path.stat()
        mime, _ = mimetypes.guess_type(str(path))
        art = Artifact(
            id=uuid.uuid4().hex,
            filename=path.name,
            path=str(path),
            mime=mime or "application/octet-stream",
            size=stat.st_size,
            created_at=_now(),
        )
        return art

    def read_artifact(self, artifact_id: str) -> Optional[bytes]:
        for task in self._tasks.values():
            for art in task.artifacts:
                if art.id == artifact_id:
                    p = Path(art.path)
                    if p.exists():
                        return p.read_bytes()
        return None

    def find_artifact(self, task_id: str, artifact_id: str) -> Optional[Artifact]:
        task = self._tasks.get(task_id)
        if not task:
            return None
        for art in task.artifacts:
            if art.id == artifact_id:
                return art
        return None
