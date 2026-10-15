"""S2 #136 —— 危险动作拦截：设置里的规则表 + 一个钩子，命中即拒。

ADR 0008 第 6 条：危险动作在执行前被**设置里的规则表**当场拒掉，命中即不执行，
把 ``{block, reason}`` 当这次调用的结果回给模型（模型自己换路）。**不弹框、不进
图重入、不改参数、不 terminate**——这四条是与 pi 的 ``tool_call`` 钩子的有意分歧
（pi 可原地改参数且改完不复核、且 ``block`` 当场产出错误结果；见
``docs/research-pi-agent-construction.md`` §5）。

本片是**纯增量**：确认闸门仍由 S4 删除，这里只钉住新钩子的三条判据——
命中即拒 / 不命中照旧 / 规则表为空零回归。判定单位是**一次调用**（工具名 + 入参），
不是计划里的一句话（那是 `risk.py` 的规划期扫描，两者不同源）。

零回归口径：记录体、事件载荷、对话面消息**都不多一个键**——被拒的调用就落在「没执行」
那两格里（``status:"skipped"`` + 理由在 ``error``），没有新字段；它从没进过闸门，
所以 ``confirm_outcome`` 是空串，这一点也是它与人替拒绝的区别。
"""

from __future__ import annotations

import json
import threading
from typing import Any, Dict, Optional

from backend.api.schemas import StepRecord, ToolCallRecord
from backend.config import Settings
from backend.core.agent.graph import _after_executor
from backend.core.agent.nodes import AgentRuntime
from backend.core.agent.policy import DangerPolicy
from backend.core.agent.state import AgentState
from backend.core.llm.client import MockLLMClient
from backend.core.tools.base import BaseTool, ToolResult
from backend.services.event_bus import EventBus
from backend.tests.conftest import make_settings

TASK_ID = "t_policy_block"

#: 一次调用在 ``executor`` 出口的键集合 —— 空表时一个键都不许增减（#136 AC1）。
#: （``circuit_open`` / ``retries`` 是 tool_node 真派发之后才写上的 P0 标记，不在这一层。）
PRE_S2_RECORD_KEYS = frozenset(
    {"id", "tool_name", "input", "output", "status", "error", "need_confirm", "confirmed"}
)


def _settings(tmp_path, **rules_kw: Any) -> Settings:
    """一份隔离的 Settings；规则表按 JSON 字符串写（生产路径就是这条）。"""
    return make_settings(tmp_path, **rules_kw)


def _rules(*entries: Dict[str, Any]) -> str:
    return json.dumps(list(entries), ensure_ascii=False)


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


def _executor(
    settings: Settings,
    *,
    requires_confirm: bool = False,
    confirm_enabled: bool = True,
    args: Optional[Dict[str, Any]] = None,
) -> tuple[AgentRuntime, AgentState, _Probe, _NoGateTM]:
    """Just the executor pass: one scripted probe call, decisions made, nothing dispatched."""
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
    return rt, state, tool, tm


def _run(
    settings: Settings,
    **kwargs: Any,
) -> tuple[AgentRuntime, AgentState, _Probe, _NoGateTM, EventBus]:
    """One full executor -> tool pass with a scripted single probe call."""
    rt, state, tool, tm = _executor(settings, **kwargs)
    rt.tool_node(state)
    return rt, state, tool, tm, tm.event_bus


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
def test_hit_denies_the_call_without_executing_it(tmp_path):
    """命中 → 不执行 + ``{block, reason}`` 回模型。"""
    reason = "probe is not allowed here"
    settings = _settings(tmp_path, policy_rules=_rules({"tool": "probe", "reason": reason}))
    _rt, state, tool, tm, _bus = _run(settings)

    assert tool.calls == [], "被规则拦下的调用还是执行了"
    rec = _rec(state)
    assert rec["status"] == "skipped"
    assert rec["error"] == reason
    assert set(rec) == PRE_S2_RECORD_KEYS, "记录体多了一个改造前没有的键"

    content = json.loads(_last_tool_message(state)["content"])
    assert content["block"] is True
    assert content["reason"] == reason

    # 事件面：这次调用照样在时间线上，但**没有任何**人工确认事件；
    # 结果体就是这条记录（状态 / 理由与状态面同一份）。
    types = [e["type"] for e in _events(tm)]
    assert types == ["tool_call", "tool_result"]
    result = [e["data"] for e in _events(tm) if e["type"] == "tool_result"][0]
    assert result["status"] == "skipped"
    assert result["error"] == reason


