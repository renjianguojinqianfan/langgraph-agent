"""C1 (#156) deletion regression: the removed capability faces stay removed.

One assertion per seam the slice moved, so a leftover import, config key,
route or tier member fails mechanically instead of surviving as a ghost:

* the built-in tool face (knowledge base, Git, plugins, OpenAPI);
* the read-only / sub-agent tiers (no Git, no knowledge base);
* settings (`kb_*`, `git_*`, `openapi_*`, `plugins_*`, `aux_llm_*`);
* REST routes (`/kb*`, `/tasks/{id}/replan`) and the runtime knobs behind them
  (re-plan round, auxiliary model);
* plugin auto-discovery and OpenAPI generation helpers.
"""

from __future__ import annotations

import importlib.util
import inspect

from backend.api.routes import router
from backend.config import Settings
from backend.core.agent.nodes import AgentRuntime
from backend.core.agent.subagent import EXECUTE_TIER, EXPLORE_TIER, TOOL_TIERS
from backend.core.tools import registry
from backend.core.tools.registry import build_tools
from backend.tests.conftest import make_settings

REMOVED_TOOL_NAMES = {"kb_query", "memory_search", "example_echo"}


def test_builtin_tool_face_has_no_removed_features(tmp_path):
    names = {t.name for t in build_tools(make_settings(tmp_path))}

    assert not names & REMOVED_TOOL_NAMES
    assert not [n for n in names if n.startswith("git_")]
    # The survivors of C1, still mounted (C3 replaces code_exec/http_request
    # with bash; this slice does not touch them).
    assert {
        "read",
        "write",
        "edit",
        "ls",
        "glob",
        "grep",
        "load_skill",
        "web_search",
        "spawn_subagent",
        "code_exec",
        "http_request",
    } <= names


def test_plugin_and_openapi_discovery_helpers_are_gone():
    assert not hasattr(registry, "discover_plugins")
    assert not hasattr(registry, "make_openapi_tool")
    assert importlib.util.find_spec("backend.plugins") is None


def test_settings_have_no_removed_prefixes():
    removed_prefixes = ("kb_", "git_", "openapi_", "plugins_", "aux_llm_")
    leftovers = [
        name for name in Settings.model_fields if name.startswith(removed_prefixes)
    ]
    assert leftovers == []


def test_routes_have_no_kb_or_replan_endpoints():
    paths = {getattr(route, "path", "") for route in router.routes}
    assert not [p for p in paths if "/kb" in p]
    assert not [p for p in paths if p.endswith("/replan")]


def test_task_manager_has_no_removed_wiring(tmp_path):
    from backend.services.task_manager import TaskManager

    assert not hasattr(TaskManager, "_load_git_tools")
    assert not hasattr(TaskManager, "_load_openapi_tools")
    assert not hasattr(TaskManager, "replan")

    from backend.core.llm.client import MockLLMClient
    from backend.tests.conftest import make_manager

    tm = make_manager(make_settings(tmp_path), MockLLMClient(final_answer="ok"))
    assert not hasattr(tm, "_kb")
    assert not hasattr(tm, "_aux_llm")


def test_read_only_and_subagent_tiers_are_the_six_name_sets():
    explore = set(TOOL_TIERS[EXPLORE_TIER])
    assert explore == {"read", "ls", "glob", "grep", "load_skill", "web_search"}
    assert set(TOOL_TIERS[EXECUTE_TIER]) == explore | {"write", "edit"}


def test_runtime_has_no_aux_or_replan_knobs():
    params = inspect.signature(AgentRuntime.__init__).parameters
    assert "aux_llm" not in params
    assert "replan" not in params
