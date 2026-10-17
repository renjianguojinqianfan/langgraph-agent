"""QA independent boundary tests — P1 item 2 (sub-agent collaboration).

Issue #56 collapsed the feature to one entry point, so the QA angle is now:

* ``spawn_subagent`` keeps its ``requires_confirm=True`` flag (inert metadata
  since #138 removed the gate) and the delegation actually runs;
* the parent's context carries only the folded digest (as a *tool* turn, never
  replayed as an assistant one — #51), never the subtask's internal messages;
* subtask internal events live on the subtask's own channel, not the parent's;
* #70 ruling A restored the ``subtask_start`` / ``subtask_result`` /
  ``subtask_failed`` summary events on the **parent** channel only (spawn path) —
  a failing subtask additionally rides back as a failed tool call, and the parent
  stays alive;
* a subtask's face is a declared tier, so a dangerous tool is structurally
  absent from it;
* ``subagent_enabled=False`` removes the entry point from the tool face.
"""

from __future__ import annotations

import json
from typing import Any, Dict

import pytest

from backend.core.agent.graph import build_graph
from backend.core.agent.nodes import AgentRuntime
from backend.core.agent.subagent import (
    DEFAULT_TIER,
    EXPLORE_TIER,
    SubTaskSpec,
    tier_face_names,
)
from backend.core.llm.client import LLMResponse, MockLLMClient
from backend.services.snapshots import set_current_task_id
from backend.tests.conftest import make_manager, make_settings
from backend.tests.test_graph import _run_until_done

#: Only a subtask ever says this; it is how the parent-side assertions tell a
#: folded digest apart from a leaked internal turn.
SUBTASK_ANSWER = "SUBTASK-内部结论"
SUBTASK_MARKER = "子任务探针"
#: Part of the subtask instruction; the mock plans differently when it sees it,
#: which is what makes "whose plan_update landed on which channel" observable.
SUBTASK_INSTRUCTION_FRAGMENT = "检索素材"
SUBTASK_PLAN = ["子任务内部步骤：检索素材"]


def _spec(subtask_id: str, name: str, instruction: str, **overrides: Any) -> SubTaskSpec:
    return SubTaskSpec(subtask_id=subtask_id, name=name, instruction=instruction, **overrides)


@pytest.fixture(autouse=True)
def _clear_task_ctx():
    """Keep the parent-task contextvar from one test's subtask run out of this
    thread into the next test (``_exec_one`` re-seats it, never restores)."""
    set_current_task_id(None)
    yield
    set_current_task_id(None)


def _main_state(user_input: str) -> Dict[str, Any]:
    return {
        "task_id": "qa-main",
        "messages": [{"role": "user", "content": user_input}],
        "plan": [],
        "steps": [],
        "artifacts": [],
        "status": "RUNNING",
        "stop_requested": False,
        "step_index": 0,
        "final_answer": "",
        "error": "",
        "_last_action": "",
        "_current_tool_calls": [],
        "subtasks": [],
        "_is_subtask": False,
    }


class _QaResearchMock(MockLLMClient):
    """Parent delegates once and then reports; the subtask writes its notes.

    Entry B used to reach this shape from the wording alone; the same scripted
    turns now drive the ``spawn_subagent`` path. Plans differ per side, which is
    what makes «whose round landed on which channel» observable.
    """

    def __init__(self):
        super().__init__(plan=["调研", "写作"], final_answer="父任务完成")
        self.subtask_turns = 0

    def complete(self, messages, tools=None, **kwargs):
        user = " ".join(
            str(m.get("content") or "") for m in messages if m.get("role") == "user"
        )
        if not tools:
            # Plans differ per side on purpose: a shared mock would otherwise
            # make "whose plan_update is on which channel" unobservable.
            plan = SUBTASK_PLAN if SUBTASK_INSTRUCTION_FRAGMENT in user else self.plan
            return LLMResponse(content=json.dumps(plan))
        if "检索" in user:
            self.subtask_turns += 1
            if self.subtask_turns == 1:
                return LLMResponse(
                    content="",
                    tool_calls=[
                        {
                            "id": "qasub",
                            "name": "write",
                            "arguments": {
                                "path": "qa_sub_notes.txt",
                                "content": "QA sub notes",
                            },
                        }
                    ],
                )
            return LLMResponse(content="研究完成")
        if "撰写" in user:
            return LLMResponse(content="报告已完成")
        # Parent's first executor turn delegates; the second one reports.
        if self._executor_turn == 0:
            self._executor_turn += 1
            return LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "qaspawn",
                        "name": "spawn_subagent",
                        "arguments": {
                            "name": "研究子任务",
                            "instruction": "检索素材并记下要点",
                            "tier": DEFAULT_TIER,
                        },
                    }
                ],
            )
        return LLMResponse(content=self.final_answer)


