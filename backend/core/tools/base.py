"""Base tool contract.

Every tool exposes ``name`` / ``description`` / ``args_schema`` (a JSON Schema
fragment for the function-calling interface) / ``requires_confirm`` /
``registers_artifact`` and implements :meth:`run`. Tools must return a
:class:`ToolResult` and never raise an uncaught exception into the kernel — the
kernel also wraps ``run`` in a try/except as a final safety net.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict

from ...config import Settings


@dataclass
class ToolResult:
    """Uniform tool return value."""

    success: bool
    data: Any = None
    error: str = ""


class BaseTool(ABC):
    """Abstract base for all Agent tools."""

    # Subclasses override these as class attributes.
    name: str = ""
    description: str = ""
    args_schema: Dict[str, Any] = field(default_factory=dict)  # JSON Schema (parameters)
    requires_confirm: bool = False
    # Whether this tool is a source of task products (issue #72: product = what
    # the task wrote). Registration needs both halves: the class declares True
    # *and* the result advertises the written file under ``data["path"]`` as a
    # real file. Read-only tools keep this False even when their result carries
    # a ``path`` (``read`` does). A tool that writes files without advertising
    # one under ``path`` (``bash``) stays False too — it has no single
    # deliverable to register. A wrapper around an external tool (MCP) opts in
    # by setting this True on its class.
    registers_artifact: bool = False

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
