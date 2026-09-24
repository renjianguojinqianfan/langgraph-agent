"""P1 item 2 — sub-agent collaboration tests (fully offline).

Covers the built-in scenario splitter, end-to-end isolation (parent channel /
messages never polluted by subtask internals), parallel vs serial scheduling,
the anti-recursion tool filtering, and the ``spawn_subagent`` tool wiring.
"""

from __future__ import annotations

import json
import time

from backend.core.agent.graph import build_graph
from backend.core.agent.nodes import AgentRuntime
from backend.core.agent.subagent import (
    DEFAULT_SPLIT_SCENARIOS,
    SubAgentExecutor,
    SubTaskResult,
    SubTaskSpec,
    split_plan_for_scenario,
)
from backend.core.llm.client import LLMResponse, MockLLMClient
from backend.services.snapshots import set_current_task_id
from backend.tests.conftest import make_manager, make_settings
from backend.tests.test_graph import _run_until_done


def test_split_plan_detects_research_scenario():
    specs = split_plan_for_scenario("调研 RAG 最新进展并写报告", [])
    assert len(specs) == 2
    assert specs[0].name == "研究子任务"
    assert specs[1].name == "写作子任务"
    assert specs[0].instruction and specs[1].instruction


def test_split_plan_returns_empty_for_other_tasks():
    assert split_plan_for_scenario("帮我算一下 1+1", []) == []


def test_default_scenarios_nonempty():
    assert len(DEFAULT_SPLIT_SCENARIOS) >= 1


class _ResearchMock(MockLLMClient):
    """Parent final-answers immediately; research subtask calls file_io once;
    writing subtask final-answers directly."""

    def __init__(self):
        super().__init__(plan=["调研", "写作"], final_answer="父任务完成")

    def complete(self, messages, tools=None, **kwargs):
        user = " ".join(
            str(m.get("content") or "") for m in messages if m.get("role") == "user"
        )
        if not tools:
            return LLMResponse(content=json.dumps(self.plan))
        if "检索" in user:
            if self._executor_turn == 0:
                self._executor_turn += 1
                return LLMResponse(
                    content="",
                    tool_calls=[
                        {
                            "id": "sub1",
                            "name": "file_io",
                            "arguments": {
                                "action": "write",
                                "path": "research_notes.txt",
                                "content": "RAG notes",
                            },
                        }
                    ],
                )
            return LLMResponse(content="研究完成")
        if "撰写" in user:
            return LLMResponse(content="报告已完成")
        return LLMResponse(content="父任务完成")


def test_subtask_end_to_end_isolation(tmp_path, event_bus):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, _ResearchMock(), event_bus=event_bus)
    task_id = tm.create_task(title="t", user_input="调研 RAG 最新进展并写报告")
    task = _run_until_done(tm, task_id)

    assert task.status.value == "COMPLETED"
    assert len(task.subtasks) == 2

    events = event_bus.replay(task_id)
    types = {e["type"] for e in events}
    assert "subtask_start" in types
    assert "subtask_result" in types

    # Parent channel never sees the subtask's internal tool_call.
    parent_tool_calls = [e["data"] for e in events if e["type"] == "tool_call"]
    assert all(e.get("tool_name") != "file_io" for e in parent_tool_calls)

    # The subtask channel does carry the internal tool_call (isolation).
    sub_id = task.subtasks[0].subtask_id
    sub_events = event_bus.replay(sub_id)
    assert any(
        e["type"] == "tool_call" and e["data"]["tool_name"] == "file_io"
        for e in sub_events
    )

    # Parent artifacts include the subtask-produced file (registered by path).
    assert any("research_notes.txt" in a.filename for a in task.artifacts)


class _SleepingMock(MockLLMClient):
    def __init__(self, sleep=0.4):
        super().__init__(plan=["调研"], final_answer="done")
        self.sleep = sleep

    def complete(self, messages, tools=None, **kwargs):
        time.sleep(self.sleep)
        return super().complete(messages, tools, **kwargs)


def test_parallel_subtasks_overlap(tmp_path):
    settings = make_settings(tmp_path, subagent_max_concurrency=2)
    tm = make_manager(settings, _SleepingMock(sleep=0.4))
    ex = SubAgentExecutor(tm, settings)

    start = time.time()
    results = ex.run_plan_with_subtasks("parent", "调研 RAG 并写报告", [])
    elapsed = time.time() - start

    assert len(results) == 2
    assert all(r.status == "completed" for r in results)
    # Two subtasks each sleep ~0.8s of LLM calls; with concurrency 2 the wave
    # takes ~0.8s, not ~1.6s.
    assert elapsed < 1.2, f"subtasks did not overlap (elapsed={elapsed:.2f}s)"


def test_serial_subtasks_when_concurrency_1(tmp_path):
    settings = make_settings(tmp_path, subagent_max_concurrency=1)
    tm = make_manager(settings, _SleepingMock(sleep=0.4))
    ex = SubAgentExecutor(tm, settings)

    start = time.time()
    results = ex.run_plan_with_subtasks("parent", "调研 RAG 并写报告", [])
    elapsed = time.time() - start

    assert len(results) == 2
    assert elapsed >= 1.4, f"subtasks ran in parallel despite concurrency=1 (elapsed={elapsed:.2f}s)"


