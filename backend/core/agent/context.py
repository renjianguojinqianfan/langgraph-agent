"""Context compression / management (P0 item 1).

A long multi-step history can grow without bound and eventually blow the LLM
context window. This module provides:

* :func:`estimate_tokens` — cheap ``chars/4`` token estimate (conservative for
  Chinese text);
* :func:`compress_messages` — the single entry point the kernel calls *before*
  every LLM invocation; it either returns the messages untouched (under the
  budget) or rewrites the early history into a truncation placeholder / an LLM
  summary while always keeping the most recent ``keep_recent`` raw messages;
* :func:`summarize_messages` — optional LLM-based summarisation of the dropped
  early messages (only used when ``strategy="summarize"`` and an LLM is given).

The default strategy is ``truncate``: deterministic and **zero extra LLM
calls**. OpenAI message fields (``role`` / ``content`` / ``tool_calls`` /
``tool_call_id``) are preserved verbatim so LangGraph message passthrough is
never broken.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from ...utils.logging import get_logger

logger = get_logger("agent.context")

# Truncation placeholder inserted where the dropped early history used to be.
PLACEHOLDER_PREFIX = "[上下文已截断：前 "
PLACEHOLDER_SUFFIX = " 步历史已省略]"
# Never drop below this many raw messages so at least one assistant+tool round
# survives — the compressed history is never emptied entirely.
_MIN_KEEP_RECENT = 2

Meta = Dict[str, Any]


def estimate_tokens(messages: List[Dict[str, Any]]) -> int:
    """Rough token estimate for OpenAI-style messages (≈ chars/4).

    Content strings are counted as ``len(content) // 4`` (conservative for
    Chinese). ``tool_calls`` JSON and a small per-message role overhead are
    added on top. Returns at least 1.
    """
    total = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            # Multimodal content parts: [{"type":"text","text":...}, ...]
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str):
                        total += len(text)
                elif isinstance(part, str):
                    total += len(part)
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            try:
                total += len(json.dumps(tool_calls, ensure_ascii=False))
            except Exception:
                total += 256
        total += 4  # role / separator overhead
    return max(1, total // 4)


def _messages_to_text(messages: List[Dict[str, Any]]) -> str:
    """Render messages as flat ``role: content`` lines for summarisation."""
    parts: List[str] = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content") or ""
        if isinstance(content, list):
            texts = []
            for part in content:
                if isinstance(part, dict) and part.get("text"):
                    texts.append(part["text"])
                elif isinstance(part, str):
                    texts.append(part)
            content = " ".join(texts)
        line = f"{role}: {content}"
        if msg.get("tool_calls"):
            line += f" [tool_calls: {json.dumps(msg['tool_calls'], ensure_ascii=False)}]"
        parts.append(line)
    return "\n".join(parts)


def summarize_messages(
    llm: Any,
    messages: List[Dict[str, Any]],
    max_tokens: int = 300,
) -> str:
    """Ask ``llm`` to compress ``messages`` into a single summary string.

    Failures are non-fatal: any error degrades to a short fallback note so the
    caller always receives a usable system block.
    """
    if not messages:
        return "[无早期消息]"
    text = _messages_to_text(messages)
    budget_chars = max(50, int(max_tokens) * 3)
    prompt = (
        "请将以下 Agent 历史对话压缩为一段中文摘要，保留关键事实、已执行的动作与结果，"
        f"总字数控制在约 {budget_chars} 字以内，不要输出额外说明：\n\n{text}"
    )
    try:
        resp = llm.complete([{"role": "user", "content": prompt}])
        summary = (resp.content or "").strip()
        return summary or "[上下文摘要生成失败，已省略早期消息]"
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("summarize_messages failed: %s", exc)
        return f"[上下文摘要失败({exc})，已省略早期消息]"


def _compress_truncate(
    messages: List[Dict[str, Any]],
    keep_recent: int,
    meta_base: Meta,
) -> Tuple[List[Dict[str, Any]], Meta]:
    """Drop the early messages and insert a deterministic placeholder."""
    n = len(messages)
    keep = max(_MIN_KEEP_RECENT, min(int(keep_recent), n))
    if keep >= n:
        meta = dict(meta_base)
        meta["compressed"] = False
        return messages, meta
    keep_msgs = messages[-keep:]
    dropped = n - keep
    placeholder = {
        "role": "system",
        "content": f"{PLACEHOLDER_PREFIX}{dropped}{PLACEHOLDER_SUFFIX}",
    }
    result = [placeholder, *keep_msgs]
    meta = dict(meta_base)
    meta["compressed"] = True
    meta["dropped"] = dropped
    meta["context_tokens"] = estimate_tokens(result)
    return result, meta


def _compress_summarize(
    messages: List[Dict[str, Any]],
    keep_recent: int,
    llm: Any,
    summary_max_tokens: int,
    meta_base: Meta,
) -> Tuple[List[Dict[str, Any]], Meta]:
    """Replace dropped early messages with a single LLM summary block."""
    n = len(messages)
    keep = max(_MIN_KEEP_RECENT, min(int(keep_recent), n))
    if keep >= n:
        meta = dict(meta_base)
        meta["compressed"] = False
        return messages, meta
    early = messages[:-keep]
    keep_msgs = messages[-keep:]
    summary = summarize_messages(llm, early, summary_max_tokens)
    result = [{"role": "system", "content": summary}, *keep_msgs]
    meta = dict(meta_base)
    meta["compressed"] = True
    meta["dropped"] = len(early)
    meta["context_tokens"] = estimate_tokens(result)
    return result, meta


def compress_messages(
    messages: List[Dict[str, Any]],
    budget: int,
    keep_recent: int = 10,
    max_messages: int = 0,
    strategy: str = "truncate",
    llm: Optional[Any] = None,
    summary_max_tokens: int = 300,
) -> Tuple[List[Dict[str, Any]], Meta]:
    """Compress ``messages`` when they exceed the configured thresholds.

    Returns ``(messages, meta)`` where ``meta`` carries:

    * ``compressed`` — whether a rewrite happened;
    * ``dropped`` — number of messages removed;
    * ``context_tokens`` — estimate of the returned list;
    * ``strategy`` — the effective strategy (``summarize`` falls back to
      ``truncate`` when no LLM is available);
    * ``trigger`` — ``"token"`` | ``"count"`` | ``"none"``.

    Under the budget the input list is returned unchanged (identity).
    """
    meta_base: Meta = {
        "compressed": False,
        "dropped": 0,
        "context_tokens": estimate_tokens(messages),
        "strategy": "truncate",
        "trigger": "none",
    }
    if not messages:
        return messages, meta_base

    over_token = meta_base["context_tokens"] > int(budget)
    over_count = int(max_messages) > 0 and len(messages) > int(max_messages)
    if not (over_token or over_count):
        return messages, meta_base

    meta_base["trigger"] = "token" if over_token else "count"

    if strategy == "summarize" and llm is not None:
        meta_base["strategy"] = "summarize"
        return _compress_summarize(messages, keep_recent, llm, summary_max_tokens, meta_base)

    meta_base["strategy"] = "truncate"
    return _compress_truncate(messages, keep_recent, meta_base)
