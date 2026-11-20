"""Round changes: capture successful ``write`` / ``edit`` calls (#343).

Contract: ``docs/specs/workspace-files-changes-ui.md`` §5.1–5.3.

Capture is a hook around a tool execution, not a change to the tool:

* :class:`ChangesRecorder` reads a touched file's content *before* dispatch;
* after a **successful** result it hands both bytes to :class:`ChangesStore`,
  which keeps the first-touch ``before`` and the latest ``after`` for the
  ``(task, round, path)`` triple;
* at round end :meth:`ChangesStore.finalize_round` re-reads the current file so
  later shell writes inside the same round are reflected in ``after``.

Storage (``<changes_dir>/<task_id>/<round>/<index>/{meta.json,before,after}``)
never leaves this module's directory through an API: callers only ever see the
task-relative POSIX path. Content stays in sidecars — never in ``tasks.json``,
trace, SSE or logs.
"""

from __future__ import annotations

import difflib
import json
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional

from ..config import Settings
from ..core.runtime_context import reject_non_local_path
from ..core.workspace_files import resolve_in_workspace
from ..utils.logging import get_logger

logger = get_logger("changes")

#: Hard caps from the #343 contract. Instance constructor arguments exist for
#: tests and for a future settings surface; production uses these defaults.
CHANGES_MAX_FILES = 500
CHANGES_MAX_FILE_BYTES = 2 * 1024 * 1024
CHANGES_MAX_ROUND_BYTES = 64 * 1024 * 1024
CHANGES_MAX_RENDERED_LINES = 2000
CHANGES_DIFF_TIMEOUT_SEC = 0.1

_TASK_ID_CHARS = set("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-")
_CONTEXT_LINES = 3


def _is_safe_task_id(task_id: str) -> bool:
    return bool(task_id) and len(task_id) <= 64 and set(task_id) <= _TASK_ID_CHARS


def _is_safe_rel_path(path: object) -> bool:
    """A task-relative POSIX path we are willing to echo through the API.

    Sidecars live under the local data directory; a hand-edited ``meta.json``
    must not turn an absolute / UNC / traversal string into an API payload.
    """
    if not isinstance(path, str) or not path or "\x00" in path:
        return False
    if path.startswith("/") or "\\" in path:
        return False
    pure = PurePosixPath(path)
    if pure.is_absolute():
        return False
    parts = pure.parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        return False
    first = parts[0]
    if len(first) == 2 and first[1] == ":" and first[0].isalpha():
        return False
    try:
        reject_non_local_path(path)
    except ValueError:
        return False
    return True


def _decode(raw: Optional[bytes]) -> tuple[Optional[str], bool]:
    """``(text, binary)`` for one side; absent stays ``(None, False)``."""
    if raw is None:
        return None, False
    if b"\x00" in raw:
        return None, True
    try:
        return raw.decode("utf-8"), False
    except UnicodeDecodeError:
        return None, True


def _line_count(text: Optional[str]) -> int:
    return len(text.splitlines()) if text is not None else 0


def _stats(before: str, after: str, timeout_sec: float) -> tuple[int, int, bool]:
    """``(added, deleted, coarse)`` for a text change.

    The timeout is measured around difflib. A timeout (or a non-positive
    budget) degrades to one coarse whole-file replacement so the result is
    deterministic instead of a partial diff.
    """
    old = before.splitlines()
    new = after.splitlines()
    started = time.perf_counter()
    if timeout_sec <= 0:
        return len(new), len(old), True
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    opcodes = matcher.get_opcodes()
    if time.perf_counter() - started > timeout_sec:
        return len(new), len(old), True
    added = deleted = 0
    for tag, i1, i2, j1, j2 in opcodes:
        if tag in ("replace", "delete"):
            deleted += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, deleted, False


def _coarse_hunk(old: List[str], new: List[str]) -> Dict[str, Any]:
    return {
        "oldStart": 1 if old else 0,
        "oldLines": len(old),
        "newStart": 1 if new else 0,
        "newLines": len(new),
        "lines": [f"-{line}" for line in old] + [f"+{line}" for line in new],
    }


