"""Persistence: in-memory store + JSON file on disk (P1-3).

Tasks and artifacts are kept in memory and periodically flushed to
``<data_dir>/tasks.json``. Artifact *files* live under ``artifacts_dir``. The
interface is intentionally narrow so a SQLite-backed implementation can replace
it later without touching the service layer.
"""

from __future__ import annotations

import json
import mimetypes
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
        self._tasks: Dict[str, Task] = {}
        self._load()

    def _load(self) -> None:
        if not self._tasks_file.exists():
            return
        try:
            raw = json.loads(self._tasks_file.read_text(encoding="utf-8"))
            for item in raw:
                try:
                    self._tasks[item["id"]] = Task(**item)
                except Exception:
                    logger.warning("Skipping corrupt task record %s", item.get("id"))
        except Exception:
            logger.warning("Could not load tasks.json; starting fresh.")

    def _dump(self) -> None:
        try:
            data = [t.model_dump(mode="json") for t in self._tasks.values()]
            self._tasks_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            logger.exception("Failed to persist tasks")

    # ── tasks ──
    def save_task(self, task: Task) -> None:
        self._tasks[task.id] = task
        self._dump()

    def load_task(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def list_tasks(self, limit: int = 50) -> List[Task]:
        ordered = sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)
        return ordered[:limit]

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
