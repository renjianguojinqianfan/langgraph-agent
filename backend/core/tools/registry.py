"""Tool registry and auto-discovery.

Tools register their *class* (keyed by ``name``) via the :func:`register`
decorator. :func:`build_tools` instantiates every registered tool with the
current :class:`~backend.config.Settings`, which is what the kernel uses.
Adding a new tool is therefore a matter of writing a ``BaseTool`` subclass and
decorating it with ``@register`` — the Agent picks it up automatically.
"""

from __future__ import annotations

from typing import Any, Dict, List, Type

from ...config import Settings
from ...utils.logging import get_logger
from .base import BaseTool

logger = get_logger("tool.registry")

_REGISTRY: Dict[str, Type[BaseTool]] = {}


def register(cls: Type[BaseTool]) -> Type[BaseTool]:
    """Class decorator that registers a tool by its ``name``.

    If another tool with the same ``name`` is already registered, the first one
    wins and a warning is logged (the new class is still returned so chained
    usage keeps working, but it is *not* installed into the registry).
    """
    if not cls.name:
        raise ValueError(f"Tool {cls.__name__} must define a non-empty `name`.")
    existing = _REGISTRY.get(cls.name)
    if existing is not None:
        logger.warning(
            "Tool name %r already registered by %s; keeping the existing one "
            "(new %s ignored).",
            cls.name,
            existing.__name__,
            cls.__name__,
        )
        return cls
    _REGISTRY[cls.name] = cls
    return cls


def get_tool(name: str) -> Type[BaseTool] | None:
    cls = _REGISTRY.get(name)
    return cls


def list_tools() -> List[Type[BaseTool]]:
    return list(_REGISTRY.values())


def build_tools(settings: Settings) -> List[BaseTool]:
    """Instantiate all registered tools with the given settings."""
    return [cls(settings) for cls in _REGISTRY.values()]


def build_tool_schemas(settings: Settings) -> List[Dict[str, Any]]:
    """OpenAI-compatible function schemas for every registered tool."""
    return [tool.to_openai_schema() for tool in build_tools(settings)]
