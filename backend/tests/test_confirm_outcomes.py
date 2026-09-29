"""Issue #57 AC5 —— 确认终态分档：拒绝 / 超时未答 / 停止打断 三者可区分。

改造前只有一个失败终态「未批准即拒绝」：`consume_confirm` 回 False 就一律记成
拒绝，人在不在、答没答、被没被 stop 折在同一句 ``rejected by user`` 里（对标
Codex 的 ``Denied`` / ``TimedOut`` / ``Abort`` 是三个枚举值，不是一个布尔的假）。

这里钉住的是**判据**：先问「有没有人给过判决」，再问「是被谁打断的」。

* 人明确拒了 → ``denied``，不写 ``pending_confirm``（这是一次真判决）；
* 到点没人答 → ``timed_out``，**不**写 ``pending_confirm``（闸口自己收口了、运行已
  继续，那个标记的意思是「还停在闸口等人」）；
* stop 打断 → ``aborted``，写 ``pending_confirm``（同上，#4 的语义保持）；
* 人批了 → ``approved``。

三档同时落在**事件**（``tool_result`` 的记录体）与**状态**（``_current_tool_calls``
/ ``steps[].tool_calls[]`` / ``pending_confirm``）两侧，持久化面走
``ToolCallRecord.confirm_outcome``。

红线：``human_confirm_node`` 里的 ``_needs_confirm`` 重算块（P0 死循环修复）一字未动，
本文件也不测它——它由 ``test_graph.py`` 的路由用例钉住。
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

from backend.api.schemas import StepRecord, ToolCallRecord
from backend.config import Settings
from backend.core.agent.nodes import (
    CONFIRM_ABORTED,
    CONFIRM_APPROVED,
    CONFIRM_AUTO_APPROVED,
    CONFIRM_DENIED,
    CONFIRM_TIMED_OUT,
    AgentRuntime,
)
from backend.core.agent.state import AgentState
from backend.core.llm.client import MockLLMClient
from backend.core.tools.base import BaseTool, ToolResult
from backend.services.event_bus import EventBus

TASK_ID = "t_confirm_outcome"


class _Gated(BaseTool):
    """A ``requires_confirm`` probe — the gate subject is the verdict, not the tool."""

    name = "gate_probe"
    description = "confirm-requiring probe (test only)"
    args_schema: Dict[str, Any] = {"type": "object", "properties": {}}
    requires_confirm = True

    def run(self, **kwargs: Any) -> ToolResult:
        return ToolResult(success=True, data="ran")


class _GateTM:
    """TaskManager 的最小替身：挂起、判决、停止旗标三件事各自可控。

    ``verdict`` 是 ``consume_confirm`` 的返回值 —— ``None`` 表示**人根本没给过
    判决**（区别于 False 那次「给了、给的是不」）。``answered`` 才对应真人的
    「按下按钮」：真 False 时 ``request_confirm`` 返回的 Event 永不 set，等待只可能
    被 stop 或到期打断。两个开关分开写，是因为 #57 的分档判据正是「答没答」与
    「被谁打断」这两问。
    """

    def __init__(
        self,
        settings: Settings,
        verdict: Optional[bool] = None,
        stop: bool = False,
        answered: bool = True,
    ):
        self.settings = settings
        self.event_bus = EventBus()
        self._verdict = verdict
        self._stop = stop
        self._answered = answered
        self.asked: list[str] = []

    def request_confirm(self, task_id: str, tool_call_id: str) -> threading.Event:
        self.asked.append(tool_call_id)
        ev = threading.Event()
        if self._answered:
            ev.set()
        return ev

    def consume_confirm(self, task_id: str, tool_call_id: str) -> Optional[bool]:
        return self._verdict

    def is_stop_flagged(self, task_id: str) -> bool:
        # stop 是在等待**当中**落下的：从第一次挂起起才算被旗标打断。
        # 一进门就旗标为真会连 executor 那一步都短路掉，测不到闸门。
        return self._stop and bool(self.asked)

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


def _run_gate(
    settings: Settings,
    *,
    verdict: Optional[bool] = None,
    stop: bool = False,
    confirm_timeout_sec: Optional[float] = None,
    answered: Optional[bool] = None,
) -> tuple[AgentRuntime, AgentState, _GateTM]:
    """One full gate pass: executor -> human_confirm -> tool (skipped or executed).

    ``answered`` 默认跟着 ``verdict`` 走（给了判决就等于按过按钮）；只有「答案卡在
    截止点上」那一条需要把两者拆开，才显式传。``confirm_timeout_sec`` 注在 Setting
    上而不是构造参数上，因为生产路径就是 runtime 读 ``tm.settings``——注入点与生效
    路径同一条，才测得到「配置改了就真的变」。
    """
    if answered is None:
        answered = verdict is not None
    if confirm_timeout_sec is not None:
        settings = settings.model_copy(update={"confirm_timeout_sec": confirm_timeout_sec})
    tool = _Gated(settings)
    tm = _GateTM(settings, verdict=verdict, stop=stop, answered=answered)
    rt = AgentRuntime(
        TASK_ID, tm, llm=MockLLMClient(
            tool_calls=[{"id": "c1", "name": tool.name, "arguments": {}}]
        ),
        tools=[tool], tool_schemas=[tool.to_openai_schema()],
        confirm_enabled=True,
    )
    state = _state()
    rt.executor(state)
    assert state["_current_tool_calls"][0]["need_confirm"] is True
    rt.human_confirm_node(state)
    rt.tool_node(state)
    return rt, state, tm


def _rec(state: AgentState) -> Dict[str, Any]:
    return state["_current_tool_calls"][0]


def _resolved(tm: _GateTM) -> list[Dict[str, Any]]:
    """闸门给出的每一格终态，按事件顺序（asked / replied 成对，#57 AC5 的事件面）。"""
    return [e["data"] for e in tm.event_bus.replay(TASK_ID) if e["type"] == "human_confirm_resolved"]