class _SpawnOnceMock(MockLLMClient):
    """Parent spawns once; the subtask answers in a single turn.

    The parent and the subtask share this instance, so the subtask turn is
    recognised by the marker in its instruction.
    """

    def __init__(self):
        super().__init__(plan=["委托子任务"], final_answer="父任务完成")
        self.parent_turns = 0
        self.subtask_turns = 0

    def complete(self, messages, tools=None, **kwargs):
        user = " ".join(
            str(m.get("content") or "") for m in messages if m.get("role") == "user"
        )
        if not tools:
            return LLMResponse(content=json.dumps(self.plan))
        if SUBTASK_MARKER in user:
            self.subtask_turns += 1
            return LLMResponse(content=SUBTASK_ANSWER)
        self.parent_turns += 1
        if self.parent_turns == 1:
            return LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "qa-spawn",
                        "name": "spawn_subagent",
                        "arguments": {
                            "name": "probe",
                            "instruction": f"{SUBTASK_MARKER}：给一句话结论",
                        },
                    }
                ],
            )
        return LLMResponse(content=self.final_answer)


# ── the spawn flag ───────────────────────────────────────────────────────────
def test_spawn_subagent_tool_requires_confirm(tmp_path):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(plan=["p"], final_answer="d"))
    tool = next(t for t in tm._tools if t.name == "spawn_subagent")
    assert tool.requires_confirm is True


# ── context isolation ────────────────────────────────────────────────────────
def test_parent_context_carries_only_the_folded_digest(tmp_path):
    """父任务上下文里只有折叠后的回传（tool 轮），绝不出现子任务内部轮次。

    Rewrite of the old «父消息只含折叠摘要» case: entry B wrote a ``system``
    digest into the parent state and filled ``state["subtasks"]``; the surviving
    path hands the digest back as the *tool result* of the spawn call.
    """
    settings = make_settings(tmp_path)
    tm = make_manager(settings, _SpawnOnceMock())
    runtime = AgentRuntime(
        task_id="qa-main",
        task_manager=tm,
        llm=tm._llm,
        tools=tm._tools,
        tool_schemas=tm._tool_schemas,
        aux_llm=tm._aux_llm,
    )
    graph = build_graph(runtime)
    final = graph.invoke(_main_state("派个子任务给一句话结论"))

    assert final["status"] == "COMPLETED"
    # #70 裁定 A 重裁（原断 == []，钉 #56 的「生产者已断」）：spawn 路径把折叠回传
    # 回登进图状态，本用例正好是它的图级落点；上下文隔离的本体（消息面只有 tool 轮）
    # 一条没少，见下。
    assert final["subtasks"] and final["subtasks"][0]["status"] == "completed"

    messages = final["messages"]
    roles = [m.get("role") for m in messages]
    assert "tool" in roles  # the spawn result is the folded digest
    folded = [m for m in messages if m.get("role") == "tool" and SUBTASK_ANSWER in str(m)]
    assert folded, "子任务结论没有作为工具回传落到父上下文"
    # #51 carried over: a system-produced digest must never be replayed as if
    # the model itself had said it.
    assert not [
        m
        for m in messages
        if m.get("role") == "assistant" and SUBTASK_ANSWER in str(m.get("content", ""))
    ]
    # The subtask's own plan never enters the parent's plan.
    assert all(SUBTASK_MARKER not in str(p.get("description", "")) for p in final["plan"])


