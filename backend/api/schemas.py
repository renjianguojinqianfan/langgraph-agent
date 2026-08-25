"""Pydantic models — the single source of truth for API / persistence schema.

Frontend ``src/types/index.ts`` mirrors these field names exactly.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


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


# ── Request bodies ──
class CreateTaskRequest(BaseModel):
    title: Optional[str] = None
    input: str


class ConfirmRequest(BaseModel):
    tool_call_id: str
    approved: bool


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
