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
    MESSAGE_PLACEHOLDER_PREFIX,
    NOTE_PREFIXES,
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
    """Contents in ``out`` that are eviction notes (tool marker or 消息 marker)."""
    return [
        m["content"]
        for m in out
        if str(m.get("content", "")).startswith(NOTE_PREFIXES)
    ]


def _note_markers(content: str) -> int:
    """便签头的条数——一条消息最多一层便签，所以恒为 1。"""
    return sum(str(content).count(prefix) for prefix in NOTE_PREFIXES)


def _mixed_band() -> List[Dict[str, Any]]:
    """带内既有工具结果也有 user / assistant 轮次的一组消息（#49 评审 F1/F2）。

    首条会被截断挤到带外，剩下四条进保护带：assistant 调用（content 空，无可挤）、
    工具结果、user、assistant——两种前缀、两种指针形态正好同框。
    """
    return [
        _msg("user", "任务：把这几段读完"),
        _assistant_call("c0", "read"),
        _tool("t" * 4000, "c0"),
        _msg("user", "u" * 4000),
        _msg("assistant", "a" * 4000),
    ]


def _notes_by_role(out: list) -> Dict[str, str]:
    """带内便签按 role 归堆，方便逐个点名。"""
    return {
        str(m.get("role")): m["content"]
        for m in out[1:]
        if str(m.get("content", "")).startswith(NOTE_PREFIXES)
    }


