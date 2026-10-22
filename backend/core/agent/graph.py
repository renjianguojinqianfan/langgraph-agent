"""LangGraph ``StateGraph`` construction (langgraph 1.x idioms).

Wires the :class:`~backend.core.agent.nodes.AgentRuntime` node methods into a
single loop: ``agent -> (finish | tool) -> (agent | finish)``.

The topology is one loop with a single ``agent`` node; a task's mode is not a
second node — it selects the prompt and the tool face inside ``agent``, and a
round ends when the node returns no tool call (history: ADR 0008).

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


def _after_agent(s: AgentState) -> Literal["finish", "tool"]:
    """Agent -> finish on a round ending (no tool call), stop, or error.

    A round's exit is "no tool call" (ADR 0008 decision 4) — the same signal in
    plan and build mode. An LLM error short-circuits to finish so it is reported
    as FAILED instead of the tool node running on an empty call set.
    """
    if s.get("stop_requested") or s.get("error"):
        return "finish"
    if s.get("_last_action") == "final_answer":
        return "finish"
    return "tool"


def _tool_router(
    runtime: AgentRuntime,
) -> Callable[[AgentState], Literal["finish", "agent"]]:
    """Bind the runtime into the tool router (the step budget lives here).

    A factory rather than a closure inside :func:`build_graph`, so the router
    stays a named, ``Literal``-annotated function: 1.x reads the annotation off
    it to declare the branch statically (verified to work through a factory).
    It captures ``runtime`` — not ``runtime.max_steps`` — so the budget is read
    at routing time.

    The budget counts LLM calls **in the current round** (``round_steps``), not
    across the whole conversation: max_steps is the guard against a runaway
    loop, not a cap on how long a task may live.
    """

    def _after_tool(s: AgentState) -> Literal["finish", "agent"]:
        """Tool -> agent until the round's step budget runs out, or finish on stop."""
        if s.get("stop_requested"):
            return "finish"
        if s.get("round_steps", 0) >= runtime.max_steps:
            return "finish"
        return "agent"

    return _after_tool


def build_graph(runtime: AgentRuntime, checkpointer: Any | None = None):
    """Compile and return the agent graph for ``runtime``.

    There is one topology for main tasks and subtasks alike:

        agent -> (finish | tool) -> (agent | finish)

    ``checkpointer`` optionally mounts a LangGraph checkpointer so every step
    of the loop is persisted under the caller's ``thread_id`` (spec Issue #4:
    ``thread_id == task_id`` enables stop / crash resume, and since S5 also the
    "say one more thing" continuation after a round ends).
    """
    g = StateGraph(AgentState)

    g.add_node("agent", runtime.agent)
    g.add_node("tool", runtime.tool_node)
    g.add_node("finish", runtime.finish)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", _after_agent)
    g.add_conditional_edges("tool", _tool_router(runtime))
    g.add_edge("finish", END)
    return g.compile(checkpointer=checkpointer)
