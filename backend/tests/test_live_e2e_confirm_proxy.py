"""Issue #68 —— live 脚本的「代签员」：闸门照拦，脚本替人应答。

``scripts/live_e2e.py`` 场景 2 测的是 stop → rebuild → resume，不是闸门本身。真实模
型中途选了 ``requires_confirm`` 工具（实测 deepseek-v4.1-flash 回环后选 ``code_exec``）
时，``human_confirm_node`` 会挂起等一个不在场的人 —— 默认 ``confirm_timeout_sec``
是 1800s，而脚本只等 180s，于是**必超时**。票面裁定 A：脚本订阅 ``human_confirm_required``
并以批准应答（走 ``TaskManager.confirm``，与 REST ``POST /tasks/{id}/confirm`` 同一条
路），而不是把闸门旁路掉。

与被旁路的区别（本文件钉住的那条线）：评测态 ``auto_approve`` 是 harness 预先 supplying
判决，``human_confirm_required`` 根本不发（见 ``test_confirm_outcomes.py``）；代签员是
**真人会走的同一条应答路径**，所以 ask/reply 事件对齐全、终态是 ``approved`` 而不是
``auto_approved`` —— 「闸门该拦」这件事仍然被真实测到。

seam：脚本模块级函数（``importlib`` 直读，同 ``test_commit_msg_validator.py`` 的口径）
+ ``TaskManager`` / ``EventBus`` 公开面，不碰内核内部。
"""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path
from typing import Any, Dict

from backend.config import Settings
from backend.core.llm.client import MockLLMClient
from backend.core.tools.base import BaseTool, ToolResult
from backend.services.event_bus import EventBus
from backend.services.persistence import Persistence
from backend.services.task_manager import TaskManager

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "live_e2e.py"

# 文件缺失时这里就红（exec_module 抛 FileNotFoundError）——守卫在 import 期。
_spec = importlib.util.spec_from_file_location("live_e2e", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
_live_e2e = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_live_e2e)


class _Gated(BaseTool):
    """A ``requires_confirm`` probe: the subject is the verdict, not the tool."""

    name = "gate_probe"
    description = "confirm-requiring probe (test only)"
    args_schema: Dict[str, Any] = {"type": "object", "properties": {}}
    requires_confirm = True

    def run(self, **kwargs: Any) -> ToolResult:
        return ToolResult(success=True, data="ran")


def _gated_manager(settings: Settings) -> tuple[TaskManager, EventBus, str]:
    """One real run of a gated tool call, with the live script answering it."""
    script = MockLLMClient(
        plan=["Call the gated probe"],
        tool_calls=[{"id": "p1", "name": "gate_probe", "arguments": {}}],
        final_answer="probe done",
    )
    bus = EventBus()
    tm = TaskManager(settings, bus, Persistence(settings), llm_client=script, tools=[_Gated(settings)])
    task_id = tm.create_task(title="gated-run", user_input="call the gated probe")
    return tm, bus, task_id


