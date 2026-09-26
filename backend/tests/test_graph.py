"""Tests for the LangGraph orchestration kernel.

Exercises ``build_graph`` + :class:`AgentRuntime` end-to-end with a scripted
:class:`MockLLMClient` (no LLM key, no network):

* the graph compiles;
* the compiled graph *declares* its topology (langgraph 1.x derives each
  branch's ``path_map`` from the router's ``Literal`` return annotation);
* a full task produces a ``final_answer`` and a persisted artifact;
* ``stop`` interrupts the loop within 2 seconds (P0-9);
* the ``human_confirm`` node pauses *before* a dangerous (requires_confirm) tool
  and only proceeds after approval (and skips after rejection) (P1-2).

Also wraps the engineer's offline smoke test so it runs under pytest.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from backend.core.agent.graph import _after_planner_subtask, build_graph
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


def _auto_confirm_in_background(tm, event_bus, task_id, approved, timeout=10):
    """Poll the event buffer and confirm/reject any pending confirmation."""

    def _watcher():
        deadline = time.time() + timeout
        while time.time() < deadline:
            for ev in event_bus.replay(task_id):
                if ev["type"] == "human_confirm_required":
                    tm.confirm(task_id, ev["data"]["tool_call_id"], approved)
                    return
            time.sleep(0.01)

    t = threading.Thread(target=_watcher, daemon=True)
    t.start()
    return t


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

    assert ("planner", "risk_scan") in edges
    assert ("planner", "finish") in edges
    # Issue #56: the keyword-triggered ``subagent_split`` node is gone, so the
    # risk scan hands straight to the executor.
    assert ("risk_scan", "executor") in edges
    assert "subagent_split" not in set(compiled.get_graph().nodes)
    for src, dst in edges:
        assert "subagent_split" not in (src, dst)
    assert ("executor", "human_confirm") in edges
    assert ("executor", "tool") in edges
    assert ("tool", "reflect") in edges
    assert ("reflect", "planner") in edges
    # 确认闸门之后是静态边（总是去 tool），不是条件边。
    assert ("human_confirm", "tool") in edges
    # planner 不可能直达 tool：这条边一旦出现，就说明 path_map 退化成了
    # “可能指向任何节点”。
    assert ("planner", "tool") not in edges


def test_subtask_topology_declares_no_gate_and_no_risk(settings):
    """子任务图的存在意义就是“不可能递归拆分、不可能卡在确认闸”。"""
    edges = _declared_edges(build_graph(_runtime(), mode="subtask"))

    assert ("planner", "executor") in edges
    assert ("executor", "tool") in edges
    assert ("tool", "reflect") in edges
    for src, dst in edges:
        assert src not in ("human_confirm", "risk_scan", "subagent_split")
        assert dst not in ("human_confirm", "risk_scan", "subagent_split")


def test_unknown_mode_is_rejected(settings):
    """静默退回 subtask 拓扑会丢掉风险扫描与确认闸门，必须响亮报错。"""
    with pytest.raises(ValueError):
        build_graph(_runtime(), mode="nonsense")


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


# ── human confirmation (P1-2) ──
def test_human_confirm_pauses_then_proceeds_on_approval(settings, event_bus):
    mock = MockLLMClient(
        plan=["p"],
        tool_calls=[
            {
                "id": "cc1",
                "name": "code_exec",
                "arguments": {"language": "python", "code": "print(1 + 1)"},
            }
        ],
        final_answer="Ran the code.",
    )
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="code", user_input="run python")
    _auto_confirm_in_background(tm, event_bus, task_id, approved=True)

    task = _run_until_done(tm, task_id)

    assert task.status.value == "COMPLETED"
    events = event_bus.replay(task_id)
    assert "human_confirm_required" in {e["type"] for e in events}
    # The code_exec tool actually executed (approval granted).
    tool_results = [e["data"] for e in events if e["type"] == "tool_result"]
    assert any(
        r.get("tool_name") == "code_exec" and r.get("status") == "success"
        for r in tool_results
    )


def test_human_confirm_skips_tool_on_rejection(settings, event_bus):
    mock = MockLLMClient(
        plan=["p"],
        tool_calls=[
            {
                "id": "cc1",
                "name": "code_exec",
                "arguments": {"language": "python", "code": "print(1 + 1)"},
            }
        ],
        final_answer="Skipped the code.",
    )
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="code", user_input="run python")
    _auto_confirm_in_background(tm, event_bus, task_id, approved=False)

    task = _run_until_done(tm, task_id)

    assert task.status.value == "COMPLETED"
    events = event_bus.replay(task_id)
    assert "human_confirm_required" in {e["type"] for e in events}
    tool_results = [e["data"] for e in events if e["type"] == "tool_result"]
    assert any(
        r.get("tool_name") == "code_exec" and r.get("status") == "skipped"
        for r in tool_results
    )


def test_openapi_write_operation_parks_at_the_gate(tmp_path, event_bus):
    """A spec-generated POST must be gated end to end, not just by its flag (#59)."""
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "pets", "version": "1"},
        "paths": {
            "/pets": {
                "post": {
                    "operationId": "createPet",
                    "responses": {"200": {"description": "ok"}},
                },
            },
        },
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    st = make_settings(tmp_path / "store", openapi_enabled=True, openapi_spec_path=str(spec_path))

    mock = MockLLMClient(
        plan=["p"],
        tool_calls=[{"id": "cc1", "name": "createPet", "arguments": {"name": "Rex"}}],
        final_answer="Not sent.",
    )
    tm = make_manager(st, mock, event_bus=event_bus)
    task_id = tm.create_task(title="post", user_input="create a pet")
    _auto_confirm_in_background(tm, event_bus, task_id, approved=False)

    task = _run_until_done(tm, task_id)

    assert task.status.value == "COMPLETED"
    events = event_bus.replay(task_id)
    assert "human_confirm_required" in {e["type"] for e in events}
    tool_results = [e["data"] for e in events if e["type"] == "tool_result"]
    assert any(
        r.get("tool_name") == "createPet" and r.get("status") == "skipped"
        for r in tool_results
    )


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


def test_subtask_planner_failure_short_circuits_to_finish():
    """The subtask router must also skip the executor on a planner failure
    (Issue #12: both topologies share the planner node)."""
    assert _after_planner_subtask({"error": "planner boom"}) == "finish"
    assert _after_planner_subtask({"stop_requested": True}) == "finish"
    assert _after_planner_subtask({}) == "executor"


# ── engineer smoke (offline) included in the unified run ──
def test_engineer_smoke_passes(tmp_path):
    # Issue #11: inject an isolated make_settings(tmp_path) so the smoke run
    # writes tasks.json / artifacts under pytest's temp dir, never real data/.
    assert smoke_main(make_settings(tmp_path)) == 0
