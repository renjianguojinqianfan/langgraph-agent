"""Context compression / management (P0 item 1).

A long multi-step history can grow without bound and eventually blow the LLM
context window. This module provides:

* :func:`estimate_tokens` — cheap ``chars/4`` token estimate (conservative for
  Chinese text);
* :func:`evict_tool_results` — T1.4, runs *before* :func:`compress_messages`:
  replaces oversized old ``tool`` messages with a head/tail preview placeholder
  (pointing at the JSONL trace for the full text) — purely mechanical, zero
  extra LLM calls;
* :func:`compress_messages` — the single entry point the kernel calls *before*
  every LLM invocation; it either returns the messages untouched (under the
  budget) or rewrites the early history into a truncation placeholder / an LLM
  summary, keeping the most recent ``keep_recent`` messages verbatim — unless the
  protected band alone still outgrows the budget, in which case it is squeezed
  oldest-first into the same kind of note (:func:`evict_tool_results`' contract);
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


# T1.4 tool-result eviction: the marker every placeholder starts with (also the
# idempotency guard — an already-evicted message is never evicted twice).
EVICT_PLACEHOLDER_PREFIX = "[tool result 已移除"
# In-band squeeze (#49): how many trailing messages stay verbatim before the band
# starts giving way. One, because the last reply is what the model answers against;
# pi keeps the newest suffix verbatim and opencode v1 only protects a couple of
# turns — neither protects the whole band. It is not a settings key: tuning the band
# width is ``context_keep_recent``'s job, this is the squeeze's own floor.
_BAND_PROTECT_NEWEST = 1


def _note_ref(trace_ref: str, anchor: str) -> str:
    """Trace pointer of a note: ``<file>#<tool_call_id>``, honest when unavailable."""
    if not trace_ref:
        return "无（trace 未开启）"
    return f"{trace_ref}#{anchor}" if anchor else trace_ref


def _note_marker(text: str, *, label: str, ref: str) -> str:
    """The single-line header every note starts with (``原文`` size included)."""
    return f"{EVICT_PLACEHOLDER_PREFIX} | {label} | 原文 {len(text)} 字符 | 留痕: {ref}]"


def _note_preview(text: str, head_chars: int, tail_chars: int) -> str:
    """Head/tail preview body appended under a note marker."""
    return (
        f"\n--- 头部预览（前 {head_chars} 字符）---\n{text[:head_chars]}\n"
        f"--- 尾部预览（后 {tail_chars} 字符）---\n"
        f"{text[-tail_chars:] if tail_chars > 0 else ''}"
    )


def _note_label_and_anchor(msg: Dict[str, Any], names: Dict[str, str]) -> Tuple[str, str]:
    """Note label + trace anchor for one message.

    ``tool`` results keep the T1.4 contract verbatim (tool name, ``tool_call_id``
    pointer). Other roles have no per-call anchor in the trace, so they say what
    they are (``消息: <role>``) and point at the trace file itself.
    """
    role = str(msg.get("role") or "unknown")
    if role == "tool":
        call_id = str(msg.get("tool_call_id") or "?")
        return f"工具: {names.get(call_id, '?')}", call_id
    return f"消息: {role}", ""


def _squeeze_content(
    content: Any,
    *,
    label: str,
    anchor: str,
    trace_ref: str,
    head_chars: int,
    tail_chars: int,
    with_preview: bool,
) -> Optional[str]:
    """Shrink one message's content by a notch, or ``None`` when it cannot shrink.

    Notches: full text → note with head/tail preview → marker-only note. A note is
    only ever demoted to its own header, never re-wrapped, so notes cannot nest and
    re-running the pass is a no-op (``compress_messages`` runs before every LLM call).
    """
    if not isinstance(content, str) or not content:
        return None  # non-text / empty content is left alone (same as T1.4)
    if content.startswith(EVICT_PLACEHOLDER_PREFIX):
        if with_preview:
            return None  # already a note at this notch: idempotent skip
        header, sep, rest = content.partition("\n")
        if not sep or not rest:
            return None  # marker-only already: no notch left
        return header
    marker = _note_marker(content, label=label, ref=_note_ref(trace_ref, anchor))
    if with_preview and int(head_chars) + int(tail_chars) < len(content):
        candidate = marker + _note_preview(content, head_chars, tail_chars)
    else:
        candidate = marker  # preview would swallow the saving: go marker-only
    # Only a strictly smaller content is accepted — the squeeze can never spin.
    return candidate if len(candidate) < len(content) else None