# ── event-channel isolation ──────────────────────────────────────────────────
def test_subtask_internal_events_stay_on_subtask_channel(tmp_path, event_bus):
    settings = make_settings(tmp_path)
    mock = _QaResearchMock()
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="qa-iso", user_input="调研 RAG 最新进展并写报告")
    task = _run_until_done(tm, task_id)
    assert task.status.value == "COMPLETED"

    parent_events = event_bus.replay(task_id)
    subtask_id = next(
        e["data"]["output"]["subtask_id"]
        for e in parent_events
        if e["type"] == "tool_result" and e["data"]["tool_name"] == "spawn_subagent"
    )
    sub_events = event_bus.replay(subtask_id)

    # Each side planned for itself: the parent channel carries only the parent's
    # plan, the subtask's own plan stays on its own channel.
    parent_plans = [e["data"]["plan"] for e in parent_events if e["type"] == "plan_update"]
    sub_plans = [e["data"]["plan"] for e in sub_events if e["type"] == "plan_update"]
    assert parent_plans and all(
        SUBTASK_INSTRUCTION_FRAGMENT not in str(p) for plan in parent_plans for p in plan
    ), "子任务的计划回灌进了父频道"
    assert sub_plans and all(
        SUBTASK_INSTRUCTION_FRAGMENT in str(p) for plan in sub_plans for p in plan
    ), "子任务频道上的不是它自己的计划"

    # The subtask's file write is visible only on its own channel.
    assert any(
        e["type"] == "tool_call" and e["data"]["tool_name"] == "write" for e in sub_events
    )
    assert not [
        e
        for e in parent_events
        if e["type"] in ("tool_call", "tool_result") and e["data"]["tool_name"] == "write"
    ]
    # The parent channel only learned that an artifact appeared.
    assert any(e["type"] == "artifact_created" for e in parent_events)


def test_summary_events_land_on_parent_channel_not_subtask_channel(tmp_path, event_bus):
    """#70 裁定 A 重裁（原断「两个频道都不该再出现」，钉的是 #56 的退役契约）。

    父频道补回了这三条汇总事件（载荷与顺序见
    ``test_summary_events_publish_on_the_parent_channel``）；本用例钉没变的那半——
    子任务内部频道依旧只带它自己的轮次，汇总事件不回灌进去。
    """
    settings = make_settings(tmp_path)
    mock = _QaResearchMock()
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="qa-events", user_input="调研 RAG 最新进展并写报告")
    _run_until_done(tm, task_id)

    subtask_id = next(
        e["data"]["output"]["subtask_id"]
        for e in event_bus.replay(task_id)
        if e["type"] == "tool_result" and e["data"]["tool_name"] == "spawn_subagent"
    )
    parent_seen = {e["type"] for e in event_bus.replay(task_id)}
    assert {"subtask_start", "subtask_result"} <= parent_seen
    assert "subtask_failed" not in parent_seen  # 这条研究腿成功了
    sub_seen = {e["type"] for e in event_bus.replay(subtask_id)}
    assert sub_seen.isdisjoint({"subtask_start", "subtask_result", "subtask_failed"})


def test_subtask_failure_returns_through_the_tool_and_keeps_the_parent_alive(
    tmp_path, event_bus, monkeypatch
):
    """子任务崩溃 -> 一次失败的 spawn 工具回传；父任务自己收场，不跟着崩。

    #137 重裁（#46 曾把这里改判成 UNVERIFIED）：验证层退役后不再有机器判决，父任务
    声称完成就是 COMPLETED；本用例的钉子（不崩、失败经工具面回传）一条没少。
    """
    settings = make_settings(tmp_path)
    mock = _SpawnOnceMock()
    tm = make_manager(settings, mock, event_bus=event_bus)
    assert tm._subagent is not None

    def _boom(spec, publish=None):
        raise RuntimeError("subtask worker crashed")

    monkeypatch.setattr(tm._subagent, "_exec_one", _boom)
    task_id = tm.create_task(title="qa-crash", user_input="派个子任务")
    task = _run_until_done(tm, task_id)

    assert task.status.value == "COMPLETED"
    # #70 裁定 A 重裁（原断 == []，钉 #56 的「无生产者」）：崩掉的子任务也如实登一条
    # failed ——面板上「派出去又折回来的失败」必须看得见，不是「暂无子任务」。
    # 「不跟着子任务一起崩」「失败经工具面回传」两条原钉子一条没少。
    assert len(task.subtasks) == 1
    assert task.subtasks[0].status == "failed"
    assert "crashed" in (task.subtasks[0].error or "")
    results = [
        e["data"]
        for e in event_bus.replay(task_id)
        if e["type"] == "tool_result" and e["data"]["tool_name"] == "spawn_subagent"
    ]
    assert results and results[-1]["status"] == "failed"
    assert "crashed" in results[-1]["error"]
    assert mock.subtask_turns == 0  # the crash happened before any subtask round


