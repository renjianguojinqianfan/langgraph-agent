"""Tool layer package.

Importing this package registers every built-in tool so the orchestration
kernel can discover them automatically via :func:`registry.build_tools`.
"""

from __future__ import annotations

# Every import below has a side effect: the module's @register decorators fill the
# shared registry at import time (the P1 kb-retrieval and sub-agent spawner tools
# included), so none of them is a removable "unused" import — each name is
# re-exported in __all__ below. Their order is dictated by the ruff isort gate
# (pyproject.toml) and deliberately carries no grouping narrative.
from .base import BaseTool, ToolResult
from .code_exec import CodeExecTool
from .file_io import FileIOTool

# P2 item 2: Git tools are NOT @register'd — TaskManager instantiates them via
# build_git_tools(settings) so the git_enabled switch can filter them out.
from .git_tools import (
    GitBranchTool,
    GitCheckoutTool,
    GitCommitTool,
    GitDiffTool,
    GitInitTool,
    GitLogTool,
    GitStatusTool,
    GitToolRunner,
    build_git_tools,
)
from .http_api import HttpTool
from .kb_tools import KbQueryTool, MemorySearchTool
from .registry import build_tools, get_tool, list_tools, register
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
    "FileIOTool",
    "CodeExecTool",
    "HttpTool",
    "SpawnSubagentTool",
    "MemorySearchTool",
    "KbQueryTool",
    "GitToolRunner",
    "GitStatusTool",
    "GitDiffTool",
    "GitCommitTool",
    "GitLogTool",
    "GitBranchTool",
    "GitCheckoutTool",
    "GitInitTool",
    "build_git_tools",
]