# ── 四档各钉一次 ─────────────────────────────────────────────────────────────
def test_explicit_rejection_is_denied(settings):
    _rt, state, tm = _run_gate(settings, verdict=False)
    rec = _rec(state)

    assert rec["confirm_outcome"] == CONFIRM_DENIED
    assert rec["status"] == "skipped"
    assert rec["error"] == "rejected by user"
    assert state["_rejected_ids"] == ["c1"]
    # 这是一次真判决：续跑闸门不必再把它当「等人答」拦下来。
    assert state["pending_confirm"] == {}
    assert tm.asked == ["c1"]


def test_a_real_verdict_outranks_a_concurrent_stop(settings):
    """判据顺序的骨架两问同时命中时，判决赢：先问「人答没答」，再问「被谁打断」。

    人答了「不」的同一刻 stop 也落下来 —— 这一格是 ``denied``（一次真判决，不写
    ``pending_confirm``），不是 ``aborted``。缺了这条，``_ask_human`` docstring 自称
    的那根骨架只钉了一半。
    """
    _rt, state, tm = _run_gate(settings, verdict=False, stop=True)
    rec = _rec(state)

    assert tm.is_stop_flagged(TASK_ID) is True, "stop 没真在场，这条就没测到优先级"
    assert rec["confirm_outcome"] == CONFIRM_DENIED
    assert state["_rejected_ids"] == ["c1"]
    assert state["pending_confirm"] == {}
    # 记录体上此刻还没有分档文案：stop 让 tool_node 在入口就短路（与 aborted 那条
    # 断言 status=="pending" 同源），文案要等运行真走到 tool 节点才写。
    assert rec["status"] == "pending"


def test_approval_is_approved(settings):
    _rt, state, _tm = _run_gate(settings, verdict=True)
    rec = _rec(state)

    assert rec["confirm_outcome"] == CONFIRM_APPROVED
    assert rec["status"] == "success"
    assert state["_confirmed_ids"] == ["c1"]
    assert state["pending_confirm"] == {}


def test_stop_while_awaiting_is_aborted(settings):
    """#4 的原语义（stop 打断要写 pending_confirm）保持不变，只是不再冒充「拒绝」。"""
    _rt, state, tm = _run_gate(settings, verdict=None, stop=True)
    rec = _rec(state)

    assert rec["confirm_outcome"] == CONFIRM_ABORTED
    assert state["pending_confirm"]["tool_call_id"] == "c1"
    assert tm.is_stop_flagged(TASK_ID) is True
    # 事件面：被 stop 打断的那一档走不到 tool 节点，所以它没有 tool_result——
    # 终态靠成对的 human_confirm_resolved 事件带出来（task 侧由 task_interrupted 收尾）。
    assert _resolved(tm)[0]["outcome"] == CONFIRM_ABORTED
    assert not [e for e in tm.event_bus.replay(TASK_ID) if e["type"] == "tool_result"]
    assert rec["status"] == "pending"