def _squeeze_band(
    result: List[Dict[str, Any]],
    budget: int,
    *,
    names: Dict[str, str],
    head_chars: int,
    tail_chars: int,
    trace_ref: str,
) -> int:
    """Squeeze the band ``keep_recent`` protects when that band alone outgrows the budget.

    Mutates ``result`` (index 0 is the truncation placeholder, everything after it
    is the band). Runs oldest-first so the newest messages survive verbatim; the
    newest :data:`_BAND_PROTECT_NEWEST` give way only at the last notch, after every
    other band message is already marker-only. The budget is the only threshold: the
    first layout that fits ends the squeeze, and a message that cannot shrink
    strictly is skipped, so the pass is bounded by the band size and can never spin.

    ``names`` is the ``tool_call_id`` → tool name map of the **whole** history, not
    just the band: truncation drops the oldest messages, and a band tool result
    whose assistant call just went out of the band still has to be named in its
    note (T1.4 contract), not degraded to ``?``.

    Returns how many band messages gave way (0 = nothing squeezed). A message
    demoted at both notches counts once.
    """
    if len(result) <= 1 or estimate_tokens(result) <= int(budget):
        return 0
    # Pass 1 keeps a preview and stops short of the newest; pass 2 demotes to
    # marker-only and runs all the way through, so the newest only gives way last.
    newest = max(1, len(result) - _BAND_PROTECT_NEWEST)
    squeezed_idx: set[int] = set()
    fits = False
    for with_preview, stop in ((True, newest), (False, len(result))):
        for i in range(1, stop):
            label, anchor = _note_label_and_anchor(result[i], names)
            squeezed = _squeeze_content(
                result[i].get("content"),
                label=label,
                anchor=anchor,
                trace_ref=trace_ref,
                head_chars=head_chars,
                tail_chars=tail_chars,
                with_preview=with_preview,
            )
            if squeezed is None:
                continue
            # role / tool_call_id / tool_calls ride on untouched — pairings survive.
            result[i] = {**result[i], "content": squeezed}
            squeezed_idx.add(i)
            if estimate_tokens(result) <= int(budget):
                fits = True
                break
        if fits:
            break
    return len(squeezed_idx)


def _tool_names_by_call_id(messages: List[Dict[str, Any]]) -> Dict[str, str]:
    """Map ``tool_call_id`` → tool name via the assistant ``tool_calls`` blocks."""
    names: Dict[str, str] = {}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function")
            name = fn.get("name") if isinstance(fn, dict) else None
            call_id = tc.get("id")
            if isinstance(call_id, str) and isinstance(name, str) and name:
                names[call_id] = name
    return names


