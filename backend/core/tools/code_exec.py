"""Code execution tool (restricted subprocess sandbox).

Runs Python / shell code via :mod:`backend.utils.sandbox`. Because arbitrary
code execution is risky, ``requires_confirm`` is ``True`` so the kernel pauses
for human confirmation before running it (P1-2).
"""

from __future__ import annotations

from typing import Any, Dict

from ...config import Settings
from ...utils.logging import get_logger
from .base import BaseTool, ToolResult
from .registry import register

logger = get_logger("tool.code_exec")


@register
class CodeExecTool(BaseTool):
    name = "code_exec"
    description = (
        "Execute Python or shell code in a restricted sandbox with a timeout. "
        "Returns captured stdout/stderr and the exit code. Requires confirmation."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "language": {
                "type": "string",
                "enum": ["python", "shell", "bash", "cmd"],
                "description": "Language/runtime to execute.",
            },
            "code": {"type": "string", "description": "Source code to execute."},
        },
        "required": ["language", "code"],
    }
    requires_confirm = True
    # P0 resilience: non-idempotent / user-visible execution — no retry, no breaker.
    retryable = False
    max_retries = 0
    circuit_breaker = False

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self._timeout = settings.sandbox_timeout if settings else 30

    def run(self, **kwargs: Any) -> ToolResult:
        language = str(kwargs.get("language", "python")).lower()
        code = str(kwargs.get("code", ""))
        if not code:
            return ToolResult(success=False, error="`code` is required.")
        from ...utils.sandbox import run_code

        result = run_code(language=language, code=code, timeout=self._timeout)
        ok = result.get("exit_code") == 0
        return ToolResult(
            success=ok,
            data=result,
            error="" if ok else result.get("error", "execution failed"),
        )
