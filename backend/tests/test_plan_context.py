"""#119 plan-context injection tests — ``state["plan"]`` reaches the executor.

Before #119 the only consumers of ``state["plan"]`` were ``risk_scan`` and the
``plan_update`` event: the executor LLM never saw the plan it was supposedly
carrying out. These tests pin the fix at the single seam that builds the LLM
payload (:meth:`AgentRuntime._build_messages`), driven through the real nodes so
the call site is covered too:

* **AC1** — the executor's system message carries the current plan rendering
  (first round, and again after a re-plan);
* **AC2** — replacement, not stacking: the section appears exactly once however
  many planner rounds ran, always rendering the *latest* plan, and nothing of it
  is pushed into the transcript;
* **AC3** — the section rides on the system message, so no ``context_*``
  configuration can evict or compress it away (same fakes as the compression
  suite);
* the planner side keeps its byte-identical system message — the fix is
  executor-only, which is also what lets the pre-existing injection tests
  (``test_context_injection``) keep asserting exact contents.

Section anchor ``# Current Plan`` is asserted as a literal string: like the
injection block's anchors, it is the block's public contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, cast

import pytest

from backend.config import Settings
from backend.core.agent.inject import ENV_SECTION, build_inject_block
from backend.core.agent.nodes import AgentRuntime
from backend.core.agent.prompts import (
    EXECUTOR_SYSTEM,
    PLAN_SECTION,
    PLANNER_SYSTEM,
    render_plan_section,
)
from backend.core.agent.state import AgentState
from backend.core.llm.client import LLMResponse
from backend.services.event_bus import EventBus
from backend.tests.conftest import make_settings


# ── helpers ──────────────────────────────────────────────────────────────────
def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    """Compression/injection knobs default OFF via conftest's env block; a test
    that wants them on passes them explicitly, never relying on the machine."""
    return make_settings(tmp_path, **overrides)


def _runtime(settings: Settings, llm: Any) -> AgentRuntime:
    return AgentRuntime(
        task_id="t119",
        task_manager=SimpleNamespace(
            settings=settings, event_bus=EventBus(), add_artifact=lambda *a, **k: None
        ),
        llm=llm,
        tools=[],
        # A non-empty schema list is what marks a call as the executor's, both
        # for the fake below and for MockLLMClient.
        tool_schemas=[{"type": "function", "function": {"name": "noop"}}],
    )


def _state(**kw: Any) -> AgentState:
    base: Dict[str, Any] = {
        "messages": [{"role": "user", "content": "do the thing"}],
        "steps": [],
        "step_index": 0,
    }
    base.update(kw)
    # Partial/extra keys by design: these tests drive one or two nodes, not a run.
    return cast(AgentState, base)


def _plan(*descriptions: str) -> List[Dict[str, Any]]:
    return [
        {"index": i, "description": desc, "status": "pending"}
        for i, desc in enumerate(descriptions, 1)
    ]


class _FakeLLM:
    """Offline stand-in that records every payload and answers by call kind.

    ``tools`` present → the executor's call, answered with a plain final answer
    (no tool calls, so no tool has to be mounted). No ``tools`` → a planner call
    taken from ``plans`` if the script has one left, else a summary string (the
    ``summarize`` compression strategy asks for exactly that).
    """

    def __init__(self, plans: Optional[List[List[str]]] = None) -> None:
        self._plans = plans or []
        self._planner_calls = 0
        self.payloads: List[Dict[str, Any]] = []

    def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        system = str(messages[0].get("content", "")) if messages else ""
        self.payloads.append({"system": system, "is_executor": bool(tools), "messages": messages})
        if tools:
            return LLMResponse(content="全部完成（mock）。")
        if self._planner_calls < len(self._plans):
            plan = self._plans[self._planner_calls]
            self._planner_calls += 1
            return LLMResponse(content=json.dumps(plan, ensure_ascii=False))
        return LLMResponse(content="（早期历史摘要）")

    def systems(self, *, executor: bool) -> List[str]:
        return [str(p["system"]) for p in self.payloads if p["is_executor"] is executor]

    @property
    def executor_systems(self) -> List[str]:
        return self.systems(executor=True)

    @property
    def transcript(self) -> List[Dict[str, Any]]:
        """Every non-system message that ever left through ``complete``."""
        return [
            m
            for p in self.payloads
            for m in p["messages"]
            if m.get("role") != "system"
        ]


def _big_history() -> List[Dict[str, Any]]:
    """A history that trips every compressor knob at once (evictable tool note,
    over-budget early turns, more turns than any ``context_max_messages``)."""
    return [
        {"role": "user", "content": "x" * 40000},
        {"role": "tool", "tool_call_id": "c1", "content": "t" * 9000},
        {"role": "assistant", "content": "y" * 1000},
        {"role": "user", "content": "z" * 1000},
        {"role": "assistant", "content": "w" * 1000},
    ]


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``Path.home()`` onto ``tmp_path`` (never read the real ``~``)."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


# ── renderer (pure) ─────────────────────────────────────────────────────────
def test_renderer_lists_index_and_status() -> None:
    """渲染面：PlanStep 列表逐条带 index 与 status（票面「带 index/status」）。"""
    plan = [
        {"index": 1, "description": "Read the file", "status": "done"},
        {"index": 2, "description": "Delete the cache", "status": "skipped"},
    ]
    rendered = render_plan_section(plan)
    assert rendered == f"\n\n{PLAN_SECTION}\n1. [done] Read the file\n2. [skipped] Delete the cache"


def test_renderer_empty_plan_is_empty_string() -> None:
    """没有计划（首轮之前 / planner 失败置空）→ 返回 ``""``，system 逐字不变。"""
    assert render_plan_section([]) == ""


def test_renderer_tolerates_bare_string_steps() -> None:
    """非 dict 条目（``_mark_high_steps_skipped`` / ``risk.scan_keywords`` 同样容忍的
    旧形态）降级成一条描述，而不是把 executor 的整次调用炸掉。"""
    assert render_plan_section(["Research X", {"index": 9, "description": "Act"}]) == (
        f"\n\n{PLAN_SECTION}\n1. [pending] Research X\n9. [pending] Act"
    )


# ── AC1: the executor sees the current plan ─────────────────────────────────
def test_executor_sees_plan_first_round(tmp_path: Path) -> None:
    """AC1（首轮）：planner 产出计划后，executor 那次调用的 system 含其渲染。"""
    llm = _FakeLLM(plans=[["Read the file", "Write the report"]])
    rt = _runtime(_settings(tmp_path), llm)
    state = _state()

    rt.planner(state)
    rt.executor(state)

    system = llm.executor_systems[-1]
    assert system.startswith(EXECUTOR_SYSTEM)
    assert PLAN_SECTION in system
    assert "1. [pending] Read the file" in system
    assert "2. [pending] Write the report" in system


def test_executor_sees_updated_plan(tmp_path: Path) -> None:
    """AC1（更新后）+ 替换的一半：重新规划后渲染换成新计划，旧计划整段消失。"""
    llm = _FakeLLM(plans=[["Old step"], ["New first", "New second"]])
    rt = _runtime(_settings(tmp_path), llm)
    state = _state()

    rt.planner(state)
    rt.executor(state)
    rt.planner(state)
    rt.executor(state)

    first, second = llm.executor_systems
    assert "Old step" in first
    assert "1. [pending] New first" in second and "2. [pending] New second" in second
    assert "Old step" not in second


def test_executor_status_follows_the_plan(tmp_path: Path) -> None:
    """AC1 的另一半：计划被就地改写（如 risk pause 把高危步置 skipped）后，
    下一次 executor 看到的是改写后的状态，不是首轮快照。"""
    llm = _FakeLLM(plans=[["Inspect", "Drop the table"]])
    rt = _runtime(_settings(tmp_path), llm)
    state = _state()

    rt.planner(state)
    state["plan"][1]["status"] = "skipped"
    rt.executor(state)

    system = llm.executor_systems[-1]
    assert "2. [skipped] Drop the table" in system
    assert "2. [pending] Drop the table" not in system


def test_planner_system_carries_no_plan(tmp_path: Path) -> None:
    """注入点在 executor 侧：planner 的 system 不带计划段——因此既有逐字断言
    （``test_context_injection`` 对 planner 的 byte-identical 检查）不破。"""
    llm = _FakeLLM(plans=[["Next round plan"]])
    rt = _runtime(_settings(tmp_path), llm)

    rt.planner(_state(plan=_plan("Alpha", "Bravo")))

    # 注入关闭（conftest 环境块）时 planner 的 system 逐字仍是 PLANNER_SYSTEM
    assert llm.systems(executor=False) == [PLANNER_SYSTEM]


# ── AC2: replacement, never stacking ────────────────────────────────────────
def test_plan_section_appears_once_across_rounds(tmp_path: Path) -> None:
    """AC2：连续多轮 planner 之后，system 里「当前计划」段只有一份，且是最新那份。"""
    llm = _FakeLLM(plans=[["Alpha"], ["Bravo"], ["Charlie"]])
    rt = _runtime(_settings(tmp_path), llm)
    state = _state()

    for _ in range(3):
        rt.planner(state)
        rt.executor(state)

    systems = llm.executor_systems
    assert len(systems) == 3
    for system in systems:
        assert system.count(PLAN_SECTION) == 1
    assert systems[-1].count("Charlie") == 1
    assert "Alpha" not in systems[-1] and "Bravo" not in systems[-1]
    # 堆叠在结构上无处发生：该段只在 system 尾部，从不进 transcript。
    assert llm.transcript, "the executor payload should carry history"
    assert all(PLAN_SECTION not in str(m.get("content", "")) for m in llm.transcript)


# ── AC3: no compression / eviction configuration drops it ───────────────────
@pytest.mark.parametrize(
    "overrides",
    [
        {"context_token_budget": 1, "context_keep_recent": 2},
        {"context_token_budget": 1, "context_keep_recent": 20},
        {"context_token_budget": 1, "context_max_messages": 1},
        {"context_token_budget": 100, "context_keep_recent": 2, "context_compress_strategy": "summarize"},
        {"context_token_budget": 1, "context_evict_enabled": True, "context_keep_recent": 2},
        {"context_token_budget": 1, "context_keep_recent": 2, "context_compress_strategy": "truncate"},
    ],
    ids=[
        "tiny-budget",
        "band-covers-everything",
        "count-trigger",
        "summarize",
        "evict-on",
        "truncate-explicit",
    ],
)
def test_plan_section_survives_compression(tmp_path: Path, overrides: Dict[str, Any]) -> None:
    """AC3：``context_*`` 任取配置值，「当前计划」段都不被淘汰（同款 fake、真压缩）。"""
    settings = _settings(tmp_path, **overrides)
    llm = _FakeLLM()
    rt = _runtime(settings, llm)
    state = _state(messages=_big_history(), plan=_plan("Write the report"))

    rt.executor(state)

    system = llm.executor_systems[-1]
    assert PLAN_SECTION in system
    assert "1. [pending] Write the report" in system
    # 非空洞断言：这条路径上压缩/回收确实动过历史
    assert state.get("compressed") or state.get("messages") != _big_history()
    assert all(PLAN_SECTION not in str(m.get("content", "")) for m in state["messages"])


def test_plan_section_sits_after_inject_block(tmp_path: Path, tmp_home: Path) -> None:
    """位置钉住：计划段固定在注入块之后、system 尾部（票面「置于注入块之后」），
    两段同时在场时先后次序可判。"""
    settings = _settings(tmp_path, context_inject_enabled=True)
    sandbox = Path(settings.artifacts_path)
    sandbox.mkdir(parents=True, exist_ok=True)
    (sandbox / "AGENTS.md").write_text("SANDBOX-RULES", encoding="utf-8")
    llm = _FakeLLM()
    rt = _runtime(settings, llm)

    rt.executor(_state(plan=_plan("Alpha")))

    system = llm.executor_systems[-1]
    assert system == (
        EXECUTOR_SYSTEM
        + build_inject_block(settings, home_dir=tmp_home)
        + render_plan_section(_plan("Alpha"))
    )
    assert system.index(ENV_SECTION) < system.index(PLAN_SECTION)


def test_plan_section_survives_disabled_injection(
    tmp_path: Path, tmp_home: Path
) -> None:
    """README 那句改写措辞的回归锚：计划块**不受** ``context_inject_enabled`` 管辖。

    「关掉 AGENTS.md 注入」和「关掉计划的供给」不是一件事，本票把它写成了文档里的新真相，
    所以钉成机械断言而不是留在散文里。**显式**关注入、**显式**给非空 plan——不依赖 conftest
    那条进程级 ``CONTEXT_INJECT_ENABLED=false``，那个默认值哪天翻转这条断言也不失守。
    """
    settings = _settings(tmp_path, context_inject_enabled=False)
    sandbox = Path(settings.artifacts_path)
    sandbox.mkdir(parents=True, exist_ok=True)
    (sandbox / "AGENTS.md").write_text("SANDBOX-RULES", encoding="utf-8")
    home_agents = tmp_home / ".agents"
    home_agents.mkdir(parents=True, exist_ok=True)
    (home_agents / "AGENTS.md").write_text("HOME-RULES", encoding="utf-8")
    llm = _FakeLLM()
    rt = _runtime(settings, llm)

    rt.executor(_state(plan=_plan("Alpha")))

    system = llm.executor_systems[-1]
    # 注入块确实被这个开关关掉了——两层文件此刻都在盘上有内容，所以「不在」不是空断言。
    assert "SANDBOX-RULES" not in system and "HOME-RULES" not in system
    assert ENV_SECTION not in system
    # 计划块照在场：开关只管上面三段，管不到它。
    assert PLAN_SECTION in system
    assert "1. [pending] Alpha" in system
    assert system == EXECUTOR_SYSTEM + render_plan_section(_plan("Alpha"))


def test_executor_with_empty_plan_is_unchanged(tmp_path: Path) -> None:
    """零回归面：plan 为空时 executor 的 system 与 #119 之前逐字相同。"""
    settings = _settings(tmp_path, context_token_budget=1, context_keep_recent=2)
    llm = _FakeLLM()
    rt = _runtime(settings, llm)

    rt.executor(_state(messages=[{"role": "user", "content": "hi"}], plan=[]))

    assert llm.executor_systems[-1] == EXECUTOR_SYSTEM