def test_block_wins_over_the_confirm_gate(tmp_path):
    """命中即拒**先于**闸门：不置 ``need_confirm``，路由也不指向 ``human_confirm``。

    ``_NoGateTM`` 谁碰闸门谁抛错，所以这条同时钉住「不弹框」与「不进图重入」。
    """
    settings = _settings(tmp_path, policy_rules=_rules({"tool": "probe", "reason": "no"}))
    _rt, state, tool, _tm, _bus = _run(settings, requires_confirm=True, confirm_enabled=True)

    assert tool.calls == []
    assert _rec(state)["need_confirm"] is False
    assert state["_needs_confirm"] is False
    assert state["_rejected_ids"] == []
    assert _after_executor(state) == "tool", "路由仍然指向确认闸门"


def test_block_holds_without_a_gate_on_this_runtime(tmp_path):
    """子任务那侧没有闸门（``confirm_enabled=False``），规则照样拦——拦截不是闸门。"""
    settings = _settings(tmp_path, policy_rules=_rules({"tool": "probe", "reason": "no"}))
    _rt, state, tool, _tm, _bus = _run(settings, confirm_enabled=False)

    assert tool.calls == []
    assert _rec(state)["status"] == "skipped"


def test_a_command_pattern_is_matched_against_the_arguments(tmp_path):
    """「命令模式」= 对入参 JSON 文本做正则；同一个工具的另一次调用不受累。"""
    settings = _settings(
        tmp_path,
        policy_rules=_rules(
            {"tool": "probe", "pattern": r"rm\s+-rf", "reason": "destructive shell"}
        ),
    )
    _hit_rt, hit_state, hit_tool, _tm, _bus = _run(settings, args={"cmd": "sudo rm -rf /"})
    _miss_rt, miss_state, miss_tool, _tm2, _bus2 = _run(settings, args={"cmd": "ls -la"})

    assert hit_tool.calls == []
    assert _rec(hit_state)["status"] == "skipped"
    assert miss_tool.calls == [{"cmd": "ls -la"}], "不命中的调用被连坐拦了"
    assert _rec(miss_state)["status"] == "success"


def test_block_does_not_mutate_the_arguments(tmp_path):
    """钩子不改参数：记录体里的入参与模型给的一模一样。"""
    args = {"cmd": "rm -rf /"}
    settings = _settings(tmp_path, policy_rules=_rules({"tool": "probe", "reason": "no"}))
    _rt, state, _tool, _tm, _bus = _run(settings, args=args)

    assert _rec(state)["input"] == {"cmd": "rm -rf /"}
    assert args == {"cmd": "rm -rf /"}


def test_block_does_not_terminate_the_run(tmp_path):
    """不 terminate：这一轮照常收口，球回给 planner（终止由人按停）。"""
    settings = _settings(tmp_path, policy_rules=_rules({"tool": "probe", "reason": "no"}))
    _rt, state, _tool, _tm, _bus = _run(settings)

    assert state["_last_action"] == "tool_done"
    assert state.get("stop_requested") is not True
    assert state["error"] == ""


# ── 不命中照旧 / 空表零回归 ──────────────────────────────────────────────────
def test_no_rules_is_byte_identical_to_before(tmp_path):
    """规则表为空 → 与改造前逐字节一致：executor 出口的记录体连键集合都不变。"""
    settings = _settings(tmp_path)
    _rt, state, tool, tm = _executor(settings, args={"cmd": "ls"})

    # executor 出口：与改造前逐字节同形（还没派发，所以 status 仍是 pending）。
    rec = _rec(state)
    assert set(rec) == PRE_S2_RECORD_KEYS, "未配置时记录体的键集合变了"
    assert rec["status"] == "pending"
    assert rec["error"] == ""
    assert rec["need_confirm"] is False

    _rt.tool_node(state)
    assert tool.calls == [{"cmd": "ls"}]
    assert _rec(state)["status"] == "success"
    # 一次真派发之后仍只有这两个额外的 P0 标记（tool_node 写的）。
    assert set(_rec(state)) == PRE_S2_RECORD_KEYS | {"circuit_open", "retries"}
    assert [e["type"] for e in _events(tm)] == ["tool_call", "tool_result"]
    assert json.loads(_last_tool_message(state)["content"]) == {"ran": True}


