"""Pydantic models — the single source of truth for API / persistence schema.

Frontend ``src/types/index.ts`` mirrors these field names exactly.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"
    # #46: ran to the end and claimed completion, but our verification did not
    # pass. Not FAILED — nothing crashed (ADR 0005 keeps P0-B Q4 intact).
    UNVERIFIED = "UNVERIFIED"


class Expectation(BaseModel):
    """A caller-declared, mechanically decidable completion condition (#46).

    Declared at submission, never self-reported by the model: a condition the
    model could declare would be one it could unsay (ADR 0006).
    """

    type: Literal["artifact", "tool_success"]
    # ``artifact``: path relative to the sandbox root. ``tool_success``: tool
    # name, or ``*`` for "at least one successful call of any tool".
    # Required and non-empty: a declaration without a target is a condition
    # nothing can be judged against, and letting it through would quietly mean
    # "any tool" — the one shape of slack this contract exists to remove.
    target: str = Field(min_length=1)


class PlanStep(BaseModel):
    index: int
    description: str
    status: str = "pending"  # pending | active | done


class ToolCallRecord(BaseModel):
    id: str
    tool_name: str
    input: Dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    status: str = "pending"  # pending | success | failed | skipped
    error: Optional[str] = None
    need_confirm: bool = False
    confirmed: bool = False
    # #57 AC5: 进过闸门的调用带终态分档（approved | denied | timed_out | aborted |
    # auto_approved）；空串是「没进过闸门」，不是某种失败。
    confirm_outcome: str = ""
    circuit_open: bool = False  # P0: short-circuited by the circuit breaker
    retries: int = 0  # P0: retries performed by the tool executor


class StepRecord(BaseModel):
    index: int
    thought: str = ""
    tool_calls: List[ToolCallRecord] = Field(default_factory=list)
    status: str = "pending"  # pending | running | done | failed


class Artifact(BaseModel):
    id: str
    filename: str
    path: str
    mime: str = "application/octet-stream"
    size: int = 0
    created_at: str = ""


# ── P1 item 1: risk scan ──
class RiskItem(BaseModel):
    step_index: int = 1
    level: str = "none"  # none | low | medium | high
    matched_keywords: List[str] = Field(default_factory=list)
    suggestion: str = ""
    action: str = "allow"  # confirm | allow | block


# ── P1 item 2: sub-agent ──
class SubTask(BaseModel):
    subtask_id: str
    name: str
    status: str = "pending"  # pending | running | completed | failed
    summary: str = ""
    artifacts: List[str] = Field(default_factory=list)
    error: Optional[str] = None


# ── P1 item 3: knowledge base ──
class KbDoc(BaseModel):
    doc_id: str
    path: str
    size: int = 0
    chunks: int = 0
    indexed_at: str = ""


class KbHit(BaseModel):
    doc_id: str
    path: str
    chunk_index: int = 0
    content: str = ""
    score: float = 0.0


# ── P2 item 1: MCP server diagnostics ──
class McpServerInfo(BaseModel):
    name: str
    transport: str = "stdio"  # stdio (implemented) | http (reserved)
    status: str = "disabled"  # connected | error | disabled
    tools_count: int = 0
    error: Optional[str] = None


class TaskReview(BaseModel):
    """One human acceptance of an ``UNVERIFIED`` verdict (#46 slice B).

    Deliberately a *sibling* of :attr:`Task.verification`, never an edit to it: the
    machine's verdict stays exactly as it was issued (``passed: False``, its original
    ``reason``), and this record says who changed the terminal state, when, over
    which verdict, and on what grounds. Flipping ``verification.passed`` would be the
    rubber stamp ADR 0005 exists to prevent, wearing a different hat.

    ``reviewer`` is self-declared because the platform has no user accounts — token
    auth (P1 item 5) authenticates *that* a caller is allowed in, not *which* person
    it is. ``overruled_reason`` copies the ``verification.reason`` the acceptance
    walked over, so「哪一档被人工放过了」remains countable (#46 试测要的持续采样).
    """

    at: str
    reviewer: str
    note: str
    overruled_reason: str


class Task(BaseModel):
    id: str
    title: str = ""
    user_input: str = ""
    status: TaskStatus = TaskStatus.PENDING
    steps: List[StepRecord] = Field(default_factory=list)
    plan: List[PlanStep] = Field(default_factory=list)
    artifacts: List[Artifact] = Field(default_factory=list)
    final_answer: str = ""
    error: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    risk_report: List[RiskItem] = Field(default_factory=list)  # P1 item 1
    subtasks: List[SubTask] = Field(default_factory=list)  # P1 item 2
    verification: Optional[Dict[str, Any]] = None  # P0-B: {passed, failures, attempts, reason}
    # #46 slice B: one human acceptance of an UNVERIFIED verdict. Absent == nobody
    # has overruled anything; ``verification`` above still holds the machine's say.
    review: Optional[TaskReview] = None
    # #46: the completion contract this run is judged against (persisted, so a
    # resumed task is still held to what its caller declared).
    expectations: List[Expectation] = Field(default_factory=list)
    # #46 second step: the caller's free-text "what done looks like", judged by an
    # independent grader. Empty == nothing to grade (informational task, and the
    # pre-#46 shape): a rubric nobody declared must never summon a judge.
    rubric: str = ""


# ── Request bodies ──
class CreateTaskRequest(BaseModel):
    title: Optional[str] = None
    input: str
    expectations: List[Expectation] = Field(default_factory=list)  # #46
    rubric: str = ""  # #46 第二步


class ConfirmRequest(BaseModel):
    tool_call_id: str
    approved: bool


class ReviewRequest(BaseModel):
    """Body of ``POST /api/tasks/{id}/review`` — the human review door (#46 slice B).

    Accepting is the only review decision this platform has today (ADR 0005's
    重开条件, settled by #46 B), hence no ``decision`` field yet — one verb, one door.

    Both fields are required, because both are the point of the door: ``note`` is the
    reason an unmet acceptance line is being waived, ``reviewer`` is who waived it.
    The second is as strong as this platform's identity model gets — token auth (P1
    item 5) authenticates that a caller may act, not which person it is, so the name
    is self-declared and is recorded as such.

    Both are also bounded above, and that bound is this paragraph's whole point:
    whatever arrives here is persisted verbatim on ``Task.review``, written to the
    trace JSONL in the clear (``trace.py`` is a bare ``json.dumps``; the repo redacts
    nothing anywhere), and broadcast over SSE — three sinks with no size limit of
    their own. Validating the size at the request edge is the cheap half of bounding
    it, and the request model is where this repo already refuses an empty
    declaration (``Expectation.target`` is ``min_length=1``). The ceilings are
    deliberately generous: a waiver is a name and a sentence or two, not a document,
    and overlong input is a 422 — never a silent truncation at write time.
    """

    # 上界的理由见 docstring：无界自由文本原样进不脱敏的持久化 / trace / SSE 三处。
    reviewer: str = Field(min_length=1, max_length=64)
    note: str = Field(min_length=1, max_length=2000)


class AuthTokenRequest(BaseModel):
    token: str


class AuthTokenResponse(BaseModel):
    token: str = ""
    expires_at: str = ""
    ok: bool = True


# ── Unified response envelope ──
class ApiResponse(BaseModel):
    code: int = 0
    data: Any = None
    message: str = "ok"
