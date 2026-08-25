"""QA independent edge-case tests — plugin tool registration (P0 item 3).

Reviewer-perspective coverage: ``plugins_autoload=false`` truly skips discovery
inside TaskManager, plugin tools end up in the manager's tool list when autoload
is on, single-file modules without ``__init__.py`` load, the real example plugin
registers and is callable, empty-name registration is rejected, classes without
``@register`` never leak into the registry, and name conflicts instantiate the
first-registered class.
"""

from __future__ import annotations

import importlib.util

import pytest

import backend.core.tools.registry as registry
from backend.config import PROJECT_ROOT, Settings
from backend.core.tools.base import BaseTool, ToolResult
from backend.core.tools.registry import build_tools, discover_plugins, get_tool, register
from backend.services.event_bus import EventBus
from backend.services.persistence import Persistence
from backend.services.task_manager import TaskManager


@pytest.fixture
def clean_registry():
    saved = dict(registry._REGISTRY)
    saved_imported = set(registry._IMPORTED_PLUGINS)
    yield
    registry._REGISTRY.clear()
    registry._REGISTRY.update(saved)
    registry._IMPORTED_PLUGINS.clear()
    registry._IMPORTED_PLUGINS.update(saved_imported)


_SIMPLE_BODY = (
    "from backend.core.tools.base import BaseTool, ToolResult\n"
    "from backend.core.tools.registry import register\n"
    "@register\n"
    "class QaTool(BaseTool):\n"
    "    name = 'qa_plugin'\n"
    "    description = 'qa plugin'\n"
    "    args_schema = {}\n"
    "    def run(self, **kwargs):\n"
    "        return ToolResult(success=True, data={'ok': True})\n"
)


def _manager_settings(tmp_path, **overrides) -> Settings:
    base = {
        "data_dir": str(tmp_path / "data"),
        "artifacts_dir": str(tmp_path / "artifacts"),
        "trace_dir": str(tmp_path / "traces"),
        "plugins_dir": str(tmp_path),
        "use_mock_llm": True,
    }
    base.update(overrides)
    return Settings(**base)


# ── autoload switch behaviour inside TaskManager ──────────────
def test_plugins_autoload_false_skips_discovery(tmp_path, clean_registry, monkeypatch):
    (tmp_path / "qa_tool.py").write_text(_SIMPLE_BODY, encoding="utf-8")

    called = {"n": 0}
    real = registry.discover_plugins

    def spy(_dir):
        called["n"] += 1
        return real(_dir)

    monkeypatch.setattr(registry, "discover_plugins", spy)
    settings = _manager_settings(tmp_path, plugins_autoload=False, trace_enabled=False)
    TaskManager(settings, EventBus(), Persistence(settings))

    assert called["n"] == 0
    assert get_tool("qa_plugin") is None


def test_task_manager_discovers_plugin_into_tools(tmp_path, clean_registry):
    (tmp_path / "qa_tool.py").write_text(_SIMPLE_BODY, encoding="utf-8")
    settings = _manager_settings(tmp_path, plugins_autoload=True)
    tm = TaskManager(settings, EventBus(), Persistence(settings))

    names = {t.name for t in tm._tools}
    assert "qa_plugin" in names


# ── discovery details ─────────────────────────────────────────
def test_single_file_module_without_init_loads(tmp_path, clean_registry):
    # No __init__.py anywhere in the tree — plain single-file module.
    (tmp_path / "standalone.py").write_text(_SIMPLE_BODY, encoding="utf-8")
    assert discover_plugins(tmp_path) == 1
    assert get_tool("qa_plugin") is not None


def test_class_without_register_not_registered(tmp_path, clean_registry):
    body = (
        "from backend.core.tools.base import BaseTool, ToolResult\n"
        "class Plain(BaseTool):\n"
        "    name = 'plain_tool'\n"
        "    description = 'd'\n"
        "    args_schema = {}\n"
        "    def run(self, **kwargs):\n"
        "        return ToolResult(success=True, data={})\n"
    )
    (tmp_path / "plain.py").write_text(body, encoding="utf-8")
    discover_plugins(tmp_path)
    assert get_tool("plain_tool") is None


def test_register_rejects_empty_name(clean_registry):
    class NoName(BaseTool):
        name = ""
        description = "d"
        args_schema = {}

        def run(self, **kwargs) -> ToolResult:
            return ToolResult(success=True)

    with pytest.raises(ValueError):
        register(NoName)


def test_conflict_build_tools_instantiates_first(clean_registry):
    class First(BaseTool):
        name = "dup_qa"
        description = "first"
        args_schema = {}

        def run(self, **kwargs) -> ToolResult:
            return ToolResult(success=True, data={"which": "first"})

    class Second(BaseTool):
        name = "dup_qa"
        description = "second"
        args_schema = {}

        def run(self, **kwargs) -> ToolResult:
            return ToolResult(success=True, data={"which": "second"})

    register(First)
    register(Second)
    tools = build_tools(Settings())
    dup = [t for t in tools if t.name == "dup_qa"]
    assert len(dup) == 1
    assert dup[0].run().data == {"which": "first"}


def test_example_plugin_registered_from_real_dir(clean_registry):
    """P0 3.4 smoke: the shipped example plugin registers and is callable."""
    path = PROJECT_ROOT / "backend" / "plugins" / "example_tool.py"
    spec = importlib.util.spec_from_file_location("_qa_example_plugin", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # triggers @register

    cls = get_tool("example_echo")
    assert cls is not None
    tool = cls(Settings())
    res = tool.run(text="hi")
    assert res.success is True
    assert res.data == {"echo": "hi"}
