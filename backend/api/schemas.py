"""Pydantic models — the single source of truth for API / persistence schema.

Frontend ``src/types/index.ts`` mirrors these field names exactly.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    """Task lifecycle (ADR 0008 decision 5).

    Three loop states: RUNNING (跑着) / INTERRUPTED (停下，等你说话) / FAILED
    (出错). ``PENDING`` is only the instant between ``create_task()`` writing
    the record and the worker flipping it to RUNNING.

    There is deliberately **no COMPLETED**: a round ends when no tool call
    comes back, and that is the same "停下" as a human stop — the task can
    always be continued. ``Persistence._load`` maps the retired value onto
    ``INTERRUPTED`` so pre-S5 history stays readable.
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


class TaskMode(str, Enum):
    """Sticky per-task mode (ADR 0008 decision 3): plan or build."""

    PLAN = "plan"
    BUILD = "build"


class PlanStep(BaseModel):
    index: int
    description: str
    status: str = "pending"  # pending | active | done


class PlanItem(BaseModel):
    """One editable plan row (request side; the server assigns ``index``)."""

    description: str
    status: Literal["pending", "active", "done"] = "pending"


class ToolCallRecord(BaseModel):
    id: str
    tool_name: str
    input: Dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    status: str = "pending"  # pending | success | failed | skipped
    error: Optional[str] = None


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


# ── P1 item 2: sub-agent ──
class SubTask(BaseModel):
    subtask_id: str
    name: str
    status: str = "pending"  # pending | running | completed | failed
    summary: str = ""
    artifacts: List[str] = Field(default_factory=list)
    error: Optional[str] = None


# ── P2 item 1: MCP server diagnostics ──
class McpServerInfo(BaseModel):
    name: str
    transport: str = "stdio"  # stdio (implemented) | http (reserved)
    status: str = "disabled"  # connected | error | disabled
    tools_count: int = 0
    error: Optional[str] = None


class ChatTurn(BaseModel):
    """One user/assistant text turn, persisted for the conversation view.

    Tool traffic stays out of here: it already lives in ``Task.steps``. Only
    what a person would read as the conversation is kept.
    """

    role: str  # user | assistant
    content: str = ""


class Task(BaseModel):
    id: str
    title: str = ""
    user_input: str = ""
    status: TaskStatus = TaskStatus.PENDING
    #: Sticky mode (ADR 0008 decision 3): default build, user switches explicitly.
    mode: TaskMode = TaskMode.BUILD
    steps: List[StepRecord] = Field(default_factory=list)
    plan: List[PlanStep] = Field(default_factory=list)
    artifacts: List[Artifact] = Field(default_factory=list)
    #: User/assistant text turns across every round of the conversation.
    conversation: List[ChatTurn] = Field(default_factory=list)
    final_answer: str = ""
    error: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    subtasks: List[SubTask] = Field(default_factory=list)  # P1 item 2


# ── Request bodies ──
class CreateTaskRequest(BaseModel):
    title: Optional[str] = None
    input: str
    mode: TaskMode = TaskMode.BUILD


class UpdateModeRequest(BaseModel):
    mode: TaskMode


class UpdatePlanRequest(BaseModel):
    """The editable plan (ADR 0008 decision 3 / pivot item 1).

    Indices are assigned server-side (1..n), so the client sends only order,
    descriptions and (optionally) status per step.
    """

    plan: List[PlanItem] = Field(default_factory=list)


class SendMessageRequest(BaseModel):
    input: str


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