# ── a subtask's face is a declared tier, never the parent's ──────────────────
class _GatedAskingMock(MockLLMClient):
    """Subtask that asks for a dangerous tool, then settles."""

    def __init__(self):
        super().__init__(plan=["试着执行代码"], final_answer="拿不到执行能力")
        self.turns = 0

    def complete(self, messages, tools=None, **kwargs):
        if not tools:
            return LLMResponse(content=json.dumps(self.plan))
        self.turns += 1
        if self.turns == 1:
            return LLMResponse(
                content="",
                tool_calls=[
                    {"id": "g1", "name": "code_exec", "arguments": {"code": "print(1)"}}
                ],
            )
        return LLMResponse(content=self.final_answer)


def test_subtask_cannot_reach_a_dangerous_tool(tmp_path, event_bus):
    """要问人（或本就危险）的工具必须在子任务面上就不存在。"""
    settings = make_settings(tmp_path)
    mock = _GatedAskingMock()
    tm = make_manager(settings, mock, event_bus=event_bus)
    res = tm._subagent.run_subtask(_spec("qa:sub:gate", "gate", "试着执行代码"))

    # #137 重裁（#46 曾把这里改判成 failed）：验证层退役后，「零成功调用却自称完成」
    # 不再被机器拦住，子任务照常收尾成 completed；「危险工具在子任务面上不存在」
    # 这条钉子没变——工具面拒绝仍在 tool_result 上留痕。
    assert res.status == "completed"
    sub_events = event_bus.replay("qa:sub:gate")
    refused = [e["data"] for e in sub_events if e["type"] == "tool_result"]
    assert refused and refused[0]["status"] == "failed"
    assert "unknown tool" in refused[0]["error"]


def test_subtask_planner_failure_folds_back_as_failed(tmp_path):
    """A planner LLM error inside a subtask must fold back as ``failed`` with
    the real cause — not as a silent ``completed`` with an empty summary.

    Regression for the Issue #12 boundary: the subtask graph now ends FAILED
    (error is set), so the wrapper must read ``final["status"]`` instead of
    hard-coding ``completed``.
    """

    class _FailingPlannerLLM(MockLLMClient):
        """Planner call raises; executor calls behave normally."""

        def complete(self, messages, tools=None, **kwargs):
            if not tools:
                raise RuntimeError("simulated subtask planner 403")
            return super().complete(messages, tools, **kwargs)

    settings = make_settings(tmp_path)
    tm = make_manager(settings, _FailingPlannerLLM(plan=["p"]))

    res = tm._subagent._exec_one(
        _spec("sub:1", "研究子任务", "检索素材", tier=EXPLORE_TIER)
    )

    assert res.status == "failed"
    assert "403" in res.error
    assert res.summary == "(no final answer)"
    assert res.tool_face == tier_face_names([t.name for t in tm._tools], EXPLORE_TIER)


# ── the switch ───────────────────────────────────────────────────────────────
def test_subagent_disabled_removes_the_entry_point(tmp_path, event_bus):
    """``subagent_enabled=False``：入口本身从面上消失。

    #137 重裁（#46 曾把这里改判成 UNVERIFIED）：面上没回归，终态也没有机器判决了——
    唯一的调用被拒成 unknown tool、零成功却声称完成，现在照常收尾成 COMPLETED。
    """
    settings = make_settings(tmp_path, subagent_enabled=False)
    mock = _QaResearchMock()
    tm = make_manager(settings, mock, event_bus=event_bus)
    assert "spawn_subagent" not in {t.name for t in tm._tools}
    assert "spawn_subagent" not in {s["function"]["name"] for s in tm._tool_schemas}

    task_id = tm.create_task(title="qa-off", user_input="调研 RAG 最新进展并写报告")
    task = _run_until_done(tm, task_id)

    assert task.status.value == "COMPLETED"
    assert task.subtasks == []
    types = {e["type"] for e in event_bus.replay(task_id)}
    assert types.isdisjoint({"subtask_start", "subtask_result", "subtask_failed"})
    # The model can still *ask* for the entry point; the kernel refuses it as an
    # unknown tool, and no subtask round ever happens.
    calls = [e["data"] for e in event_bus.replay(task_id) if e["type"] == "tool_call"]
    assert {c["tool_name"] for c in calls} == {"spawn_subagent"}
    results = [e["data"] for e in event_bus.replay(task_id) if e["type"] == "tool_result"]
    assert results and all(
        r["status"] == "failed" and "unknown tool" in r["error"] for r in results
    )
    assert mock.subtask_turns == 0
    assert list(settings.artifacts_path.glob("**/*") if settings.artifacts_path.exists() else []) == []


