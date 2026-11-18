"""Persistence: in-memory store + JSON file on disk (P1-3).

Tasks are kept in memory and periodically flushed to
``<data_dir>/tasks.json``. The interface is intentionally narrow so a
SQLite-backed implementation can replace it later without touching the service
layer.

ADR 0013 decision 1: the current record format is the only readable one. A
record carrying a retired shape is refused with :class:`UnsupportedTasksFileError`
and the file is left byte-for-byte untouched — no read-time backfill, no
migration. Byte-level corruption keeps its own path (preserve the file, start
fresh), because that is damage, not a format this build never wrote.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..api.schemas import Task, TaskStatus
from ..config import Settings
from ..core.runtime_context import reject_non_local_path, workspace_key
from ..utils.logging import get_logger

logger = get_logger("persistence")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PersistenceError(OSError):
    """A mutation could not be written to tasks.json and was rolled back."""


class UnsupportedTasksFileError(RuntimeError):
    """``tasks.json`` holds records this build does not accept (ADR 0013).

    Raised while loading, before anything is written or moved: a record with a
    retired shape (a ``COMPLETED`` status, no workspace, no conversation
    ledger, a step without its 1-based round, a non-object tool-call input) is
    refused with the file path, the record and a next step. The file itself is
    left byte-for-byte untouched — no migration, rewrite, rename or backup.
    """


# Retired record shapes are checked before Pydantic sees the record, so the
# refusal can name the field instead of quoting a validation traceback.
def _retired_problem(item: Any) -> Optional[str]:
    """Why ``item`` belongs to a retired format; None when it is current."""
    if not isinstance(item, dict):
        return "record is not a JSON object"
    status = item.get("status")
    if status not in TaskStatus.__members__:
        return (
            f"status {status!r} is not a current TaskStatus "
            "(COMPLETED was retired by ADR 0008 decision 5)"
        )
    if not str(item.get("workspace") or "").strip():
        return "record has no workspace (records written before ADR 0010)"
    conversation = item.get("conversation")
    if not isinstance(conversation, list) or not conversation:
        return "record has no conversation ledger (records written before #265)"
    steps = item.get("steps") or []
    if not isinstance(steps, list):
        return "steps is not a list"
    for step in steps:
        if not isinstance(step, dict):
            return "step is not a JSON object"
        round_no = step.get("round")
        if isinstance(round_no, bool) or not isinstance(round_no, int) or round_no < 1:
            return (
                f"step {step.get('index')!r} has no 1-based round "
                "(records written before #265)"
            )
        calls = step.get("tool_calls") or []
        if not isinstance(calls, list):
            return "tool_calls is not a list"
        for call in calls:
            if not isinstance(call, dict):
                return "tool call is not a JSON object"
            if not isinstance(call.get("input", {}), dict):
                return (
                    f"tool call {call.get('id')!r} input is not an object "
                    "(records written before the object contract)"
                )
    return None


class Persistence:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.data_path = settings.data_path
        self.data_path.mkdir(parents=True, exist_ok=True)
        # Fallback root for tools that run outside a task context
        # (``runtime_context.workspace_root``); not a task default.
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
            if not isinstance(raw, list):
                raise ValueError("tasks.json is not a JSON array")
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
            return
        self._load_records(raw)

    def _load_records(self, raw: List[Any]) -> None:
        """Load current-contract records; refuse the file on a retired one."""
        for item in raw:
            problem = _retired_problem(item)
            if problem is not None:
                record_id = item.get("id") if isinstance(item, dict) else None
                raise UnsupportedTasksFileError(
                    f"{self._tasks_file} 含本版本不接受的旧任务记录"
                    f"（record={record_id!r}：{problem}）。当前合同要求：status 属于"
                    f" {', '.join(TaskStatus.__members__)}、workspace 非空、"
                    "conversation 从用户第一轮开始、每个 step 带 1-based round、"
                    "tool call input 为对象。文件保持原样未改动：本版本不做迁移，"
                    "请先备份并移走该文件（ADR 0013 决策 1）。"
                )
            try:
                task = Task(**item)
            except Exception:
                logger.warning("Skipping corrupt task record %s", item.get("id"))
                continue
            try:
                # A workspace this build cannot even name is damaged data, not
                # a retired format: drop that record (the file is untouched)
                # instead of refusing the whole history over one bad path.
                reject_non_local_path(task.workspace)
            except ValueError as exc:
                logger.warning(
                    "Skipping task record %s with an unusable workspace: %s",
                    task.id,
                    exc,
                )
                continue
            self._tasks[task.id] = task

    def _dump(self, overrides: Optional[Dict[str, Task]] = None) -> bool:
        # Atomic replace so a crash mid-write can never tear tasks.json.
        # Returns False when the disk was left untouched, so callers whose
        # success promise depends on durability (deletion) can roll back.
        # ``overrides`` serializes detached candidates without adopting them
        # into ``_tasks`` yet (#337: the file is written first, memory second).
        pending = overrides or {}
        tmp_file = self._tasks_file.with_suffix(".json.tmp")
        try:
            data: list[dict[str, Any]] = []
            existing: set[str] = set()
            for task in self._tasks.values():
                existing.add(task.id)
                data.append(pending.get(task.id, task).model_dump(mode="json"))
            data.extend(
                candidate.model_dump(mode="json")
                for task_id, candidate in pending.items()
                if task_id not in existing
            )
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
    def save_task(self, task: Task) -> bool:
        """Persist ``task``; return False when the disk was left untouched.

        A new record is removed again on failure, so a caller cannot observe a
        task that was never durable. Existing records are mutated in place by
        most callers, so they must restore their own fields before treating a
        False return as a rolled-back update.
        """
        with self._lock:
            previous = self._tasks.get(task.id)
            self._tasks[task.id] = task
            if self._dump():
                return True
            if previous is None:
                self._tasks.pop(task.id, None)
            else:
                self._tasks[task.id] = previous
            logger.error(
                "Task %s was not persisted; tasks.json was left unchanged", task.id
            )
            return False

    def save_tasks(self, tasks: List[Task]) -> bool:
        """Persist detached candidates with ONE dump, then adopt them.

        Candidates must be copies: live records are not touched until the
        atomic replace of ``tasks.json`` has succeeded, so a failed dump leaves
        memory and disk on the last durable state. Zero candidates is a
        successful no-op and never writes (#337 S11).
        """
        if not tasks:
            return True
        candidates = {task.id: task for task in tasks}
        with self._lock:
            if not self._dump(candidates):
                logger.error(
                    "%d task record(s) were not persisted; tasks.json was left unchanged",
                    len(candidates),
                )
                return False
            for task_id, candidate in candidates.items():
                live = self._tasks.get(task_id)
                if live is None:
                    self._tasks[task_id] = candidate
                    continue
                for name in Task.model_fields:
                    setattr(live, name, getattr(candidate, name))
            return True

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
