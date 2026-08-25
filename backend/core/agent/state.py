"""Agent state schema shared by the LangGraph nodes.

This is the single in-memory representation threaded through the
``Planner -> Executor -> Tool -> Reflect`` loop. Field names mirror the
persistence / API models (``Task``, ``StepRecord``, ``ToolCallRecord``,
``PlanStep``, ``Artifact``) so mapping at the end of a run is trivial.
"""

from __future__ import annotations

from typing import Any, Dict, List, TypedDict


class AgentState(TypedDict, total=False):
    """Mutable state passed between graph nodes."""

    # ── Public / persisted fields ──
    task_id: str
    messages: List[Dict[str, Any]]          # OpenAI-style message dicts
    plan: List[Dict[str, Any]]              # PlanStep dicts
    steps: List[Dict[str, Any]]             # StepRecord dicts
    artifacts: List[Dict[str, Any]]         # Artifact dicts
    status: str
    stop_requested: bool
    pending_confirm: Dict[str, Any]

    # ── Loop bookkeeping (internal) ──
    step_index: int
    final_answer: str
    error: str
    compressed: bool  # P0: whether messages were compressed before the last LLM call
    context_tokens: int  # P0: estimated token count of the (possibly compressed) messages
    _last_action: str                       # plan | tool_call | tool_done | final_answer | stop
    _current_tool_calls: List[Dict[str, Any]]
    _confirmed_ids: List[str]
    _rejected_ids: List[str]
    _needs_confirm: bool

    # ── P1 item 1: risk scan ──
    risk_report: List[Dict[str, Any]]       # latest round of RiskItem dicts
    _risk_blocked: bool                     # high-risk round -> executor must confirm

    # ── P1 item 2: sub-agent ──
    subtasks: List[Dict[str, Any]]          # SubTask dicts (folded summaries)
    _is_subtask: bool                       # True inside a subtask state (anti-recursion)
