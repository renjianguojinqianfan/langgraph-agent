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
from .runtime_context import reject_non_local_path

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
    """Regular files under ``root``; never through a link that leaves it.

    ``os.walk`` follows Windows junctions even with ``followlinks=False``, so
    every directory is resolved before descending and cut when it is outside
    the root; visited real paths stop junction/symlink cycles. Files are
    resolved too (a link may point outside) and must be regular.
    """
    root = root.resolve()
    files: list[Path] = []
    seen_files: set[Path] = set()
    visited_dirs: set[Path] = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        try:
            current = Path(dirpath).resolve()
        except OSError:
            dirnames[:] = []
            continue
        if current in visited_dirs:
            dirnames[:] = []
            continue
        visited_dirs.add(current)

        kept: list[str] = []
        for name in dirnames:
            if name == ".git":
                continue
            try:
                candidate = (Path(dirpath) / name).resolve()
            except OSError:
                continue
            if candidate != root and root not in candidate.parents:
                continue
            kept.append(name)
        dirnames[:] = kept

        for name in filenames:
            try:
                candidate = (Path(dirpath) / name).resolve()
            except OSError:
                continue
            if candidate != root and root not in candidate.parents:
                continue
            if not candidate.is_file() or candidate in seen_files:
                continue
            seen_files.add(candidate)
            files.append(candidate)
    return files


def workspace_files(root: Path) -> list[Path]:
    """Every regular file under ``root`` glob/grep should consider."""
    resolved = root.resolve()
    git_files = _git_files(resolved)
    if git_files is not None:
        return sorted(git_files)
    return sorted(_walk_files(resolved))


def resolve_in_workspace(root: Path, rel: str) -> Optional[Path]:
    """Resolve ``rel`` under ``root``; None when it escapes or is non-local.

    UNC / device / NUL-bearing inputs are refused *before* ``Path.resolve()``,
    which on Windows dials the SMB server named in the path (NTLM handshake).
    The same guard the workspace switcher uses runs first here too (#254).
    """
    try:
        reject_non_local_path(rel)
        resolved = root.resolve()
        candidate = (resolved / rel).resolve()
    except Exception:
        return None
    if candidate != resolved and resolved not in candidate.parents:
        return None
    return candidate
