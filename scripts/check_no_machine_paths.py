r"""Fail when a tracked doc / evidence file carries a machine-specific absolute path.

Usage: python scripts/check_no_machine_paths.py

Scope: every tracked Markdown file (same set as ``check_doc_links.py``), the
tracked ``.json`` / ``.txt`` evidence under ``docs/`` (``docs/assets/verification/``)
and every tracked file under ``lessons/`` (public HTML lessons included). Code is
out of scope on purpose: path-boundary tests embed literals such as ``C:\tmp\x``
as *inputs*, and system references like ``C:\Windows\...`` are legitimate.

Enforces the path half of ``docs/security.md`` §3 (「不得出现 ``C:\Users<用户名>``、
家目录 / 个人笔记路径……；文档一律用占位符」): an absolute path is a violation when
it is machine-specific — Windows user profiles, personal checkouts, git-bash drive
mounts (``/e/...``), POSIX homes — unless the path carries a documented
``<placeholder>`` segment (``C:\Users<user>`` is the rule's own spelling). Only
four system roots (``windows`` / ``program files`` / ``programdata`` …) and
ellipsis shorthands (``C:\...``) are exempt — the allowlist is an explicit list,
never a "looks like a temp dir" shape, so a new real path (``E:\code\demo\new``)
still fails.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Evidence formats under docs/ beyond Markdown (verification assets).
EVIDENCE_SUFFIXES = {".json", ".txt"}

#: Windows absolute path ``<drive>:\...`` / ``<drive>:/...``; the lookbehind keeps
#: a word that merely ends in the drive letter (``note:C:\x``) from matching.
WIN_ABS = re.compile(r"(?i)(?<![\w])[a-z]:\\[^\s\"'`|]+")
WIN_ABS_SLASH = re.compile(r"(?i)(?<![\w])[a-z]:/[^\s\"'`|]+")
#: git-bash drive mount: ``/e/...`` (single letter between slashes); the lookbehind
#: keeps URLs (``reddit.com/r/x``) from matching.
POSIX_DRIVE = re.compile(r"(?<![\w.:])/[a-z]/[^\s\"'`|]+")
#: POSIX home directory.
POSIX_HOME = re.compile(r"(?<![\w.])/(?:Users|home)/[^\s\"'`|]+")

#: First path segments that are system-wide rather than machine-specific.
SYSTEM_ROOTS = {"windows", "program files", "program files (x86)", "programdata"}

PATH_PATTERNS = (WIN_ABS, WIN_ABS_SLASH, POSIX_DRIVE, POSIX_HOME)


def is_violation(text: str) -> bool:
    """Decide one matched path: documented placeholder or system root -> fine."""
    if "<" in text:
        return False
    if WIN_ABS.fullmatch(text) or WIN_ABS_SLASH.fullmatch(text):
        # ``C:\\Windows\\...`` keeps its first segment after doubled separators.
        first = text[3:].lstrip("\\/").split("\\", 1)[0].split("/", 1)[0]
        if set(first) <= {"."}:
            return False  # ``C:\...`` ellipsis shorthand, not a real path
        return first.lower() not in SYSTEM_ROOTS
    return True


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
    for path in scoped_files():
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # binary asset (e.g. a PNG): sanitised by hand, see security.md §3
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pattern in PATH_PATTERNS:
                for match in pattern.finditer(line):
                    if is_violation(match.group(0)):
                        found = match.group(0)
                        if len(found) > 80:
                            found = found[:77] + "..."
                        violations.append(f"{path.relative_to(ROOT)}:{lineno}  {found}")
    if violations:
        print("Machine-specific absolute paths in tracked docs:")
        for item in violations:
            print(f"  {item}")
        return 1
    print("No machine-specific absolute paths in tracked docs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
