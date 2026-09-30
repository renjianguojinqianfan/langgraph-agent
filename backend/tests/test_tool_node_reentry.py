"""Issue #100 —— tool_node 重入不得重复 dispatch 已执行完的调用。

触发条件是真实供应商常见的一回合并行 tool call：一轮 LLM 输出含 **≥2 个待确认
调用**，人逐个批准。图的走法就是那两条边（graph.py:76-100）——
``_after_executor`` 见 ``_needs_confirm`` 进 ``human_confirm``，``human_confirm``
静态回到 ``tool``，``_after_tool`` 只要还有没判决的就再进闸门。于是
``tool_node`` 一进门、二进门……而循环入口原先只有「已拒绝」与「尚未批准」两道
守卫，**没有「已执行」守卫**；``rec["status"]`` 是原地改成终态的、
``_current_tool_calls`` 全程活着，所以上一轮已经跑完的 c1 在第二次进门时两道守卫
都过，被原样再 dispatch 一次。

副作用双跑砸的是确认闸门的存在意义：「人批准的危险操作恰好执行一次」变成
「批准一次、执行 N 次」。它同时在对话面上留下同一 ``tool_call_id`` 的两条 tool
message —— 那是不合法的消息序列，跨供应商时兼容端点可以直接 400。

这里钉住的四件事：每个批准过的调用恰好 dispatch 一次（AC1）、恰好一条 tool
message 与一条 ``tool_result`` 事件（AC1）；已拒绝那一档重入时不得再喂一次
（#101 并入的第 3 条）；重试的账关在 ToolExecutor 里、与图重入无关（AC3）。
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable, Dict, List, Optional

from backend.config import Settings
from backend.core.agent.nodes import AgentRuntime
from backend.core.agent.state import AgentState
from backend.core.llm.client import LLMResponse, MockLLMClient
from backend.core.tools.base import BaseTool, ToolResult
from backend.core.tools.resilience import ToolExecutor
from backend.services.event_bus import EventBus
from backend.tests.conftest import make_settings

TASK_ID = "t_tool_node_reentry"


class _Probe(BaseTool):
    """自带副作用计数的探针工具：``run()`` 每被叫一次记一笔。

    ``circuit_breaker = False`` 是把熔断从计数里摘出去（#100 只问「跑了几遍」，
    熔断是另一本账，见 ADR 0002 的冻结区）；重试相关的三个开关只在 AC3 那条用。
    """

    description = "probe (test only)"
    args_schema: Dict[str, Any] = {"type": "object", "properties": {}}
    requires_confirm = True
    circuit_breaker = False

    def __init__(
        self,
        settings: Settings,
        name: str,
        *,
        gated: bool = True,
        fail: bool = False,
        retryable: bool = False,
        max_retries: Optional[int] = None,
    ) -> None:
        super().__init__(settings)
        self.name = name
        self.requires_confirm = gated
        self.fail = fail
        self.retryable = retryable
        self.max_retries = max_retries
        self.calls: List[Dict[str, Any]] = []

    def run(self, **kwargs: Any) -> ToolResult:
        self.calls.append(kwargs)
        if self.fail:
            return ToolResult(success=False, error="probe boom")
        return ToolResult(success=True, data=f"ran:{self.name}")


class _CountingExecutor(ToolExecutor):
    """AC1 要的「dispatch 计数」：只在入口处记一笔，``super()`` 原样跑。

    熔断 / 重试语义一字未动（本票零触碰 ``resilience.py``），这里只是观测面。
    """

    def __init__(
        self,
        settings: Settings,
        publish_fn: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        super().__init__(settings, publish_fn=publish_fn)
        self.dispatched: List[str] = []

    def dispatch(self, tool: BaseTool, **kwargs: Any) -> ToolResult:
        self.dispatched.append(tool.name)
        return super().dispatch(tool, **kwargs)


class _VerdictTM:
    """TaskManager 的最小替身：**按 tool_call_id 各给一个判决**。

    ``test_confirm_outcomes._GateTM`` 是「全体共用一个判决」，那造不出「人逐个
    批准」的形状；这里换成字典，才测得到批准 c1 之后 c2 还挂在闸门上。
    """

    def __init__(self, settings: Settings, verdicts: Dict[str, Optional[bool]]) -> None:
        self.settings = settings
        self.event_bus = EventBus()
        self._verdicts = verdicts
        self.asked: List[str] = []

    def request_confirm(self, task_id: str, tool_call_id: str) -> threading.Event:
        self.asked.append(tool_call_id)
        ev = threading.Event()
        ev.set()  # 人当场就答了：不靠超时收尾
        return ev

    def consume_confirm(self, task_id: str, tool_call_id: str) -> Optional[bool]:
        return self._verdicts.get(tool_call_id)

    def is_stop_flagged(self, task_id: str) -> bool:
        return False

    def add_artifact(self, *args: Any, **kwargs: Any) -> None:
        return None


class _ParallelRoundMock(MockLLMClient):
    """一回合吐 N 个 tool_calls —— 真实供应商的 parallel tool calls 形状。

    本体的 ``MockLLMClient`` 每回合只发一个调用（``tool_calls=[tc]``），而
    ``executor`` 每次覆盖 ``_current_tool_calls``，所以拿它「脚本化两个 tool_calls」
    造出来的是一回合一个调用的两个回合，触发不了本票；这里把整轮一次给完。
    """

    def __init__(
        self, plan: List[str], round_calls: List[Dict[str, Any]], final_answer: str = "done"
    ) -> None:
        super().__init__(plan=plan, final_answer=final_answer)
        self._round_calls = round_calls
        self._turn = 0

    def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        if not tools:
            return LLMResponse(content=json.dumps(self.plan))
        self._turn += 1
        if self._turn == 1:
            return LLMResponse(content="", tool_calls=list(self._round_calls))
        return LLMResponse(content=self.final_answer)


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


def _run_round(
    settings: Settings,
    tools: List[BaseTool],
    calls: List[Dict[str, Any]],
    verdicts: Dict[str, Optional[bool]],
) -> tuple[AgentRuntime, AgentState, _CountingExecutor, _VerdictTM]:
    """按图的真实路由跑一整轮：executor →（闸门 ⇄ tool）直到没有待判决的调用。

    重入完全由路由产生（``human_confirm`` 的静态边回到 ``tool``），不是测试手工
    叠出来的第二次调用 —— 这样「门进几次」这件事本身也在被测范围内。
    """
    tm = _VerdictTM(settings, verdicts)
    rt = AgentRuntime(
        TASK_ID,
        tm,
        llm=_ParallelRoundMock(plan=["一步"], round_calls=calls),
        tools=tools,
        tool_schemas=[t.to_openai_schema() for t in tools],
        confirm_enabled=True,
    )
    executor = _CountingExecutor(settings, publish_fn=rt._publish)
    rt._te = executor
    state = _state()

    rt.executor(state)
    while state["_needs_confirm"]:
        rt.human_confirm_node(state)
        rt.tool_node(state)
    return rt, state, executor, tm


def _tool_messages(state: AgentState) -> List[str]:
    return [m["tool_call_id"] for m in state["messages"] if m["role"] == "tool"]


def _result_events(tm: _VerdictTM) -> List[str]:
    """``tool_result`` 事件按 id 列出（重复 dispatch 在事件面上就是重复的 id）。"""
    return [e["data"]["id"] for e in tm.event_bus.replay(TASK_ID) if e["type"] == "tool_result"]


def _calls(c1: str, c2: str) -> List[Dict[str, Any]]:
    return [
        {"id": c1, "name": "gate_a", "arguments": {}},
        {"id": c2, "name": "gate_b", "arguments": {}},
    ]


# ── AC1：一轮两个 gated 调用、都批准 → 每个恰好执行一次 ────────────────────────
def test_two_approved_gated_calls_each_dispatch_once(settings):
    gate_a = _Probe(settings, "gate_a")
    gate_b = _Probe(settings, "gate_b")
    _rt, state, ex, tm = _run_round(
        settings, [gate_a, gate_b], _calls("c1", "c2"), {"c1": True, "c2": True}
    )

    # 批准一次 = 恰好 dispatch 一次；顺序也钉住（c1 在第一次进门、c2 在第二次）。
    assert ex.dispatched == ["gate_a", "gate_b"]
    assert gate_a.calls == [{}] and gate_b.calls == [{}]
    # 对话面：同一 tool_call_id 只有一条 tool message（两条是不合法的消息序列）。
    assert _tool_messages(state) == ["c1", "c2"]
    assert _result_events(tm) == ["c1", "c2"]
    assert state["_confirmed_ids"] == ["c1", "c2"]
    assert [r["status"] for r in state["_current_tool_calls"]] == ["success", "success"]


def test_an_extra_tool_node_entry_changes_nothing(settings):
    """终态守卫给的真正性质是**幂等**：门再进几次都无副作用。

    上游路由以后怎么改（新路径、重连、续跑）都不该把「一次批准」变成「两次执行」，
    所以这里直接多敲一次门，而不是只测眼前三跳。
    """
    gate_a = _Probe(settings, "gate_a")
    gate_b = _Probe(settings, "gate_b")
    rt, state, ex, tm = _run_round(
        settings, [gate_a, gate_b], _calls("c1", "c2"), {"c1": True, "c2": True}
    )
    before = (list(ex.dispatched), gate_a.calls.copy(), gate_b.calls.copy())

    rt.tool_node(state)
    rt.tool_node(state)

    assert (list(ex.dispatched), gate_a.calls, gate_b.calls) == before
    assert _tool_messages(state) == ["c1", "c2"]
    assert _result_events(tm) == ["c1", "c2"]


# ── 已拒绝那一档：重入时不得再喂一次（#101 并入第 3 条）───────────────────────
def test_rejected_call_is_fed_once_across_the_reentry(settings):
    """#95 之后「人已拒绝」那支也开始发 tool message，于是重入会再发一条。

    终态守卫把它一起收掉：拒绝的账只记一次 —— 状态、事件、对话三面各一条。
    """
    gate_a = _Probe(settings, "gate_a")
    gate_b = _Probe(settings, "gate_b")
    _rt, state, ex, tm = _run_round(
        settings, [gate_a, gate_b], _calls("c1", "c2"), {"c1": False, "c2": True}
    )

    assert ex.dispatched == ["gate_b"]  # 被拒的从没跑，批准的跑一次
    assert _tool_messages(state) == ["c1", "c2"]
    assert _result_events(tm) == ["c1", "c2"]
    rec_a = state["_current_tool_calls"][0]
    assert rec_a["status"] == "skipped"
    assert rec_a["error"] == "rejected by user"
    assert state["_rejected_ids"] == ["c1"] and state["_confirmed_ids"] == ["c2"]


# ── AC3：重试的账关在 ToolExecutor 内，不经图重入 ─────────────────────────────
def test_retries_stay_inside_dispatch_and_reentry_never_redispatches(tmp_path):
    """一个调用跑了几遍是**两本账**，这条把它们分开钉住。

    ``gate_a`` 是 retryable 且 ``max_retries=1``：一次 dispatch 里 ``with_retry``
    跑 2 遍（1 + 1 次重试），``ToolResult.retries`` 记的就是这个数；而图重入
    **不得**再多敲一次 dispatch。所以断言是「dispatch 1 次 / run 2 次」这一对：
    只测 run 次数会把 ToolExecutor 自己的重试误判成双跑，只测 dispatch 次数又
    证明不了重试确实发生在同一扇门里。

    ``tool_backoff_base=0.0`` 只是别让测试真睡退避；``confirm_timeout_sec`` 压小
    是防我自己在某个判决上漏键，让 ``_ask_human`` 干等 30 分钟。
    """
    settings = make_settings(
        tmp_path, tool_backoff_base=0.0, tool_max_retries=1, confirm_timeout_sec=0.05
    )
    gate_a = _Probe(settings, "gate_a", fail=True, retryable=True, max_retries=1)
    gate_b = _Probe(settings, "gate_b")
    _rt, state, ex, tm = _run_round(
        settings, [gate_a, gate_b], _calls("c1", "c2"), {"c1": True, "c2": True}
    )

    assert ex.dispatched.count("gate_a") == 1, "图重入又 dispatch 了一次"
    assert len(gate_a.calls) == 2, "重试应当发生在同一次 dispatch 内"
    assert state["_current_tool_calls"][0]["retries"] == 1
    assert state["_current_tool_calls"][0]["status"] == "failed"
    # 失败的那条也只喂一次（同 #95 的对话面，重入不得翻倍）。
    assert _result_events(tm) == ["c1", "c2"]


# ── 非 gated 调用同样吃重入：守卫是按 rec 幂等，不是按闸门 ────────────────────
def test_ungated_call_in_a_gated_round_also_dispatches_once(tmp_path):
    """同一回合里混一个不要确认的调用：它在第一次进门就跑完，第二次进门不许再跑。

    守卫放在循环入口、按 ``rec`` 的终态判，所以它管的是「每个调用」而不是
    「每个过闸门的调用」——这条钉住那个覆盖面。
    """
    settings = make_settings(
        tmp_path, tool_backoff_base=0.0, tool_max_retries=1, confirm_timeout_sec=0.05
    )
    gate_a = _Probe(settings, "gate_a")
    gate_b = _Probe(settings, "gate_b")
    open_probe = _Probe(settings, "open_probe", gated=False)
    calls = [
        {"id": "c0", "name": "open_probe", "arguments": {}},
        {"id": "c1", "name": "gate_a", "arguments": {}},
        {"id": "c2", "name": "gate_b", "arguments": {}},
    ]
    _rt, state, ex, tm = _run_round(settings, [open_probe, gate_a, gate_b], calls, {"c1": True, "c2": True})

    assert ex.dispatched == ["open_probe", "gate_a", "gate_b"]
    assert len(open_probe.calls) == 1
    assert _tool_messages(state) == ["c0", "c1", "c2"]
    assert _result_events(tm) == ["c0", "c1", "c2"]
