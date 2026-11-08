"""#67 P4-1: capability packages / presets (spec #287 D-53…D-56).

Tests pin the static manifest, the assembly face (tools / schema / dispatch
view / injection / MCP / sub-agent), startup fail-fast and the "full = the old
behaviour" equivalence. Behaviour of the default face is additionally covered
by the pre-#67 suites, which must keep passing unchanged.

Spec Testing exception (P4-1): this suite reads `TaskManager._tools` /
`_tool_schemas` / `_subagent` and `AgentRuntime._planner_system` directly.
P4-1 deliberately ships no public face-query API (that would freeze one before
P4-2's settings page); the private reads are the bounded seam the spec Testing
section calls for, not a product contract.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, List, cast

import pytest

from backend import cli, repl, server_lock
from backend.config import Settings
from backend.core.agent.inject import (
    AGENTS_SECTION,
    ENV_SECTION,
    SKILLS_SECTION,
    build_inject_block,
)
from backend.core.agent.nodes import AgentRuntime
from backend.core.agent.prompts import EXECUTOR_SYSTEM, PLANNER_SYSTEM, planner_system
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
    assert resolve_packages("minimal") == frozenset({"files", "bash"})


def test_empty_preset_is_an_error_not_a_silent_full_fallback():
    # M-rework: str(preset or DEFAULT) used to turn "" into "full" silently.
    for empty in ("", "   ", None):
        with pytest.raises(ValueError, match="empty plugins preset"):
            resolve_packages(empty)


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


def test_cli_repl_dispatch_goes_through_the_preset_guard(tmp_path, monkeypatch, capsys):
    """#306 M1: `qianfan repl` must not bypass the preset validation."""
    settings = make_settings(tmp_path, plugins_preset="bogus")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    entered: List[List[str]] = []

    code = cli.main(["repl"], repl=lambda argv: entered.append(argv) or 0)

    assert code == 1
    assert entered == []
    assert "unknown plugins preset" in capsys.readouterr().out


def test_repl_rejects_unknown_preset_before_claiming(tmp_path, monkeypatch):
    settings = make_settings(
        tmp_path, plugins_preset="bogus", single_backend_guard_enabled=True
    )
    monkeypatch.setattr(repl, "get_settings", lambda: settings)
    claimed: List[int] = []
    built: List[int] = []
    monkeypatch.setattr(
        server_lock, "claim_lock", lambda *args, **kwargs: claimed.append(1) or True
    )
    out: List[str] = []

    code = repl.repl_main(
        [],
        runtime_factory=lambda _settings: built.append(1),
        input_fn=lambda prompt: "exit",
        output=out.append,
        configure_stdio=False,
    )

    assert code == 1
    assert claimed == []
    assert built == []
    assert any("unknown plugins preset" in line for line in out)