def _hunks(before: str, after: str, coarse: bool) -> List[Dict[str, Any]]:
    old = before.splitlines()
    new = after.splitlines()
    if coarse:
        return [_coarse_hunk(old, new)]
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    groups = list(matcher.get_grouped_opcodes(_CONTEXT_LINES))
    hunks: List[Dict[str, Any]] = []
    for group in groups:
        i1 = group[0][1]
        j1 = group[0][3]
        old_lines = sum(i2 - i1_ for _, i1_, i2, _, _ in group)
        new_lines = sum(j2 - j1_ for _, _, _, j1_, j2 in group)
        lines: List[str] = []
        for tag, a1, a2, b1, b2 in group:
            if tag == "equal":
                lines.extend(f" {line}" for line in old[a1:a2])
            elif tag == "delete":
                lines.extend(f"-{line}" for line in old[a1:a2])
            elif tag == "insert":
                lines.extend(f"+{line}" for line in new[b1:b2])
            else:  # replace
                lines.extend(f"-{line}" for line in old[a1:a2])
                lines.extend(f"+{line}" for line in new[b1:b2])
        hunks.append(
            {
                # Unified convention: a zero-length side keeps the boundary
                # index as its start (0 for an insertion before line 1).
                "oldStart": i1 + 1 if old_lines else i1,
                "oldLines": old_lines,
                "newStart": j1 + 1 if new_lines else j1,
                "newLines": new_lines,
                "lines": lines,
            }
        )
    return hunks


def _trim_hunks(
    hunks: List[Dict[str, Any]], limit: int
) -> tuple[List[Dict[str, Any]], bool]:
    """Cap rendered diff lines at ``limit``; returns ``(hunks, truncated)``."""
    rendered = sum(len(h["lines"]) for h in hunks)
    if rendered <= limit:
        return hunks, False
    trimmed: List[Dict[str, Any]] = []
    remaining = limit
    for hunk in hunks:
        lines = hunk["lines"]
        if remaining <= 0:
            break
        kept = lines[:remaining]
        remaining -= len(kept)
        old_lines = sum(1 for line in kept if line[:1] in (" ", "-"))
        new_lines = sum(1 for line in kept if line[:1] in (" ", "+"))
        trimmed.append({**hunk, "oldLines": old_lines, "newLines": new_lines, "lines": kept})
    return trimmed, True


