"""The files glob/grep may see: skip ``.git`` and gitignored paths.

Git repositories ask git once (``ls-files --cached --others
--exclude-standard``), so the same ignore rules the user's shell sees apply
without a second implementation. A non-git directory (or a broken git call)
falls back to a walk that only skips ``.git``. ``ls`` and a named ``read`` do
not use this list: an ignored file stays reachable when asked for by name.

The workspace file list (C5) reuses :func:`workspace_files`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

GIT_TIMEOUT_SEC = 5.0


def _git_files(root: Path) -> Optional[list[Path]]:
    """Files from git's own view, or None when this is not a usable repo."""
    git = shutil.which("git")
    if git is None:
        return None
    try:
        top = subprocess.run(
            [git, "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            timeout=GIT_TIMEOUT_SEC,
        )
        if top.returncode != 0:
            return None
        repo = Path(os.fsdecode(top.stdout).strip())
        listed = subprocess.run(
            [
                git,
                "-C",
                str(repo),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            timeout=GIT_TIMEOUT_SEC,
        )
    except Exception:
        return None
    if listed.returncode != 0:
        return None

    resolved_root = root.resolve()
    files: list[Path] = []
    for raw in listed.stdout.split(b"\0"):
        if not raw:
            continue
        path = (repo / os.fsdecode(raw)).resolve()
        if path != resolved_root and resolved_root not in path.parents:
            continue
        if path.is_file():
            files.append(path)
    return files


def _walk_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name != ".git"]
        for name in filenames:
            files.append((Path(dirpath) / name).resolve())
    return files


def workspace_files(root: Path) -> list[Path]:
    """Every regular file under ``root`` glob/grep should consider."""
    resolved = root.resolve()
    git_files = _git_files(resolved)
    if git_files is not None:
        return sorted(git_files)
    return sorted(_walk_files(resolved))
