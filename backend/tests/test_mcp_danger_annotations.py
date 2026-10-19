"""Issue #57 —— MCP 危险性判定改读协议自报标注（``destructiveHint`` / ``readOnlyHint``）。

票面已拍板的边界：**自报标注只决定「要不要打扰人」，不作为安全边界**——真边界在
子代理收窄与隔离那一侧。本文件钉住判定面的四件事：

* ``annotations`` 从 ``list_tools`` 结果透传到 ``McpTool``（AC1，此前 client 只留
  name / description / inputSchema，想用也没数据）；
* 判定优先读 ``destructiveHint`` / ``readOnlyHint``（AC1）；
* server 两个 hint 都没给 → 退化为「必问」，**不再默认放行**（AC2，对标 opencode
  的兜底 ask：`findLast(match) ?? {action:"ask"}`）；
* 动词启发式**退出判定面**（AC4 选「直接删除」那条）：名字里带 ``delete`` 但 server
  自报只读 → 免问；名字里没有任何写类动词但自报 destructive → 必问。

#138（片 S4）退役了确认闸门：这个判定不再喂任何判决，只剩 ``McpTool.danger_signal``
这一格惰性元数据。本文件仍钉住「标注怎么读」——名字里有 ``delete`` 但自报只读 →
``safe``；名字里没有写类动词但自报 destructive → ``dangerous``；两个 hint 都没有 →
``unknown``。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from backend.config import Settings
from backend.core.llm.client import MockLLMClient
from backend.core.mcp.client import McpClientManager
from backend.core.tools.mcp_tool import McpTool
from backend.services.event_bus import EventBus
from backend.services.persistence import Persistence
from backend.services.task_manager import TaskManager

ECHO_SERVER = str(Path(__file__).resolve().parent / "mcp_echo_server.py")

#: dangerous / safe / unknown —— 见 ``McpTool.danger_signal``。
DANGEROUS = "dangerous"
SAFE = "safe"
UNKNOWN = "unknown"


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    base: dict[str, Any] = dict(
        data_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
    )
    base.update(overrides)
    return Settings(**base)


def _echo_settings(tmp_path: Path, **overrides: Any) -> Settings:
    """Settings wired to the local echo MCP server (subprocess, offline)."""
    base: dict[str, Any] = dict(
        data_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        mcp_enabled=True,
        mcp_connect_timeout_sec=15,
        mcp_timeout_sec=10,
        mcp_servers=json.dumps(
            [
                {
                    "name": "echo",
                    "command": sys.executable,
                    "args": [ECHO_SERVER],
                    "enabled": True,
                }
            ]
        ),
    )
    base.update(overrides)
    return Settings(**base)


class _NullManager:
    """Judgement-only stand-in: no call is ever forwarded (these tests never run)."""

    def call_tool(self, *args: Any, **kwargs: Any) -> dict:  # pragma: no cover
        raise AssertionError("judgement tests must not execute the MCP tool")


def _tool(
    settings: Settings,
    *,
    tool_name: str = "do_thing",
    description: str = "Does a thing.",
    annotations: Any = None,
) -> McpTool:
    return McpTool(
        server_name="echo",
        tool_name=tool_name,
        description=description,
        input_schema={},
        manager=_NullManager(),
        settings=settings,
        annotations=annotations,
    )


# ── AC1：annotations 透传 ─────────────────────────────────────────────────────
def test_list_tools_annotations_reach_the_discovered_tool_dict(tmp_path):
    """client 不再丢弃 ``annotations``——它是判定面的数据来源。"""
    mgr = McpClientManager(_echo_settings(tmp_path))
    discovered = {t["name"]: t for t in mgr.connect_all()}
    mgr.cleanup()

    assert discovered["echo"]["annotations"]["readOnlyHint"] is True
    assert discovered["write_file"]["annotations"]["destructiveHint"] is True


def test_task_manager_hands_the_self_report_to_the_tool(tmp_path):
    """透传要一路走到装载面：TaskManager 建的 McpTool 读得到 server 的自报。"""
    settings = _echo_settings(tmp_path)
    bus = EventBus()
    tm = TaskManager(
        settings, bus, Persistence(settings), llm_client=MockLLMClient(final_answer="ok")
    )
    try:
        by_name = {t.name: t for t in tm._tools}
        assert by_name["mcp__echo__echo"].danger_signal == SAFE
        assert by_name["mcp__echo__write_file"].danger_signal == DANGEROUS
    finally:
        tm.shutdown()


# ── AC1 + AC2：分类读 hint，两个 hint 都没给即 unknown ────────────────────────
def test_destructive_hint_true_is_dangerous():
    s = _settings(Path("."))
    tool = _tool(s, annotations={"destructiveHint": True})
    assert tool.danger_signal == DANGEROUS


def test_read_only_hint_true_is_safe():
    s = _settings(Path("."))
    tool = _tool(s, annotations={"readOnlyHint": True})
    assert tool.danger_signal == SAFE


def test_explicitly_non_destructive_is_safe():
    """``destructiveHint=False`` 是 server 给定的安全信号，与 ``readOnlyHint`` 同权。"""
    s = _settings(Path("."))
    tool = _tool(s, annotations={"destructiveHint": False, "readOnlyHint": False})
    assert tool.danger_signal == SAFE


def test_no_hints_at_all_falls_back_to_unknown():
    """AC2：两个 hint 都没给 → ``unknown``（不是「默认放行」，也不是「猜动词」）。"""
    s = _settings(Path("."))
    empty_annotations = _tool(s, annotations={})
    none_annotations = _tool(s, annotations=None)
    only_other_hints = _tool(s, annotations={"openWorldHint": True, "title": "Do a thing"})

    for tool in (empty_annotations, none_annotations, only_other_hints):
        assert tool.danger_signal == UNKNOWN


def test_non_bool_hint_value_is_not_trusted_as_safe():
    """自报值不是 bool（脏数据）时按「没给」处理 → ``unknown``，而不是当成 False 放行。"""
    s = _settings(Path("."))
    tool = _tool(s, annotations={"readOnlyHint": "yes", "destructiveHint": 0})
    assert tool.danger_signal == UNKNOWN


def test_read_only_hint_false_alone_is_unknown():
    """「不是只读」不等于「安全」：destructiveHint 缺失时落回 ``unknown``。"""
    s = _settings(Path("."))
    tool = _tool(s, annotations={"readOnlyHint": False})
    assert tool.danger_signal == UNKNOWN


# ── AC4：动词启发式退出判定面 ─────────────────────────────────────────────────
def test_self_report_outranks_the_verb_in_the_name():
    """名字里带 ``delete`` 但 server 自报只读 → ``safe``：判定不再看动词表。"""
    s = _settings(Path("."))
    tool = _tool(
        s,
        tool_name="delete_cache",
        description="Delete the cached rows (read-only under the hood).",
        annotations={"readOnlyHint": True},
    )
    assert tool.danger_signal == SAFE


def test_verb_heuristic_table_is_gone():
    """启发式是「猜」，票面给了二选一，这里选的是删除：模块不再携带动词表。"""
    from backend.core.tools import mcp_tool

    assert not hasattr(mcp_tool, "_WRITE_VERBS")
    assert not hasattr(McpTool, "_heuristic_write_like")
    assert not hasattr(McpTool, "_write_like")