def test_subagent_enabled_still_shares_one_executor_with_the_tool(tmp_path):
    """The one wiring the tool depends on: ``tm._subagent`` *is* the tool's executor."""
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(plan=["p"], final_answer="d"))
    tool = next(t for t in tm._tools if t.name == "spawn_subagent")
    assert tool.executor is tm._subagent


# ── #70 裁定 A：spawn 路径补回观测生产者 ─────────────────────────────────────
def test_spawned_subtask_lands_on_task_subtasks(tmp_path, event_bus):
    """#70 第一步：一次真实派生 = 父任务记录上一条 ``Task.subtasks``。

    #56 删入口 B 时顺手断了 ``state["subtasks"]`` 的唯一生产者，REST 字段与前端
    ``SubtaskList`` 从此恒空（票面称之为「骗人面」）。裁定 A 把生产者补回 spawn
    路径：``tool_node`` 处理 ``spawn_subagent`` 结果时把折叠回传登进图状态，
    ``_finalize_terminal`` 原样拷进 ``Task.subtasks``。
    """
    settings = make_settings(tmp_path)
    mock = _SpawnOnceMock()
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="qa-obs", user_input="派个子任务给一句话结论")
    task = _run_until_done(tm, task_id)

    assert task.status.value == "COMPLETED"
    assert mock.subtask_turns >= 1
    spawn = [
        e["data"]
        for e in event_bus.replay(task_id)
        if e["type"] == "tool_result" and e["data"]["tool_name"] == "spawn_subagent"
    ]
    assert spawn and spawn[0]["status"] == "success"

    assert len(task.subtasks) == 1
    st = task.subtasks[0]
    assert st.subtask_id == spawn[0]["output"]["subtask_id"]
    assert st.subtask_id.startswith("spawn:sub:")
    assert st.name == "probe"
    assert st.status == "completed"
    assert st.summary == SUBTASK_ANSWER


def test_summary_events_publish_on_the_parent_channel(tmp_path, event_bus):
    """#70 第二步：一次真实派生在父频道 = 一条 ``subtask_start`` + 一条 ``subtask_result``。

    事件面随裁定 A 恢复词汇表：``SubAgentExecutor.run_subtask`` 在父线程上发布——
    提交池之前发 start（status=running），回折之后按结果发终态。时间线顺序是
    「派出 → 回传 → spawn 的 tool_result」，载荷沿用入口 B 当年的键名。
    """
    settings = make_settings(tmp_path)
    mock = _SpawnOnceMock()
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="qa-obs-ev", user_input="派个子任务给一句话结论")
    _run_until_done(tm, task_id)

    events = event_bus.replay(task_id)
    types = [e["type"] for e in events]
    assert types.count("subtask_start") == 1
    assert types.count("subtask_result") == 1
    assert "subtask_failed" not in types
    start = next(e for e in events if e["type"] == "subtask_start")
    result = next(e for e in events if e["type"] == "subtask_result")
    spawn_result = next(
        e
        for e in events
        if e["type"] == "tool_result" and e["data"]["tool_name"] == "spawn_subagent"
    )
    assert events.index(start) < events.index(result) < events.index(spawn_result)

    assert start["data"]["subtask_id"].startswith("spawn:sub:")
    assert start["data"]["name"] == "probe"
    assert start["data"]["status"] == "running"
    assert start["data"]["parent_task_id"] == task_id
    assert result["data"]["subtask_id"] == start["data"]["subtask_id"]
    assert result["data"]["status"] == "completed"
    assert result["data"]["summary"] == SUBTASK_ANSWER
    assert result["data"]["artifacts"] == []
