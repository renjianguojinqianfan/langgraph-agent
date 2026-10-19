"""The files glob/grep may see: skip ``.git`` and gitignored paths.

Git repositories ask git once (``ls-files --cached --others
--exclude-standard``), so the same ignore rules the user's shell sees apply
without a second implementation. A non-git directory (or a broken git call)
falls back to a walk that only skips ``.git``. ``ls`` and a named ``read`` do
not use this list: an ignored file stays reachable when asked for by name.

The workspace file list (C5) reuses :func:`workspace_files`.

A nested repository is not expanded: the outer git lists it as a gitlink, and
its files stay invisible to glob/grep. Point the workspace at the nested repo
to search there. A non-git directory has no ignore engine, so only ``.git`` is
skipped (``.qianfan``'s own ``.gitignore`` only works inside a repo).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from ..utils.logging import get_logger

logger = get_logger("workspace.files")
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
            return None  # not a repository: the normal fallback
        repo = Path(os.fsdecode(top.stdout).rstrip("\r\n"))
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
        logger.warning("git ls-files failed; falling back to a .git-skipping walk", exc_info=True)
        return None
    if listed.returncode != 0:
        logger.warning(
            "git ls-files exited %s; falling back to a .git-skipping walk",
            listed.returncode,
        )
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
            # No resolve(): a symlink is resolved by the caller, which checks
            # containment. Resolving here would make a scoped path raise
            # relative_to (the target can be outside the base).
            files.append(Path(dirpath) / name)
    return files


def workspace_files(root: Path) -> list[Path]:
    """Every regular file under ``root`` glob/grep should consider."""
    resolved = root.resolve()
    git_files = _git_files(resolved)
    if git_files is not None:
        return sorted(git_files)
    return sorted(_walk_files(resolved))
