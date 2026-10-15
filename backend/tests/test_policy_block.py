"""S2 #136 —— 危险动作拦截：设置里的规则表 + 一个钩子，命中即拒。

ADR 0008 第 6 条：危险动作在执行前被**设置里的规则表**当场拒掉，命中即不执行，
把 ``{block, reason}`` 当这次调用的结果回给模型（模型自己换路）。**不弹框、不进
图重入、不改参数、不 terminate**——这四条是与 pi 的 ``tool_call`` 钩子的有意分歧
（pi 可原地改参数且改完不复核；见 ``docs/research-pi-agent-construction.md`` §5）。

本片是**纯增量**：确认闸门仍由 S4 删除，这里只钉住新钩子的三条判据——
命中即拒 / 不命中照旧 / 规则表为空零回归。判定单位是**一次调用**（工具名 + 入参），
不是计划里的一句话（那是 `risk.py` 的规划期扫描，两者不同源）。
"""

from __future__ import annotations

import json
import threading
from typing import Any, Dict, Optional

from backend.api.schemas import StepRecord, ToolCallRecord
from backend.config import Settings
from backend.core.agent.nodes import AgentRuntime
from backend.core.agent.policy import DangerPolicy
from backend.core.agent.state import AgentState
from backend.core.llm.client import MockLLMClient
from backend.core.tools.base import BaseTool, ToolResult
from backend.services.event_bus import EventBus

TASK_ID = "t_policy_block"


class _Probe(BaseTool):
    """一个会记账的探针：``calls`` 非空就等于「这一次真的执行了」。"""

    name = "probe"
    description = "counting probe (test only)"
    args_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {"cmd": {"type": "string"}},
    }

    def __init__(self, settings: Settings | None = None, *, requires_confirm: bool = False):
        super().__init__(settings)
        self.requires_confirm = requires_confirm
        self.calls: list[Dict[str, Any]] = []

    def run(self, **kwargs: Any) -> ToolResult:
        self.calls.append(dict(kwargs))
        return ToolResult(success=True, data={"ran": True})


