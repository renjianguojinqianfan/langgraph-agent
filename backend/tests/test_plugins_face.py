"""#67 P4-3: file-config face snapshots + closed-face end-to-end.

Every test here drives the capability face through a real data/plugins.json
under the test's tmp DATA_DIR (not through Settings.plugins_preset), so the
P4-2 precedence and persistence seams are exercised end to end.
"""

from __future__ import annotations

import builtins
import json
from pathlib import Path
from typing import cast

import pytest

from backend.core.agent.inject import AGENTS_SECTION, SKILLS_SECTION
from backend.core.agent.nodes import AgentRuntime
from backend.core.agent.state import AgentState
from backend.core.agent.subagent import EXECUTE_TIER, resolve_tool_face
from backend.core.capability_packages import resolve_packages
from backend.core.llm.client import MockLLMClient
from backend.core.tools.base import BaseTool, ToolResult
from backend.services.plugins_store import (
    PluginsConfigError,
    plugins_path,
    save_plugins_config,
)
from backend.services.task_manager import TaskManager
from backend.tests.conftest import make_manager, make_settings

FILES = {"read", "write", "edit", "ls", "glob", "grep"}


def _settings_with_file(tmp_path, preset, overrides, **settings_kw):
    settings = make_settings(tmp_path, **settings_kw)
    save_plugins_config(settings, preset, dict(overrides))
    path = plugins_path(settings)
    # Lead requirement: file-config tests must use the tmp DATA_DIR, never the
    # repository's data/. conftest redirects DATA_DIR; assert the file is there.
    assert path.parent == settings.data_path
    assert str(path).startswith(str(tmp_path))
    return settings


def _write_raw(settings, payload: object) -> Path:
    path = plugins_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert str(path).startswith(str(settings.data_path))
    return path


def _manager(tmp_path, preset, overrides, **settings_kw):
    settings = _settings_with_file(tmp_path, preset, overrides, **settings_kw)
    return make_manager(settings), settings


def _names(tm) -> set:
    return {tool.name for tool in tm._tools}


def _schema_names(tm) -> set:
    return {schema["function"]["name"] for schema in tm._tool_schemas}


def _runtime(tm) -> AgentRuntime:
    return AgentRuntime(
        task_id="t-p43",
        task_manager=tm,
        llm=MockLLMClient(),
        tools=list(tm._tools),
        tool_schemas=tm._tool_schemas,
        packages=tm.packages,
        injection_fragments=tm.injection_fragments,
    )


def _call_state(tool_name: str, tool_input: dict) -> AgentState:
    return cast(
        AgentState,
        {
            "step_index": 1,
            "steps": [
                {"index": 1, "thought": "", "tool_calls": [], "status": "running"}
            ],
            "_current_tool_calls": [
                {
                    "id": "c1",
                    "tool_name": tool_name,
                    "input": tool_input,
                    "output": None,
                    "status": "pending",
                    "error": "",
                }
            ],
        },
    )


def _write_injection_fixtures(settings) -> None:
    root = Path(settings.artifacts_path)
    root.mkdir(parents=True, exist_ok=True)
    (root / "AGENTS.md").write_text("workspace conventions", encoding="utf-8")
    skill = root / ".agents" / "skills" / "demo"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: demo description\n---\nbody\n",
        encoding="utf-8",
    )


# ── 1) face snapshots: preset + representative overrides from the real file ──
FACE_CASES = [
    (
        "minimal",
        {"skills": True},
        {"read", "write", "edit", "ls", "glob", "grep", "bash", "load_skill"},
    ),
    (
        "full",
        {"mcp": False},
        {
            "read",
            "write",
            "edit",
            "ls",
            "glob",
            "grep",
            "bash",
            "load_skill",
            "web_search",
            "spawn_subagent",
        },
    ),
    (
        "research",
        {"bash": False, "web-search": True},
        {
            "read",
            "write",
            "edit",
            "ls",
            "glob",
            "grep",
            "load_skill",
            "web_search",
            "spawn_subagent",
        },
    ),
]


@pytest.mark.parametrize("preset,overrides,expected", FACE_CASES)
def test_file_config_face_snapshot(tmp_path, preset, overrides, expected):
    tm, _settings = _manager(tmp_path, preset, overrides)
    assert _names(tm) == expected
    assert _schema_names(tm) == expected  # names, never counts
    assert tm.packages == resolve_packages(preset, overrides)


def test_file_config_unknown_preset_errors_with_path_and_fix(tmp_path):
    settings = make_settings(tmp_path)
    path = _write_raw(settings, {"preset": "ultra", "overrides": {}})
    with pytest.raises(PluginsConfigError) as excinfo:
        make_manager(settings)
    message = str(excinfo.value)
    assert "ultra" in message
    assert str(path) in message
    assert "修法" in message


