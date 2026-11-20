"""Pure diff arithmetic for round changes (#343).

Split out of :mod:`backend.services.changes` so the storage/lifecycle seam and
the deterministic text math stay separately readable. Nothing here touches the
filesystem, the store or an API: callers pass bytes/text in and get stats or
unified hunks out.

Contract: ``docs/specs/workspace-files-changes-ui.md`` §5.1–5.3.
"""

from __future__ import annotations

import difflib
import time
from typing import Any, Dict, List, Optional

_CONTEXT_LINES = 3


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
