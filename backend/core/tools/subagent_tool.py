"""``spawn_subagent`` tool (P1 item 2).

Lets the main agent delegate an isolated subtask through the ordinary
function-calling interface. The tool is ``requires_confirm=True`` so the
existing P0 ``human_confirm`` flow asks the user **before** the subtask is
spawned (subtasks themselves run a simplified graph with no confirmations, so
no confirmation can ever get stranded inside a subtask).

The actual execution is delegated to the :class:`SubAgentExecutor` wired by
:class:`TaskManager` (``SpawnSubagentTool.executor``). When the executor is
not wired (e.g. unit tests constructing the tool directly) the tool returns a
clear error instead of raising.
"""

from __future__ import annotations

import uuid
from typing import Any

from ...config import Settings
from ...services.snapshots import get_current_task_id
from ...utils.logging import get_logger
from ..agent.subagent import (
    DEFAULT_TIER,
    TOOL_TIERS,
    SubAgentExecutor,
    SubTaskSpec,
    TierError,
)
from .base import BaseTool, ToolResult
from .registry import register

logger = get_logger("tool.subagent")


@register
class SpawnSubagentTool(BaseTool):
    name = "spawn_subagent"
    description = (
        "Spawn an isolated sub-agent to complete a focused subtask. The "
        "sub-agent has its own context and a declared capability tier "
        f"(default '{DEFAULT_TIER}'); its final answer is returned as the tool "
        "result. Requires human confirmation before execution."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Short name of the subtask (e.g. 'research').",
            },
            "instruction": {
                "type": "string",
                "description": "Precise instruction for the sub-agent.",
            },
            "tier": {
                "type": "string",
                "enum": sorted(TOOL_TIERS),
                "description": (
                    f"Capability tier (default '{DEFAULT_TIER}'): 'explore' is "
                    "read-only, 'execute' also writes files inside the sandbox. "
                    "Nothing that needs human confirmation is available to a "
                    "sub-agent in any tier."
                ),
            },
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tool names to restrict the sub-agent "
                "to. Narrows the chosen tier only: a name outside it is "
                "rejected instead of silently dropped.",
            },
        },
        "required": ["name", "instruction"],
    }
    requires_confirm = True
    # Local orchestration: no retry, no circuit breaker.
    retryable = False
    max_retries = 0
    circuit_breaker = False

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self.executor: SubAgentExecutor | None = None  # injected by TaskManager

    def run(self, **kwargs: Any) -> ToolResult:
        executor = self.executor
        if executor is None:
            return ToolResult(
                success=False,
                error="spawn_subagent is not wired to a SubAgentExecutor",
            )
        name = str(kwargs.get("name", "")).strip()
        instruction = str(kwargs.get("instruction", "")).strip()
        if not name or not instruction:
            return ToolResult(
                success=False,
                error="`name` and `instruction` are required.",
            )
        tier = str(kwargs.get("tier") or DEFAULT_TIER)
        raw_tools = kwargs.get("tools")
        subset: list[str] | None = None
        if raw_tools:
            if not isinstance(raw_tools, list):
                return ToolResult(
                    success=False, error="`tools` must be an array of tool names."
                )
            subset = [str(t) for t in raw_tools]
        spec = SubTaskSpec(
            subtask_id=f"spawn:sub:{uuid.uuid4().hex[:8]}",
            name=name,
            instruction=instruction,
            # The tool runs on the parent's worker thread, where TaskManager has
            # already seated the task id — so the parent link is available, and
            # #60's stop propagation depends on it (Issue #33 拍板 5 also wants
            # subtask writes on the parent's ledger).
            parent_task_id=get_current_task_id() or "",
            tier=tier,
            tools=subset,
        )
        # Resolved here rather than inside the executor so an out-of-tier
        # request is refused before anything runs (issue #56: no side effect
        # without an approved cause, and the model gets the reason back).
        try:
            executor.check_face(spec)
        except TierError as exc:
            return ToolResult(success=False, error=str(exc))
        logger.info("spawn_subagent: %s (tier=%s)", name, tier)
        result = executor.run_subtask(spec, publish=None)
        return ToolResult(
            success=result.status == "completed",
            data=result.to_dict(),
            error=result.error or ("" if result.status == "completed" else "subtask failed"),
        )