def test_file_config_unknown_override_errors_with_package_and_fix(tmp_path):
    settings = make_settings(tmp_path)
    _write_raw(settings, {"preset": "full", "overrides": {"nope": True}})
    with pytest.raises(PluginsConfigError) as excinfo:
        make_manager(settings)
    message = str(excinfo.value)
    assert "nope" in message
    assert "可选值" in message
    assert "修法" in message


# ── 2) closed-face end to end through the real file ──
def test_file_config_files_off_schema_and_dispatch(tmp_path):
    tm, _settings = _manager(tmp_path, "full", {"files": False})
    assert _names(tm) == {"bash", "load_skill", "web_search", "spawn_subagent"}
    assert _schema_names(tm) == _names(tm)
    assert FILES.isdisjoint(_names(tm))

    runtime = _runtime(tm)
    target = tmp_path / "must-not-exist.txt"
    state = _call_state("write", {"path": str(target), "content": "nope"})
    runtime.tool_node(state)
    rec = state["_current_tool_calls"][0]
    # Layer 2: a hallucinated name still reaches tool_node and is refused with
    # no side effect (the schema layer above already lacks it).
    assert rec["status"] == "failed"
    assert "unknown tool: write" in rec["error"]
    assert not target.exists()


def test_file_config_skills_off_schema_and_injection(tmp_path):
    settings = _settings_with_file(
        tmp_path, "full", {"skills": False}, context_inject_enabled=True
    )
    _write_injection_fixtures(settings)
    tm = make_manager(settings)
    assert "load_skill" not in _names(tm)
    assert "load_skill" not in _schema_names(tm)

    runtime = _runtime(tm)
    state = cast(
        AgentState,
        {"messages": [{"role": "user", "content": "hi"}], "step_index": 0},
    )
    system = str(runtime._build_messages(state, "SYS")[0]["content"])
    assert SKILLS_SECTION not in system
    assert AGENTS_SECTION in system  # other injection blocks are unaffected


def test_file_config_subagent_off_face_and_executor(tmp_path):
    tm, _settings = _manager(tmp_path, "full", {"subagent": False})
    assert tm._subagent is None
    assert "spawn_subagent" not in _names(tm)
    assert "spawn_subagent" not in _schema_names(tm)
    # Actual sub-agent face = fixed tier ∩ enabled packages (tier unchanged).
    assert {
        tool.name for tool in resolve_tool_face(list(tm._tools), EXECUTE_TIER)
    } == {
        "read",
        "write",
        "edit",
        "ls",
        "glob",
        "grep",
        "load_skill",
        "web_search",
    }

    runtime = _runtime(tm)
    state = _call_state("spawn_subagent", {"name": "x", "instruction": "y"})
    runtime.tool_node(state)
    rec = state["_current_tool_calls"][0]
    assert rec["status"] == "failed"
    assert "unknown tool: spawn_subagent" in rec["error"]
    assert not state.get("subtasks")


def test_file_config_mcp_off_skips_lazy_import_and_connection(tmp_path, monkeypatch):
    real_import = builtins.__import__

    def guard(name, globals=None, locals=None, fromlist=(), level=0):
        if name.endswith("core.mcp.client") or name.endswith("mcp.client"):
            raise AssertionError(
                "MCP client was lazily imported while the mcp package is off"
            )
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guard)
    settings = make_settings(
        tmp_path,
        mcp_enabled=True,
        mcp_servers='[{"name": "demo", "command": "demo-cmd"}]',
    )
    save_plugins_config(settings, "full", {"mcp": False})
    path = plugins_path(settings)
    assert path.parent == settings.data_path
    assert str(path).startswith(str(tmp_path))

    tm = make_manager(settings)
    assert tm._mcp is None
    assert "mcp" not in tm.packages
    assert "mcp_fetch" not in _names(tm)


# ── 3) LOW: dynamic name colliding with a disabled package's builtin ──
def test_dynamic_name_colliding_with_disabled_builtin_is_reported(
    tmp_path, monkeypatch
):
    class _DynamicWrite(BaseTool):
        name = "write"
        description = "dynamic MCP tool that happens to be called write"
        args_schema = {}

        def run(self, **kwargs) -> ToolResult:
            return ToolResult(success=True, data="ok")

    def fake_load(self, settings):
        self._tools.append(_DynamicWrite(settings))

    monkeypatch.setattr(TaskManager, "_load_mcp_tools", fake_load)
    tm, _settings = _manager(tmp_path, "full", {"files": False})

    # files is off, but the dynamically mounted name is a real tool on the face:
    # active.tools must report it instead of losing it to package ownership.
    assert "write" in {tool.name for tool in tm._tools}
    assert "write" in tm.assembled_tool_names
    assert "write" in _schema_names(tm)
