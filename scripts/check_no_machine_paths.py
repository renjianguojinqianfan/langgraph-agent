r"""Fail when a tracked doc / evidence file carries a machine-specific absolute path.

Usage: python scripts/check_no_machine_paths.py

Scope: every tracked Markdown file (same set as ``check_doc_links.py``), the
tracked ``.json`` / ``.txt`` evidence under ``docs/`` (``docs/assets/verification/``)
and every tracked file under ``lessons/`` (public HTML lessons included). Code is
out of scope on purpose: path-boundary tests embed literals such as ``C:\tmp\x``
as *inputs*, and system references like ``C:\Windows\...`` are legitimate.

Enforces the path half of ``docs/security.md`` §3 (「不得出现真实用户名 / 本机绝对
路径；占位符按清单」). The exemptions are an **exact list**, never a shape guess, so
a new real path (``E:\code\demo\new``) still fails:

* every machine-identifying segment before the first placeholder is a generic
  root — Windows (``C:\Users\<user>``) or git-bash mount (``/c/Users/<user>``);
  ``C:\Users\DELL\<workspace>`` fails, a placeholder only whitewashes what
  *follows* a generic root;
* a system root, resolved on the raw line so spaced roots
  (``C:\Program Files\...``) survive the token stopping at the space;
* a lone ellipsis segment (``C:\...``) — and nothing after it.

Known false positive (#318 re-review): a placeholder immediately followed by a
single-letter path segment (``<user>/a/b``) restarts the match after the
placeholder and is flagged. Real scrub output uses multi-letter segments
(``<user>/Desktop/...``), so this does not fire in practice. The gate is the
mechanical half of §3; the edge is recorded here rather than loosened.

Two shapes are rejected on sight because they launder a real path behind an
exemption: any ``..`` segment (``C:\Windows\..\Users\DELL`` — the system root
before it proves nothing) and a placeholder or ellipsis with a real tail.

``PLACEHOLDERS`` mirrors the §3 vocabulary verbatim — doc and gate move together.
The red/green matrix is locked by ``backend/tests/test_no_machine_paths.py``.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent

#: Evidence formats under docs/ beyond Markdown (verification assets).
EVIDENCE_SUFFIXES = {".json", ".txt"}

#: Documented placeholders (docs/security.md §3). A path segment that is exactly
#: one of these is documentation, not a machine name; any other angle-bracket
#: token (``<code>`` and the like) is NOT a placeholder.
PLACEHOLDERS = (
    "<repo-root>",
    "<workspace>",
    "<tmp>",
    "<knowledge-base>",
    "<user>",
    "<git-root>",
    "<python-install>",
    "<personal-notes>",
)

#: System-wide roots; matched with their spaces on the raw line, because the
#: token itself stops at the first space.
SYSTEM_ROOTS = (
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
)

#: Roots that may precede a placeholder segment without naming the machine: the
#: system roots plus the two profile markers (``C:\Users\<user>`` and
#: ``/home/<user>`` are the spellings security.md §3 itself uses).
GENERIC_ROOTS = tuple(root.lower() for root in SYSTEM_ROOTS) + ("users", "home")

_PLACEHOLDER_ALT = "|".join(re.escape(p) for p in PLACEHOLDERS)
_SYSTEM_ROOT_ALT = "|".join(re.escape(root) for root in SYSTEM_ROOTS)
#: One unit of a path run: a documented placeholder, or any character but
#: whitespace, quotes, backticks, pipes and angle brackets — so HTML tags
#: (``<code>``) and following prose never glue on. Separators stay inside the
#: run so JSON-escaped forms (``E:\\code\\demo``) match whole.
_PATH_UNIT = rf"(?:{_PLACEHOLDER_ALT}|[^\s\"'`|<>])"
_SEP = r"[\\/]"

#: ``<drive>:\...`` / ``<drive>:/...``; the lookbehind keeps a word that merely
#: ends in the drive letter (``note:C:\x``) from matching.
WIN_PATH = re.compile(rf"(?i)(?<![\w])[a-z]:{_SEP}(?:{_PATH_UNIT})+")
#: git-bash drive mount: ``/e/...`` (single letter between slashes); the
#: lookbehind keeps URLs (``reddit.com/r/x``) from matching.
POSIX_DRIVE = re.compile(rf"(?<![\w.:])/[a-z]{_SEP}(?:{_PATH_UNIT})+")
#: POSIX home directory — case-sensitive on the root, so key-name prose such as
#: ``←/→/Home/End`` does not read as a path.
POSIX_HOME = re.compile(rf"(?<![\w.])/(?:Users|home){_SEP}(?:{_PATH_UNIT})+")

#: System-root prefix — Windows (``C:\Windows``) and git-bash mount
#: (``/c/Windows``) spellings, spaces and doubled separators included, resolved
#: against the raw line rather than the (possibly space-truncated) token. The
#: root must end at a non-alphanumeric boundary, so ``C:\WindowsFoo`` is not a
#: system root while ``/c/Windows`` (root alone, e.g. quoted in prose) is.
_SYSTEM_PREFIX = re.compile(
    rf"(?i)(?:[a-z]:|/[a-z]){_SEP}+(?:{_SYSTEM_ROOT_ALT})(?![A-Za-z0-9])"
)
_DRIVE_ONLY = re.compile(r"(?i)[a-z]:$")
_MOUNT_LETTER = re.compile(r"[A-Za-z]$")


def _segments(token: str) -> list[str]:
    r"""Path segments, without the drive prefix or empty doubled-separator parts.

    The drive prefix is either the Windows spelling (``C:``) or the git-bash
    mount (``/c``), so ``/c/Users/<user>`` segments exactly like
    ``C:\Users\<user>``.
    """
    parts = [part for part in re.split(_SEP, token) if part]
    if parts and (_DRIVE_ONLY.fullmatch(parts[0]) or _MOUNT_LETTER.fullmatch(parts[0])):
        parts = parts[1:]
    return parts


def _segments_ok(segments: list[str]) -> bool:
    """True when the machine-identifying part of the path is placeholders only."""
    if ".." in segments:
        return False  # traversal: a system root or placeholder BEFORE it proves nothing
    first_placeholder = next(
        (i for i, seg in enumerate(segments) if seg in PLACEHOLDERS), None
    )
    if first_placeholder is None:
        # No placeholder: only a lone ellipsis segment (``C:\...``) is documentation.
        return len(segments) == 1 and set(segments[0]) <= {"."}
    return all(seg.lower() in GENERIC_ROOTS for seg in segments[:first_placeholder])


def _allows(line: str, match: re.Match[str]) -> bool:
    """Verdict for one absolute-path token: exemptions first, segments second."""
    segments = _segments(match.group(0))
    if ".." not in segments and _SYSTEM_PREFIX.match(line, match.start()):
        return True
    return _segments_ok(segments)


@dataclass(frozen=True)
class Rule:
    """One pattern plus its verdict — a new rule cannot fall through silently."""

    pattern: re.Pattern[str]
    allows: Callable[[str, "re.Match[str]"], bool]


RULES = (
    Rule(WIN_PATH, _allows),
    Rule(POSIX_DRIVE, _allows),
    Rule(POSIX_HOME, _allows),
)


def scoped_files() -> list[Path]:
    """Docs surface: tracked Markdown, docs/ evidence, tracked lessons/ files."""
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    files: list[Path] = []
    for line in out.stdout.splitlines():
        if not line:
            continue
        path = Path(line)
        if path.suffix == ".md" or path.parts[:1] == ("lessons",) or (
            path.parts[:1] == ("docs",) and path.suffix in EVIDENCE_SUFFIXES
        ):
            files.append(ROOT / line)
    return files


def main() -> int:
    violations: list[str] = []
    undecodable: list[str] = []
    for path in scoped_files():
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Binary asset (e.g. a PNG): not text-scannable, sanitised by hand per
            # security.md §3 — named in the output so it is never invisible.
            undecodable.append(str(path.relative_to(ROOT)))
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for rule in RULES:
                for match in rule.pattern.finditer(line):
                    if not rule.allows(line, match):
                        found = match.group(0)
                        if len(found) > 80:
                            found = found[:77] + "..."
                        violations.append(f"{path.relative_to(ROOT)}:{lineno}  {found}")
    if undecodable:
        print(f"Skipped {len(undecodable)} undecodable (binary) file(s):")
        for item in undecodable:
            print(f"  {item}")
    if violations:
        print("Machine-specific absolute paths in tracked docs:")
        for item in violations:
            print(f"  {item}")
        return 1
    print("No machine-specific absolute paths in tracked docs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