def test_no_verdict_before_the_deadline_is_timed_out(settings):
    """第三档必须真的可达：到点没人答 = ``timed_out``，不是 ``denied`` 也不是 ``aborted``。"""
    _rt, state, tm = _run_gate(settings, verdict=None, stop=False, confirm_timeout_sec=0.05)
    rec = _rec(state)

    assert rec["confirm_outcome"] == CONFIRM_TIMED_OUT
    assert rec["status"] == "skipped"
    assert "timeout" in rec["error"]
    assert "rejected" not in rec["error"]
    # 超时不等于「停在闸口」：调用已进 _rejected_ids、运行已经往下走，所以不写
    # 续跑闸门读的那个标记（task_manager._raise_if_parked_on_gate 的通道 1）。
    # 写了就等于一次没人答把任务永久锁成不可 resume，还配上「was stopped」的假原因。
    assert state["pending_confirm"] == {}
    assert state["_rejected_ids"] == ["c1"]  # 通道 2 靠这条判「已有交代」
    assert tm.asked == ["c1"]


def test_the_three_failure_states_are_pairwise_distinguishable(settings):
    """票面判据：三档在事件与状态里都读得出是哪一档。"""
    denied = _run_gate(settings, verdict=False)
    timed = _run_gate(settings, verdict=None, confirm_timeout_sec=0.05)
    aborted = _run_gate(settings, verdict=None, stop=True)

    # 状态面：记录体上的字段。
    outcomes = [_rec(s[1])["confirm_outcome"] for s in (denied, timed, aborted)]
    assert outcomes == [CONFIRM_DENIED, CONFIRM_TIMED_OUT, CONFIRM_ABORTED]
    assert len(set(outcomes)) == 3, "三档又折成一档了"
    # 事件面：三档都发得出，不必等 tool_result；asked / replied 一一对应。
    for s in (denied, timed, aborted):
        types = [e["type"] for e in s[2].event_bus.replay(TASK_ID)]
        assert types.count("human_confirm_required") == 1
        assert types.count("human_confirm_resolved") == 1
        assert types.index("human_confirm_resolved") > types.index("human_confirm_required")
    # 文案面：走到 tool 节点的两档，错误文案不再是同一句。
    texts = [_rec(s[1])["error"] for s in (denied, timed)]
    assert len(set(texts)) == 2, f"错误文案仍混写：{texts}"
    assert "rejected" not in _rec(timed[1])["error"]
    # 只有「被 stop 折回来」那一档还停在闸口（该写 pending_confirm）：denied 是人
    # 给的真判决，timed_out 是闸口自己收口并继续跑——两者都不该拦续跑。
    assert denied[1]["pending_confirm"] == {}
    assert timed[1]["pending_confirm"] == {}
    assert aborted[1]["pending_confirm"]


def test_outcome_rides_the_tool_result_event(settings):
    """事件面：SSE / trace / run manifest 镜像的都是这条记录体。"""
    _rt, _state, tm = _run_gate(settings, verdict=None, confirm_timeout_sec=0.05)
    events = tm.event_bus.replay(TASK_ID)
    results = [e["data"] for e in events if e["type"] == "tool_result"]

    assert len(results) == 1
    assert results[0]["confirm_outcome"] == CONFIRM_TIMED_OUT
    assert "timeout" in results[0]["error"]


def test_outcome_survives_persistence(settings):
    """状态面要能回看：持久化 schema 不收尾，API 与重启后就读不出是哪一档。"""
    _rt, state, _tm = _run_gate(settings, verdict=None, stop=True)
    step = state["steps"][0]

    record = StepRecord(**step)
    assert record.tool_calls[0].confirm_outcome == CONFIRM_ABORTED
    # 默认空串：从没进过闸门的调用不该被凭空安一个终态。
    assert ToolCallRecord(id="x", tool_name="y").confirm_outcome == ""