def test_a_rule_for_another_tool_does_not_match(tmp_path):
    """规则表非空但这条不匹配 → 照旧执行（判定按调用算，不是一票否决）。"""
    settings = _settings(tmp_path, policy_rules=_rules({"tool": "other_tool", "reason": "no"}))
    _rt, state, tool, _tm, _bus = _run(settings, args={"cmd": "ls"})

    assert tool.calls == [{"cmd": "ls"}]
    assert _rec(state)["status"] == "success"


# ── 规则表本身：解析与匹配 ──────────────────────────────────────────────────
def _policy(*rules: Dict[str, Any]) -> DangerPolicy:
    return DangerPolicy.from_settings(Settings(policy_rules=json.dumps(list(rules))))


def test_rows_without_a_tool_are_not_rules():
    assert _policy().rules == ()
    assert _policy({"reason": "no tool"}).rules == ()
    assert _policy({"tool": "  "}).rules == ()


def test_a_rule_without_a_reason_still_says_something():
    reason = _policy({"tool": "probe"}).check("probe", {})

    assert reason, "命中却没有理由可回给模型"
    assert "probe" in reason


def test_exact_name_and_glob_and_pattern():
    policy = _policy(
        {"tool": "probe", "reason": "exact"},
        {"tool": "mcp__*__write", "reason": "mcp writer"},
        {"tool": "code_exec", "pattern": "DROP TABLE", "reason": "ddl"},
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
    policy = _policy({"tool": "probe", "pattern": "(", "reason": "no"})

    assert policy.rules == ()
    assert policy.check("probe", {}) is None


def test_the_first_matching_rule_wins():
    policy = _policy({"tool": "probe", "reason": "first"}, {"tool": "probe", "reason": "second"})

    assert policy.check("probe", {}) == "first"


def test_check_never_mutates_the_arguments():
    args = {"cmd": "rm -rf /"}
    _policy({"tool": "probe", "pattern": "rm", "reason": "no"}).check("probe", args)

    assert args == {"cmd": "rm -rf /"}


def test_settings_expose_the_rule_table_as_a_list(tmp_path):
    """配置层：JSON 字符串 -> 规则 dict 列表；坏 JSON / 非数组 -> 空表（不炸）。"""
    s = _settings(tmp_path, policy_rules=_rules({"tool": "probe", "reason": "no"}))
    assert s.policy_rules_list == [{"tool": "probe", "reason": "no"}]
    for raw in ("", "nope", '{"tool": "probe"}', '["probe"]', "null"):
        assert Settings(policy_rules=raw).policy_rules_list == [], f"{raw!r} 不该产出规则"
    # 非 dict 的条目丢掉，dict 的留下（判定单位是「一条规则」）
    assert Settings(policy_rules='[1, {"tool": "a"}]').policy_rules_list == [{"tool": "a"}]


# ── 持久化面 ────────────────────────────────────────────────────────────────
def test_a_blocked_call_survives_persistence(tmp_path):
    """记录体能回看：状态与理由都在，且它从没进过闸门（``confirm_outcome`` 空）。"""
    reason = "no"
    settings = _settings(tmp_path, policy_rules=_rules({"tool": "probe", "reason": reason}))
    _rt, state, _tool, _tm, _bus = _run(settings)

    record = StepRecord(**state["steps"][0]).tool_calls[0]
    assert record.status == "skipped"
    assert record.error == reason
    assert record.confirm_outcome == ""
    # 没人批过它，也没人拒过它：这不是一次闸门判决。
    assert ToolCallRecord(id="x", tool_name="y").status == "pending"
