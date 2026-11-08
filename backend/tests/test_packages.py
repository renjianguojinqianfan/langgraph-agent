"""#67 P4-1: capability packages / presets (spec #287 D-53…D-56).

Tests pin the static manifest, the assembly face (tools / schema / dispatch
view / injection / MCP / sub-agent), startup fail-fast and the "full = the old
behaviour" equivalence. Behaviour of the default face is additionally covered
by the pre-#67 suites, which must keep passing unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, cast

import pytest

from backend import cli
from backend.config import Settings
from backend.core.agent.inject import (
    AGENTS_SECTION,
    ENV_SECTION,
    SKILLS_SECTION,
    build_inject_block,
)
from backend.core.agent.nodes import AgentRuntime
from backend.core.agent.state import AgentState
from backend.core.agent.subagent import (
    EXECUTE_TIER,
    EXPLORE_TIER,
    TOOL_TIERS,
    resolve_tool_face,
)
from backend.core.capability_packages import (
    DEFAULT_PRESET,
    INJECTION_SKILLS,
    PACKAGE_BY_ID,
    PACKAGE_IDS,
    PACKAGES,
    PRESETS,
    enabled_tool_names,
    injection_fragments,
    mcp_package_enabled,
    package_for_tool,
    preset_names,
    resolve_packages,
    subagent_package_enabled,
    validate_packages,
)
from backend.core.llm.client import MockLLMClient
from backend.core.tools.base import BaseTool, ToolResult
from backend.core.tools.bash import BashTool
from backend.core.tools.file_io import ReadTool
from backend.core.tools.registry import (
    _REGISTRY,
    build_tool_schemas,
    build_tools,
    get_tool,
)
from backend.main import create_app
from backend.runtime import build_runtime
from backend.services.event_bus import EventBus
from backend.services.model_store import ModelStore
from backend.services.persistence import Persistence
from backend.services.task_manager import TaskManager
from backend.tests.conftest import make_manager, make_settings


def _fake_tool(name: str = "fake_echo") -> BaseTool:
    class _Fake(BaseTool):
        def run(self, **kwargs: Any) -> ToolResult:  # pragma: no cover - not run
            return ToolResult(success=True, data="ok")

    tool = _Fake()
    tool.name = name
    return tool


def _manager(settings: Settings, packages: Any = None) -> TaskManager:
    return make_manager(settings, packages=packages)


# ── static manifest (single source of truth) ──
def test_package_ids_and_tool_ownership_are_static():
    assert [package.id for package in PACKAGES] == [
        "files",
        "bash",
        "skills",
        "web-search",
        "subagent",
        "mcp",
    ]
    assert PACKAGE_IDS == {
        "files",
        "bash",
        "skills",
        "web-search",
        "subagent",
        "mcp",
    }
    for package in PACKAGES:
        for tool_name in package.tools:
            assert get_tool(tool_name) is not None, tool_name
            assert package_for_tool(tool_name) == package.id
    # No tool is claimed by two packages, and unknown names map to None.
    owned = [name for package in PACKAGES for name in package.tools]
    assert len(owned) == len(set(owned))
    assert package_for_tool("not_a_tool") is None


def test_manifest_declares_injection_mcp_and_subagent_face():
    table = {
        package.id: (
            bool(package.injection),
            package.connects_mcp,
            package.affects_subagent_face,
        )
        for package in PACKAGES
    }
    assert table == {
        "files": (False, False, True),
        "bash": (False, False, False),
        "skills": (True, False, True),
        "web-search": (False, False, True),
        "subagent": (False, False, False),
        "mcp": (False, True, False),
    }
    assert PACKAGE_BY_ID["skills"].injection == (INJECTION_SKILLS,)


def test_presets_are_the_declared_combinations_and_full_is_default():
    assert preset_names() == ("minimal", "research", "full")
    assert PRESETS == {
        "minimal": ("files", "bash"),
        "research": (
            "files",
            "bash",
            "skills",
            "web-search",
            "subagent",
            "mcp",
        ),
        "full": ("files", "bash", "skills", "web-search", "subagent", "mcp"),
    }
    assert DEFAULT_PRESET == "full"
    assert resolve_packages("full") == PACKAGE_IDS
    assert resolve_packages("") == PACKAGE_IDS
    assert resolve_packages("minimal") == frozenset({"files", "bash"})


def test_unknown_preset_and_package_ids_raise_readable_errors():
    with pytest.raises(ValueError, match="unknown plugins preset"):
        resolve_packages("minimal-plus")
    with pytest.raises(ValueError, match="unknown capability package"):
        resolve_packages("full", {"files": True, "nope": False})
    with pytest.raises(ValueError, match="unknown capability package"):
        validate_packages(frozenset({"files", "nope"}))


def test_package_derived_sets():
    minimal = resolve_packages("minimal")
    assert enabled_tool_names(minimal) == {
        "read",
        "write",
        "edit",
        "ls",
        "glob",
        "grep",
        "bash",
    }
    assert injection_fragments(minimal) == frozenset()
    assert mcp_package_enabled(minimal) is False
    assert subagent_package_enabled(minimal) is False

    research = resolve_packages("research")
    assert injection_fragments(research) == frozenset({INJECTION_SKILLS})
    assert mcp_package_enabled(research) is True
    assert subagent_package_enabled(research) is True

    # Overrides are the shape P4-2 will feed from data/plugins.json.
    trimmed = resolve_packages("full", {"mcp": False, "skills": False})
    assert trimmed == frozenset({"files", "bash", "web-search", "subagent"})


def test_tier_constants_are_untouched():
    """D-55: explore/execute tiers stay fixed; presets only intersect them."""
    assert TOOL_TIERS[EXPLORE_TIER] == [
        "read",
        "ls",
        "glob",
        "grep",
        "load_skill",
        "web_search",
    ]
    assert TOOL_TIERS[EXECUTE_TIER] == [
        "read",
        "ls",
        "glob",
        "grep",
        "load_skill",
        "web_search",
        "write",
        "edit",
    ]


# ── assembly: preset face snapshots (name sets, not counts) ──
_FACE = {
    "minimal": {"read", "write", "edit", "ls", "glob", "grep", "bash"},
    "research": {
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
    "full": {
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
}


@pytest.mark.parametrize("preset", ["minimal", "research", "full"])
def test_preset_tool_face_snapshot(tmp_path, preset):
    settings = make_settings(tmp_path, plugins_preset=preset)
    tm = _manager(settings)
    names = {tool.name for tool in tm._tools}
    schema_names = {schema["function"]["name"] for schema in tm._tool_schemas}
    assert names == _FACE[preset]
    assert schema_names == _FACE[preset]
    assert tm.packages == resolve_packages(preset)
    assert tm.injection_fragments == injection_fragments(tm.packages)
    # The preset field reaches the manager through Settings (the P4-2 path).
    assert resolve_packages(settings.plugins_preset) == tm.packages


def test_full_default_and_explicit_full_match_pre_67_face(tmp_path):
    default = make_manager(make_settings(tmp_path))
    explicit = make_manager(make_settings(tmp_path, plugins_preset="full"))
    injected = make_manager(
        make_settings(tmp_path, plugins_preset="full"),
        packages=resolve_packages("full"),
    )
    reference = build_tools(make_settings(tmp_path))
    reference_schemas = build_tool_schemas(make_settings(tmp_path))

    default_names = {tool.name for tool in default._tools}
    assert default_names == {tool.name for tool in reference}
    assert {s["function"]["name"] for s in default._tool_schemas} == {
        s["function"]["name"] for s in reference_schemas
    }
    assert {tool.name for tool in explicit._tools} == default_names
    assert {tool.name for tool in injected._tools} == default_names


# ── true close: disabled packages are never instantiated / wired ──
def test_disabled_packages_are_never_instantiated(tmp_path, monkeypatch):
    def _boom(settings):
        raise AssertionError("disabled package tool was constructed")

    class _BoomBash(BashTool):
        def __init__(self, settings):
            _boom(settings)

    class _BoomRead(ReadTool):
        def __init__(self, settings):
            _boom(settings)

    monkeypatch.setitem(_REGISTRY, "bash", _BoomBash)
    monkeypatch.setitem(_REGISTRY, "read", _BoomRead)

    settings = make_settings(tmp_path)
    # Only the skills package: neither files nor bash may be constructed.
    tm = make_manager(settings, packages=frozenset({"skills"}))
    assert {tool.name for tool in tm._tools} == {"load_skill"}

    # The filter layer itself is the no-instantiation contract.
    with pytest.raises(AssertionError, match="disabled package tool"):
        build_tools(settings, names={"bash"})


def test_injected_tool_list_is_filtered_by_package(tmp_path):
    settings = make_settings(tmp_path)
    tools = [_fake_tool(), BashTool(settings), ReadTool(settings)]
    tm = TaskManager(
        settings,
        EventBus(),
        Persistence(settings),
        llm_client=MockLLMClient(),
        tools=tools,
        default_workspace=settings.artifacts_path,
        model_store=ModelStore(settings, home_dir=settings.data_path / "home"),
        packages=frozenset({"bash"}),
    )
    try:
        names = {tool.name for tool in tm._tools}
        # Unknown/test-double names pass through; files-package tools do not.
        assert names == {"fake_echo", "bash"}
    finally:
        tm.shutdown()


def test_mcp_package_off_never_connects_even_when_enabled(tmp_path, monkeypatch):
    from backend.core.mcp import client as mcp_client

    class _BoomManager:
        def __init__(self, *args, **kwargs):
            raise AssertionError("MCP manager constructed while mcp package is off")

    monkeypatch.setattr(mcp_client, "McpClientManager", _BoomManager)

    settings = make_settings(tmp_path, plugins_preset="minimal", mcp_enabled=True)
    tm = make_manager(settings)
    assert tm._mcp is None
    assert not mcp_package_enabled(tm.packages)


# ── injection: skills fragment follows the package, other blocks stay ──
def _write_injection_fixtures(settings: Settings) -> None:
    root = Path(settings.artifacts_path)
    root.mkdir(parents=True, exist_ok=True)
    (root / "AGENTS.md").write_text("workspace conventions", encoding="utf-8")
    skill = root / ".agents" / "skills" / "demo"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: demo description\n---\nbody\n",
        encoding="utf-8",
    )


def _system_messages(settings: Settings) -> str:
    _write_injection_fixtures(settings)
    tm = _manager(settings)
    runtime = AgentRuntime(
        task_id="t-packages",
        task_manager=tm,
        llm=MockLLMClient(),
        tools=list(tm._tools),
        tool_schemas=tm._tool_schemas,
        packages=tm.packages,
    )
    state = cast(
        AgentState,
        {"messages": [{"role": "user", "content": "hi"}], "step_index": 0},
    )
    return str(runtime._build_messages(state, "SYS")[0]["content"])


def test_skills_injection_disappears_without_the_skills_package(tmp_path):
    off = _system_messages(
        make_settings(
            tmp_path / "off",
            plugins_preset="minimal",
            context_inject_enabled=True,
            context_inject_skills_budget=20000,
        )
    )
    on = _system_messages(
        make_settings(
            tmp_path / "on",
            plugins_preset="research",
            context_inject_enabled=True,
            context_inject_skills_budget=20000,
        )
    )

    # The skills catalogue is gone; the AGENTS.md + environment blocks remain.
    assert SKILLS_SECTION not in off
    assert AGENTS_SECTION in off
    assert ENV_SECTION in off
    assert SKILLS_SECTION in on
    assert "demo-skill: demo description" in on


def test_build_inject_block_include_skills_flag(tmp_path):
    settings = make_settings(tmp_path)
    home = tmp_path / "home"
    skills = home / ".agents" / "skills" / "demo"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: demo description\n---\nbody\n",
        encoding="utf-8",
    )
    (home / ".agents" / "AGENTS.md").write_text("home conventions", encoding="utf-8")

    with_skills = build_inject_block(settings, home_dir=home)
    without = build_inject_block(settings, home_dir=home, include_skills=False)

    assert SKILLS_SECTION in with_skills
    assert SKILLS_SECTION not in without
    assert AGENTS_SECTION in with_skills and AGENTS_SECTION in without


# ── sub-agent face is tier ∩ mounted packages ──
def test_subagent_face_is_derived_from_packages(tmp_path):
    minimal = make_manager(make_settings(tmp_path, plugins_preset="minimal"))
    assert minimal._subagent is None
    assert "spawn_subagent" not in {tool.name for tool in minimal._tools}
    assert "spawn_subagent" not in {
        s["function"]["name"] for s in minimal._tool_schemas
    }
    assert {t.name for t in resolve_tool_face(list(minimal._tools), EXECUTE_TIER)} == {
        "read",
        "write",
        "edit",
        "ls",
        "glob",
        "grep",
    }

    research = make_manager(make_settings(tmp_path, plugins_preset="research"))
    assert research._subagent is not None
    assert "spawn_subagent" in {tool.name for tool in research._tools}
    assert {
        t.name for t in resolve_tool_face(list(research._tools), EXPLORE_TIER)
    } == {
        "read",
        "ls",
        "glob",
        "grep",
        "load_skill",
        "web_search",
    }


# ── startup fail-fast: every entry geometry ──
def test_build_runtime_rejects_unknown_preset(tmp_path):
    with pytest.raises(ValueError, match="unknown plugins preset"):
        build_runtime(make_settings(tmp_path, plugins_preset="bogus"))


def test_create_app_rejects_unknown_preset(tmp_path):
    with pytest.raises(ValueError, match="unknown plugins preset"):
        create_app(make_settings(tmp_path, plugins_preset="bogus"))


def test_cli_rejects_unknown_preset_before_serving(tmp_path, monkeypatch, capsys):
    settings = make_settings(tmp_path, plugins_preset="bogus")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    served: List[tuple] = []

    code = cli.main([], serve=lambda host, port: served.append((host, port)))

    assert code == 1
    assert served == []
    out = capsys.readouterr().out
    assert "unknown plugins preset" in out
