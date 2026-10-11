"""#46 第一步：悬空 ``tool_call`` 在循环入口清扫。

定义（规格 §5）：一条 assistant 的 tool_call 没有配对的 ``role: tool`` 结果——
回合记录永停 ``pending``。真实缺口是「停在确认闸门后被 stop」与「运行中途被
中断」，危害是 resume / 续跑后把一条没有回答的 ``tool_calls`` 发给 OpenAI 协议
端点（#95 留下的两个缺口）。

不变量：**任何一次进入 executor 之前**，消息面不存在孤儿 tool_calls、记录面不
存在残留 pending。所以这里测的是 executor 这个节点入口，而不是某个私有函数。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple

from backend.core.agent.nodes import AgentRuntime
from backend.core.agent.state import AgentState
from backend.core.llm.client import MockLLMClient
from backend.services.event_bus import EventBus
from backend.tests.conftest import make_settings


def _rt(tmp_path: Path) -> Tuple[AgentRuntime, List[Dict[str, Any]]]:
    settings = make_settings(tmp_path)
    bus = EventBus()
    events: List[Dict[str, Any]] = []
    bus.subscribe("t1", lambda e: events.append(e))
    tm = SimpleNamespace(
        settings=settings,
        event_bus=bus,
        add_artifact=lambda *a, **k: None,
        is_stop_flagged=lambda _tid: False,
        _active_states={"t1": {"artifacts": []}},
    )
    rt = AgentRuntime(
        "t1", tm, llm=MockLLMClient(final_answer="收尾答案"), tools=[], tool_schemas=[]
    )
    return rt, events


def _rec(call_id: str, status: str, tool_name: str = "write") -> Dict[str, Any]:
    return {
        "id": call_id,
        "tool_name": tool_name,
        "input": {},
        "output": None,
        "status": status,
        "error": "",
        "need_confirm": False,
        "confirmed": False,
    }


def _turn(*call_ids: str) -> Dict[str, Any]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": cid, "type": "function", "function": {"name": "write", "arguments": "{}"}}
            for cid in call_ids
        ],
    }


def _state(
    *, messages: List[Dict[str, Any]], records: List[Dict[str, Any]]
) -> AgentState:
    """A state shaped exactly like ``TaskManager.run()``'s initial dict, one round in."""
    return {
        "task_id": "t1",
        "messages": list(messages),
        "plan": [],
        "steps": [{"index": 1, "thought": "", "status": "running", "tool_calls": list(records)}],
        "artifacts": [],
        "status": "RUNNING",
        "stop_requested": False,
        "pending_confirm": {},
        "step_index": 1,
        "final_answer": "",
        "error": "",
        "_last_action": "",
        "_current_tool_calls": [],
        "_confirmed_ids": [],
        "_rejected_ids": [],
        "_needs_confirm": False,
        "risk_report": [],
        "_risk_blocked": False,
        "subtasks": [],
        "_is_subtask": False,
    }


def _orphans(messages: List[Dict[str, Any]]) -> List[str]:
    """Tool-call ids on the message face that carry no matching tool answer."""
    answered = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
    asked: List[str] = []
    for m in messages:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                if tc.get("id") not in answered:
                    asked.append(tc["id"])
    return asked


def _answers(messages: List[Dict[str, Any]], call_id: str) -> List[Dict[str, Any]]:
    return [m for m in messages if m.get("role") == "tool" and m.get("tool_call_id") == call_id]


def test_executor_entry_answers_orphan_tool_call(tmp_path: Path) -> None:
    """一条被 stop 折回来的 pending 调用：进 executor 后消息面补齐、记录成 skipped。"""
    rt, _ = _rt(tmp_path)
    state = _state(
        messages=[{"role": "user", "content": "写文件"}, _turn("c1")],
        records=[_rec("c1", "pending")],
    )
    out = rt.executor(state)

    assert _orphans(out["messages"]) == []
    assert len(_answers(out["messages"], "c1")) == 1
    rec = out["steps"][0]["tool_calls"][0]
    assert rec["status"] == "skipped"
    assert rec["error"]


def test_executor_entry_leaves_answered_call_alone(tmp_path: Path) -> None:
    """已执行过的调用不被二次补齐（记录仍 success、消息仍只有一条）。"""
    rt, _ = _rt(tmp_path)
    state = _state(
        messages=[
            {"role": "user", "content": "写文件"},
            _turn("c1", "c2"),
            {"role": "tool", "tool_call_id": "c1", "content": "{}"},
            {"role": "tool", "tool_call_id": "c2", "content": "{}"},
        ],
        records=[_rec("c1", "success"), _rec("c2", "success")],
    )
    before = len(state["messages"])
    out = rt.executor(state)

    assert len([m for m in out["messages"] if m.get("role") == "tool"]) == 2
    assert len(out["messages"]) == before + 1  # only this round's assistant turn
    assert [r["status"] for r in out["steps"][0]["tool_calls"]] == ["success", "success"]


def test_executor_entry_answers_orphan_whose_record_already_settled(tmp_path: Path) -> None:
    """记录终态但消息面缺答（#95 之前的中途失败路径）：补消息，不改写记录终态。"""
    rt, _ = _rt(tmp_path)
    state = _state(
        messages=[{"role": "user", "content": "写文件"}, _turn("c1")],
        records=[_rec("c1", "failed", )],
    )
    out = rt.executor(state)

    assert _orphans(out["messages"]) == []
    assert out["steps"][0]["tool_calls"][0]["status"] == "failed"


def test_executor_entry_sweeps_across_rounds(tmp_path: Path) -> None:
    """孤儿可能落在历史回合里：清扫看整条消息面，不只最后一轮。"""
    rt, _ = _rt(tmp_path)
    state = _state(
        messages=[
            {"role": "user", "content": "写文件"},
            _turn("c1"),
            {"role": "tool", "tool_call_id": "c1", "content": "{}"},
            _turn("c2", "c3"),
        ],
        records=[_rec("c1", "success")],
    )
    state["steps"].append(
        {
            "index": 2,
            "thought": "",
            "status": "running",
            "tool_calls": [_rec("c2", "pending"), _rec("c3", "pending")],
        }
    )
    out = rt.executor(state)

    assert _orphans(out["messages"]) == []
    settled = [
        r["status"] for s in out["steps"] for r in s["tool_calls"] if r["id"] in ("c2", "c3")
    ]
    assert settled == ["skipped", "skipped"]
