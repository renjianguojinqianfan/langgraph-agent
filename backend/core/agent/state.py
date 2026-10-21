"""Agent state schema shared by the LangGraph nodes.

This is the single in-memory representation threaded through the
``Agent -> Tool`` loop (issue #139 / ADR 0008 decision 2). Field names mirror
the persistence / API models (``Task``, ``StepRecord``, ``ToolCallRecord``,
``PlanStep``, ``ChatTurn``) so mapping at the end of a run is trivial.
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
    _is_subtask: bool                       # True inside a subtask state
