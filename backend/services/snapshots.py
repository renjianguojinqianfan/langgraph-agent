"""File-level before-image snapshots — the P1-B rollback ledger.

Every sandbox file write first copies the file's *previous* content out of the
sandbox into ``<data_dir>/snapshots/<task_id>/`` and appends one line to a
ledger; rolling back replays that ledger backwards, returning the sandbox to
the state it had before the task's first write.

Terminology (Issue #33, fixed): **snapshot / before-image / ledger (undo log)**
is this module — workspace file rollback. **checkpoint** is the P3 langgraph
graph-state store (``data/checkpoints/``). The two recovery planes are
deliberately independent: ``restore_task`` never touches graph state, and
``TaskManager.resume`` never touches workspace files.

Design rules (Issue #34 拍板 12):

* **fail-open** — a broken snapshot store must never break the tool call that
  follows it (:func:`capture_before_image` returns False and logs);
* **undoable in data** — restoring first photographs the files' current state
  into ``retention/`` (the v2 "undo the undo" entry point reads these);
* **sandbox-root boundary** — only paths inside ``Settings.artifacts_path`` are
  captured or replayed; ledger entries pointing elsewhere are skipped, so a
  tampered ledger cannot write outside the sandbox.

Layout::

    <data_dir>/snapshots/<task_id>/
      ledger.jsonl          # one line per capture, in write order
      0001.bak, …           # the before-image bytes (existed=true entries only)
      retention/
        retention.jsonl     # pre-restore photos (v2 undo source)
        NNNN.bak
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import Settings
from ..utils.logging import get_logger

logger = get_logger("services.snapshots")

LEDGER_NAME = "ledger.jsonl"
RETENTION_DIR = "retention"
RETENTION_LEDGER = "retention.jsonl"

# Task id owned by the current worker thread. Set at the TaskManager worker
# entry points (run / _resume_run) and re-seated by SubAgentExecutor._exec_one,
# because pool threads start with a fresh context. A write tool with no task
# binding is simply not captured — never an error.
_current_task_id: ContextVar[Optional[str]] = ContextVar("snapshot_task_id", default=None)

# One lock for every ledger mutation: subtasks may run on parallel pool threads
# and capture into the same task ledger, so seq allocation and the backup
# filename derived from it must never collide.
_lock = threading.Lock()


def set_current_task_id(task_id: Optional[str]) -> None:
    """Bind (``None`` = clear) the capturing task on the current thread."""
    _current_task_id.set(task_id or None)


def get_current_task_id() -> Optional[str]:
    """Return the task id writes on this thread are accounted to."""
    return _current_task_id.get()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _task_dir(settings: Settings, task_id: str) -> Path:
    return settings.snapshots_path / task_id


def _relative_in_sandbox(path: Path, settings: Settings) -> Optional[str]:
    """Return the POSIX sandbox-relative path, or None when outside the root."""
    try:
        root = settings.artifacts_path.resolve()
        candidate = Path(path).resolve()
        if candidate != root and root not in candidate.parents:
            return None
        return candidate.relative_to(root).as_posix()
    except Exception:
        return None


def _confined(root: Path, rel: str) -> Optional[Path]:
    """Resolve ``rel`` under the sandbox root; None when it escapes."""
    try:
        candidate = (root / rel).resolve()
    except Exception:
        return None
    if candidate != root and root not in candidate.parents:
        return None
    return candidate


def _next_seq(ledger: Path) -> int:
    """Next sequence number = recorded lines + 1 (ledger files stay small)."""
    if not ledger.exists():
        return 1
    with ledger.open("r", encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip()) + 1


def _read_ledger(ledger: Path) -> List[Dict[str, Any]]:
    """Parse the ledger, skipping unreadable lines (one bad line never aborts
    a rollback)."""
    entries: List[Dict[str, Any]] = []
    if not ledger.exists():
        return entries
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except Exception:
            logger.warning("snapshot ledger line skipped (not JSON): %r", line[:120])
            continue
        if isinstance(entry, dict) and entry.get("path"):
            entries.append(entry)
    return entries


def _digest(path: Path) -> Optional[str]:
    """sha256 of a file, or None when it does not exist / is unreadable."""
    try:
        if path.is_file():
            return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return None
    return None


def capture_before_image(path: Path, *, settings: Settings | None = None) -> bool:
    """Record ``path``'s current bytes in the task ledger before it is written.

    Returns True when an entry was recorded; False on every skip or failure —
    this is fail-open by contract (拍板 12①): a broken snapshot store must
    never break the tool call that follows.
    """
    if settings is None or not settings.snapshot_enabled:
        return False
    task_id = get_current_task_id()
    if not task_id:
        return False
    rel = _relative_in_sandbox(path, settings)
    if rel is None:
        return False
    target = Path(path)
    try:
        with _lock:
            task_dir = _task_dir(settings, task_id)
            task_dir.mkdir(parents=True, exist_ok=True)
            ledger = task_dir / LEDGER_NAME
            seq = _next_seq(ledger)
            existed = target.is_file()
            backup: Optional[str] = None
            if existed:
                backup = f"{seq:04d}.bak"
                shutil.copyfile(target, task_dir / backup)
            entry = {
                "seq": seq,
                "path": rel,
                "existed": existed,
                "backup": backup,
                "chars": target.stat().st_size if existed else 0,
                "ts": _now(),
            }
            with ledger.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except Exception as exc:
        logger.warning(
            "snapshot capture failed for %s (%s: %s) — the write proceeds unbacked",
            rel,
            type(exc).__name__,
            exc,
        )
        return False


def _capture_retention(task_dir: Path, root: Path, unique: List[str]) -> None:
    """Photograph the current state of every touched file before restoring.

    拍板 12② (data half): the rollback itself stays undoable in data — the v2
    entry point reads these. Failures are warnings only; a blocked retention
    photo must never block the restore the user asked for.
    """
    try:
        retention = task_dir / RETENTION_DIR
        retention.mkdir(parents=True, exist_ok=True)
        ledger = retention / RETENTION_LEDGER
        seq = _next_seq(ledger)
        with ledger.open("a", encoding="utf-8") as fh:
            for rel in unique:
                source = _confined(root, rel)
                if source is None or not source.is_file():
                    continue
                name = f"{seq:04d}.bak"
                seq += 1
                shutil.copyfile(source, retention / name)
                fh.write(
                    json.dumps(
                        {"seq": seq - 1, "path": rel, "backup": name, "ts": _now()},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
    except Exception as exc:
        logger.warning("retention capture failed (%s: %s) — rollback proceeds", type(exc).__name__, exc)


def _replay_entry(root: Path, task_dir: Path, entry: Dict[str, Any]) -> None:
    """Apply one ledger entry backwards: restore the before-image, or delete a
    file the task created. Skips entries that escape either boundary."""
    rel = str(entry.get("path", ""))
    target = _confined(root, rel)
    if target is None:
        logger.warning("snapshot ledger entry escapes the sandbox; skipped: %r", rel)
        return
    try:
        if entry.get("existed"):
            name = str(entry.get("backup") or "")
            if not name or Path(name).name != name:
                logger.warning("snapshot ledger backup name rejected: %r", name)
                return
            source = task_dir / name
            if not source.is_file():
                logger.warning("before-image %s missing; skipped", source)
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        elif target.is_file():
            target.unlink()
    except Exception as exc:
        logger.warning("replay of %s failed (%s: %s)", rel, type(exc).__name__, exc)


def restore_task(task_id: str, *, settings: Settings) -> Dict[str, Any]:
    """Replay the ledger backwards: the sandbox returns to its pre-task state.

    Whole-task granularity (拍板 10): every file the task touched ends at the
    state it had before the task's *first* write to it. ``files`` lists only
    the paths that actually changed, so calling it twice answers "已是原样"
    (拍板 13) instead of erroring.
    """
    result: Dict[str, Any] = {"ok": True, "files": [], "already_original": True}
    if not settings.snapshot_enabled:
        return result
    task_dir = _task_dir(settings, task_id)
    entries = _read_ledger(task_dir / LEDGER_NAME)
    if not entries:
        return result

    root = settings.artifacts_path.resolve()
    # De-duplicate by path in first-write order: retention and the before/after
    # comparison are per unique file, not per write.
    unique: List[str] = []
    for entry in entries:
        rel = str(entry["path"])
        if rel not in unique:
            unique.append(rel)

    _capture_retention(task_dir, root, unique)
    before = {rel: _digest(root / rel) for rel in unique}

    for entry in reversed(entries):
        _replay_entry(root, task_dir, entry)

    after = {rel: _digest(root / rel) for rel in unique}
    changed = [rel for rel in unique if before[rel] != after[rel]]
    return {"ok": True, "files": changed, "already_original": not changed}


def cleanup_expired(*, settings: Settings) -> int:
    """Drop task snapshot dirs older than ``snapshot_retention_days`` (拍板 11).

    Runs once per TaskManager construction — there is no scheduler host yet, so
    the sweep rides the startup hook that already exists for orphan
    reconciliation. A failed sweep is a warning, never a startup blocker.
    """
    days = int(settings.snapshot_retention_days)
    if days <= 0:
        return 0
    root = settings.snapshots_path
    if not root.is_dir():
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for child in root.iterdir():
        try:
            if not child.is_dir() or child.stat().st_mtime >= cutoff:
                continue
            shutil.rmtree(child)
            removed += 1
            logger.info("Expired snapshot dir removed: %s", child)
        except Exception as exc:  # per-dir isolation
            logger.warning("snapshot cleanup failed for %s: %s", child, exc)
    if removed:
        logger.info("Snapshot cleanup: removed %d expired task dir(s).", removed)
    return removed