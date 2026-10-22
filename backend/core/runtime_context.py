"""Per-run context: the current task id and its workspace root.

The worker thread that runs a task seats this context; every tool that touches
the filesystem reads it instead of a process-wide sandbox root. Sub-agents
re-seat it with the parent's workspace, so a subtask writes where the parent
task lives.

A context that is not seated (direct tool unit tests, startup paths) falls back
to ``Settings.artifacts_path`` via :func:`workspace_root`.
"""

from __future__ import annotations

import os
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Optional, Tuple

from ..config import Settings

_task_id: ContextVar[Optional[str]] = ContextVar("qianfan_task_id", default=None)
_workspace: ContextVar[Optional[Path]] = ContextVar("qianfan_workspace", default=None)

Tokens = Tuple[Token[Optional[str]], Token[Optional[Path]]]


def _reject_non_local_path(value: "str | Path") -> None:
    """Refuse UNC / device paths before ``resolve()`` can touch the network.

    ``Path.resolve()`` on Windows dials an SMB server for ``\\\\host\\share``
    (and its forward-slash spelling), which turns an unauthenticated GET into
    an NTLM handshake with an attacker-chosen host. Device namespaces
    (``\\\\?\\``, ``\\\\.\\``) and NUL bytes are rejected for the same reason:
    they are never a task workspace. Windows treats ``/`` and ``\\`` as
    interchangeable separators, so mixed spellings (``/\\host\\share``) are
    folded to backslashes before the prefix check.
    """
    raw = os.fspath(value)
    if isinstance(raw, bytes):
        raw = os.fsdecode(raw)
    if "\x00" in raw:
        raise ValueError("workspace path must not contain a NUL byte")
    if raw.replace("/", "\\").startswith(("\\\\", "\\??\\")):
        raise ValueError("workspace must be a local path; UNC and device paths are rejected")


def normalize_workspace(value: "str | Path") -> str:
    """Absolute path used for storage and execution.

    Resolves ``~``, relative paths (against the process cwd), ``..`` and
    symlinks, keeping the original letter case for display. UNC, device and
    NUL-bearing paths are refused before any filesystem call (see
    :func:`_reject_non_local_path`).
    """
    _reject_non_local_path(value)
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return str(path.resolve())


def workspace_key(value: "str | Path") -> str:
    """Comparison key: ``normalize_workspace`` + ``os.path.normcase``.

    On Windows this folds drive-letter case and slash direction, so two
    spellings of one directory filter identically.
    """
    return os.path.normcase(normalize_workspace(value))


def get_current_task_id() -> Optional[str]:
    return _task_id.get()


def get_current_workspace() -> Optional[Path]:
    return _workspace.get()


def workspace_root(settings: Settings) -> Path:
    """The task's workspace, or the historical sandbox root outside a run."""
    current = _workspace.get()
    return current if current is not None else settings.artifacts_path


def set_runtime_context(task_id: Optional[str], workspace: Path) -> Tokens:
    return (_task_id.set(task_id or None), _workspace.set(Path(workspace)))


def reset_runtime_context(tokens: Tokens) -> None:
    id_token, workspace_token = tokens
    _workspace.reset(workspace_token)
    _task_id.reset(id_token)


def set_current_task_id(task_id: Optional[str]) -> Token[Optional[str]]:
    return _task_id.set(task_id or None)
