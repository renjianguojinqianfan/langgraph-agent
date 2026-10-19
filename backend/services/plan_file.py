"""The plan is one Markdown file in the task workspace (ADR 0010 decision 4).

``<workspace>/.qianfan/plans/<task_id>.md`` is the only source of truth. A
``.qianfan/.gitignore`` containing ``*`` keeps the directory out of the user's
``git status``. The model writes the plan in plan mode; the system stores it.
Execution gets it as exactly one message in the conversation, refreshed from
the file whenever it changes (including external edits and model writes).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

PLAN_DIR = Path(".qianfan") / "plans"
GITIGNORE = "*\n"
PLAN_OPEN = "<qianfan-plan>"
PLAN_CLOSE = "</qianfan-plan>"
PLAN_MESSAGE_PREFIX = "[qianfan-plan]"
PLAN_MESSAGE_RULE = "以计划文件当前内容为准，与早先消息冲突时按计划做。"
PLAN_MAX_CHARS = 100_000
PLAN_MESSAGE_MAX_CHARS = 20_000


def plan_path(workspace: Path, task_id: str) -> Path:
    root = Path(workspace).resolve()
    candidate = (root / PLAN_DIR / f"{task_id}.md").resolve()
    if root not in candidate.parents:
        raise ValueError(f"plan path escapes the workspace: {task_id!r}")
    return candidate


def ensure_gitignore(workspace: Path) -> None:
    """Keep ``.qianfan/`` out of the user's ``git status`` (also on model writes)."""
    qianfan = Path(workspace) / ".qianfan"
    try:
        qianfan.mkdir(parents=True, exist_ok=True)
        gitignore = qianfan / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(GITIGNORE, encoding="utf-8")
    except OSError:  # pragma: no cover - the workspace is unwritable; the read still works
        pass


def read_plan(workspace: Path, task_id: str) -> str:
    try:
        text = plan_path(workspace, task_id).read_text(encoding="utf-8")
    except OSError:
        return ""
    if text.strip():
        ensure_gitignore(workspace)
    return text


def write_plan(workspace: Path, task_id: str, text: str) -> None:
    if len(text) > PLAN_MAX_CHARS:
        raise ValueError(f"plan is too long ({len(text)} > {PLAN_MAX_CHARS} chars)")
    path = plan_path(workspace, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    qianfan = path.parent.parent
    gitignore = qianfan / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(GITIGNORE, encoding="utf-8")
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def plan_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_plan(content: str) -> Optional[str]:
    """The Markdown between the agreement markers, or None for a normal reply."""
    raw = content or ""
    start = raw.find(PLAN_OPEN)
    if start < 0:
        return None
    end = raw.find(PLAN_CLOSE, start + len(PLAN_OPEN))
    if end < 0:
        return None
    text = raw[start + len(PLAN_OPEN) : end].strip()
    return text or None


def is_plan_message(content: Any) -> bool:
    """A system-injected plan turn, not a user message that happens to start alike."""
    text = str(content or "").lstrip()
    return text.startswith(PLAN_MESSAGE_PREFIX) and PLAN_MESSAGE_RULE in text


def render_plan_message(text: str) -> str:
    if len(text) > PLAN_MESSAGE_MAX_CHARS:
        text = text[:PLAN_MESSAGE_MAX_CHARS] + "\n\n…（计划过长，已截断；完整内容见计划文件）"
    return f"{PLAN_MESSAGE_PREFIX}\n{PLAN_MESSAGE_RULE}\n\n{text}"


def sync_plan_message(messages: List[Dict[str, Any]], text: str) -> bool:
    """Keep exactly one plan message, at the end, matching ``text``.

    Returns whether the list changed. The plan is re-appended on every call so
    it always sits after the newest user/tool turns and survives compression
    (compression runs before this function). Only user-role plan turns are
    replaced; a tool result carrying the prefix is left alone.
    """
    filtered = [
        m
        for m in messages
        if not (m.get("role") == "user" and is_plan_message(m.get("content")))
    ]
    changed = len(filtered) != len(messages)
    if text.strip():
        filtered.append({"role": "user", "content": render_plan_message(text)})
        changed = True
    if changed:
        messages[:] = filtered
    return changed
