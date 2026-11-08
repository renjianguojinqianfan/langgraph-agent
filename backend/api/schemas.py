"""Pydantic models — the single source of truth for API / persistence schema.

Frontend ``src/types/index.ts`` mirrors these field names exactly.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, StrictBool, field_validator, model_validator


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




class ToolCallRecord(BaseModel):
    id: str
    tool_name: str
    input: Dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    status: str = "pending"  # pending | success | failed | skipped
    error: Optional[str] = None

    @field_validator("input", mode="before")
    @classmethod
    def _normalize_input(cls, value: Any) -> Dict[str, Any]:
        """Wrap non-dict inputs (legacy list / scalar / null) at the read boundary.

        Old records stored a few calls' args as a list or scalar, and a provider
        may return non-object JSON (``openai_compat.py`` only maps decode errors
        to ``{}``); wrapping keeps the record readable instead of failing the
        whole task record. Dispatch reads args from ``state["_current_tool_calls"]``,
        never from this persisted field, so tool arguments are not rewritten.
        """
        return value if isinstance(value, dict) else {"value": value}


class StepRecord(BaseModel):
    index: int
    #: 1-based conversation round this step belongs to (#265). 0 marks records
    #: persisted before the field existed; the UI renders those as a legacy
    #: block instead of guessing a round.
    round: int = 0
    thought: str = ""
    #: D-38: the model's reasoning for this step. Persisted only after
    #: `TaskManager.redact_reasoning` (known keys stripped, max 8000 chars);
    #: the raw trace stays in `state.messages` for the next protocol turn.
    reasoning: str = ""
    tool_calls: List[ToolCallRecord] = Field(default_factory=list)
    status: str = "pending"  # pending | running | done | failed


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
    #: Absolute, normalized directory the task reads/writes (ADR 0010 decision 1).
    workspace: str = ""
    #: Model profile id; empty -> the global default (ADR 0010 decision 7).
    model: str = ""
    status: TaskStatus = TaskStatus.PENDING
    #: Sticky mode (ADR 0008 decision 3): default build, user switches explicitly.
    mode: TaskMode = TaskMode.BUILD
    steps: List[StepRecord] = Field(default_factory=list)
    #: Plan file state (the text lives in ``<workspace>/.qianfan/plans/``).
    plan_version: int = 0
    plan_hash: str = ""
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
    #: Page-supplied directory; absent -> the backend's launch directory.
    workspace: Optional[str] = None
    #: Model profile id; absent -> the global default.
    model: Optional[str] = None


class UpdateModeRequest(BaseModel):
    mode: TaskMode


class UpdatePlanRequest(BaseModel):
    """The whole plan as Markdown; the server writes the plan file."""

    text: str


class TaskModelRequest(BaseModel):
    """Model switch payload (D-34).

    ``{model: <entry-id>}`` keeps the pre-P2-3 shape; ``{provider, model}``
    names a raw model under that provider and its catalog entry is created on
    demand (source ``manual``). Both forms are required to carry ``model``.
    """

    model: Optional[str] = None
    provider: Optional[str] = None

    @model_validator(mode="after")
    def _require_model(self) -> "TaskModelRequest":
        if not (self.model or "").strip():
            raise ValueError("`model` is required")
        return self


class ModelCreateRequest(BaseModel):
    name: str
    base_url: str
    model: str
    api_key_env: str = "LLM_API_KEY"


class ModelUpdateRequest(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_key_env: Optional[str] = None


class ModelKeyRequest(BaseModel):
    key: str = ""


class ProviderCreateRequest(BaseModel):
    """A provider card (D-34). `key` is write-only: no response echoes it.

    D-36: the four sampling fields are provider-level defaults; a model entry
    may override them. All are optional (None = client default).
    """

    id: str
    name: str = ""
    protocol: str = "openai-compatible"
    base_url: str
    api_key_env: str = "LLM_API_KEY"
    key: str = ""
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    thinking: Optional[bool] = None
    reasoning_effort: Optional[str] = None


class ProviderUpdateRequest(BaseModel):
    """Partial provider edit; `id` is immutable and therefore not a field."""

    name: Optional[str] = None
    protocol: Optional[str] = None
    base_url: Optional[str] = None
    api_key_env: Optional[str] = None
    key: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    thinking: Optional[bool] = None
    reasoning_effort: Optional[str] = None


class ProviderModelCreateRequest(BaseModel):
    model: str
    name: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    thinking: Optional[bool] = None
    reasoning_effort: Optional[str] = None


class ProviderModelUpdateRequest(BaseModel):
    """Partial entry edit (D-36); `id` / `provider_id` / `source` are immutable."""

    model: Optional[str] = None
    name: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    thinking: Optional[bool] = None
    reasoning_effort: Optional[str] = None


class SendMessageRequest(BaseModel):
    input: str


class AuthTokenRequest(BaseModel):
    token: str


class AuthTokenResponse(BaseModel):
    token: str = ""
    expires_at: str = ""
    ok: bool = True


# ── #67 P4-2: capability packages / presets ──
class PluginsUpdateRequest(BaseModel):
    """PUT /api/plugins body: a full preset + per-package overrides.

    Overrides are strict booleans (a JSON string "true" is a 422, not a silent
    coercion); unknown preset / package ids are rejected by the route against the
    static manifest before anything is written.
    """

    preset: str
    overrides: Dict[str, StrictBool] = Field(default_factory=dict)


# ── Unified response envelope ──
class ApiResponse(BaseModel):
    code: int = 0
    data: Any = None
    message: str = "ok"
