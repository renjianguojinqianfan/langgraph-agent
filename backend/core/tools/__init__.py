"""Tool layer package.

Importing this package registers every built-in tool so the orchestration
kernel can discover them automatically via :func:`registry.build_tools`.
"""

from __future__ import annotations

from .base import BaseTool, ToolResult
from .registry import build_tools, get_tool, list_tools, register
from .code_exec import CodeExecTool
from .file_io import FileIOTool
from .http_api import HttpTool
from .web_search import WebSearchTool
# P1 tools: registering these modules fills the shared registry with the
# sub-agent spawner and the knowledge-base retrieval tools.
from .subagent_tool import SpawnSubagentTool
from .kb_tools import KbQueryTool, MemorySearchTool
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
