"""Base tool contract.

Every tool exposes ``name`` / ``description`` / ``args_schema`` (a JSON Schema
fragment for the function-calling interface) / ``requires_confirm`` and
implements :meth:`run`. Tools must return a :class:`ToolResult` and never raise
an uncaught exception into the kernel — the kernel also wraps ``run`` in a
try/except as a final safety net.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ...config import Settings


@dataclass
class ToolResult:
    """Uniform tool return value."""

    success: bool
    data: Any = None
    error: str = ""
    circuit_open: bool = False  # True when the call was short-circuited by a breaker
    retries: int = 0  # number of retries actually performed by the executor


class BaseTool(ABC):
    """Abstract base for all Agent tools."""

    # Subclasses override these as class attributes.
    name: str = ""
    description: str = ""
    args_schema: Dict[str, Any] = field(default_factory=dict)  # JSON Schema (parameters)
    requires_confirm: bool = False
    # P0 resilience knobs (all optional; built-ins override as needed).
    retryable: bool = True  # whether transient failures may be retried
    max_retries: Optional[int] = None  # None -> use global Settings.tool_max_retries
    circuit_breaker: bool = True  # whether consecutive failures trip a breaker

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings

    @abstractmethod
    def run(self, **kwargs: Any) -> ToolResult:
        """Execute the tool. Must return a :class:`ToolResult`."""
        raise NotImplementedError

    def to_openai_schema(self) -> Dict[str, Any]:
        """Return the OpenAI function-calling schema for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_schema,
            },
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.__class__.__name__} name={self.name!r} confirm={self.requires_confirm}>"