def test_subtask_tools_exclude_spawn(tmp_path):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(plan=["p"], final_answer="d"))
    ex = SubAgentExecutor(tm, settings)
    names = {t.name for t in ex._subtask_tools()}
    assert "spawn_subagent" not in names  # anti-recursion
    assert "web_search" in names  # still shares the shared tool set


def test_subtask_graph_compiles_simplified(tmp_path):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(plan=["p"], final_answer="d"))
    runtime = AgentRuntime(
        task_id="sub",
        task_manager=tm,
        llm=tm._llm,
        tools=tm._tools,
        tool_schemas=tm._tool_schemas,
        confirm_enabled=False,
        subagent_executor=None,
    )
    graph = build_graph(runtime, mode="subtask")
    assert graph is not None


def test_run_subtask_single_spec(tmp_path):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(plan=["p"], final_answer="single done"))
    ex = SubAgentExecutor(tm, settings)
    spec = SubTaskSpec(subtask_id="x:sub:1", name="x", instruction="do it")
    res = ex.run_subtask(spec)
    assert res.status == "completed"
    assert "single done" in res.summary


def test_spawn_subagent_tool_wired_and_runs(tmp_path):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(plan=["p"], final_answer="spawned done"))
    tool = next(t for t in tm._tools if t.name == "spawn_subagent")
    assert tool.executor is not None
    res = tool.run(name="research", instruction="collect facts")
    assert res.success is True
    assert res.data["status"] == "completed"
    assert "spawned done" in res.data["summary"]


class _KeepLoopingMock(MockLLMClient):
    """Executor never converges: one tool call per turn, counted.

    Used to prove a stop signal actually short-circuits the loop — an unfixed
    child runs every ``max_steps`` turn, a stopped child barely calls the LLM.
    """

    def __init__(self) -> None:
        super().__init__(plan=["p"], final_answer="never reached")
        self.executor_calls = 0

    def complete(self, messages, tools=None, **kwargs):
        if not tools:
            return LLMResponse(content=json.dumps(self.plan))
        self.executor_calls += 1
        return LLMResponse(
            content="",
            tool_calls=[
                {
                    "id": f"c{self.executor_calls}",
                    "name": "file_io",
                    "arguments": {"action": "write", "path": "loop.txt", "content": "x"},
                }
            ],
        )


def _run_stopped_subtask(tmp_path, stop_key: str, parent_task_id: str):
    settings = make_settings(tmp_path)
    mock = _KeepLoopingMock()
    tm = make_manager(settings, mock)
    tm._stop_flags[stop_key] = True  # the authoritative signal nodes poll
    ex = SubAgentExecutor(tm, settings)
    spec = SubTaskSpec(
        subtask_id="p60:sub:1",
        name="looping",
        instruction="keep going",
        parent_task_id=parent_task_id,
    )
    return ex.run_subtask(spec), mock


def test_parent_stop_propagates_to_subtask(tmp_path):
    """Flagging the PARENT must stop the child run (Issue #60)."""
    res, mock = _run_stopped_subtask(tmp_path, stop_key="parent-1", parent_task_id="parent-1")
    assert res.status != "completed"
    assert mock.executor_calls < 5, f"child ignored the parent stop: {mock.executor_calls} turns"


def test_direct_stop_on_subtask_id_still_works(tmp_path):
    """Propagation must not replace the child's own stop key (single-parent trap)."""
    res, mock = _run_stopped_subtask(tmp_path, stop_key="p60:sub:1", parent_task_id="parent-1")
    assert res.status != "completed"
    assert mock.executor_calls < 5


def test_spawn_subagent_records_parent_task_id(tmp_path):
    """The spawn path runs on the parent's thread, so the parent id is available
    and must land on the spec — it is the only mapping left once入口 B is gone."""
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(plan=["p"], final_answer="x"))
    tool = next(t for t in tm._tools if t.name == "spawn_subagent")
    captured: dict = {}

    class _StubExecutor:
        def run_subtask(self, spec, publish=None):
            captured["spec"] = spec
            return SubTaskResult(
                subtask_id=spec.subtask_id,
                name=spec.name,
                status="completed",
                summary="ok",
                artifacts=[],
                error="",
            )

    tool.executor = _StubExecutor()
    set_current_task_id("parent-9")
    try:
        res = tool.run(name="research", instruction="collect facts")
    finally:
        set_current_task_id(None)
    assert res.success is True
    assert captured["spec"].parent_task_id == "parent-9"


def test_subtask_failure_does_not_crash_parent(tmp_path):
    settings = make_settings(tmp_path)

    class _CrashMock(MockLLMClient):
        def complete(self, messages, tools=None, **kwargs):
            if not tools:
                return LLMResponse(content=json.dumps(self.plan))
            raise RuntimeError("boom")

    tm = make_manager(settings, _CrashMock(plan=["调研"], final_answer="never"))
    ex = SubAgentExecutor(tm, settings)
    results = ex.run_plan_with_subtasks("parent", "调研 X 并写报告", [])
    assert len(results) == 2
    # The executor catches per-subtask crashes and folds them as failures.
    assert all(r.status in ("completed", "failed") for r in results)
