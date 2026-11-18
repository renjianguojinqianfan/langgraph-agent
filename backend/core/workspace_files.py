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
from stat import S_ISLNK, S_ISREG
from typing import Optional

from ..utils.logging import get_logger
from .runtime_context import reject_non_local_path

logger = get_logger("workspace.files")
GIT_TIMEOUT_SEC = 5.0


def _accepted_file(base: Path, root: Path) -> Optional[tuple[Path, bool]]:
    """One directory entry -> (resolved file, was-a-link) or None.

    A regular entry is its own resolved path: callers pass an already-resolved
    parent, so the per-file ``Path.resolve()`` (the dominant cost on Windows)
    is only needed when the entry itself is a symlink. A link must still land
    inside ``root`` and be a regular file (the old full-resolve semantics).
    """
    try:
        mode = base.lstat().st_mode
    except OSError:
        return None
    if S_ISREG(mode):
        return base, False
    if not S_ISLNK(mode):
        return None
    try:
        target = base.resolve()
    except OSError:
        return None
    if target != root and root not in target.parents:
        return None
    try:
        if not target.is_file():
            return None
    except OSError:
        return None
    return target, True


def _resolve_candidates(
    candidates: list[Path], root: Path, limit: Optional[int], *, dedupe: bool
) -> list[Path]:
    """Resolve entries in sorted order, stopping after ``limit`` only when safe.

    Without links, the sorted lexical candidates are already the sorted
    resolved output, so a bounded listing can stop early. If a file link shows
    up, finish the walk and sort the resolved paths — stopping there could
    change the prefix. Git callers pass ``limit=None`` themselves when a
    linked parent is present (and slice afterwards).
    """
    if limit == 0:
        return []
    ordered = sorted(candidates)
    # A file link may resolve to a name outside this lexical prefix; only a
    # link-free tree guarantees prefix == sorted resolved output. Links are
    # rare, so the full path stays the fallback.
    if limit is not None and any(os.path.islink(candidate) for candidate in ordered):
        limit = None
    accepted: list[Path] = []
    seen: set[Path] = set()
    saw_link = False

    for candidate in ordered:
        result = _accepted_file(candidate, root)
        if result is None:
            continue
        path, linked = result
        saw_link = saw_link or linked
        if dedupe and path in seen:
            continue
        if dedupe:
            seen.add(path)
        accepted.append(path)
        if limit is not None and len(accepted) >= limit and not saw_link:
            return accepted[:limit]

    if limit is None or saw_link:
        return sorted(accepted)[:limit]
    return accepted


def _git_files(root: Path, limit: Optional[int]) -> Optional[list[Path]]:
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

    candidates = sorted(
        repo / os.fsdecode(raw) for raw in listed.stdout.split(b"\0") if raw
    )
    parent_cache: dict[Path, Optional[Path]] = {}

    def resolved_parent(candidate: Path) -> Optional[Path]:
        parent = candidate.parent
        if parent not in parent_cache:
            try:
                resolved: Optional[Path] = parent.resolve()
            except OSError:
                resolved = None
            if resolved is not None and (
                resolved != root and root not in resolved.parents
            ):
                resolved = None
            parent_cache[parent] = resolved
        return parent_cache[parent]

    bases: list[Path] = []
    has_link = False
    for candidate in candidates:
        parent = resolved_parent(candidate)
        if parent is None:
            continue
        if os.path.normcase(str(parent)) != os.path.normcase(str(candidate.parent)):
            has_link = True
        bases.append(parent / candidate.name)
    # A linked parent can move a path outside its lexical position, so only a
    # link-free candidate set may use the bounded prefix.
    files = _resolve_candidates(
        bases,
        root,
        None if limit is not None and has_link else limit,
        dedupe=False,
    )
    return files if limit is None else files[:limit]


def _walk_files(root: Path, limit: Optional[int]) -> list[Path]:
    """Regular files under ``root``; never through a link that leaves it.

    ``os.walk`` follows Windows junctions even with ``followlinks=False``, so
    every directory is resolved before descending and cut when it is outside
    the root; visited real paths stop junction/symlink cycles. Files under a
    resolved directory reuse it instead of resolving every path again; only a
    file that is itself a link needs a target resolution.
    """
    candidates: list[Path] = []
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
            candidates.append(current / name)
    return _resolve_candidates(candidates, root, limit, dedupe=True)


def workspace_files(root: Path, limit: Optional[int] = None) -> list[Path]:
    """Every regular file under ``root`` glob/grep should consider.

    ``limit`` is for the bounded list route: the result is the prefix of the
    unlimited sorted list (never an arbitrary enumeration-order stop). Glob and
    grep call without it and still see every file.
    """
    if limit == 0:
        return []
    resolved = root.resolve()
    git_files = _git_files(resolved, limit)
    if git_files is not None:
        return git_files
    return _walk_files(resolved, limit)


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
