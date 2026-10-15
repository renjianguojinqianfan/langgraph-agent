"""LangGraph ``StateGraph`` construction (langgraph 1.x idioms).

Wires the :class:`~backend.core.agent.nodes.AgentRuntime` node methods into a
single graph with conditional edges implementing the
``Planner -> RiskScan -> Executor -> Tool`` loop, the ``human_confirm``
interrupt, and graceful ``finish`` on completion / failure / stop.

#137 (slice S3) removed the ``reflect`` node: a claimed completion now goes
straight to ``finish``, and a finished tool round loops back to ``planner``
directly. The step budget that used to live in the reflect router moved into
the tool router, which is where the loop-back now happens.

Two build modes (P1 item 2):

* ``mode="main"`` (default) — full topology including ``risk_scan`` and
  ``human_confirm``;
* ``mode="subtask"`` — simplified topology (planner -> executor -> tool ->
  finish) with **no** risk / confirm nodes; used by
  :class:`~backend.core.agent.subagent.SubAgentExecutor` so subtasks can never
  recurse or block on confirmations that cannot route back to the parent. Their
  tool face is a declared tier instead (issue #56), which is what keeps a
  confirmation-less subtask from being a way to skip the gate.

Issue #56 removed the third main-mode node, ``subagent_split``: it dispatched
subtasks on a keyword match, i.e. a side effect no one had approved.

langgraph 1.x conventions used here (Issue #7 migration; the measurements
behind each one are in ``docs/migration-langgraph-1x.md``):

* the entry point is a plain ``add_edge(START, ...)`` — 1.x's
  ``set_entry_point`` is exactly that call and nothing more;
* every router is a **named** function whose return type is a ``Literal`` of
  its real destinations. 1.x derives the branch's ``path_map`` from that
  annotation, so the compiled graph declares its own topology instead of
  leaving langgraph to assume the edge could go anywhere (verified: an
  unannotated router or a ``lambda`` loses the declaration);
* ``human_confirm`` always goes to ``tool``, so it is a static ``add_edge``
  rather than a conditional edge wrapping a constant router;
* main and subtask have their own routers — the subtask topology has no
  ``human_confirm`` node, and one shared router would derive a ``path_map``
  pointing at a node that does not exist in that mode;
* a router that needs the runtime (the step budget) is a factory returning a
  named, ``Literal``-annotated function — the annotation survives the factory
  (verified).
"""

from __future__ import annotations

from typing import Any, Callable, Literal

from langgraph.graph import END, START, StateGraph

from .nodes import AgentRuntime
from .state import AgentState

# ── routers: main topology ───────────────────────────────────────────────────
#
# Unqualified names are the main topology (the default mode); ``_subtask``
# suffixed ones are the simplified variant. The tool routers are factories (they
# need ``runtime.max_steps``), and their inner functions keep the names the 0.2
# build used (``_after_tool`` / ``_after_tool_subtask``).


def _after_planner(s: AgentState) -> Literal["finish", "risk_scan"]:
    """Planner -> risk scan, unless stopped or planning failed (Issue #12).

    A planner LLM error sets ``state["error"]``; short-circuiting to finish
    reports FAILED with the real cause instead of running the risk/exec flow
    on an empty plan.
    """
    if s.get("stop_requested") or s.get("error"):
        return "finish"
    return "risk_scan"


def _after_risk_scan(s: AgentState) -> Literal["finish", "executor"]:
    """Risk scan -> executor, unless a stop was requested."""
    return "finish" if s.get("stop_requested") else "executor"


def _after_executor(s: AgentState) -> Literal["finish", "human_confirm", "tool"]:
    """Executor -> finish on a final answer, else the confirm gate or the tool.

    #137: a claimed completion goes straight to ``finish`` — nothing verifies it
    any more, so there is no reflect node to pass through.
    """
    if s.get("stop_requested"):
        return "finish"
    if s.get("_last_action") == "final_answer":
        return "finish"
    if s.get("_needs_confirm"):
        return "human_confirm"
    return "tool"