def test_a_verdict_arriving_at_the_deadline_wins(settings):
    """先问「有没有判决」再问「被谁打断」：等待到期的那一次，若判决同时已在，判决说了算。"""
    _rt, state, _tm = _run_gate(
        settings, verdict=False, answered=False, stop=False, confirm_timeout_sec=0.05
    )

    assert _rec(state)["confirm_outcome"] == CONFIRM_DENIED
    assert state["pending_confirm"] == {}


def test_default_bound_comes_from_settings(settings):
    """上界是配置项、不是 0：没到点就不许凭空长出「超时未答」。"""
    rt = AgentRuntime("t", _GateTM(settings), llm=None, tools=[], tool_schemas=[])
    assert rt.confirm_timeout_sec == settings.confirm_timeout_sec
    assert settings.confirm_timeout_sec > 60, "上界短到会把在场的人判成超时"


# ── AC6：评测态 --auto-approve 只把「必问」变放行 ──────────────────────────────
class _NeverAsksTM(_GateTM):
    """评测态的裁判：闸门一旦去向「人」发问，测试当场失败。"""

    def request_confirm(self, task_id: str, tool_call_id: str) -> threading.Event:
        raise AssertionError("auto-approve must not wait on a human")

    def consume_confirm(self, task_id: str, tool_call_id: str) -> Optional[bool]:
        raise AssertionError("auto-approve must not read a human verdict")


def _auto_runtime(settings: Settings, tm: _GateTM) -> tuple[AgentRuntime, AgentState]:
    tool = _Gated(settings)
    rt = AgentRuntime(
        TASK_ID, tm, llm=MockLLMClient(
            tool_calls=[{"id": "c1", "name": tool.name, "arguments": {}}]
        ),
        tools=[tool], tool_schemas=[tool.to_openai_schema()],
        confirm_enabled=True, auto_approve=True,
    )
    state = _state()
    return rt, state


def test_auto_approve_runs_the_gate_and_records_auto_approved(settings):
    """整块旁路 → 判定照算、闸门照进，只是终态是 ``auto_approved`` 不是 ``approved``。

    这条是 AC6 的核心可观测差别：改造前 ``confirm_enabled=False`` 让 ``need_confirm``
    恒为 False，闸门连「这一下本来要问人」都不留痕；``auto_approved`` 与
    ``approved`` 也由此分得开——后者是真人批的。
    """
    tm = _NeverAsksTM(settings, answered=False)
    rt, state = _auto_runtime(settings, tm)
    rt.executor(state)
    rt.human_confirm_node(state)
    rt.tool_node(state)

    rec = _rec(state)
    assert rec["need_confirm"] is True
    assert rec["confirm_outcome"] == CONFIRM_AUTO_APPROVED
    assert rec["status"] == "success"
    assert state["_confirmed_ids"] == ["c1"]
    assert state["_rejected_ids"] == []
    assert state["_needs_confirm"] is False
    # 「五档里只有 aborted 写 pending_confirm」这句话此前缺第五档的断言：评测态自动
    # 放行同样不许在状态里留下「还停在闸口」的标记。
    assert state["pending_confirm"] == {}
    types = [e["type"] for e in tm.event_bus.replay(TASK_ID)]
    assert types.count("human_confirm_resolved") == 1
    # 真人不在，就不许发「已向人发问」这种事件（评测态不伪造 ask）。
    assert "human_confirm_required" not in types
    assert _resolved(tm)[0]["outcome"] == CONFIRM_AUTO_APPROVED
    # 没向任何人发问：_NeverAsksTM 若被调用会直接抛。
    assert tm.asked == []


def test_auto_approve_never_outvotes_an_explicit_deny(settings):
    """显式 deny 不动：已被人拒掉的调用，评测态不许替它翻案成批准。"""
    tm = _NeverAsksTM(settings, answered=False)
    rt, state = _auto_runtime(settings, tm)
    rt.executor(state)
    # 人先说过不（跨 era 带回 _rejected_ids 的形状，评测态不得重开）。
    state["_rejected_ids"] = ["c1"]
    rt.human_confirm_node(state)
    rt.tool_node(state)

    rec = _rec(state)
    # 闸门压根没重开这一格：没有新终态，更没有 auto_approved。
    assert rec.get("confirm_outcome", "") == ""
    assert state["_confirmed_ids"] == []
    assert rec["status"] == "skipped"
    assert rec["error"] == "rejected by user"
    assert _resolved(tm) == []
