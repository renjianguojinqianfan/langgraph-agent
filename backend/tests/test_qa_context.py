"""QA independent edge-case tests — context compression (P0 item 1).

These tests are written from a reviewer's perspective, covering boundaries the
engineer's own suite does not stress: Chinese token estimation, multimodal
content parts, assistant+tool pairing preservation, count-trigger boundaries,
keep_recent clamping, summarize LLM failure fallback, and the integration of
``_build_messages`` (state write-back + ``context_compressed`` event).
"""

from __future__ import annotations

from types import SimpleNamespace

from backend.config import Settings
from backend.core.agent.context import (
    compress_messages,
    estimate_tokens,
    summarize_messages,
)
from backend.core.agent.nodes import AgentRuntime
from backend.services.event_bus import EventBus


def _msg(role: str, content: str, **extra) -> dict:
    m = {"role": role, "content": content}
    m.update(extra)
    return m


def _many(n: int = 20) -> list:
    return [
        _msg("user" if i % 2 == 0 else "assistant", f"msg {i} " + "x" * 60)
        for i in range(n)
    ]


def _pair_messages() -> list:
    """One full assistant+tool round with OpenAI fields intact."""
    return [
        _msg("user", "please search"),
        _msg(
            "assistant",
            "",
            tool_calls=[
                {
                    "id": "call_9",
                    "type": "function",
                    "function": {"name": "web_search", "arguments": '{"query": "ai"}'},
                }
            ],
        ),
        _msg("tool", '{"results": []}', tool_call_id="call_9"),
        _msg("assistant", "done"),
    ]


# ── estimate_tokens edges ─────────────────────────────────────
def test_estimate_tokens_chinese_grows_with_length():
    small = [_msg("user", "你好")]
    large = [_msg("user", "你好" * 500)]
    assert estimate_tokens(small) >= 1
    assert estimate_tokens(large) > estimate_tokens(small)


def test_estimate_tokens_multimodal_content_parts():
    msgs = [_msg("user", [{"type": "text", "text": "hello world"}])]
    assert estimate_tokens(msgs) >= 1


def test_estimate_tokens_content_none_does_not_crash():
    msgs = [{"role": "assistant", "content": None}]
    assert estimate_tokens(msgs) >= 1


# ── compress_messages boundaries ──────────────────────────────
def test_compress_keeps_recent_assistant_tool_pair_intact():
    msgs = _many(10) + _pair_messages()
    out, meta = compress_messages(msgs, budget=100, keep_recent=4)
    assert meta["compressed"] is True
    # The last 4 raw messages are preserved verbatim: the assistant message
    # with tool_calls and its paired tool result keep their OpenAI fields.
    assert out[-4:] == _pair_messages()
    assert out[-3]["tool_calls"][0]["id"] == "call_9"  # assistant w/ tool_calls
    assert out[-2]["tool_call_id"] == "call_9"  # paired tool result
    assert out[0]["role"] == "system"
    assert out[0]["content"]  # placeholder present


def test_compress_no_trigger_when_keep_recent_ge_n():
    msgs = _many(5)
    out, meta = compress_messages(msgs, budget=1, keep_recent=100)
    assert out is msgs  # identity
    assert meta["compressed"] is False


def test_compress_count_trigger_boundary():
    msgs = _many(10)
    # Exactly max_messages -> NOT triggered.
    out, meta = compress_messages(msgs, budget=1_000_000, max_messages=10)
    assert meta["compressed"] is False
    # One more than max_messages -> triggered.
    out2, meta2 = compress_messages(_many(11), budget=1_000_000, max_messages=10)
    assert meta2["compressed"] is True
    assert meta2["trigger"] == "count"


def test_compress_clamps_keep_recent_to_min_two():
    msgs = _many(20)
    for kr in (0, 1, -5):
        out, meta = compress_messages(msgs, budget=1, keep_recent=kr)
        assert meta["compressed"] is True
        # Placeholder + at least 2 raw messages (one assistant+tool round).
        assert len(out) == 3


# ── summarize edge: LLM failure must not break the caller ─────
def test_summarize_messages_llm_error_fallback():
    class BoomLLM:
        def complete(self, messages, tools=None, **kwargs):
            raise RuntimeError("llm down")

    summary = summarize_messages(BoomLLM(), _many(5), max_tokens=50)
    assert "省略" in summary or "失败" in summary


def test_summarize_messages_empty_input():
    assert summarize_messages(SimpleNamespace(), [], max_tokens=50) == "[无早期消息]"


# ── AgentRuntime._build_messages integration ──────────────────
def _runtime_with(settings: Settings, event_bus: EventBus) -> AgentRuntime:
    tm = SimpleNamespace(settings=settings, event_bus=event_bus)
    return AgentRuntime(
        task_id="qa-t1",
        task_manager=tm,
        llm=SimpleNamespace(),
        tools=[],
        tool_schemas=[],
    )


def test_build_messages_compresses_and_writes_state():
    settings = Settings(
        context_token_budget=50,
        context_keep_recent=4,
        context_max_messages=0,
        context_compress_strategy="truncate",
    )
    bus = EventBus()
    collected = []
    bus.subscribe("qa-t1", lambda e: collected.append(e))
    rt = _runtime_with(settings, bus)

    state = {"messages": _many(30), "step_index": 3}
    out = rt._build_messages(state, "SYS")

    # State write-back: messages rewritten, compressed flag set, tokens updated.
    assert state["compressed"] is True
    assert state["context_tokens"] > 0
    assert state["messages"][0]["role"] == "system"
    # Returned list = real system prompt + compressed history.
    assert out[0] == {"role": "system", "content": "SYS"}
    # Optional event published.
    assert any(e["type"] == "context_compressed" for e in collected)


def test_build_messages_no_compression_returns_unchanged():
    settings = Settings(context_token_budget=1_000_000, context_keep_recent=10)
    bus = EventBus()
    rt = _runtime_with(settings, bus)

    msgs = _many(4)
    state = {"messages": msgs, "step_index": 1}
    out = rt._build_messages(state, "SYS")

    assert out[0] == {"role": "system", "content": "SYS"}
    assert out[1:] == msgs
    # No rewrite: state untouched apart from context_tokens.
    assert "compressed" not in state
    assert state["context_tokens"] == estimate_tokens(msgs)
