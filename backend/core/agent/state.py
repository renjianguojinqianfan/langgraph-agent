"""Agent state schema shared by the LangGraph nodes.

This is the single in-memory representation threaded through the
``Agent -> Tool`` loop (issue #139 / ADR 0008 decision 2). Field names mirror
the persistence / API models (``Task``, ``StepRecord``, ``ToolCallRecord``,
``ChatTurn``) so mapping at the end of a run is trivial.
"""

from __future__ import annotations

from typing import Any, Dict, List, TypedDict


class AgentState(TypedDict, total=False):
    """Mutable state passed between graph nodes."""

    # ── Public / persisted fields ──
    task_id: str
    mode: str                              # plan | build (sticky task switch)
    messages: List[Dict[str, Any]]          # OpenAI-style message dicts
    plan_text: str                          # current plan file content
    plan_version: int                       # bumped when the file content changes
    plan_hash: str                          # sha256 of the content last seen
    steps: List[Dict[str, Any]]             # StepRecord dicts
    status: str
    stop_requested: bool

    # ── Loop bookkeeping (internal) ──
    round_no: int                           # 1-based conversation round (#265)
    step_index: int                         # cumulative across the whole task
    round_steps: int                        # agent calls (steps) this round (budget)
    final_answer: str
    error: str
    compressed: bool  # P0: whether messages were compressed before the last LLM call
    context_tokens: int  # P0: estimated token count of the (possibly compressed) messages
    _last_action: str                       # tool_call | tool_done | final_answer | stop
    _current_tool_calls: List[Dict[str, Any]]

    # ── P1 item 2: sub-agent ──
    subtasks: List[Dict[str, Any]]          # SubTask dicts folded into the parent record


def initial_agent_state(
    *,
    task_id: str,
    user_input: str,
    mode: str = "build",
    round_no: int = 1,
    plan_text: str = "",
    plan_version: int = 0,
    plan_hash: str = "",
) -> AgentState:
    """The one starting state for a fresh round (main task or subtask).

    Both admission paths used to spell these 17 channels out by hand; one
    factory keeps the defaults — and the key set every node assumes — from
    drifting between the manager and the sub-agent executor.
    """
    return {
        "task_id": task_id,
        "mode": mode,
        "messages": [{"role": "user", "content": user_input}],
        "plan_text": plan_text,
        "plan_version": plan_version,
        "plan_hash": plan_hash,
        "steps": [],
        "status": "RUNNING",
        "stop_requested": False,
        "round_no": round_no,
        "step_index": 0,
        "round_steps": 0,
        "final_answer": "",
        "error": "",
        "_last_action": "",
        "_current_tool_calls": [],
        "subtasks": [],
    }