def test_start_rejects_unknown_preset_before_claim_or_spawn(
    tmp_path, monkeypatch, capsys
):
    import start
    from backend import config as backend_config

    settings = make_settings(tmp_path, plugins_preset="bogus")
    monkeypatch.setattr(backend_config, "get_settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["start.py", "--backend-only"])
    actions: List[str] = []
    monkeypatch.setattr(start, "_run", lambda *args, **kwargs: actions.append("spawn"))
    monkeypatch.setattr(
        server_lock,
        "claim_lock",
        lambda *args, **kwargs: actions.append("claim") or True,
    )

    code = start.main()

    assert code == 2
    assert actions == []
    assert "unknown plugins preset" in capsys.readouterr().out


# ── prompt text follows enabled packages (D-56) ──
def test_prompt_text_does_not_advertise_disabled_packages(tmp_path):
    state = cast(
        AgentState,
        {"messages": [{"role": "user", "content": "hi"}], "step_index": 0},
    )

    minimal_tm = _manager(make_settings(tmp_path, plugins_preset="minimal"))
    minimal = AgentRuntime(
        task_id="t-min-prompt",
        task_manager=minimal_tm,
        llm=MockLLMClient(),
        tools=list(minimal_tm._tools),
        tool_schemas=minimal_tm._tool_schemas,
        packages=minimal_tm.packages,
    )
    minimal_planner = str(
        minimal._build_messages(state, minimal._planner_system)[0]["content"]
    )
    # No unavailable tool may be advertised in the model context, and the
    # delegation paragraph becomes package-neutral.
    assert "spawn_subagent" not in minimal_planner
    assert "load_skill" not in minimal_planner
    assert "web_search" not in minimal_planner
    assert minimal_planner != PLANNER_SYSTEM

    for preset in ("research", "full"):
        tm = _manager(make_settings(tmp_path, plugins_preset=preset))
        runtime = AgentRuntime(
            task_id=f"t-{preset}-prompt",
            task_manager=tm,
            llm=MockLLMClient(),
            tools=list(tm._tools),
            tool_schemas=tm._tool_schemas,
            packages=tm.packages,
        )
        planner = str(runtime._build_messages(state, runtime._planner_system)[0]["content"])
        executor = str(runtime._build_messages(state, EXECUTOR_SYSTEM)[0]["content"])
        assert planner == PLANNER_SYSTEM  # default full/research keep old text
        assert "spawn_subagent" in planner
        assert "spawn_subagent" not in executor

    # packages=None keeps the pre-#67 prompt for direct runtime tests.
    legacy_tm = _manager(make_settings(tmp_path, plugins_preset="minimal"))
    legacy = AgentRuntime(
        task_id="t-legacy-prompt",
        task_manager=legacy_tm,
        llm=MockLLMClient(),
        tools=list(legacy_tm._tools),
        tool_schemas=legacy_tm._tool_schemas,
    )
    assert legacy._planner_system == PLANNER_SYSTEM


def _state_for_prompt() -> AgentState:
    return cast(
        AgentState,
        {"messages": [{"role": "user", "content": "hi"}], "step_index": 0},
    )


_PRE_67_PLANNER_SYSTEM = (
    "You are the planner for an autonomous task agent.\n\n\n"
    "You are in PLAN mode: reading and inspecting is allowed, changing anything is not.\n"
    "Use the read-only tools available to you when you need facts about the workspace,\n"
    "then produce a concise, ordered plan as Markdown (a heading and a numbered list).\n"
    "Wrap the final plan exactly in <qianfan-plan> and </qianfan-plan>; put no other\n"
    "commentary around it. The system stores that Markdown as the task's plan file.\n"
    "If you need a clarification, reply with your question as plain text and no markers.\n"
    "\n"
    "Delegation: if part of the task is a focused, self-contained job that will feed a\n"
    "later step, plan it as one plain step such as \"Research X in a sub-agent\"; in\n"
    "execution mode the agent may then call `spawn_subagent` for it."
)


def test_default_planner_prompt_is_byte_identical_to_pre_67():
    """LOW (#306): the full/default text must not lose its blank line."""
    assert "\n\nDelegation:" in PLANNER_SYSTEM
    assert planner_system(None) == _PRE_67_PLANNER_SYSTEM
    assert planner_system(PACKAGE_IDS) == _PRE_67_PLANNER_SYSTEM
    assert planner_system(PACKAGE_IDS) == PLANNER_SYSTEM


def test_planner_prompt_honours_the_legacy_subagent_switch(tmp_path):
    """A package-on but subagent_enabled=false run must not advertise spawn."""
    assert planner_system(resolve_packages("full"), subagent_enabled=False) != (
        PLANNER_SYSTEM
    )
    assert "spawn_subagent" not in planner_system(
        resolve_packages("full"), subagent_enabled=False
    )
    # Both enabled keeps the pre-#67 text.
    assert planner_system(PACKAGE_IDS, subagent_enabled=True) == PLANNER_SYSTEM

    settings = make_settings(tmp_path, plugins_preset="full", subagent_enabled=False)
    tm = _manager(settings)
    runtime = AgentRuntime(
        task_id="t-legacy-switch",
        task_manager=tm,
        llm=MockLLMClient(),
        tools=list(tm._tools),
        tool_schemas=tm._tool_schemas,
        packages=tm.packages,
    )
    planner = str(runtime._build_messages(_state_for_prompt(), runtime._planner_system)[0]["content"])
    assert "spawn_subagent" not in planner
    assert planner != PLANNER_SYSTEM
    assert tm._subagent is None
    assert "spawn_subagent" not in {tool.name for tool in tm._tools}


# ── dispatch face: a disabled tool is unknown, not executed (spec Testing) ──
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


def test_disabled_tool_is_unknown_and_has_no_side_effect(tmp_path):
    # (a) minimal: spawn_subagent is absent from the face and dispatch refuses it.
    settings = make_settings(tmp_path)
    tm = _manager(settings, packages=resolve_packages("minimal"))
    runtime = AgentRuntime(
        task_id="t-dispatch",
        task_manager=tm,
        llm=MockLLMClient(),
        tools=list(tm._tools),
        tool_schemas=tm._tool_schemas,
        packages=tm.packages,
    )
    state = _call_state("spawn_subagent", {"name": "x", "instruction": "y"})
    runtime.tool_node(state)
    rec = state["_current_tool_calls"][0]
    assert rec["status"] == "failed"
    assert "unknown tool: spawn_subagent" in rec["error"]
    assert "spawn_subagent" not in {tool.name for tool in tm._tools}

    # (b) an explicitly narrowed face (skills only): write is unknown and the
    #     target file is never created.
    skills_only = _manager(settings, packages=frozenset({"skills"}))
    runtime2 = AgentRuntime(
        task_id="t-dispatch-2",
        task_manager=skills_only,
        llm=MockLLMClient(),
        tools=list(skills_only._tools),
        tool_schemas=skills_only._tool_schemas,
        packages=skills_only.packages,
    )
    target = tmp_path / "must-not-exist.txt"
    state2 = _call_state("write", {"path": str(target), "content": "nope"})
    runtime2.tool_node(state2)
    rec2 = state2["_current_tool_calls"][0]
    assert rec2["status"] == "failed"
    assert "unknown tool: write" in rec2["error"]
    assert not target.exists()
