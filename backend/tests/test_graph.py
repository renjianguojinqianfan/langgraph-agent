"""Tests for the LangGraph orchestration kernel.

Exercises ``build_graph`` + :class:`AgentRuntime` end-to-end with a scripted
:class:`MockLLMClient` (no LLM key, no network):

* the graph compiles and *declares* its topology (langgraph 1.x derives each
  branch's ``path_map`` from the router's ``Literal`` return annotation);
* one ``agent`` node, one round = no tool call (issue #139 / ADR 0008);
* a finished task is parked as ``INTERRUPTED`` — there is no COMPLETED;
* ``stop`` interrupts the loop within 2 seconds (P0-9);
* an LLM error is surfaced as ``FAILED``, not degraded.

Also wraps the engineer's offline smoke test so it runs under pytest.
"""

from __future__ import annotations

import time

from backend.core.agent.graph import (
    _after_agent,
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
        if task and task.status.value in ("FAILED", "INTERRUPTED"):
            return task
        time.sleep(0.05)
    return tm.get_task(task_id)


# ── build / compile ──
def test_build_graph_compiles(settings):
    graph = build_graph(_runtime())
    assert graph is not None


# ── langgraph 1.x: 拓扑静态声明（Issue #7 迁移）──
def test_topology_is_one_agent_loop_and_statically_declared(settings):
    """1.x 从路由函数的 ``Literal`` 返回注解推导 path_map。

    推导失败（lambda / 漏注解）时 langgraph 不知道条件边去哪，编译出的图就
    声明不出真实分支：运行照跑，但拓扑不再可读，可视化与静态校验全部失效。
    这条测试把“声明出来了”钉住，免得日后改回 lambda 静默退化。
    """
    compiled = build_graph(_runtime())
    edges = _declared_edges(compiled)
    nodes = set(compiled.get_graph().nodes)

    # #139（S5 / ADR 0008 决策 2）：一个 loop、一个 agent 节点。
    assert ("agent", "tool") in edges
    assert ("agent", "finish") in edges
    assert ("tool", "agent") in edges
    assert ("tool", "finish") in edges
    # agent 不可能直达 finish 之外的终点；tool 不可能直达 tool。
    assert ("agent", "agent") not in edges
    assert ("tool", "tool") not in edges
    # 合并掉的旧节点与 #56 / #137 / #138 删掉的节点都不该再出现。
    for gone in ("planner", "executor", "reflect", "subagent_split", "risk_scan", "human_confirm"):
        assert gone not in nodes
        assert all(gone not in (src, dst) for src, dst in edges)


def test_after_agent_router_ends_the_round_on_no_tool_call():
    """一轮的结束 = 这一轮没有工具调用（ADR 0008 决策 4）。"""
    assert _after_agent({"_last_action": "tool_call"}) == "tool"
    assert _after_agent({"_last_action": "final_answer"}) == "finish"
    assert _after_agent({"stop_requested": True, "_last_action": "tool_call"}) == "finish"
    assert _after_agent({"error": "boom"}) == "finish"


def test_tool_router_spends_the_round_budget_into_finish():
    """步数预算按轮计：预算未尽回 agent，用尽即 finish；stop 优先于预算。"""
    router = _tool_router(_runtime(max_steps=3))
    assert router({"round_steps": 2, "_last_action": "tool_done"}) == "agent"
    assert router({"round_steps": 3}) == "finish"
    assert router({"round_steps": 99, "stop_requested": True}) == "finish"


def test_build_mode_runs_and_parks_without_planning(settings, event_bus):
    """默认执行模式：不自动出计划；答案即「停下」，不是「完成」。"""
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
    assert task.status.value == "INTERRUPTED"
    assert task.mode.value == "build"
    assert "out.txt" in task.final_answer
    assert (settings.artifacts_path / "out.txt").is_file()

    events = {e["type"] for e in event_bus.replay(task_id)}
    # #139: build mode never re-plans behind the user's back (the old planner
    # ran on every round). The plan face only fills in plan mode.
    assert "plan_update" not in events
    assert "tool_call" in events
    assert "tool_result" in events
    assert "final_answer" in events
    assert "task_interrupted" in events


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

    interrupted = [e for e in event_bus.replay(task_id) if e["type"] == "task_interrupted"]
    # One stop, one event — stop() claims the announcement and finalize stays quiet.
    assert len(interrupted) == 1


# ── LLM failure is surfaced, not degraded ──
class _FailingLLM(MockLLMClient):
    """Every agent call raises, in either mode."""

    def complete(self, messages, tools=None, **kwargs):
        raise RuntimeError("simulated provider 403")


def test_agent_llm_failure_fails_the_task(settings, event_bus):
    """An LLM error must land in ``state["error"]`` -> FAILED, and the terminal
    persist step must not crash on an empty/partial plan."""
    tm = make_manager(settings, _FailingLLM(plan=["p"]), event_bus=event_bus)
    task_id = tm.create_task(title="llm-fail", user_input="do something")

    task = _run_until_done(tm, task_id)

    assert task is not None
    assert task.status.value == "FAILED"
    assert "403" in (task.error or "")
    assert task.plan == []


# ── engineer smoke (offline) included in the unified run ──
def test_engineer_smoke_passes(tmp_path):
    # Issue #11: inject an isolated make_settings(tmp_path) so the smoke run
    # writes tasks.json / artifacts under pytest's temp dir, never real data/.
    assert smoke_main(make_settings(tmp_path)) == 0
