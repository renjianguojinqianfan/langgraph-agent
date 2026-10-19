"""Tool layer package.

Importing this package registers every built-in tool so the orchestration
kernel can discover them automatically via :func:`registry.build_tools`.
"""

from __future__ import annotations

# Every import below has a side effect: the module's @register decorators fill the
# shared registry at import time (the sub-agent spawner included), so none of
# them is a removable "unused" import — each name is re-exported in __all__
# below. Their order is dictated by the ruff isort gate (pyproject.toml) and
# deliberately carries no grouping narrative.
from .base import BaseTool, ToolResult
from .bash import BashTool
from .file_io import EditTool, GlobTool, GrepTool, LsTool, ReadTool, WriteTool
from .registry import build_tools, get_tool, list_tools, register
from .skill_tool import LoadSkillTool
from .subagent_tool import SpawnSubagentTool
from .web_search import WebSearchTool

__all__ = [
    "BaseTool",
    "ToolResult",
    "register",
    "build_tools",
    "get_tool",
    "list_tools",
    "WebSearchTool",
    "ReadTool",
    "WriteTool",
    "EditTool",
    "GlobTool",
    "GrepTool",
    "LsTool",
    "BashTool",
    "SpawnSubagentTool",
    "LoadSkillTool",
]
