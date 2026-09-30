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
  oldest-first into a note of the same shape as :func:`evict_tool_results`' (its
  marker names the role that gave way). The squeeze lives at this entry point, so
  it runs under both strategies and over the whole history when ``keep_recent``
  covers every message (#85);
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


# T1.4 tool-result eviction: the marker a tool note starts with (also part of the
# idempotency guard — an already-evicted message is never evicted twice).
EVICT_PLACEHOLDER_PREFIX = "[tool result 已移除"
# A squeezed user / assistant / system message is not a tool result, so its note
# says so (#49 review F1): the marker names what actually went away.
MESSAGE_PLACEHOLDER_PREFIX = "[消息原文已移除"
# Every note header, for the idempotency guards — a note is never re-wrapped.
NOTE_PREFIXES = (EVICT_PLACEHOLDER_PREFIX, MESSAGE_PLACEHOLDER_PREFIX)
# In-band squeeze (#49): how many trailing messages stay verbatim before the band
# starts giving way. One, because the last reply is what the model answers against;
# pi keeps the newest suffix verbatim and opencode v1 only protects a couple of
# turns — neither protects the whole band. It is not a settings key: tuning the band
# width is ``context_keep_recent``'s job, this is the squeeze's own floor.
_BAND_PROTECT_NEWEST = 1


def _note_ref(trace_ref: str, anchor: str) -> str:
    """Trace pointer of a note: ``<file>#<tool_call_id>``, honest when unavailable.

    Only a tool result has an anchor to point at. ``trace.py`` mirrors **events**, and
    ``tool_result`` is the one event kind that carries a message's full text; a user /
    assistant turn is in no event at all, so pointing such a note at the trace file
    would promise text that was never written there (#49 review F2).
    """
    if not anchor:
        return "无（trace 不落该条原文）"
    if not trace_ref:
        return "无（trace 未开启）"
    return f"{trace_ref}#{anchor}"


def _note_marker(text: str, *, prefix: str, label: str, ref: str) -> str:
    """The single-line header every note starts with (``原文`` size included)."""
    return f"{prefix} | {label} | 原文 {len(text)} 字符 | 留痕: {ref}]"


def _note_preview(text: str, head_chars: int, tail_chars: int) -> str:
    """Head/tail preview body appended under a note marker."""
    return (
        f"\n--- 头部预览（前 {head_chars} 字符）---\n{text[:head_chars]}\n"
        f"--- 尾部预览（后 {tail_chars} 字符）---\n"
        f"{text[-tail_chars:] if tail_chars > 0 else ''}"
    )


def _note_form(msg: Dict[str, Any], names: Dict[str, str]) -> Tuple[str, str, str]:
    """Note marker prefix + label + trace anchor for one message.

    ``tool`` results keep the T1.4 contract verbatim (its marker, tool name,
    ``tool_call_id`` pointer). Other roles have no marker claiming a tool result and
    no per-call anchor in the trace, so their note just says what it folded away
    (``消息: <role>``).
    """
    role = str(msg.get("role") or "unknown")
    if role == "tool":
        call_id = str(msg.get("tool_call_id") or "?")
        return EVICT_PLACEHOLDER_PREFIX, f"工具: {names.get(call_id, '?')}", call_id
    return MESSAGE_PLACEHOLDER_PREFIX, f"消息: {role}", ""


def _squeeze_content(
    content: Any,
    *,
    prefix: str,
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
    if content.startswith(NOTE_PREFIXES):
        if with_preview:
            return None  # already a note at this notch: idempotent skip
        header, sep, rest = content.partition("\n")
        if not sep or not rest:
            return None  # marker-only already: no notch left
        return header
    marker = _note_marker(content, prefix=prefix, label=label, ref=_note_ref(trace_ref, anchor))
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
    head: int,
    names: Dict[str, str],
    head_chars: int,
    tail_chars: int,
    trace_ref: str,
) -> int:
    """Squeeze the band ``keep_recent`` protects when that band alone outgrows the budget.

    Mutates ``result``. The first ``head`` entries are the head block laid over the
    band (the truncation placeholder / the summary) and are never squeezed; with
    nothing dropped there is no head block, so the band is the whole list and
    ``head`` is 0 (#85). Runs oldest-first so the newest messages survive verbatim.
    The budget is the only threshold: the first layout that fits ends the squeeze,
    and a message that cannot shrink strictly is skipped, so the pass is bounded by
    the band size and can never spin.

    The last notch is role-gated (#49 review): only ``tool`` results may be demoted to
    a bare marker, newest-first, because their full text still sits in the trace
    behind the ``#tool_call_id`` anchor. A user/assistant preview is the last copy
    that exists — no event carries their text — so their notes keep their preview.
    When the tool candidates run out and the budget is still out of reach, the squeeze
    stops and :func:`compress_messages` reports ``converged=False``.

    ``names`` is the ``tool_call_id`` → tool name map of the **whole** history, not
    just the band: truncation drops the oldest messages, and a band tool result
    whose assistant call just went out of the band still has to be named in its
    note (T1.4 contract), not degraded to ``?``.

    Returns how many band messages gave way (0 = nothing squeezed). A message
    demoted at both notches counts once.
    """
    if len(result) <= head or estimate_tokens(result) <= int(budget):
        return 0
    # Pass 1 keeps a preview and stops short of the newest; pass 2 is the last notch
    # and only ever touches tool results, so the newest message gives way last.
    newest = max(head, len(result) - _BAND_PROTECT_NEWEST)
    squeezed_idx: set[int] = set()
    fits = False
    passes = ((True, range(head, newest)), (False, range(len(result) - 1, head - 1, -1)))
    for with_preview, indices in passes:
        for i in indices:
            if not with_preview and result[i].get("role") != "tool":
                continue  # A′: a non-tool note never loses its preview
            prefix, label, anchor = _note_form(result[i], names)
            squeezed = _squeeze_content(
                result[i].get("content"),
                prefix=prefix,
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
        if content.startswith(NOTE_PREFIXES):
            continue  # idempotent: every LLM call re-runs this pass
        if int(head_chars) + int(tail_chars) >= len(content):
            continue  # degenerate preview: no space would be saved
        call_id = str(msg.get("tool_call_id") or "?")
        name = names.get(call_id, "?")
        placeholder = _note_marker(
            content,
            prefix=EVICT_PLACEHOLDER_PREFIX,
            label=f"工具: {name}",
            ref=_note_ref(trace_ref, call_id),
        )
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


def _truncate_candidate(
    messages: List[Dict[str, Any]], keep_recent: int
) -> Tuple[List[Dict[str, Any]], int]:
    """The ``truncate`` layout: placeholder for the early history + the protected band.

    Returns ``(candidate, dropped)``. ``keep`` is clamped to at least
    ``_MIN_KEEP_RECENT``, so a history that fits inside the band comes back as the
    input list with ``dropped == 0`` — the band then *is* the whole history, which
    :func:`compress_messages` still squeezes when it outgrows the budget (#85 残窗一:
    the count-based early exit used to leave that band uncompressed, which is the
    #82 symptom surviving at that boundary without even flooding events).

    Whether the round counts as a compression is the entry point's call (the
    convergence guard), not this function's.
    """
    n = len(messages)
    keep = max(_MIN_KEEP_RECENT, min(int(keep_recent), n))
    if keep >= n:
        return messages, 0
    dropped = n - keep
    placeholder = {
        "role": "system",
        "content": f"{PLACEHOLDER_PREFIX}{dropped}{PLACEHOLDER_SUFFIX}",
    }
    return [placeholder, *messages[-keep:]], dropped


def _summarize_candidate(
    messages: List[Dict[str, Any]],
    keep_recent: int,
    llm: Any,
    summary_max_tokens: int,
) -> Tuple[List[Dict[str, Any]], int]:
    """The ``summarize`` layout: one LLM summary block in place of the early history.

    Same ``(candidate, dropped)`` contract as :func:`_truncate_candidate`. Its early
    exit matters twice over: with nothing dropped there is no early history to
    summarise, so a squeeze-only round must never spend an LLM call.
    """
    n = len(messages)
    keep = max(_MIN_KEEP_RECENT, min(int(keep_recent), n))
    if keep >= n:
        return messages, 0
    early = messages[:-keep]
    summary = summarize_messages(llm, early, summary_max_tokens)
    return [{"role": "system", "content": summary}, *messages[-keep:]], len(early)


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
    * ``band_evicted`` — number of still-protected messages squeezed to a note.
      Both strategies squeeze (#85), and with ``keep_recent`` covering the whole
      history the band *is* that history: ``band_evicted`` can be non-zero while
      ``dropped`` stays ``0``;
    * ``converged`` — whether the returned context fits ``budget``. ``False`` means the
      squeeze ran out of what it may lawfully give way (see the role gate in
      :func:`_squeeze_band`) and the context is still oversized;
    * ``context_tokens`` — estimate of the returned list;
    * ``strategy`` — the effective strategy (``summarize`` falls back to
      ``truncate`` when no LLM is available);
    * ``trigger`` — ``"token"`` | ``"count"`` | ``"none"``.

    Under the budget the input list is returned unchanged (identity) — as it is on the
    token trigger when a compression round would leave the context no smaller (see
    ``compressed``).
    """
    tokens_in = estimate_tokens(messages)
    meta_base: Meta = {
        "compressed": False,
        "dropped": 0,
        "band_evicted": 0,
        "context_tokens": tokens_in,
        "strategy": "truncate",
        "trigger": "none",
        # Whether the context we hand back fits the budget. This default is honest for
        # the two early returns below, which only fire when it already does.
        "converged": tokens_in <= int(budget),
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
        candidate, dropped = _summarize_candidate(messages, keep_recent, llm, summary_max_tokens)
    else:
        candidate, dropped = _truncate_candidate(messages, keep_recent)

    # The squeeze runs here, not inside one strategy's happy path (#85 残窗二): an
    # oversized protected band is the same failure whichever block was laid over it.
    # The candidate is copied first when nothing was dropped, because the squeeze
    # rewrites entries in place and a round that converges nowhere must hand the
    # caller its own list back untouched.
    band = candidate if candidate is not messages else list(messages)
    band_evicted = _squeeze_band(
        band,
        budget,
        head=1 if dropped else 0,
        names=_tool_names_by_call_id(messages),
        head_chars=head_chars,
        tail_chars=tail_chars,
        trace_ref=trace_ref,
    )
    tokens = estimate_tokens(band)
    if not (dropped or band_evicted):
        # No head block to insert and nothing in the band gave way: no rewrite, so
        # nothing to report and no event to flood the stream.
        return messages, meta_base
    if over_token and tokens >= tokens_in:
        # Convergence guard (#49), now one definition for both strategies: on the token
        # trigger a round that leaves the context no smaller is not a compression — the
        # head block merely recycled the previous placeholder / summary. The count
        # trigger keeps truncating unconditionally (bounding the message count is its
        # job, token size is not).
        return messages, meta_base
    meta = dict(meta_base)
    meta["compressed"] = True
    meta["dropped"] = dropped
    meta["band_evicted"] = band_evicted
    # One definition, both strategies: converged means the result fits the budget,
    # whether or not this round managed to rewrite anything.
    meta["context_tokens"] = tokens
    meta["converged"] = tokens <= int(budget)
    return band, meta
