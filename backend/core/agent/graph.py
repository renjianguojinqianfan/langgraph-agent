"""LangGraph ``StateGraph`` construction (langgraph 1.x idioms).

Wires the :class:`~backend.core.agent.nodes.AgentRuntime` node methods into a
single graph with conditional edges implementing the
``Planner -> Executor -> Tool`` loop and graceful ``finish`` on completion /
failure / stop.

#137 (slice S3) removed the ``reflect`` node: a claimed completion now goes
straight to ``finish``, and a finished tool round loops back to ``planner``
directly. The step budget that used to live in the reflect router moved into
the tool router, which is where the loop-back now happens.

#138 (slice S4) removed the ``risk_scan`` node and the ``human_confirm`` gate,
so the main and subtask topologies are now the same graph
(``planner -> (finish | executor) -> (finish | tool) -> (planner | finish)``)
and the ``mode`` parameter is gone. A subtask still cannot block on a human
decision — nothing in the graph asks for one — and its narrower tool face is a
declared tier instead (issue #56).

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
* a router that needs the runtime (the step budget) is a factory returning a
  named, ``Literal``-annotated function — the annotation survives the factory
  (verified).
"""

from __future__ import annotations

from typing import Any, Callable, Literal

from langgraph.graph import END, START, StateGraph

from .nodes import AgentRuntime
from .state import AgentState


def _after_planner(s: AgentState) -> Literal["finish", "executor"]:
    """Planner -> executor, unless stopped or planning failed (Issue #12).

    A planner LLM error sets ``state["error"]``; short-circuiting to finish
    reports FAILED with the real cause instead of running the executor on an
    empty plan.
    """
    if s.get("stop_requested") or s.get("error"):
        return "finish"
    return "executor"


def _after_executor(s: AgentState) -> Literal["finish", "tool"]:
    """Executor -> finish on a final answer, else the tool node.

    #137: a claimed completion goes straight to ``finish`` — nothing verifies it
    any more, so there is no reflect node to pass through.
    """
    if s.get("stop_requested"):
        return "finish"
    if s.get("_last_action") == "final_answer":
        return "finish"
    return "tool"


def _tool_router(
    runtime: AgentRuntime,
) -> Callable[[AgentState], Literal["finish", "planner"]]:
    """Bind the runtime into the tool router (the step budget lives here).

    A factory rather than a closure inside :func:`build_graph`, so the router
    stays a named, ``Literal``-annotated function: 1.x reads the annotation off
    it to declare the branch statically (verified to work through a factory).
    It captures ``runtime`` — not ``runtime.max_steps`` — so the budget is read
    at routing time. Until #137 that budget sat in the reflect router; the tool
    node is where a finished round loops back now, so it moved here.
    """

    def _after_tool(s: AgentState) -> Literal["finish", "planner"]:
        """Tool -> planner until the step budget runs out, or finish on stop."""
        if s.get("stop_requested"):
            return "finish"
        if s.get("step_index", 0) >= runtime.max_steps:
            return "finish"
        return "planner"

    return _after_tool


def build_graph(runtime: AgentRuntime, checkpointer: Any | None = None):
    """Compile and return the agent graph for ``runtime``.

    There is one topology for main tasks and subtasks alike:

        planner -> (finish | executor) -> (finish | tool) -> (planner | finish)

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
    g.add_conditional_edges("planner", _after_planner)
    g.add_conditional_edges("executor", _after_executor)
    g.add_conditional_edges("tool", _tool_router(runtime))
    g.add_edge("finish", END)
    return g.compile(checkpointer=checkpointer)
