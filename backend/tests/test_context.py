"""Tests for context compression (P0 item 1) — fully offline.

Covers token estimation, truncation triggers (token budget + message count),
``keep_recent`` preservation, the never-empty guarantee, the truncation
placeholder structure, the optional LLM summarise strategy (with a fake
LLM and the ``llm=None`` truncate fallback), and the in-band squeeze that makes
compression converge when the protected band itself outgrows the budget (#49).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, cast

from backend.core.agent.context import (
    EVICT_PLACEHOLDER_PREFIX,
    compress_messages,
    estimate_tokens,
    evict_tool_results,
    summarize_messages,
)
from backend.core.agent.nodes import AgentRuntime
from backend.core.agent.state import AgentState
from backend.services.event_bus import EventBus
from backend.tests.conftest import make_settings


def _msg(role: str, content: str, **extra) -> dict:
    m = {"role": role, "content": content}
    m.update(extra)
    return m


def _assistant_call(call_id: str, name: str) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": "{}"}}
        ],
    }


def _tool(content: str, call_id: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _notes(out: list) -> list:
    """Contents in ``out`` that are eviction notes."""
    return [
        m["content"]
        for m in out
        if str(m.get("content", "")).startswith(EVICT_PLACEHOLDER_PREFIX)
    ]


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
    rounds = []
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


TRACE_REF = "/tmp/traces/t49.jsonl"


def test_band_squeeze_from_oldest_keeps_the_newest_verbatim():
    """卡 3：带内从最旧开始挤，够到预算就停手，最新几条保持逐字原样。"""
    msgs = [_msg("user", "x" * 4000) for _ in range(6)]
    out, meta = compress_messages(msgs, budget=3600, keep_recent=4, trace_ref=TRACE_REF)

    assert meta["band_evicted"] == 1  # 只挤了最旧那条就到位了
    assert meta["context_tokens"] <= 3600
    assert out[1]["content"].startswith(EVICT_PLACEHOLDER_PREFIX)
    # 其余带内消息（含最新一条）没被碰过——连对象都是同一个。
    assert out[2:] == msgs[3:]
    assert out[2] is msgs[3]
    assert out[-1]["content"] == "x" * 4000


def test_band_squeeze_not_gated_by_the_evict_char_threshold():
    """卡 6：4000 字符恰在带外逐出的 ``<= threshold_chars`` 上，带内不受它约束。

    票面复现的消息就是 4000 整——带内挤压一旦复用那个跳过条件，空转立刻复发。
    """
    msgs: List[Dict[str, Any]] = []
    for i in range(3):
        msgs.append(_assistant_call(f"c{i}", "read"))
        msgs.append(_tool("x" * 4000, f"c{i}"))

    same_chars = evict_tool_results(msgs, threshold_chars=4000, protect_recent=0)
    assert same_chars[1] == [] and same_chars[0] is msgs  # 带外旧行为不动：阈值是 >4000

    out, meta = compress_messages(msgs, budget=1000, keep_recent=4, trace_ref=TRACE_REF)
    assert meta["band_evicted"] > 0  # 同样 4000 整，带内照样被挤
    assert meta["context_tokens"] <= 1000


def test_band_note_reuses_the_evict_contract():
    """卡 2 / 卡 4：工具消息的便签沿用 T1.4 契约，OpenAI 字段一个字不动。"""
    msgs = [_msg("user", "任务：把这几段读完")]
    for i in range(6):
        msgs.append(_assistant_call(f"c{i}", "read"))
        msgs.append(_tool("z" * 4000, f"c{i}"))

    out, meta = compress_messages(msgs, budget=1000, keep_recent=4, trace_ref=TRACE_REF)

    assert meta["band_evicted"] >= 1
    assert meta["context_tokens"] <= 1000
    # 带 = 末尾 4 条（a_c4 / t_c4 / a_c5 / t_c5）；挤压一条也不减，配对不会被挤散。
    assert len(out) == 5, out
    # 卡 4：assistant 的 content 是空串，无可再挤 → 原对象、tool_calls 一字未动。
    assert out[1] is msgs[9] and out[3] is msgs[11]
    assert out[1]["tool_calls"][0]["id"] == "c4"
    assert out[3]["tool_calls"][0]["id"] == "c5"
    # 两条工具结果都沿用 T1.4 便签契约：工具名 + 原文字符数 + {trace_ref}#{tool_call_id}。
    for idx, cid in ((2, "c4"), (4, "c5")):
        note = out[idx]["content"]
        assert note.startswith(EVICT_PLACEHOLDER_PREFIX)
        assert "工具: read" in note
        assert "原文 4000 字符" in note
        assert f"留痕: {TRACE_REF}#{cid}" in note
        assert out[idx]["role"] == "tool"
        assert out[idx]["tool_call_id"] == cid
        assert note.count(EVICT_PLACEHOLDER_PREFIX) == 1  # 不套便签


def test_band_note_labels_non_tool_messages_without_a_fake_anchor():
    """非工具消息没有 per-call 锚点：如实写 ``消息: <role>``，指针指向 trace 文件本身。"""
    msgs = [_msg("user", "x" * 4000) for _ in range(6)]
    out, meta = compress_messages(msgs, budget=1000, keep_recent=4, trace_ref=TRACE_REF)
    assert meta["band_evicted"] > 0
    note = _notes(out)[0]
    assert "消息: user" in note
    assert TRACE_REF in note
    assert "#" not in note  # 不编造回读不到的锚点

    # trace 关掉了就写「无（trace 未开启）」，与带外逐出同一套措辞。
    off, _ = compress_messages(msgs, budget=1000, keep_recent=4, trace_ref="")
    assert "留痕: 无（trace 未开启）" in _notes(off)[0]


def test_band_squeeze_is_idempotent_across_rounds():
    """卡 2：便签不套便签——已是 marker-only 的消息这一轮原样留着，只挤新的。"""
    marker_only = {
        "role": "user",
        "content": f"{EVICT_PLACEHOLDER_PREFIX} | 消息: user | 原文 4000 字符 | 留痕: {TRACE_REF}]",
    }
    msgs = [_msg("user", "x" * 4000), marker_only] + [
        _msg("assistant", "x" * 4000) for _ in range(4)
    ]

    out, meta = compress_messages(msgs, budget=1000, keep_recent=5, trace_ref=TRACE_REF)

    assert meta["context_tokens"] <= 1000
    # 带 = msgs[1:]，第一条就是上轮留下的 marker-only → 没 notch 可走，对象都不变。
    assert out[1] is marker_only
    assert out[1]["content"].count(EVICT_PLACEHOLDER_PREFIX) == 1
    for content in _notes(out):
        assert content.count(EVICT_PLACEHOLDER_PREFIX) == 1  # 绝不打第二层便签
    # 没被挤的仍是原对象（身份约定与 evict_tool_results 一致）。
    assert out[-1] is not msgs[-1]  # 最新这条被降到了 marker-only（见卡 6 的读法）
    assert out[-1]["role"] == "assistant"


def test_band_squeeze_stops_when_nothing_left_to_squeeze():
    """卡 7：无可再挤就停手——不空转、不发新事件，``dropped`` 如实报。"""
    msgs = _many_messages(30)  # 每条 ~58 字符，便签比原文还长 → 挤了反而变大
    out, meta = compress_messages(msgs, budget=1, keep_recent=2)

    assert meta["compressed"] is True  # 截断本身照旧（旧语义不变）
    assert meta["band_evicted"] == 0  # 带内一步没走
    assert meta["dropped"] == 28
    assert len(out) == 3
    assert _notes(out) == []


def test_summarize_strategy_reports_band_evicted_zero():
    """``band_evicted`` 是 meta 的固定字段：summarize 不挤带，报 0。"""
    msgs = _many_messages(30)
    out, meta = compress_messages(
        msgs, budget=100, keep_recent=5, strategy="summarize", llm=_FakeLLM("摘要")
    )
    assert meta["band_evicted"] == 0
    assert _notes(out) == []


def test_compressed_event_carries_band_evicted(tmp_path: Path):
    """卡 1 / 卡 5：计数随现有 ``context_compressed`` 出，不开新事件类型。"""
    bus = EventBus()
    events: List[Dict[str, Any]] = []
    bus.subscribe("t49", events.append)
    settings = make_settings(
        tmp_path,
        context_evict_enabled=False,
        context_token_budget=1000,
        context_keep_recent=10,
    )
    rt = AgentRuntime(
        task_id="t49",
        task_manager=SimpleNamespace(
            settings=settings, event_bus=bus, add_artifact=lambda *a, **k: None
        ),
        llm=SimpleNamespace(),
        tools=[],
        tool_schemas=[],
    )
    state = cast(
        AgentState,
        {
            "messages": [_msg("user" if i % 2 == 0 else "assistant", "x" * 4000) for i in range(12)],
            "step_index": 2,
        },
    )
    rt._build_messages(state, "SYS")

    compressed = [e for e in events if e["type"] == "context_compressed"]
    assert len(compressed) == 1, events
    assert compressed[0]["data"]["band_evicted"] > 0
    assert compressed[0]["data"]["dropped"] == 2  # 截断计数如实报，没被挤压污染
    assert compressed[0]["data"]["context_tokens"] <= 1000
    assert state["context_tokens"] <= 1000
    # 没有新事件类型混进来。
    assert {e["type"] for e in events} == {"context_compressed"}
