"""The plan is one Markdown file in the task workspace (ADR 0010 decision 4).

``<workspace>/.qianfan/plans/<task_id>.md`` is the only source of truth. A
``.qianfan/.gitignore`` containing ``*`` keeps the directory out of the user's
``git status``. The model writes the plan in plan mode; the system stores it.
Execution gets it as exactly one message in the conversation, checked at round
start and after compression (G-34); a mid-round edit takes effect next round.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


class PlanError(RuntimeError):
    """The plan file exists but cannot be read as the agreed UTF-8 Markdown."""


PLAN_DIR = Path(".qianfan") / "plans"
GITIGNORE = "*\n"
PLAN_OPEN = "<qianfan-plan>"
PLAN_CLOSE = "</qianfan-plan>"
PLAN_MESSAGE_PREFIX = "[qianfan-plan]"
PLAN_MESSAGE_RULE = "以计划文件当前内容为准，与早先消息冲突时按计划做。"
PLAN_MAX_CHARS = 100_000
PLAN_MESSAGE_MAX_CHARS = 20_000
#: The one gray line G-34 allows when the plan version moves. It is a protocol
#: line, not a prefix (#236): the frontend matches the whole sentence too
#: (``frontend/src/lib/rounds.ts``), so a model answer that merely starts with
#: these words must not be mistaken for the note and dropped.
PLAN_NOTE_PREFIX = "计划已更新到"
PLAN_NOTE_RE = re.compile(r"^计划已更新到第 \d+ 版。$")


def plan_path(workspace: Path, task_id: str) -> Path:
    root = Path(workspace).resolve()
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", task_id) or "task"
    candidate = (root / PLAN_DIR / f"{safe}.md").resolve()
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
    except FileNotFoundError:
        return ""
    except UnicodeDecodeError as exc:
        raise PlanError(
            f"plan file is not valid UTF-8: {plan_path(workspace, task_id)}"
        ) from exc
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


def plan_note_text(version: int) -> str:
    return f"{PLAN_NOTE_PREFIX}第 {version} 版。"


def is_plan_note(content: Any) -> bool:
    """True for the gray note, not for an answer that starts with the words."""
    return bool(PLAN_NOTE_RE.fullmatch(str(content or "").strip()))


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


def strip_plan_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop injected plan turns before eviction/compression sees them."""
    return [
        m
        for m in messages
        if not (m.get("role") == "user" and is_plan_message(m.get("content")))
    ]


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