def _atomic_write(path: Path, payload: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(payload)
    tmp.replace(path)


def _atomic_json(path: Path, data: Mapping[str, Any]) -> None:
    _atomic_write(path, json.dumps(data, ensure_ascii=False).encode("utf-8"))


def _read_meta(record_dir: Path) -> Optional[Dict[str, Any]]:
    try:
        raw = json.loads((record_dir / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or not _is_safe_rel_path(raw.get("path")):
        return None
    if not _is_safe_rel_path(raw.get("display")):
        raw["display"] = raw["path"]
    return raw


@dataclass(frozen=True)
class PendingCapture:
    """The before-side snapshot for one in-flight write/edit call."""

    round_no: int
    path: str
    tool_call_id: str
    existed: bool
    before: Optional[bytes]
    before_oversized: bool = False


class ChangesStore:
    """Thread-safe sidecar store for per-round net changes."""

    def __init__(
        self,
        settings: Settings,
        *,
        max_files: int = CHANGES_MAX_FILES,
        max_file_bytes: int = CHANGES_MAX_FILE_BYTES,
        max_round_bytes: int = CHANGES_MAX_ROUND_BYTES,
        max_rendered_lines: int = CHANGES_MAX_RENDERED_LINES,
        diff_timeout_sec: float = CHANGES_DIFF_TIMEOUT_SEC,
    ) -> None:
        self.root = settings.changes_path
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes
        self.max_round_bytes = max_round_bytes
        self.max_rendered_lines = max_rendered_lines
        self.diff_timeout_sec = diff_timeout_sec
        self._lock = threading.RLock()
        #: Per-round path -> index and stored sidecar bytes. Built lazily from
        #: disk (restart / first touch) and updated on every record; without it
        #: a 500-file round is O(n²) meta reads.
        self._indexes: Dict[tuple[str, int], Dict[str, int]] = {}
        self._round_bytes: Dict[tuple[str, int], int] = {}

    # ── paths ──
    def _task_dir(self, task_id: str) -> Optional[Path]:
        if not _is_safe_task_id(task_id):
            return None
        candidate = self.root / task_id
        try:
            if candidate.is_symlink():
                # A link in the local data tree is not a task directory.
                return None
        except OSError:
            return None
        return candidate

    def _round_dir(self, task_id: str, round_no: int) -> Optional[Path]:
        task_dir = self._task_dir(task_id)
        if task_dir is None or round_no < 1:
            return None
        candidate = task_dir / str(round_no)
        try:
            if candidate.is_symlink():
                return None
        except OSError:
            return None
        return candidate

    def _records(self, round_dir: Path) -> List[tuple[int, Path, Dict[str, Any]]]:
        with self._lock:
            self._recover_reindex(round_dir)
            records: List[tuple[int, Path, Dict[str, Any]]] = []
            try:
                children = list(round_dir.iterdir())
            except OSError:
                return records
            for child in children:
                try:
                    if not child.is_dir() or child.is_symlink() or not child.name.isdigit():
                        continue
                except OSError:
                    continue
                meta = _read_meta(child)
                if meta is not None:
                    records.append((int(child.name), child, meta))
            return sorted(
                records, key=lambda item: (item[2].get("display", ""), item[2]["path"])
            )

    @staticmethod
    def _recover_reindex(round_dir: Path) -> None:
        """Restore records a crash left under ``.reindex-N`` temp names.

        ``finalize_round`` renames every record to its display position in two
        phases; a kill between the phases must not make the captured changes
        disappear. Append the temp dirs back under free numeric indices; a
        later finalize (or read) will sort them again.
        """
        try:
            children = list(round_dir.iterdir())
        except OSError:
            return
        temps = sorted(
            (
                child
                for child in children
                if child.is_dir()
                and not child.is_symlink()
                and child.name.startswith(".reindex-")
            ),
            key=lambda child: child.name,
        )
        if not temps:
            return
        numeric = [
            int(child.name)
            for child in children
            if child.is_dir() and child.name.isdigit()
        ]
        next_index = max(numeric, default=-1) + 1
        for temp in temps:
            try:
                temp.rename(round_dir / str(next_index))
            except OSError:
                return
            next_index += 1

    def _ensure_index(
        self, task_id: str, round_no: int, round_dir: Path
    ) -> Dict[str, int]:
        key = (task_id, round_no)
        cached = self._indexes.get(key)
        if cached is not None:
            return cached
        index: Dict[str, int] = {}
        for number, _record_dir, meta in self._records(round_dir):
            index[meta["path"]] = number
        self._indexes[key] = index
        self._round_bytes[key] = self._stored_bytes(round_dir)
        return index

    @staticmethod
    def _stored_bytes(round_dir: Path) -> int:
        total = 0
        try:
            for record_dir in round_dir.iterdir():
                if not record_dir.is_dir():
                    continue
                for name in ("before", "after"):
                    try:
                        total += (record_dir / name).stat().st_size
                    except OSError:
                        pass
        except OSError:
            return 0
        return total

    # ── capture ──
    def record(
        self,
        task_id: str,
        round_no: int,
        path: str,
        *,
        before_existed: bool,
        before: Optional[bytes],
        after: Optional[bytes],
        tool_call_id: str,
        before_oversized: bool = False,
        after_oversized: bool = False,
    ) -> None:
        """Persist one successful write/edit; first touch keeps its ``before``."""
        round_dir = self._round_dir(task_id, round_no)
        if round_dir is None:
            return
        with self._lock:
            round_dir.mkdir(parents=True, exist_ok=True)
            index_map = self._ensure_index(task_id, round_no, round_dir)
            key = (task_id, round_no)
            existing_index = index_map.get(path)
            if existing_index is not None:
                index = existing_index
                record_dir = round_dir / str(index)
                meta = _read_meta(record_dir)
                if meta is None:
                    meta = {}
                before_existed = bool(meta.get("had_before", before_existed))
                if before_existed:
                    try:
                        before = (record_dir / "before").read_bytes()
                    except OSError:
                        before = None
                else:
                    before = None
                tool_call_id = str(meta.get("tool_call_id") or tool_call_id)
                binary = bool(meta.get("binary"))
                oversized = bool(meta.get("oversized"))
                coarse = bool(meta.get("coarse"))
                prev_own = self._own_stored(record_dir)
            else:
                index = max(index_map.values(), default=-1) + 1
                record_dir = round_dir / str(index)
                binary = oversized = coarse = False
                prev_own = 0
            record_dir.mkdir(parents=True, exist_ok=True)
            if record_dir.is_symlink():
                return

            before_text, before_binary = _decode(before)
            after_text, after_binary = _decode(after)
            binary = binary or before_binary or after_binary
            before_size = len(before) if before is not None else 0
            after_size = len(after) if after is not None else 0
            oversized = oversized or before_oversized or after_oversized or (
                before_size > self.max_file_bytes or after_size > self.max_file_bytes
            )
            if not binary and not oversized:
                projected = (
                    self._round_bytes.get(key, 0) - prev_own + before_size + after_size
                )
                if projected > self.max_round_bytes:
                    oversized = True

            added = deleted = 0
            if not binary and not oversized:
                added, deleted, coarse = _stats(
                    before_text or "", after_text or "", self.diff_timeout_sec
                )
            else:
                coarse = False

            status = "modified" if before_existed else "added"
            meta = {
                "path": path,
                "display": path,
                "status": status,
                "added": added,
                "deleted": deleted,
                "binary": binary,
                "oversized": oversized,
                "coarse": coarse,
                "sizes": {"before": before_size, "after": after_size},
                "tool_call_id": tool_call_id,
                "had_before": before_existed,
                "finalized": False,
            }
            self._write_record(record_dir, meta, before, after, binary, oversized)
            stored = 0 if (binary or oversized) else before_size + after_size
            self._round_bytes[key] = max(
                0, self._round_bytes.get(key, 0) - prev_own + stored
            )
            index_map[path] = index

    @staticmethod
    def _own_stored(record_dir: Path) -> int:
        size = 0
        for name in ("before", "after"):
            try:
                size += (record_dir / name).stat().st_size
            except OSError:
                pass
        return size

    def _reindex(self, task_id: str, round_no: int, round_dir: Path) -> None:
        """Make ``index`` the stable position sorted by ``display``."""
        records = self._records(round_dir)
        if not records:
            return
        if all(number == position for position, (number, _, _) in enumerate(records)):
            return
        temps: List[Path] = []
        for position, (_number, record_dir, _meta) in enumerate(records):
            tmp = round_dir / f".reindex-{position}"
            record_dir.rename(tmp)
            temps.append(tmp)
        for position, tmp in enumerate(temps):
            tmp.rename(round_dir / str(position))
        key = (task_id, round_no)
        self._indexes.pop(key, None)
        self._round_bytes.pop(key, None)

    @staticmethod
    def _write_record(
        record_dir: Path,
        meta: Dict[str, Any],
        before: Optional[bytes],
        after: Optional[bytes],
        binary: bool,
        oversized: bool,
    ) -> None:
        def sidecar(name: str, payload: Optional[bytes]) -> None:
            target = record_dir / name
            if payload is None or binary or oversized:
                target.unlink(missing_ok=True)
            else:
                _atomic_write(target, payload)

        sidecar("before", before)
        sidecar("after", after)
        _atomic_json(record_dir / "meta.json", meta)

    # ── round end ──
    def finalize_round(self, task_id: str, round_no: int, workspace: Path) -> None:
        """Re-read every touched path and settle this round's net changes.

        A missing current file with a stored ``before`` is ``deleted`` (current
        tools never delete, so this is a shell/out-of-band edge only). A
        finalized record is never rewritten: resume opens a new round, and a
        startup sweep must not erase what the round already produced.

        A missing/renamed workspace is **not** evidence that its files were
        deleted: startup reconciliation finalizes every persisted task, and
        rewriting meta there would silently corrupt the stored review history.
        Leave sidecars exactly as the last successful round end left them.
        """
        try:
            if not workspace.is_dir():
                return
        except OSError:
            return
        round_dir = self._round_dir(task_id, round_no)
        if round_dir is None or not round_dir.is_dir():
            return
        with self._lock:
            for _index, record_dir, meta in self._records(round_dir):
                if meta.get("finalized"):
                    continue
                path = str(meta["path"])
                try:
                    reject_non_local_path(path)
                except ValueError:
                    continue
                target = resolve_in_workspace(workspace, path)
                after: Optional[bytes] = None
                after_too_large = False
                after_size_stat = 0
                missing = True
                if target is not None and target.is_file():
                    try:
                        after_size_stat = target.stat().st_size
                        if after_size_stat <= self.max_file_bytes:
                            after = target.read_bytes()
                        else:
                            # Too large to read is oversized by definition; do
                            # not load it into memory just to prove it.
                            after_too_large = True
                        missing = False
                    except OSError:
                        missing = True

                before_existed = bool(meta.get("had_before"))
                before: Optional[bytes] = None
                if before_existed:
                    try:
                        before = (record_dir / "before").read_bytes()
                    except OSError:
                        before = None
                binary = bool(meta.get("binary"))
                oversized = bool(meta.get("oversized"))
                if missing:
                    if not before_existed:
                        # A file created and deleted inside one round has no net
                        # content; keep the record so nothing is silently lost.
                        meta.update(
                            {
                                "status": "deleted",
                                "added": 0,
                                "deleted": 0,
                                "sizes": {"before": 0, "after": 0},
                                "finalized": True,
                            }
                        )
                        self._write_record(record_dir, meta, None, None, binary, oversized)
                        continue
                    status = "deleted"
                else:
                    status = "modified" if before_existed else "added"

                before_text, before_binary = _decode(before)
                after_text, after_binary = _decode(after)
                binary = binary or before_binary or after_binary
                before_size = len(before) if before is not None else 0
                after_size = (
                    after_size_stat if after_too_large else (len(after) if after is not None else 0)
                )
                oversized = oversized or after_too_large or (
                    before_size > self.max_file_bytes or after_size > self.max_file_bytes
                )
                coarse = False
                added = deleted = 0
                if status == "deleted":
                    deleted = _line_count(before_text)
                elif not binary and not oversized:
                    added, deleted, coarse = _stats(
                        before_text or "", after_text or "", self.diff_timeout_sec
                    )
                meta.update(
                    {
                        "status": status,
                        "added": added,
                        "deleted": deleted,
                        "binary": binary,
                        "oversized": oversized,
                        "coarse": coarse,
                        "sizes": {"before": before_size, "after": after_size},
                        "finalized": True,
                    }
                )
                self._write_record(record_dir, meta, before, after, binary, oversized)
            self._reindex(task_id, round_no, round_dir)

    def finalize_task(self, task_id: str, workspace: Path) -> None:
        task_dir = self._task_dir(task_id)
        if task_dir is None or not task_dir.is_dir():
            return
        try:
            rounds = [int(child.name) for child in task_dir.iterdir() if child.is_dir() and child.name.isdigit()]
        except OSError:
            return
        for round_no in sorted(rounds):
            self.finalize_round(task_id, round_no, workspace)

    # ── read ──
    def read_round(self, task_id: str, round_no: int) -> Dict[str, Any]:
        round_dir = self._round_dir(task_id, round_no)
        records = self._records(round_dir) if round_dir is not None else []
        files: List[Dict[str, Any]] = []
        added = deleted = 0
        for index, _record_dir, meta in records:
            added += int(meta.get("added", 0) or 0)
            deleted += int(meta.get("deleted", 0) or 0)
            if len(files) >= self.max_files:
                continue
            files.append(
                {
                    "index": index,
                    "path": meta["path"],
                    "display": meta.get("display") or meta["path"],
                    "status": meta.get("status", "modified"),
                    "added": int(meta.get("added", 0) or 0),
                    "deleted": int(meta.get("deleted", 0) or 0),
                    "binary": bool(meta.get("binary")),
                    "oversized": bool(meta.get("oversized")),
                }
            )
        return {
            "round": round_no,
            "total": len(records),
            "truncated": len(records) > self.max_files,
            "added": added,
            "deleted": deleted,
            "files": files,
        }

    def read_detail(self, task_id: str, round_no: int, index: int) -> Optional[Dict[str, Any]]:
        round_dir = self._round_dir(task_id, round_no)
        if round_dir is None or index < 0:
            return None
        record_dir = round_dir / str(index)
        try:
            if record_dir.is_symlink():
                return None
        except OSError:
            return None
        meta = _read_meta(record_dir)
        if meta is None:
            return None
        binary = bool(meta.get("binary"))
        oversized = bool(meta.get("oversized"))
        coarse = bool(meta.get("coarse"))
        hunks: List[Dict[str, Any]] = []
        if not binary and not oversized:
            try:
                before = (record_dir / "before").read_bytes()
            except OSError:
                before = None
            try:
                after = (record_dir / "after").read_bytes()
            except OSError:
                after = None
            before_text, _ = _decode(before)
            after_text, _ = _decode(after)
            hunks = _hunks(before_text or "", after_text or "", coarse)
        hunks, truncated = _trim_hunks(hunks, self.max_rendered_lines)
        return {
            "path": meta["path"],
            "status": meta.get("status", "modified"),
            "added": int(meta.get("added", 0) or 0),
            "deleted": int(meta.get("deleted", 0) or 0),
            "binary": binary,
            "oversized": oversized,
            "coarse": coarse,
            "truncated": truncated,
            "hunks": hunks,
        }

    # ── lifecycle ──
    def cleanup_task(self, task_id: str) -> bool:
        if not _is_safe_task_id(task_id):
            return False
        task_dir = self.root / task_id
        with self._lock:
            try:
                if task_dir.is_symlink():
                    task_dir.unlink(missing_ok=True)
                elif task_dir.exists():
                    shutil.rmtree(task_dir, ignore_errors=True)
                else:
                    return False
            except OSError:
                return False
        return True

    def reconcile(self, live_task_ids: set[str]) -> int:
        """Remove task directories that no persisted task owns."""
        if not self.root.is_dir():
            return 0
        removed = 0
        with self._lock:
            try:
                children = list(self.root.iterdir())
            except OSError:
                return 0
            for child in children:
                if child.name in live_task_ids:
                    continue
                try:
                    if child.is_symlink():
                        # Never rmtree through a link; drop the link itself.
                        child.unlink(missing_ok=True)
                    elif child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        continue
                except OSError:
                    continue
                removed += 1
        return removed


class ChangesRecorder:
    """One task's capture seam around successful ``write`` / ``edit`` calls."""

    def __init__(self, store: ChangesStore, task_id: str, workspace: Path) -> None:
        self.store = store
        self.task_id = task_id
        self.workspace = Path(workspace)

    def begin(
        self,
        round_no: int,
        tool_name: str,
        tool_input: Mapping[str, Any],
        tool_call_id: str,
    ) -> Optional[PendingCapture]:
        if tool_name not in ("write", "edit"):
            return None
        raw_path = tool_input.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            return None
        try:
            target = resolve_in_workspace(self.workspace, raw_path)
            if target is None:
                return None
            root = self.workspace.resolve()
            rel = target.relative_to(root).as_posix()
            existed = target.is_file()
            before_oversized = False
            before: Optional[bytes] = None
            if existed:
                if target.stat().st_size > self.store.max_file_bytes:
                    # Never load an oversized file only to mark it oversized.
                    before_oversized = True
                else:
                    before = target.read_bytes()
        except Exception:
            logger.warning("change capture skipped for %s", raw_path, exc_info=True)
            return None
        return PendingCapture(
            round_no=round_no,
            path=rel,
            tool_call_id=tool_call_id,
            existed=existed,
            before=before,
            before_oversized=before_oversized,
        )

    def commit(self, pending: PendingCapture) -> None:
        target = resolve_in_workspace(self.workspace, pending.path)
        if target is None or not target.is_file():
            return
        try:
            after_oversized = target.stat().st_size > self.store.max_file_bytes
            after = None if after_oversized else target.read_bytes()
        except OSError:
            logger.warning("change capture failed reading %s", pending.path, exc_info=True)
            return
        try:
            self.store.record(
                self.task_id,
                pending.round_no,
                pending.path,
                before_existed=pending.existed,
                before=pending.before,
                after=after,
                tool_call_id=pending.tool_call_id,
                before_oversized=pending.before_oversized,
                after_oversized=after_oversized,
            )
        except Exception:
            # Capture must never turn a successful tool call into a failure.
            logger.warning("change capture failed for %s", pending.path, exc_info=True)
