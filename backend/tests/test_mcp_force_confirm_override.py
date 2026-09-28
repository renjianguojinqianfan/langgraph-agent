"""Issue #57 AC3 —— ``mcp_force_confirm`` 的一条 server 级通覆盖。

票面要的是「一条配置」同时支持 **整server 全部必问** 与 **全部免问**，且**不做逐工具
登记**、不新增配置键。这里把 ``mcp_force_confirm`` 的条目形状扩成：

* ``mcp__{server}__{tool}``     —— 单个工具（P2 现役语义，不变）；
* ``mcp__{server}__*``          —— 整个 server 全部必问；
* ``!`` 前缀（``!mcp__echo__*`` / ``!mcp__echo__tool``）—— 命中即免问。

优先级写死在这里，测试逐条钉住：**精确名 > server 通覆 > 自报标注**。免问覆盖能压掉
``destructiveHint=True``，这是票面已拍板的边界——自报标注只决定「要不要打扰人」，
不是安全边界（真边界在子代理收窄与隔离那一侧）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.config import Settings
from backend.core.tools.mcp_tool import McpTool

SAFE = {"readOnlyHint": True}
DESTRUCTIVE = {"destructiveHint": True}


class _NullManager:
    def call_tool(self, *args: Any, **kwargs: Any) -> dict:  # pragma: no cover
        raise AssertionError("judgement tests must not execute the MCP tool")


def _tool(
    tmp_path: Path,
    *,
    server: str = "echo",
    tool: str = "do_thing",
    force: str = "[]",
    annotations: Any = None,
) -> McpTool:
    settings = Settings(
        data_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        mcp_force_confirm=force,
    )
    return McpTool(
        server_name=server,
        tool_name=tool,
        description="Does a thing.",
        input_schema={},
        manager=_NullManager(),
        settings=settings,
        annotations=annotations,
    )


def test_server_wildcard_forces_confirm_for_the_whole_server(tmp_path):
    """``mcp__echo__*``：整台 server 全部必问，包括自报只读的工具。"""
    force = json.dumps(["mcp__echo__*"])
    read_only = _tool(tmp_path, tool="echo", force=force, annotations=SAFE)
    destructive = _tool(tmp_path, tool="purge", force=force, annotations=DESTRUCTIVE)

    assert read_only._needs_confirm({}) is True
    assert destructive._needs_confirm({}) is True


def test_negated_server_wildcard_mutes_the_whole_server(tmp_path):
    """``!mcp__echo__*``：整台 server 全部免问，包括自报破坏性的。"""
    mute = json.dumps(["!mcp__echo__*"])
    destructive = _tool(tmp_path, tool="purge", force=mute, annotations=DESTRUCTIVE)
    unreported = _tool(tmp_path, tool="mystery", force=mute, annotations=None)

    assert destructive._needs_confirm({}) is False
    # 免问覆盖压掉的正是 AC2 的兜底必问——运维显式说了不打扰。
    assert unreported._needs_confirm({}) is False


def test_exact_name_wildcard_is_per_tool_not_per_server(tmp_path):
    """通覆只吃 ``mcp__{server}__*``；写成 ``mcp__echo__echo`` 仍旧只命中那一个工具。"""
    force = json.dumps(["mcp__echo__echo"])
    assert _tool(tmp_path, tool="echo", force=force, annotations=SAFE)._needs_confirm({}) is True
    assert _tool(tmp_path, tool="other", force=force, annotations=SAFE)._needs_confirm({}) is False


def test_exact_entry_outranks_the_server_wildcard(tmp_path):
    """精确名 > server 通覆：整台 server 免问时，单独点名的那件仍然必问。"""
    force = json.dumps(["!mcp__echo__*", "mcp__echo__purge"])
    purge = _tool(tmp_path, tool="purge", force=force, annotations=SAFE)
    echo = _tool(tmp_path, tool="echo", force=force, annotations=SAFE)

    assert purge._needs_confirm({}) is True
    assert echo._needs_confirm({}) is False


def test_sanitised_server_name_is_what_the_wildcard_matches(tmp_path):
    """全名里的 server 段是清洗过的，通覆按同一个键匹配，别拿原始名猜。"""
    force = json.dumps(["mcp__my_server__*"])
    tool = _tool(tmp_path, server="my-server", tool="read_text", force=force, annotations=SAFE)
    assert tool.name == "mcp__my_server__read_text"
    assert tool._needs_confirm({}) is True


def test_garbage_override_entries_are_ignored_not_fatal(tmp_path):
    """配置面沿用既有容错：坏条目只是不命中，判定回到自报标注，不炸。"""
    force = json.dumps(["", "   ", 42, "mcp__other__*"])
    tool = _tool(tmp_path, tool="purge", force=force, annotations=DESTRUCTIVE)
    assert tool._needs_confirm({}) is True

    read_only = _tool(tmp_path, tool="echo", force=force, annotations=SAFE)
    assert read_only._needs_confirm({}) is False


def test_empty_override_keeps_the_annotation_verdict(tmp_path):
    """默认（空数组）时判定完全由自报标注说话——覆盖层不存在。"""
    assert _tool(tmp_path, tool="echo", annotations=SAFE)._needs_confirm({}) is False
    assert _tool(tmp_path, tool="purge", annotations=DESTRUCTIVE)._needs_confirm({}) is True
    assert _tool(tmp_path, tool="mystery", annotations=None)._needs_confirm({}) is True