def _runtime(tmp_path: Path, bus: EventBus, task_id: str, **settings_overrides) -> AgentRuntime:
    """A runtime wired to ``bus``, for the event-level assertions below."""
    return AgentRuntime(
        task_id=task_id,
        task_manager=SimpleNamespace(
            settings=make_settings(tmp_path, **settings_overrides),
            event_bus=bus,
            add_artifact=lambda *a, **k: None,
        ),
        llm=SimpleNamespace(),
        tools=[],
        tool_schemas=[],
    )


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
def test_ticket_49_repro_shrinks_then_stalls_without_flood():
    """票面复现（评审裁定 A′ 后的读法）：预算 1000 / keep_recent 10 / 12 条 4000 字符。

    固定 keep_recent 的旧实现每轮只能丢掉上一轮的 placeholder、再放一条新的，压缩比恒
    为 1（context_tokens 恒 10015），永远压不到预算以下，于是每轮都发一次
    ``context_compressed``。

    A′ 把 marker-only 那一档只留给 tool 结果（全文在 trace 里，锚点读得回），user/assistant
    的预览是它们的最后一份信息（没有任何事件承载原文）。所以这组 user/assistant fixture
    启用 PR 取舍①预写的退路：不再要求「压进预算」，改要求四件事——真变小、到位后不再
    自称压缩、最新一条原文没被削掉、以及如实报 ``converged=False``。数值 AC 留在
    :func:`test_tool_only_band_reaches_the_budget_at_marker_only` 那条 tool 角色用例上。
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
                "band_evicted": meta.get("band_evicted", 0),
                "converged": meta.get("converged"),
            }
        )

    # (a) 压缩比 <1：真正改写的那一轮必须严格变小（旧实现恒 10015，这一条先红）。
    assert rounds[0]["tokens"] < 12012, rounds
    assert rounds[0]["band_evicted"] > 0  # 保护带被挤过，这才有了增量
    # (b) 事件不刷屏：到位之后不得再每轮自称压缩过。
    assert [r["compressed"] for r in rounds] == [True, False, False], rounds
    # (c) 不可恢复的内容不归零：最新一条保持逐字。
    assert current[-1]["content"] == "x" * 4000, current[-1]
    # (d) 够不到预算要如实说，不许用「已压缩」糊过去。
    assert rounds[-1]["converged"] is False, rounds
    assert rounds[-1]["tokens"] > 1000, rounds


TRACE_REF = "/tmp/traces/t49.jsonl"


def test_tool_only_band_reaches_the_budget_at_marker_only():
    """A′ 的正面侧：全文在 trace 里的 tool 结果可以一路降到 marker-only，真收敛。

    与票面复现同一量级，差别只在角色。tool 便签带 ``{trace_ref}#{tool_call_id}`` 锚点，
    零预览不丢信息，所以数值 AC（压进预算）留在这条上，而不是硬塞给 user/assistant。
    """
    msgs: List[Dict[str, Any]] = []
    for i in range(12):
        msgs.append(_assistant_call(f"c{i}", "read"))
        msgs.append(_tool("x" * 4000, f"c{i}"))

    out, meta = compress_messages(msgs, budget=1000, keep_recent=10, trace_ref=TRACE_REF)

    assert meta["compressed"] is True
    assert meta["context_tokens"] <= 1000, meta
    assert meta["converged"] is True
    assert meta["band_evicted"] > 0
    # 挤过的都还是可回读的便签，不是裸删除。
    for note in _notes(out):
        assert f"留痕: {TRACE_REF}#" in note, note


def test_last_notch_demotes_tool_only_and_reports_not_converged():
    """A′ 的角色闸门：最后 notch 只准削 tool 结果，user 的预览是它最后一份信息。

    带内既有 4000 字符的 user 消息又有 tool 结果，预算小到不可能满足。tool 降到
    marker-only 之后必须停手——不许把 user 削成裸 marker，也不许每轮自称压缩。
    """
    msgs: List[Dict[str, Any]] = [
        _msg("system", "old" * 40),  # 被截到带外
        _assistant_call("c0", "read"),
        _tool("t" * 4000, "c0"),
        _msg("user", "v" * 4000),
        _msg("assistant", "w" * 4000),  # 最新一条，逐字
    ]

    out, meta = compress_messages(msgs, budget=100, keep_recent=4, trace_ref=TRACE_REF)

    assert meta["converged"] is False, meta
    user_notes = [m["content"] for m in out if m["role"] == "user" and _notes([m])]
    tool_notes = [m["content"] for m in out if m["role"] == "tool" and _notes([m])]
    assert user_notes and all("头部预览" in c for c in user_notes), out  # 预览不归零
    assert tool_notes and all("头部预览" not in c for c in tool_notes), out  # tool 降到底
    assert out[-1] is msgs[-1]  # 最新一条连对象都没换
    # 停手不空转：同一输入再来一轮，不再自称压缩、原样返回。
    again, meta2 = compress_messages(out, budget=100, keep_recent=4, trace_ref=TRACE_REF)
    assert meta2["compressed"] is False, meta2
    assert again is out


def test_band_squeeze_from_oldest_keeps_the_newest_verbatim():
    """卡 3：带内从最旧开始挤，够到预算就停手，最新几条保持逐字原样。"""
    msgs = [_msg("user", "x" * 4000) for _ in range(6)]
    out, meta = compress_messages(msgs, budget=3600, keep_recent=4, trace_ref=TRACE_REF)

    assert meta["band_evicted"] == 1  # 只挤了最旧那条就到位了
    assert meta["context_tokens"] <= 3600
    assert out[1]["content"].startswith(MESSAGE_PLACEHOLDER_PREFIX)  # user 消息，不是 tool result
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


def test_band_note_keeps_the_tool_name_when_the_call_was_truncated_away():
    """便签的工具名反查不许被截断自缚：整列表里查得到，就不能写成 ``?``。

    T1.4 契约要求便签点名工具。截断把最旧那条 assistant 调用挤到带外之后，带内
    那条 tool 结果照样要叫得出名字——同一条消息走带外逐出时点得出 ``read``，走带
    内挤压也必须点得出。
    """
    msgs = [
        _assistant_call("c1", "read"),
        _tool("a" * 4000, "c1"),
        _assistant_call("c2", "grep"),
        _tool("b" * 4000, "c2"),
    ]

    out, meta = compress_messages(msgs, budget=100, keep_recent=3, trace_ref=TRACE_REF)

    assert meta["dropped"] == 1  # c1 的 assistant 调用被截到带外了
    notes = _notes(out)
    assert len(notes) == 2, out
    assert "工具: read" in notes[0], notes[0]  # 调用已在带外，名字仍要查到
    assert "工具: grep" in notes[1], notes[1]
    assert "工具: ?" not in notes[0] + notes[1]


def test_band_note_labels_non_tool_messages_without_a_fake_anchor():
    """非工具消息没有 per-call 锚点：如实写 ``消息: <role>``，留痕一栏不假装有原文。"""
    msgs = [_msg("user", "x" * 4000) for _ in range(6)]
    out, meta = compress_messages(msgs, budget=1000, keep_recent=4, trace_ref=TRACE_REF)
    assert meta["band_evicted"] > 0
    note = _notes(out)[0]
    assert "消息: user" in note
    assert "#" not in note  # 不编造回读不到的锚点
    assert TRACE_REF not in note  # trace 里根本没有这条，指过去就是失实


def test_band_note_of_non_tool_message_does_not_claim_a_tool_result():
    """#49 评审 F1：便签前缀要说清「移走的是什么」——user 消息不是 tool result。

    带内挤压原先对所有角色无条件写 ``[tool result 已移除``，等于向模型与读流水的人
    声称移除过一条工具结果，而它从头到尾是一句话。工具结果那一侧的 T1.4 措辞不动。
    """
    out, meta = compress_messages(_mixed_band(), budget=1000, keep_recent=4, trace_ref=TRACE_REF)
    assert meta["band_evicted"] == 3, out

    notes = _notes_by_role(out)
    tool_note = notes["tool"]
    assert tool_note.startswith(EVICT_PLACEHOLDER_PREFIX)  # T1.4 契约一字不改
    assert "工具: read" in tool_note

    for role in ("user", "assistant"):
        note = notes[role]
        assert note.startswith(MESSAGE_PLACEHOLDER_PREFIX), note
        assert f"消息: {role}" in note, note
        assert EVICT_PLACEHOLDER_PREFIX not in note, note  # 不再冒名 tool result


def test_band_note_pointers_are_readable_only_where_they_are():
    """#49 评审 F2：留痕指针只承诺 trace 里真有的东西。

    trace 是 EventBus 的镜像（``backend/services/trace.py``），只有 ``tool_result`` 事件
    携带消息原文；user / assistant 轮次没有任何事件承载它，指针指过去就是第二处失实。
    """
    msgs = _mixed_band()
    out, _ = compress_messages(msgs, budget=1000, keep_recent=4, trace_ref=TRACE_REF)
    notes = _notes_by_role(out)

    # 工具结果：文件 + 锚点，grep 即定位（T1.4 契约）。
    assert f"留痕: {TRACE_REF}#c0" in notes["tool"], notes["tool"]
    # 非工具消息：不指向 trace 文件，也不留一个空洞的文件名。
    for role in ("user", "assistant"):
        assert "留痕: 无（trace 不落该条原文）" in notes[role], notes[role]
        assert TRACE_REF not in notes[role], notes[role]

    # trace 关掉时工具便签照旧写「未开启」，与带外逐出同一套措辞。
    off, _ = compress_messages(msgs, budget=1000, keep_recent=4, trace_ref="")
    assert "留痕: 无（trace 未开启）" in _notes_by_role(off)["tool"], off



def test_band_squeeze_is_idempotent_across_rounds():
    """卡 2：便签不套便签——已是 marker-only 的消息这一轮原样留着，只挤新的。

    两种前缀都在幂等判据里（``NOTE_PREFIXES``）：上一轮留下的工具便签与消息便签都不许
    被再包一层。
    """
    marker_only = {
        "role": "user",
        "content": (
            f"{MESSAGE_PLACEHOLDER_PREFIX} | 消息: user | 原文 4000 字符 | "
            "留痕: 无（trace 不落该条原文）]"
        ),
    }
    msgs = [_msg("user", "x" * 4000), marker_only] + [
        _msg("assistant", "x" * 4000) for _ in range(4)
    ]

    out, meta = compress_messages(msgs, budget=1000, keep_recent=5, trace_ref=TRACE_REF)

    # A′ 之后这组纯 user/assistant 的带压不进预算（预览不许归零），但它照样在变小。
    assert meta["context_tokens"] < estimate_tokens(msgs)
    # 带 = msgs[1:]，第一条就是上轮留下的 marker-only → 没 notch 可走，对象都不变。
    assert out[1] is marker_only
    assert _note_markers(out[1]["content"]) == 1
    for content in _notes(out):
        assert _note_markers(content) == 1  # 绝不打第二层便签
    # A′：最新一条是 assistant，最后 notch 不许碰它（预览是它最后一份信息）——连对象都是同一个。
    assert out[-1] is msgs[-1]


def test_band_squeeze_stops_when_nothing_left_to_squeeze():
    """卡 7：无可再挤就停手——不空转、不发新事件，``dropped`` 如实报。"""
    # 每条 12 字符：marker-only 便签（≈53 字符）比原文还长 → 挤了反而变大，无路可挤。
    msgs = [_msg("user" if i % 2 == 0 else "assistant", f"m{i:02d}" + "x" * 9) for i in range(30)]
    out, meta = compress_messages(msgs, budget=1, keep_recent=2)

    assert meta["compressed"] is True  # 截断本身照旧（旧语义不变）
    assert meta["band_evicted"] == 0  # 带内一步没走
    assert meta["dropped"] == 28
    assert len(out) == 3
    assert _notes(out) == []


def test_compression_stalls_when_the_rewrite_does_not_shrink_the_context():
    """票面症状的残留半边：无可再挤时不得每轮自称压缩过。

    短消息带内挤不动（便签比原文长），预算又够不着，于是截断只能丢掉上一轮的
    placeholder、再放一条新的：``context_tokens`` 恒定、压缩比恒为 1。这一轮没让
    上下文变小，就必须如实报「没压缩」并原样返回，否则 ``context_compressed`` 每
    轮一条、事件刷屏照旧。
    """
    msgs = [_msg("user" if i % 2 == 0 else "assistant", "y" * 50) for i in range(12)]

    first, meta1 = compress_messages(msgs, budget=10, keep_recent=10)
    assert meta1["compressed"] is True  # 首轮真丢了 2 条，确实变小
    assert meta1["dropped"] == 2
    assert meta1["context_tokens"] < estimate_tokens(msgs)

    seen = [meta1["context_tokens"]]
    current = first
    for _ in range(3):
        nxt, meta = compress_messages(current, budget=10, keep_recent=10)
        assert meta["compressed"] is False, meta  # 无改善 = 没压缩
        assert meta["dropped"] == 0 and meta["band_evicted"] == 0, meta
        assert nxt is current  # 原样返回：连回收占位都不做（身份约定同「预算内」）
        current = nxt
        seen.append(meta["context_tokens"])
    assert len(set(seen)) == 1, seen  # 恒定：正是票面的压缩比恒为 1，只是不再自称压缩


def test_count_trigger_still_truncates_without_a_token_gain():
    """收敛判据只管 token 触发：条数超限时截断照旧，哪怕占位比空消息还大。

    条数上限（``context_max_messages``）的职责是把消息数压回去，不是把 token 压小，
    不许被 #49 的停手判据挡住。
    """
    msgs = [_msg("user", "") for _ in range(6)]
    out, meta = compress_messages(msgs, budget=1_000_000, keep_recent=2, max_messages=4)

    assert meta["trigger"] == "count"
    assert meta["compressed"] is True  # 占位比 4 条空消息还大，也照样截
    assert meta["dropped"] == 4
    assert len(out) == 3


def test_no_context_compressed_event_once_the_squeeze_stalls(tmp_path: Path):
    """端到端：停手之后不再发事件——票面「事件刷屏」判据的真正落点。"""
    bus = EventBus()
    events: List[Dict[str, Any]] = []
    bus.subscribe("t49stall", events.append)
    rt = _runtime(tmp_path, bus, "t49stall", context_evict_enabled=False, context_token_budget=10)
    state = cast(
        AgentState,
        {
            "messages": [_msg("user" if i % 2 == 0 else "assistant", "y" * 50) for i in range(12)],
            "step_index": 2,
        },
    )

    for _ in range(3):
        rt._build_messages(state, "SYS")

    assert [e for e in events if e["type"] == "context_compressed"] == events[:1], events
    assert len(events) == 1  # 只有真正变小那一轮发了一条，此后停手


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
    # A′ 接线：收敛标志随同一个事件出，不开新事件类型。这组 fixture 是 user/assistant，
    # 预览不许归零，所以够不到预算——如实报 False。
    assert compressed[0]["data"]["converged"] is False
    assert compressed[0]["data"]["context_tokens"] < 12012
    assert state["context_tokens"] < 12012
    # 没有新事件类型混进来。
    assert {e["type"] for e in events} == {"context_compressed"}
