"""Tests for context compression (P0 item 1) — fully offline.

Covers token estimation, truncation triggers (token budget + message count),
``keep_recent`` preservation, the never-empty guarantee, the truncation
placeholder structure, and the optional LLM summarise strategy (with a fake
LLM and the ``llm=None`` truncate fallback).
"""

from __future__ import annotations

from types import SimpleNamespace

from backend.core.agent.context import compress_messages, estimate_tokens, summarize_messages


def _msg(role: str, content: str, **extra) -> dict:
    m = {"role": role, "content": content}
    m.update(extra)
    return m


def _many_messages(n: int = 20) -> list:
    return [
        _msg("user" if i % 2 == 0 else "assistant", f"message number {i} " + "x" * 40)
        for i in range(n)
    ]


# ── estimate_tokens ───────────────────────────────────────────
def test_estimate_tokens_positive_and_grows():
    small = [_msg("user", "hi")]
    large = [_msg("user", "hi " * 1000)]
    assert estimate_tokens(small) >= 1
    assert estimate_tokens(large) > estimate_tokens(small)


def test_estimate_tokens_counts_tool_calls():
    msgs = [
        _msg(
            "assistant",
            "",
            tool_calls=[
                {
                    "id": "1",
                    "type": "function",
                    "function": {"name": "web_search", "arguments": '{"query": "x"}'},
                }
            ],
        )
    ]
    assert estimate_tokens(msgs) >= 1


# ── compress_messages: no trigger ─────────────────────────────
def test_no_compression_under_budget_returns_identical():
    msgs = _many_messages(4)
    out, meta = compress_messages(msgs, budget=1_000_000, keep_recent=10)
    assert out is msgs  # identity: untouched under the budget
    assert meta["compressed"] is False
    assert meta["dropped"] == 0
    assert meta["trigger"] == "none"


def test_compress_empty_messages():
    out, meta = compress_messages([], budget=100)
    assert out == []
    assert meta["compressed"] is False


# ── compress_messages: truncate ───────────────────────────────
def test_compression_triggers_on_token_budget():
    msgs = _many_messages(30)
    out, meta = compress_messages(msgs, budget=100, keep_recent=5)
    assert meta["compressed"] is True
    assert meta["trigger"] == "token"
    assert meta["dropped"] > 0
    assert len(out) == 5 + 1  # placeholder + recent


def test_compression_keeps_recent_messages_in_order():
    msgs = _many_messages(30)
    out, meta = compress_messages(msgs, budget=100, keep_recent=4)
    recent = [m["content"] for m in out[1:]]
    assert recent == [m["content"] for m in msgs[-4:]]
    assert out[0]["role"] == "system"
    assert ("已截断" in out[0]["content"]) or ("省略" in out[0]["content"])


def test_compression_message_count_trigger():
    msgs = _many_messages(30)
    out, meta = compress_messages(msgs, budget=1_000_000, max_messages=10, keep_recent=3)
    assert meta["compressed"] is True
    assert meta["trigger"] == "count"
    assert len(out) == 3 + 1


def test_compression_never_empties_all_messages():
    msgs = _many_messages(30)
    out, meta = compress_messages(msgs, budget=1, keep_recent=1)
    assert meta["compressed"] is True
    # keep_recent is clamped to at least 2 (one assistant+tool round survives).
    assert len(out) == 3


def test_compressed_output_under_budget():
    msgs = _many_messages(50)
    out, meta = compress_messages(msgs, budget=200, keep_recent=5)
    assert meta["compressed"] is True
    assert meta["context_tokens"] <= 200


def test_compression_keeps_openai_fields_intact():
    msgs = _many_messages(30)
    msgs[-1]["tool_call_id"] = "call_1"
    msgs[-1]["tool_calls"] = [
        {"id": "c", "type": "function", "function": {"name": "f", "arguments": "{}"}}
    ]
    out, meta = compress_messages(msgs, budget=100, keep_recent=2)
    kept = out[-1]
    assert kept["tool_call_id"] == "call_1"
    assert kept["tool_calls"][0]["id"] == "c"


# ── compress_messages: summarize (optional) ───────────────────
class _FakeLLM:
    def __init__(self, content: str = "这是一段摘要") -> None:
        self.content = content
        self.calls = 0

    def complete(self, messages, tools=None, **kwargs):
        self.calls += 1
        return SimpleNamespace(content=self.content, tool_calls=[])


def test_summarize_strategy_uses_llm():
    msgs = _many_messages(30)
    llm = _FakeLLM("早期历史摘要")
    out, meta = compress_messages(
        msgs, budget=100, keep_recent=5, strategy="summarize", llm=llm
    )
    assert meta["compressed"] is True
    assert meta["strategy"] == "summarize"
    assert llm.calls == 1
    assert out[0]["role"] == "system"
    assert "摘要" in out[0]["content"]
    assert len(out) == 5 + 1


def test_summarize_falls_back_to_truncate_without_llm():
    msgs = _many_messages(30)
    out, meta = compress_messages(
        msgs, budget=100, keep_recent=5, strategy="summarize", llm=None
    )
    assert meta["compressed"] is True
    assert meta["strategy"] == "truncate"
    assert ("已截断" in out[0]["content"]) or ("省略" in out[0]["content"])


def test_summarize_messages_direct():
    llm = _FakeLLM("压缩结果")
    summary = summarize_messages(llm, _many_messages(10), max_tokens=50)
    assert summary == "压缩结果"
    assert llm.calls == 1


# ── in-band squeeze (#49: the protected band itself outgrows the budget) ──
def test_ticket_49_repro_converges_below_budget():
    """票面复现：预算 1000 / keep_recent 10 / 12 条 4000 字符消息，跑三轮。

    固定 keep_recent 的旧实现每轮只能丢掉上一轮的 placeholder、再放一条新的，
    压缩比恒为 1（context_tokens 恒 10015），永远压不到预算以下，于是每轮都发一
    次 ``context_compressed``。修后必须收敛到预算以下，且收敛之后不再空转。
    """
    msgs = [
        _msg("user" if i % 2 == 0 else "assistant", "x" * 4000) for i in range(12)
    ]
    assert estimate_tokens(msgs) == 12012  # 起点：超预算 12 倍

    current = msgs
    rounds: list = []
    for _ in range(3):
        current, meta = compress_messages(current, budget=1000, keep_recent=10)
        rounds.append(
            {
                "tokens": meta["context_tokens"],
                "compressed": meta["compressed"],
                "dropped": meta["dropped"],
                "band_evicted": meta.get("band_evicted", 0),
            }
        )

    # 票面 AC：压到预算以下（旧实现恒 10015，这一条先红，红的就是症状本身）。
    assert rounds[-1]["tokens"] <= 1000, rounds
    # 收敛：每一轮真正改写消息的，token 都必须严格变小（不收敛就是恒定的旧症状）。
    for i in range(1, len(rounds)):
        if rounds[i]["compressed"]:
            assert rounds[i]["tokens"] < rounds[i - 1]["tokens"], rounds
    assert rounds[0]["tokens"] < 12012
    assert rounds[0]["band_evicted"] > 0  # 保护带被挤过，这才有了增量
    # 事件不再刷屏：一旦到位，后续轮次不得再自称压缩过。
    assert [r["compressed"] for r in rounds] == [True, False, False], rounds