def evict_tool_results(
    messages: List[Dict[str, Any]],
    *,
    threshold_chars: int = 4000,
    protect_recent: int = 10,
    head_chars: int = 800,
    tail_chars: int = 400,
    trace_ref: str = "",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Replace oversized old ``tool`` message content with a compact placeholder.

    The message itself is never dropped (OpenAI requires the ``tool`` reply to
    stay paired with its ``tool_call_id``); only ``content`` is swapped for
    ``<marker> | tool name | original size | trace pointer`` plus a head/tail
    preview. Purely mechanical, zero extra LLM calls.

    Returns ``(messages, evictions)``. Without a single eviction the input list
    object is returned as-is (identity, same convention as
    :func:`compress_messages`); ``evictions`` is a list of
    ``{tool_call_id, tool_name, original_chars}`` dicts for the caller's event.
    """
    if not messages:
        return messages, []

    protect_start = max(0, len(messages) - max(0, int(protect_recent)))
    names = _tool_names_by_call_id(messages)
    out = list(messages)
    evictions: List[Dict[str, Any]] = []
    for i, msg in enumerate(messages):
        if i >= protect_start:
            break  # the recent tail is never touched (positional, role-agnostic)
        if msg.get("role") != "tool":
            continue
        content = msg.get("content")
        if not isinstance(content, str) or len(content) <= int(threshold_chars):
            continue
        if content.startswith(EVICT_PLACEHOLDER_PREFIX):
            continue  # idempotent: every LLM call re-runs this pass
        if int(head_chars) + int(tail_chars) >= len(content):
            continue  # degenerate preview: no space would be saved
        call_id = str(msg.get("tool_call_id") or "?")
        name = names.get(call_id, "?")
        placeholder = _note_marker(content, label=f"工具: {name}", ref=_note_ref(trace_ref, call_id))
        placeholder += _note_preview(content, head_chars, tail_chars)
        new_msg = dict(msg)
        new_msg["content"] = placeholder
        out[i] = new_msg  # fresh dict; untouched messages keep their identity
        evictions.append(
            {"tool_call_id": call_id, "tool_name": name, "original_chars": len(content)}
        )
    if not evictions:
        return messages, []
    return out, evictions


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
    budget: int,
    meta_base: Meta,
    *,
    head_chars: int = 800,
    tail_chars: int = 400,
    trace_ref: str = "",
) -> Tuple[List[Dict[str, Any]], Meta]:
    """Drop the early messages, insert a deterministic placeholder, then squeeze the band.

    ``keep_recent`` is fixed, so a band of oversized messages can outgrow the budget
    all by itself (#49): truncation then just recycles its own placeholder and the
    compression ratio stays at 1 forever. :func:`_squeeze_band` gives the band way
    under exactly that condition, oldest-first, until the budget is met.

    Convergence guard (#49): on the token trigger, a round that leaves the context no
    smaller than it was is not a compression. It reports ``compressed=False``, leaves
    the notes/placeholder bookkeeping at zero and returns the input list untouched —
    that is what stops the per-round ``context_compressed`` flood once the band has
    nothing left to give. The count trigger keeps truncating unconditionally (bounding
    the message count is its job, token size is not).
    """
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
    band_evicted = _squeeze_band(
        result,
        budget,
        names=_tool_names_by_call_id(messages),
        head_chars=head_chars,
        tail_chars=tail_chars,
        trace_ref=trace_ref,
    )
    tokens = estimate_tokens(result)
    if meta_base["trigger"] == "token" and tokens >= int(meta_base["context_tokens"]):
        # Nothing strictly smaller (band gave way nowhere, truncation only recycled the
        # previous placeholder): no rewrite to report and no event to flood the stream.
        meta = dict(meta_base)
        meta["band_evicted"] = 0
        return messages, meta
    meta = dict(meta_base)
    meta["compressed"] = True
    meta["dropped"] = dropped
    meta["band_evicted"] = band_evicted
    meta["context_tokens"] = tokens
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
    *,
    trace_ref: str = "",
    head_chars: int = 800,
    tail_chars: int = 400,
) -> Tuple[List[Dict[str, Any]], Meta]:
    """Compress ``messages`` when they exceed the configured thresholds.

    Returns ``(messages, meta)`` where ``meta`` carries:

    * ``compressed`` — whether the context actually got smaller (a round that saves
      nothing reports ``False``, the convergence guard of #49);
    * ``dropped`` — number of messages removed;
    * ``band_evicted`` — number of still-protected messages squeezed to a note
      (only the ``truncate`` strategy squeezes, so ``summarize`` reports ``0``);
    * ``context_tokens`` — estimate of the returned list;
    * ``strategy`` — the effective strategy (``summarize`` falls back to
      ``truncate`` when no LLM is available);
    * ``trigger`` — ``"token"`` | ``"count"`` | ``"none"``.

    Under the budget the input list is returned unchanged (identity) — as it is on the
    token trigger when a compression round would leave the context no smaller (see
    ``compressed``).
    """
    meta_base: Meta = {
        "compressed": False,
        "dropped": 0,
        "band_evicted": 0,
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
    return _compress_truncate(
        messages,
        keep_recent,
        budget,
        meta_base,
        head_chars=head_chars,
        tail_chars=tail_chars,
        trace_ref=trace_ref,
    )
