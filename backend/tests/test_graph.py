"""Tests for the LangGraph orchestration kernel.

Exercises ``build_graph`` + :class:`AgentRuntime` end-to-end with a scripted
:class:`MockLLMClient` (no LLM key, no network):

* the graph compiles;
* the compiled graph *declares* its topology (langgraph 1.x derives each
  branch's ``path_map`` from the router's ``Literal`` return annotation);
* a full task produces a ``final_answer`` and a persisted artifact;
* ``stop`` interrupts the loop within 2 seconds (P0-9).

Also wraps the engineer's offline smoke test so it runs under pytest.
"""

from __future__ import annotations

import time

from backend.core.agent.graph import (
    _after_planner,
    _tool_router,
    build_graph,
)
from backend.core.agent.nodes import AgentRuntime
from backend.core.llm.client import MockLLMClient
from backend.tests.conftest import make_manager, make_settings
from backend.tests.test_smoke import main as smoke_main


def _runtime(max_steps: int = 5) -> AgentRuntime:
    """A runtime good enough to compile a graph (no LLM, no tools)."""
    return AgentRuntime(
        task_id="t",
        task_manager=None,
        llm=None,
        tools=[],
        tool_schemas=[],
        max_steps=max_steps,
    )


def _declared_edges(graph) -> set:
    """The topology the compiled graph *declares* (source, target) pairs."""
    return {(e.source, e.target) for e in graph.get_graph().edges}


def _run_until_done(tm, task_id, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = tm.get_task(task_id)
        if task and task.status.value in ("COMPLETED", "FAILED", "INTERRUPTED"):
            return task
        time.sleep(0.05)
    return tm.get_task(task_id)


# ── build / compile ──
def test_build_graph_compiles(settings):
    graph = build_graph(_runtime())
    assert graph is not None


# ── langgraph 1.x: 拓扑静态声明（Issue #7 迁移）──
def test_main_topology_is_statically_declared(settings):
    """1.x 从路由函数的 ``Literal`` 返回注解推导 path_map。

    推导失败（lambda / 漏注解）时 langgraph 不知道条件边去哪，编译出的图就
    声明不出真实分支：运行照跑，但拓扑不再可读，可视化与静态校验全部失效。
    这条测试把“声明出来了”钉住，免得日后改回 lambda 静默退化。
    """
    compiled = build_graph(_runtime())
    edges = _declared_edges(compiled)
    nodes = set(compiled.get_graph().nodes)

    assert ("planner", "executor") in edges
    assert ("planner", "finish") in edges
    assert ("executor", "tool") in edges
    assert ("executor", "finish") in edges
    assert ("tool", "planner") in edges
    assert ("tool", "finish") in edges
    # planner 不可能直达 tool：这条边一旦出现，就说明 path_map 退化成了
    # “可能指向任何节点”。
    assert ("planner", "tool") not in edges
    # #56 dropped the keyword-triggered ``subagent_split``; #138 (S4) dropped the
    # ``risk_scan`` node and the ``human_confirm`` gate. Main tasks and subtasks
    # now share this one topology (``build_graph`` has no ``mode``).
    for gone in ("subagent_split", "risk_scan", "human_confirm"):
        assert gone not in nodes
        assert all(gone not in (src, dst) for src, dst in edges)


def test_tool_router_spends_the_step_budget_into_finish():
    """步数预算路由（#137 把它从 reflect 路由挪进 tool 路由）：预算未尽回 planner，
    用尽即 finish；stop 优先于预算。"""
    router = _tool_router(_runtime(max_steps=3))
    assert router({"step_index": 2, "_last_action": "tool_done"}) == "planner"
    assert router({"step_index": 3}) == "finish"
    assert router({"step_index": 99, "stop_requested": True}) == "finish"


def test_build_graph_runs_and_produces_final_answer(settings, event_bus):
    mock = MockLLMClient(
        plan=["Plan and act"],
        tool_calls=[
            {
                "id": "c1",
                "name": "write",
                "arguments": {"path": "out.txt", "content": "result"},
            }
        ],
        final_answer="I wrote out.txt.",
    )
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="demo", user_input="write a file")
    task = _run_until_done(tm, task_id)

    assert task is not None
    assert task.status.value == "COMPLETED"
    assert "out.txt" in task.final_answer
    assert any(a.filename == "out.txt" for a in task.artifacts)

    events = {e["type"] for e in event_bus.replay(task_id)}
    assert "plan_update" in events
    assert "tool_call" in events
    assert "tool_result" in events
    assert "artifact_created" in events
    assert "final_answer" in events
    assert "task_completed" in events


