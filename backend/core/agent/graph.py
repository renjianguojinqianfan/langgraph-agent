"""LangGraph ``StateGraph`` construction.

Wires the :class:`~backend.core.agent.nodes.AgentRuntime` node methods into a
single graph with conditional edges implementing the
``Planner -> [RiskScan -> SubAgentSplit] -> Executor -> Tool -> Reflect`` loop,
the ``human_confirm`` interrupt, and graceful ``finish`` on completion /
failure / stop.

Two build modes (P1 item 2):

* ``mode="main"`` (default) — full topology including ``risk_scan``,
  ``subagent_split`` and ``human_confirm``;
* ``mode="subtask"`` — simplified topology (planner -> executor -> tool ->
  reflect -> finish) with **no** risk / confirm / subagent_split nodes; used
  by :class:`~backend.core.agent.subagent.SubAgentExecutor` so subtasks can
  never recursively split or block on confirmations that cannot route back to
  the parent.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from .nodes import AgentRuntime
from .state import AgentState


def build_graph(runtime: AgentRuntime, mode: str = "main", checkpointer: Any | None = None):
    """Compile and return the agent graph for ``runtime``.

    ``mode`` is ``"main"`` (default, full topology) or ``"subtask"``
    (simplified, no risk / confirm / subagent recursion).

    ``checkpointer`` optionally mounts a LangGraph checkpointer so every step
    of the loop is persisted under the caller's ``thread_id`` (spec Issue #4:
    ``thread_id == task_id`` enables stop / crash resume).
    """
    g = StateGraph(AgentState)

    g.add_node("planner", runtime.planner)
    g.add_node("executor", runtime.executor)
    g.add_node("tool", runtime.tool_node)
    g.add_node("reflect", runtime.reflect)
    g.add_node("finish", runtime.finish)

    g.set_entry_point("planner")

    if mode == "main":
        # Main topology: planner -> risk_scan -> subagent_split -> executor
        # -> (confirm | tool) -> reflect -> (finish | planner).
        g.add_node("risk_scan", runtime.risk_scan)
        g.add_node("subagent_split", runtime.subagent_split)
        g.add_node("human_confirm", runtime.human_confirm_node)

        # Planner -> (stop? finish : risk_scan)
        g.add_conditional_edges(
            "planner",
            lambda s: "finish" if s.get("stop_requested") else "risk_scan",
        )
        # RiskScan -> (stop? finish : subagent_split)
        g.add_conditional_edges(
            "risk_scan",
            lambda s: "finish" if s.get("stop_requested") else "subagent_split",
        )
        # SubAgentSplit -> (final_answer? reflect : executor)
        def _after_split(s: AgentState) -> str:
            if s.get("stop_requested"):
                return "finish"
            if s.get("_last_action") == "final_answer":
                return "reflect"
            return "executor"

        g.add_conditional_edges("subagent_split", _after_split)

        # After confirmation, re-enter the tool node to actually execute.
        g.add_conditional_edges("human_confirm", lambda s: "tool")
    else:
        # Subtask topology: planner -> executor -> (tool | reflect) -> finish.
        g.add_conditional_edges(
            "planner",
            lambda s: "finish" if s.get("stop_requested") else "executor",
        )

    # Executor -> final_answer? reflect : (needs confirm? human_confirm : tool)
    def _after_executor(s: AgentState) -> str:
        if s.get("stop_requested"):
            return "finish"
        if s.get("_last_action") == "final_answer":
            return "reflect"
        if s.get("_needs_confirm"):
            return "human_confirm"
        return "tool"

    g.add_conditional_edges("executor", _after_executor)

    # Tool -> (stop? finish : (still needs confirm? human_confirm : reflect))
    def _after_tool(s: AgentState) -> str:
        if s.get("stop_requested"):
            return "finish"
        if s.get("_needs_confirm"):
            return "human_confirm"
        return "reflect"

    g.add_conditional_edges("tool", _after_tool)

    # Reflect -> done? finish : (max steps? finish : planner)
    def _after_reflect(s: AgentState) -> str:
        if s.get("stop_requested"):
            return "finish"
        if s.get("_last_action") == "final_answer":
            return "finish"
        if s.get("step_index", 0) >= runtime.max_steps:
            return "finish"
        return "planner"

    g.add_conditional_edges("reflect", _after_reflect)

    g.add_edge("finish", END)
    return g.compile(checkpointer=checkpointer) if checkpointer is not None else g.compile()