def _tool_router(
    runtime: AgentRuntime,
) -> Callable[[AgentState], Literal["finish", "human_confirm", "planner"]]:
    """Bind the runtime into the main tool router (the step budget lives here).

    A factory rather than a closure inside :func:`build_graph`, so the router
    stays a named, ``Literal``-annotated function: 1.x reads the annotation off
    it to declare the branch statically (verified to work through a factory).
    It captures ``runtime`` — not ``runtime.max_steps`` — so the budget is read
    at routing time. Until #137 that budget sat in the reflect router; the tool
    node is where a finished round loops back now, so it moved here.
    """

    def _after_tool(s: AgentState) -> Literal["finish", "human_confirm", "planner"]:
        """Tool -> planner, or back to the confirm gate while items stay pending.

        This re-check is the other half of the P0 dead-loop fix: the authoritative
        part is ``human_confirm_node`` recomputing ``_needs_confirm`` from
        ``_confirmed_ids`` / ``_rejected_ids`` after every decision (nodes.py — do
        not touch), which is what keeps this edge from re-entering the gate forever.
        """
        if s.get("stop_requested"):
            return "finish"
        if s.get("_needs_confirm"):
            return "human_confirm"
        if s.get("step_index", 0) >= runtime.max_steps:
            return "finish"
        return "planner"

    return _after_tool


# ── routers: subtask topology (no risk / confirm) ────────────────────────────


def _after_planner_subtask(s: AgentState) -> Literal["finish", "executor"]:
    """Subtask planner goes straight to the executor — no risk scan, no gate.

    A planner failure short-circuits to finish just like the main topology
    (Issue #12): running the executor on an empty plan would waste a call and
    cannot recover the failed planning step.
    """
    if s.get("stop_requested") or s.get("error"):
        return "finish"
    return "executor"


def _after_executor_subtask(s: AgentState) -> Literal["finish", "tool"]:
    """Like the main executor router minus the confirm gate (subtasks never
    block on a human decision that cannot route back to the parent)."""
    if s.get("stop_requested"):
        return "finish"
    if s.get("_last_action") == "final_answer":
        return "finish"
    return "tool"


def _tool_router_subtask(
    runtime: AgentRuntime,
) -> Callable[[AgentState], Literal["finish", "planner"]]:
    """Subtask tool loops back to the planner until the step budget runs out."""

    def _after_tool_subtask(s: AgentState) -> Literal["finish", "planner"]:
        if s.get("stop_requested"):
            return "finish"
        if s.get("step_index", 0) >= runtime.max_steps:
            return "finish"
        return "planner"

    return _after_tool_subtask


def build_graph(runtime: AgentRuntime, mode: str = "main", checkpointer: Any | None = None):
    """Compile and return the agent graph for ``runtime``.

    ``mode`` is ``"main"`` (default, full topology) or ``"subtask"``
    (simplified, no risk / confirm / subagent recursion). Anything else is a
    programming error and raises: silently falling back to the subtask topology
    would drop the risk scan and the confirm gate.

    ``checkpointer`` optionally mounts a LangGraph checkpointer so every step
    of the loop is persisted under the caller's ``thread_id`` (spec Issue #4:
    ``thread_id == task_id`` enables stop / crash resume).
    """
    g = StateGraph(AgentState)

    g.add_node("planner", runtime.planner)
    g.add_node("executor", runtime.executor)
    g.add_node("tool", runtime.tool_node)
    g.add_node("finish", runtime.finish)
    g.add_edge(START, "planner")

    if mode == "main":
        # Main topology: planner -> risk_scan -> executor
        # -> (confirm | tool) -> (planner | finish).
        g.add_node("risk_scan", runtime.risk_scan)
        g.add_node("human_confirm", runtime.human_confirm_node)

        g.add_conditional_edges("planner", _after_planner)
        g.add_conditional_edges("risk_scan", _after_risk_scan)
        g.add_conditional_edges("executor", _after_executor)
        g.add_conditional_edges("tool", _tool_router(runtime))
        # After a decision the gate always re-enters the tool node: whether the
        # call actually runs is the tool node's business (rejected calls are
        # skipped there), and whether the gate is entered *again* afterwards is
        # decided by the tool router.
        g.add_edge("human_confirm", "tool")
    elif mode == "subtask":
        # Subtask topology: planner -> executor -> (tool | finish) -> planner.
        g.add_conditional_edges("planner", _after_planner_subtask)
        g.add_conditional_edges("executor", _after_executor_subtask)
        g.add_conditional_edges("tool", _tool_router_subtask(runtime))
    else:
        raise ValueError(
            f"unknown graph mode {mode!r} (expected 'main' or 'subtask')"
        )

    g.add_edge("finish", END)
    return g.compile(checkpointer=checkpointer)