# ── stop within 2s (P0-9) ──
class _SlowMockLLMClient(MockLLMClient):
    """Mock client that sleeps a little each call so the run stays RUNNING
    long enough for us to exercise the stop signal."""

    def complete(self, messages, tools=None, **kwargs):
        time.sleep(0.15)
        return super().complete(messages, tools, **kwargs)


def test_stop_interrupts_loop_within_two_seconds(settings, event_bus):
    mock = _SlowMockLLMClient(
        plan=["p"],
        tool_calls=[
            {"id": f"c{i}", "name": "write", "arguments": {"path": f"f{i}.txt", "content": "x"}}
            for i in range(8)
        ],
        final_answer="never reached",
    )
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="slow", user_input="do many steps")

    # Wait until the task is actually RUNNING, then stop.
    stop_at = None
    deadline = time.time() + 10
    while time.time() < deadline:
        task = tm.get_task(task_id)
        if task and task.status.value == "RUNNING":
            stop_at = time.time()
            tm.stop(task_id)
            break
        if task and task.status.value not in ("PENDING", "RUNNING"):
            break
        time.sleep(0.02)

    task = _run_until_done(tm, task_id)
    done_at = time.time()

    assert stop_at is not None, "task never entered RUNNING state for stop test"
    assert task.status.value == "INTERRUPTED"
    # The interrupt must take effect well within the 2s requirement.
    assert (done_at - stop_at) <= 2.0

    events = {e["type"] for e in event_bus.replay(task_id)}
    assert "task_interrupted" in events


# ── planner failure is surfaced, not degraded (Issue #12) ──
class _FailingPlannerLLM(MockLLMClient):
    """Planner call raises; executor calls behave normally."""

    def complete(self, messages, tools=None, **kwargs):
        if not tools:
            raise RuntimeError("simulated planner provider 403")
        return super().complete(messages, tools, **kwargs)


def test_planner_failure_surfaces_error_and_fails_task(settings, event_bus):
    """A planner LLM error must land in ``state["error"]`` -> FAILED, and the
    terminal persist step must not crash on a malformed plan.

    Regression for Issue #12: the old code degraded the error into a string
    plan item and never set ``state["error"]``, so ``_finalize_terminal``
    raised ``TypeError`` on ``PlanStep(**p)`` and the real cause was lost.
    """
    tm = make_manager(settings, _FailingPlannerLLM(plan=["p"]), event_bus=event_bus)
    task_id = tm.create_task(title="planner-fail", user_input="do something")

    task = _run_until_done(tm, task_id)

    assert task is not None
    assert task.status.value == "FAILED"
    assert "403" in (task.error or "")
    assert task.plan == []


def test_planner_failure_short_circuits_to_finish():
    """The planner router skips the executor on a stop or a planner failure
    (Issue #12)."""
    assert _after_planner({"error": "planner boom"}) == "finish"
    assert _after_planner({"stop_requested": True}) == "finish"
    assert _after_planner({}) == "executor"


# ── engineer smoke (offline) included in the unified run ──
def test_engineer_smoke_passes(tmp_path):
    # Issue #11: inject an isolated make_settings(tmp_path) so the smoke run
    # writes tasks.json / artifacts under pytest's temp dir, never real data/.
    assert smoke_main(make_settings(tmp_path)) == 0
