"""Example plugin tool — a template for adding tools without touching core.

Copy this file into ``backend/plugins/`` (or a subdirectory) as a starting
point for a new tool. Any ``BaseTool`` subclass decorated with ``@register``
is auto-discovered on startup when ``plugins_autoload=true`` and becomes
callable by the LLM.

IMPORTANT
    Plugin modules are loaded standalone via ``importlib`` (they are not part
    of the ``backend`` package hierarchy), so **use absolute imports**
    (``from backend...``) — relative imports would fail.

This tool is registered with ``retryable=False`` / ``max_retries=0`` /
``circuit_breaker=False`` to keep it deterministic; adjust these per tool.
"""

from __future__ import annotations

from typing import Any

from backend.core.tools.base import BaseTool, ToolResult
from backend.core.tools.registry import register


@register
class ExampleEchoTool(BaseTool):
    """Echo the input text back — used to verify plugin auto-discovery."""

    name = "example_echo"
    description = (
        "Echo back the given text unchanged. A template/example plugin tool "
        "that verifies the plugin discovery mechanism works."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to echo back."},
        },
        "required": ["text"],
    }
    requires_confirm = False
    retryable = False
    max_retries = 0
    circuit_breaker = False

    def run(self, **kwargs: Any) -> ToolResult:
        text = str(kwargs.get("text", ""))
        return ToolResult(success=True, data={"echo": text})
