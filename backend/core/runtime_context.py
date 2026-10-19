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


def normalize_workspace(value: "str | Path") -> str:
    """Canonical absolute path used for storage and comparison.

    Resolves ``~``, relative paths (against the process cwd), ``..`` and
    symlinks. On Windows ``os.path.normcase`` also folds drive-letter case and
    slash direction, so two spellings of one directory compare equal.
    """
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return os.path.normcase(str(path.resolve()))


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


def reset_current_task_id(token: Token[Optional[str]]) -> None:
    _task_id.reset(token)
