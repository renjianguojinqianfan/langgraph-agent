"""T1.4 tool-result eviction tests (spec: docs/specs/t1-4-tool-result-eviction.md §四).

Pure-function layer drives ``evict_tool_results`` directly; the runtime layer
reuses conftest's ``make_settings`` and the fake ``tm`` shape from
``test_context_injection`` so ``_build_messages`` is exercised against a real
``EventBus`` subscription.

The placeholder text (marker / tool name / original char count / trace pointer /
head-tail preview) is the public contract — asserted as literal substrings.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, cast

from backend.config import Settings
from backend.core.agent.context import (
    EVICT_PLACEHOLDER_PREFIX,
    evict_tool_results,
)
from backend.core.agent.nodes import AgentRuntime
from backend.core.agent.prompts import PLANNER_SYSTEM
from backend.core.agent.state import AgentState
from backend.services.event_bus import EventBus
from backend.tests.conftest import make_settings

TRACE_REF = "/tmp/traces/t1.jsonl"


# ── helpers ──────────────────────────────────────────────────────────────────
def _assistant_call(call_id: str, name: str) -> Dict[str, Any]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": "{}"}}
        ],
    }


def _tool(content: Any, call_id: str) -> Dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _filler(n: int) -> List[Dict[str, Any]]:
    return [{"role": "user", "content": f"filler {i}"} for i in range(n)]


def _evictable_history(n: int = 3, size: int = 12000) -> List[Dict[str, Any]]:
    """``n`` oversized tool results outside the default 10-message protect band."""
    msgs: List[Dict[str, Any]] = [{"role": "user", "content": "任务：把这三段日志读完"}]
    for i in range(n):
        cid = f"c{i}"
        msgs.append(_assistant_call(cid, "read"))
        msgs.append(_tool("x" * size, cid))
    msgs.extend(_filler(10))  # protect band absorbs the tail
    return msgs


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    base: Dict[str, Any] = dict(context_evict_enabled=True)
    base.update(overrides)
    return make_settings(tmp_path, **base)


def _fake_tm(settings: Settings, bus: EventBus) -> SimpleNamespace:
    return SimpleNamespace(
        settings=settings, event_bus=bus, add_artifact=lambda *a, **k: None
    )


def _runtime(settings: Settings, bus: EventBus) -> AgentRuntime:
    return AgentRuntime(
        task_id="t1",
        task_manager=_fake_tm(settings, bus),
        llm=SimpleNamespace(),
        tools=[],
        tool_schemas=[],
    )


def _state(messages: List[Dict[str, Any]], **kw: Any) -> AgentState:
    base: Dict[str, Any] = {"messages": messages, "step_index": 0}
    base.update(kw)
    # Partial/extra keys by design: these tests drive one node, not a whole run.
    return cast(AgentState, base)


# ── 4.1 pure function ───────────────────────────────────────────────────────
def test_below_threshold_identity() -> None:
    msgs = [
        _assistant_call("c1", "read"),
        _tool("x" * 3999, "c1"),  # 阈值是 >4000，3999 不逐
        {"role": "user", "content": "hi"},
    ]
    out, evictions = evict_tool_results(msgs, trace_ref=TRACE_REF)
    assert out is msgs
    assert evictions == []


def test_placeholder_shape() -> None:
    big = "H" * 800 + "m" * 10800 + "T" * 400  # 12000 chars, distinct head/tail
    msgs = [_assistant_call("c1", "read"), _tool(big, "c1"), {"role": "user", "content": "tail"}]
    out, evictions = evict_tool_results(msgs, protect_recent=1, trace_ref=TRACE_REF)

    assert out is not msgs
    placeholder = out[1]["content"]
    assert placeholder.startswith(EVICT_PLACEHOLDER_PREFIX)
    assert "工具: read" in placeholder
    assert "原文 12000 字符" in placeholder
    assert f"{TRACE_REF}#c1" in placeholder
    assert "H" * 800 in placeholder  # 头 800
    assert "T" * 400 in placeholder  # 尾 400
    assert out[1]["role"] == "tool"
    assert out[1]["tool_call_id"] == "c1"
    assert evictions == [{"tool_call_id": "c1", "tool_name": "read", "original_chars": 12000}]
    # 未逐出的消息引用不变
    assert out[0] is msgs[0]
    assert out[2] is msgs[2]


def test_protect_band_by_position() -> None:
    msgs = [
        _assistant_call("c1", "read"),
        _tool("x" * 5000, "c1"),  # 带外 → 逐
        _assistant_call("c2", "grep"),
        _tool("y" * 5000, "c2"),  # 带内（末尾 2 条）→ 不动
        {"role": "user", "content": "tail"},
    ]
    out, evictions = evict_tool_results(msgs, protect_recent=2)
    assert evictions == [{"tool_call_id": "c1", "tool_name": "read", "original_chars": 5000}]
    assert out[1]["content"].startswith(EVICT_PLACEHOLDER_PREFIX)
    assert out[3]["content"] == "y" * 5000
    assert out[3] is msgs[3]


def test_non_tool_roles_untouched() -> None:
    msgs: List[Dict[str, Any]] = [
        {"role": "user", "content": "u" * 5000},
        {"role": "assistant", "content": "a" * 5000},
        {"role": "system", "content": "s" * 5000},
    ]
    out, evictions = evict_tool_results(msgs, protect_recent=0)
    assert out is msgs
    assert evictions == []


def test_idempotent_on_existing_placeholder() -> None:
    already = EVICT_PLACEHOLDER_PREFIX + " | 工具: read | 原文 9000 字符 | 留痕: x#c1]\n" + "p" * 9000
    msgs = [_assistant_call("c1", "read"), _tool(already, "c1"), {"role": "user", "content": "t"}]
    out, evictions = evict_tool_results(msgs, protect_recent=1, trace_ref=TRACE_REF)
    assert out is msgs
    assert evictions == []


def test_multiple_evicted_in_one_round() -> None:
    msgs = [
        _assistant_call("c1", "read"),
        _tool("a" * 5000, "c1"),
        _assistant_call("c2", "glob"),
        _tool("b" * 6000, "c2"),
        _assistant_call("c3", "grep"),
        _tool("c" * 7000, "c3"),
        {"role": "user", "content": "tail"},
    ]
    out, evictions = evict_tool_results(msgs, protect_recent=1, trace_ref=TRACE_REF)
    assert [e["tool_call_id"] for e in evictions] == ["c1", "c2", "c3"]
    assert [e["original_chars"] for e in evictions] == [5000, 6000, 7000]
    for idx in (1, 3, 5):
        assert out[idx]["content"].startswith(EVICT_PLACEHOLDER_PREFIX)


def test_trace_ref_empty_degrades() -> None:
    msgs = [_assistant_call("c1", "read"), _tool("x" * 5000, "c1"), {"role": "user", "content": "t"}]
    out, _ = evict_tool_results(msgs, protect_recent=1, trace_ref="")
    placeholder = out[1]["content"]
    assert "留痕: 无（trace 未开启）" in placeholder
    assert "#c1" not in placeholder


def test_tool_name_lookup_and_fallback() -> None:
    msgs = [
        _assistant_call("c1", "glob"),
        _tool("x" * 5000, "c1"),  # 配对命中
        _tool("y" * 5000, "c9"),  # 无 assistant 配对 → ?
        {"role": "user", "content": "tail"},
    ]
    _, evictions = evict_tool_results(msgs, protect_recent=1)
    names = {e["tool_call_id"]: e["tool_name"] for e in evictions}
    assert names == {"c1": "glob", "c9": "?"}


def test_degenerate_preview_skips() -> None:
    msgs = [_assistant_call("c1", "read"), _tool("x" * 5000, "c1"), {"role": "user", "content": "t"}]
    # head + tail (5000) >= 原长 (5000)：无可省空间，跳过
    out, evictions = evict_tool_results(
        msgs, protect_recent=1, head_chars=3000, tail_chars=2000
    )
    assert out is msgs
    assert evictions == []


def test_multimodal_list_content_untouched() -> None:
    msgs: List[Dict[str, Any]] = [
        _assistant_call("c1", "read"),
        _tool([{"type": "text", "text": "x" * 5000}], "c1"),
        {"role": "user", "content": "tail"},
    ]
    out, evictions = evict_tool_results(msgs, protect_recent=1)
    assert out is msgs
    assert evictions == []


# ── 4.2 runtime (evict → compress wiring) ────────────────────────────────────
def test_evict_before_compress_money(tmp_path: Path) -> None:
    """先搬大件再摘要：逐出后低于预算 → 压缩（含 LLM 摘要）不触发。"""
    bus = EventBus()
    settings = _settings(tmp_path, context_token_budget=8000)
    state = _state(_evictable_history())
    _runtime(settings, bus)._build_messages(state, PLANNER_SYSTEM)

    assert state.get("compressed") is not True
    assert len(state["messages"]) == 17  # 无消息被删除，只换 content
    assert state["context_tokens"] < 8000
    assert (
        sum(
            1
            for m in state["messages"]
            if str(m.get("content", "")).startswith(EVICT_PLACEHOLDER_PREFIX)
        )
        == 3
    )
    assert all(
        not str(m.get("content", "")).startswith("[上下文已截断") for m in state["messages"]
    )

    # 对照组：同一历史关掉 eviction 后会触发压缩 —— 证明省下的是这一刀
    off = _settings(tmp_path, context_token_budget=8000, context_evict_enabled=False)
    state_off = _state(_evictable_history())
    _runtime(off, bus)._build_messages(state_off, PLANNER_SYSTEM)
    assert state_off["compressed"] is True


def test_disabled_byte_identical(tmp_path: Path) -> None:
    """零回归锚：context_evict_enabled=False 时输出与改造前逐字节相同。"""
    bus = EventBus()
    history = _evictable_history()  # ≈9000 est. tokens，显式传 32000 与默认值解耦
    settings = _settings(tmp_path, context_evict_enabled=False, context_token_budget=32000)
    state = _state(history)
    out = _runtime(settings, bus)._build_messages(state, PLANNER_SYSTEM)

    assert out == [{"role": "system", "content": PLANNER_SYSTEM}, *history]
    assert state.get("compressed") is not True

    # 开关确实有效：同一历史在开启时被改写
    on = _settings(tmp_path, context_evict_enabled=True, context_token_budget=32000)
    out_on = _runtime(on, bus)._build_messages(_state(_evictable_history()), PLANNER_SYSTEM)
    assert out_on != out


def test_evicted_event_published(tmp_path: Path) -> None:
    bus = EventBus()
    events: List[Dict[str, Any]] = []
    bus.subscribe("t1", events.append)
    settings = _settings(tmp_path)
    state = _state(_evictable_history(), step_index=5)
    _runtime(settings, bus)._build_messages(state, PLANNER_SYSTEM)

    evicted = [e for e in events if e["type"] == "tool_result_evicted"]
    assert len(evicted) == 3
    for e in evicted:
        assert set(e["data"]) >= {"tool_call_id", "tool_name", "original_chars", "step_index"}
        assert e["data"]["step_index"] == 5
        assert e["data"]["tool_name"] == "read"
        assert e["data"]["original_chars"] == 12000
    assert {e["data"]["tool_call_id"] for e in evicted} == {"c0", "c1", "c2"}

    # 留痕指针指向本任务 trace 文件的绝对路径 + tool_call_id
    pointer = f"{settings.trace_path / 't1.jsonl'}#c0"
    assert any(pointer in str(m.get("content", "")) for m in state["messages"])