class _NoGateTM:
    """TaskManager 的最小替身：**闸门一被碰到就当场失败**。

    本片的核心断言之一就是「命中即拒**不弹框**」，所以替身把 ``request_confirm`` /
    ``consume_confirm`` 写成抛错——被调用即测试失败，比事后数事件更硬。
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.event_bus = EventBus()

    def request_confirm(self, task_id: str, tool_call_id: str) -> threading.Event:
        raise AssertionError("a policy block must not ask a human")

    def consume_confirm(self, task_id: str, tool_call_id: str) -> Optional[bool]:
        raise AssertionError("a policy block must not read a human verdict")

    def is_stop_flagged(self, task_id: str) -> bool:
        return False

    def add_artifact(self, *args: Any, **kwargs: Any) -> None:
        return None


def _state() -> AgentState:
    return {
        "task_id": TASK_ID,
        "step_index": 1,
        "steps": [{"index": 1, "thought": "", "tool_calls": [], "status": "running"}],
        "messages": [{"role": "user", "content": "hi"}],
        "plan": [], "artifacts": [], "status": "RUNNING", "stop_requested": False,
        "pending_confirm": {}, "final_answer": "", "error": "",
        "_last_action": "", "_current_tool_calls": [], "_confirmed_ids": [],
        "_rejected_ids": [], "_needs_confirm": False, "risk_report": [],
        "_risk_blocked": False, "subtasks": [], "_is_subtask": False,
    }


def _run(
    settings: Settings,
    *,
    requires_confirm: bool = False,
    confirm_enabled: bool = True,
    args: Optional[Dict[str, Any]] = None,
) -> tuple[AgentRuntime, AgentState, _Probe, _NoGateTM]:
    """One executor -> tool pass with a scripted single probe call."""
    tool = _Probe(settings, requires_confirm=requires_confirm)
    tm = _NoGateTM(settings)
    rt = AgentRuntime(
        TASK_ID,
        tm,
        llm=MockLLMClient(tool_calls=[{"id": "c1", "name": tool.name, "arguments": args or {}}]),
        tools=[tool],
        tool_schemas=[tool.to_openai_schema()],
        confirm_enabled=confirm_enabled,
    )
    state = _state()
    rt.executor(state)
    rt.tool_node(state)
    return rt, state, tool, tm


def _rec(state: AgentState) -> Dict[str, Any]:
    return state["_current_tool_calls"][0]


def _events(tm: _NoGateTM) -> list[Dict[str, Any]]:
    return tm.event_bus.replay(TASK_ID)


def _last_tool_message(state: AgentState, call_id: str = "c1") -> Dict[str, Any]:
    for msg in reversed(state.get("messages") or []):
        if msg.get("role") == "tool" and msg.get("tool_call_id") == call_id:
            return msg
    raise AssertionError("no tool message was fed back to the model")


# ── 命中即拒 ─────────────────────────────────────────────────────────────────
def test_hit_denies_the_call_without_executing_it(settings):
    """命中 → 不执行 + ``{block, reason}`` 回模型。"""
    settings = settings.model_copy(
        update={"policy_rules": '[{"tool": "probe", "reason": "probe is not allowed here"}]'}
    )
    _rt, state, tool, tm = _run(settings)

    assert tool.calls == [], "被规则拦下的调用还是执行了"
    rec = _rec(state)
    assert rec["blocked"] is True
    assert rec["status"] == "skipped"
    assert rec["error"] == "probe is not allowed here"

    content = json.loads(_last_tool_message(state)["content"])
    assert content["block"] is True
    assert content["reason"] == "probe is not allowed here"

    # 事件面：这次调用照样在时间线上（tool_call / tool_result 记录体），
    # 但**没有任何**人工确认事件。
    types = [e["type"] for e in _events(tm)]
    assert types == ["tool_call", "tool_result"]
    result = [e["data"] for e in _events(tm) if e["type"] == "tool_result"][0]
    assert result["blocked"] is True
    assert result["status"] == "skipped"


def test_block_wins_over_the_confirm_gate(settings):
    """命中即拒**先于**闸门：既不置 ``need_confirm``，也不进 ``human_confirm``。

    ``_NoGateTM`` 谁碰闸门谁抛错，所以这条同时钉住「不弹框」和「不进图重入」。
    """
    settings = settings.model_copy(
        update={"policy_rules": '[{"tool": "probe", "reason": "no"}]'}
    )
    _rt, state, tool, _tm = _run(settings, requires_confirm=True, confirm_enabled=True)

    assert tool.calls == []
    assert _rec(state)["need_confirm"] is False
    assert state["_needs_confirm"] is False
    assert state["_rejected_ids"] == []


def test_block_holds_without_a_gate_on_this_runtime(settings):
    """子任务那侧没有闸门（``confirm_enabled=False``），规则照样拦——拦截不是闸门。"""
    settings = settings.model_copy(
        update={"policy_rules": '[{"tool": "probe", "reason": "no"}]'}
    )
    _rt, state, tool, _tm = _run(settings, confirm_enabled=False)

    assert tool.calls == []
    assert _rec(state)["blocked"] is True


def test_a_command_pattern_is_matched_against_the_arguments(settings):
    """「命令模式」= 对入参 JSON 文本做正则；同一个工具的另一次调用不受累。"""
    settings = settings.model_copy(
        update={
            "policy_rules": (
                '[{"tool": "probe", "pattern": "rm\\\\s+-rf", "reason": "destructive shell"}]'
            )
        }
    )
    hit_state = _run(settings, args={"cmd": "sudo rm -rf /"})[1]
    miss_state, miss_tool = _run(settings, args={"cmd": "ls -la"})[1:3]

    assert _rec(hit_state)["blocked"] is True
    assert miss_tool.calls == [{"cmd": "ls -la"}], "不命中的调用被连坐拦了"
    assert "blocked" not in _rec(miss_state)


def test_block_does_not_mutate_the_arguments(settings):
    """钩子不改参数：记录体里的入参与模型给的一模一样。"""
    args = {"cmd": "rm -rf /"}
    settings = settings.model_copy(
        update={"policy_rules": '[{"tool": "probe", "reason": "no"}]'}
    )
    _rt, state, _tool, _tm = _run(settings, args=args)

    assert _rec(state)["input"] == {"cmd": "rm -rf /"}
    assert args == {"cmd": "rm -rf /"}


def test_block_does_not_terminate_the_run(settings):
    """不 terminate：这一轮照常收口，球回给 planner（终止由人按停）。"""
    settings = settings.model_copy(
        update={"policy_rules": '[{"tool": "probe", "reason": "no"}]'}
    )
    _rt, state, _tool, _tm = _run(settings)

    assert state["_last_action"] == "tool_done"
    assert state.get("stop_requested") is not True
    assert state["error"] == ""


# ── 不命中照旧 / 空表零回归 ──────────────────────────────────────────────────
def test_no_rules_is_byte_identical_to_before(settings):
    """规则表为空 → 与改造前逐字节一致：记录体里连 ``blocked`` 这个键都不出现。"""
    _rt, state, tool, tm = _run(settings, args={"cmd": "ls"})

    assert tool.calls == [{"cmd": "ls"}]
    rec = _rec(state)
    assert "blocked" not in rec, "未配置时记录体多出了一个键"
    assert rec["status"] == "success"
    assert rec["error"] == ""
    assert rec["need_confirm"] is False
    assert [e["type"] for e in _events(tm)] == ["tool_call", "tool_result"]
    assert json.loads(_last_tool_message(state)["content"]) == {"ran": True}


def test_a_rule_for_another_tool_does_not_match(settings):
    """规则表非空但这条不匹配 → 照旧执行（判定是按调用算的，不是一票否决）。"""
    settings = settings.model_copy(
        update={"policy_rules": '[{"tool": "other_tool", "reason": "no"}]'}
    )
    _rt, state, tool, _tm = _run(settings, args={"cmd": "ls"})

    assert tool.calls == [{"cmd": "ls"}]
    assert "blocked" not in _rec(state)


# ── 规则表本身：解析与匹配 ──────────────────────────────────────────────────
class _RuleSettings:
    """`DangerPolicy.from_settings` 只读这一个属性，替身比造整个 Settings 清楚。"""

    def __init__(self, rules: list[Dict[str, Any]]):
        self.policy_rules_list = rules


def _policy(rules: list[Dict[str, Any]]) -> DangerPolicy:
    return DangerPolicy.from_settings(_RuleSettings(rules))


def test_rows_without_a_tool_are_not_rules():
    assert _policy([]).rules == ()
    assert _policy([{"reason": "no tool"}]).rules == ()
    assert _policy([{"tool": "  "}]).rules == ()


def test_a_rule_without_a_reason_still_says_something():
    policy = _policy([{"tool": "probe"}])
    reason = policy.check("probe", {})

    assert reason, "命中却没有理由可回给模型"
    assert "probe" in reason


def test_exact_name_and_glob_and_pattern():
    policy = _policy(
        [
            {"tool": "probe", "reason": "exact"},
            {"tool": "mcp__*__write", "reason": "mcp writer"},
            {"tool": "code_exec", "pattern": "DROP TABLE", "reason": "ddl"},
        ]
    )

    assert policy.check("probe", {}) == "exact"
    assert policy.check("mcp__fs__write", {}) == "mcp writer"
    assert policy.check("mcp__fs__read", {}) is None
    # 命令模式不区分大小写
    assert policy.check("code_exec", {"cmd": "drop table users"}) == "ddl"
    assert policy.check("code_exec", {"cmd": "select 1"}) is None
    assert policy.check("untouched", {"cmd": "drop table users"}) is None


def test_a_broken_pattern_is_dropped_not_crashed():
    """正则写错不该把运行炸掉；该条规则作废（与 ``risk_danger_keywords`` 同口径）。"""
    policy = _policy([{"tool": "probe", "pattern": "(", "reason": "no"}])

    assert policy.rules == ()
    assert policy.check("probe", {}) is None


def test_the_first_matching_rule_wins():
    policy = _policy(
        [{"tool": "probe", "reason": "first"}, {"tool": "probe", "reason": "second"}]
    )

    assert policy.check("probe", {}) == "first"


def test_check_never_mutates_the_arguments():
    args = {"cmd": "rm -rf /"}
    _policy([{"tool": "probe", "pattern": "rm", "reason": "no"}]).check("probe", args)

    assert args == {"cmd": "rm -rf /"}


def test_settings_expose_the_rule_table_as_a_list(tmp_path):
    """配置层：JSON 字符串 -> 规则 dict 列表；坏 JSON / 非数组 -> 空表（不炸）。"""
    from backend.tests.conftest import make_settings

    s = make_settings(tmp_path, policy_rules='[{"tool": "probe", "reason": "no"}]')
    assert s.policy_rules_list == [{"tool": "probe", "reason": "no"}]
    for raw in ("", "nope", '{"tool": "probe"}', '["probe"]', "null"):
        assert Settings(policy_rules=raw).policy_rules_list == [], f"{raw!r} 不该产出规则"
    # 非 dict 的条目丢掉，dict 的留下（判定单位是「一条规则」）
    assert Settings(policy_rules='[1, {"tool": "a"}]').policy_rules_list == [{"tool": "a"}]


# ── 持久化面 ────────────────────────────────────────────────────────────────
def test_blocks_survive_persistence(settings):
    """记录体能回看：否则 API 与重启后只剩一句散文，分不出「被拦」还是别的跳过。"""
    settings = settings.model_copy(
        update={"policy_rules": '[{"tool": "probe", "reason": "no"}]'}
    )
    _rt, state, _tool, _tm = _run(settings)

    record = StepRecord(**state["steps"][0])
    assert record.tool_calls[0].blocked is True
    assert record.tool_calls[0].status == "skipped"
    # 默认 False：没被拦的调用不该被凭空安上「被拦」。
    assert ToolCallRecord(id="x", tool_name="y").blocked is False