def _wait_status(tm: TaskManager, task_id: str, timeout: float = 15.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = tm.get_task(task_id)
        if task and task.status.value in ("COMPLETED", "FAILED", "INTERRUPTED"):
            return task.status.value
        time.sleep(0.05)
    task = tm.get_task(task_id)
    return task.status.value if task else "MISSING"


def _events(bus: EventBus, task_id: str) -> list[Dict[str, Any]]:
    return bus.replay(task_id)


def test_confirm_proxy_answers_the_gate_and_the_run_completes(settings) -> None:
    """The run the live script depends on: a mid-run gate does not park it."""
    tm, bus, task_id = _gated_manager(settings)
    try:
        log = _live_e2e.attach_confirm_proxy(tm, task_id)
        status = _wait_status(tm, task_id)
    finally:
        tm.shutdown()

    assert status == "COMPLETED", f"a gate parked the run: status={status}"
    events = _events(bus, task_id)
    types = [e["type"] for e in events]
    # 闸门没有被旁路：问与答成对出现，且终态是「人批了」。
    assert "human_confirm_required" in types, "代签员把闸门测没了（ask 事件缺席）"
    resolved = [e["data"] for e in events if e["type"] == "human_confirm_resolved"]
    assert resolved and resolved[0]["outcome"] == "approved", resolved
    assert resolved[0]["tool_call_id"] == "p1"
    # 批准后工具真的执行了（不是 skipped）。
    tool_results = [e["data"] for e in events if e["type"] == "tool_result"]
    assert tool_results and tool_results[0]["status"] == "success", tool_results
    assert len(log) == 1 and log[0]["ok"] is True, log


def test_confirm_proxy_answers_a_request_asked_before_it_attached(settings) -> None:
    """迟到的代签员也得补答：真人在环里时事件已经发过，应答仍必须送达。

    ``create_task`` 起线程即开跑，``subscribe`` 只能在其后挂载 —— 竞态窗口就在
    「闸门已发布 ask、代签员还没订阅」那一段（mock LLM 秒级跑完，离线测试必踩）。
    """
    bus = EventBus()
    tm = TaskManager(settings, bus, Persistence(settings), llm_client=MockLLMClient(), tools=[_Gated(settings)])
    try:
        tm.request_confirm("t_late", "p1")  # 内核此刻正挂在这一格里等答案
        bus.publish(
            "t_late",
            "human_confirm_required",
            {"tool_call_id": "p1", "tool_name": "gate_probe", "input": {}},
        )
        log = _live_e2e.attach_confirm_proxy(tm, "t_late")
        assert tm.consume_confirm("t_late", "p1") is True
        assert log == [{"tool_call_id": "p1", "tool_name": "gate_probe", "ok": True}]
    finally:
        tm.shutdown()


def test_confirm_proxy_leaves_a_request_it_cannot_match_unanswered(settings) -> None:
    """对不上号的应答是「脚本没答上」的信号，必须自己说出来，不能悄悄吞掉。

    ``confirm`` 回 False（挂起的不是这一格 / 换了 manager）时，运行会停在闸门等
    ``confirm_timeout_sec`` —— 那正是 #68 的现场。日志记 ``ok=False``，脚本打一行
    警告，事后 trace 里读得出「代签员答错了哪一格」。
    """
    bus = EventBus()
    tm = TaskManager(settings, bus, Persistence(settings), llm_client=MockLLMClient(), tools=[_Gated(settings)])
    try:
        tm.request_confirm("t_ghost", "p_real")  # 挂着的是 p_real
        bus.publish(
            "t_ghost",
            "human_confirm_required",
            {"tool_call_id": "p_ghost", "tool_name": "gate_probe", "input": {}},
        )
        log = _live_e2e.attach_confirm_proxy(tm, "t_ghost")
        assert log == [{"tool_call_id": "p_ghost", "tool_name": "gate_probe", "ok": False}]
        assert tm.consume_confirm("t_ghost", "p_real") is None
    finally:
        tm.shutdown()


def test_gate_verdict_count_separates_approval_from_everything_else() -> None:
    """闸门证据的判据：只有「人批了」算答上，其余四档都算没答。

    纯函数用例，期望值是手写的字面量 —— 事件流由本用例自己拼，不经内核。
    """
    events = [
        {"type": "human_confirm_required", "data": {"tool_call_id": "a"}, "ts": 1.0},
        {"type": "human_confirm_resolved", "data": {"tool_call_id": "a", "outcome": "approved"}, "ts": 2.0},
        {"type": "human_confirm_required", "data": {"tool_call_id": "b"}, "ts": 3.0},
        {"type": "human_confirm_resolved", "data": {"tool_call_id": "b", "outcome": "timed_out"}, "ts": 4.0},
        {"type": "human_confirm_required", "data": {"tool_call_id": "c"}, "ts": 5.0},
        # 评测态自动放行不算「有人答了」：live 脚本走的是真人那条路。
        {"type": "human_confirm_resolved", "data": {"tool_call_id": "c", "outcome": "auto_approved"}, "ts": 6.0},
        {"type": "human_confirm_required", "data": {"tool_call_id": "d"}, "ts": 7.0},
        {"type": "tool_result", "data": {"tool_call_id": "a"}, "ts": 8.0},
    ]

    assert _live_e2e.count_gate_verdicts(events) == (4, 1)
    assert _live_e2e.count_gate_verdicts([]) == (0, 0)
