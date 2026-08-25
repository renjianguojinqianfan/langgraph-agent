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
from typing import Any, Dict

from ...config import Settings
from ...utils.logging import get_logger
from ..agent.subagent import SubAgentExecutor, SubTaskSpec
from .base import BaseTool, ToolResult
from .registry import register

logger = get_logger("tool.subagent")


@register
class SpawnSubagentTool(BaseTool):
    name = "spawn_subagent"
    description = (
        "Spawn an isolated sub-agent to complete a focused subtask. The "
        "sub-agent has its own context and tool set; its final answer is "
        "returned as the tool result. Requires human confirmation before "
        "execution."
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
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tool names to allow (P1: ignored, the "
                "full shared tool set is used).",
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
        spec = SubTaskSpec(
            subtask_id=f"spawn:sub:{uuid.uuid4().hex[:8]}",
            name=name,
            instruction=instruction,
            parent_task_id="",  # the tool has no parent context; no artifact re-registration
        )
        logger.info("spawn_subagent: %s", name)
        result = executor.run_subtask(spec, publish=None)
        return ToolResult(
            success=result.status == "completed",
            data=result.to_dict(),
            error=result.error or ("" if result.status == "completed" else "subtask failed"),
        )
