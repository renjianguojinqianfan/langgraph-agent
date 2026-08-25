"""Tests for plugin tool discovery (P0 item 3).

Covers: a compliant ``BaseTool`` dropped into a plugin directory is loaded and
callable; recursive subdirectory discovery; missing-directory skip; plugin
tools appearing in ``build_tools``; name-conflict keeps the first registered
tool with a warning; ``make_openapi_tool`` generates tools (P1 item 6).

Each test isolates the global ``_REGISTRY`` / imported-path set so it never
leaks into other tests.
"""

from __future__ import annotations

import pytest

import backend.core.tools.registry as registry
from backend.config import Settings
from backend.core.tools.base import BaseTool, ToolResult
from backend.core.tools.registry import build_tools, discover_plugins, get_tool, register


@pytest.fixture
def clean_registry():
    saved = dict(registry._REGISTRY)
    saved_imported = set(registry._IMPORTED_PLUGINS)
    yield
    registry._REGISTRY.clear()
    registry._REGISTRY.update(saved)
    registry._IMPORTED_PLUGINS.clear()
    registry._IMPORTED_PLUGINS.update(saved_imported)


def _write_plugin(dirpath, name: str, body: str):
    p = dirpath / name
    p.write_text(body, encoding="utf-8")
    return p


_SIMPLE_TOOL_BODY = (
    "from backend.core.tools.base import BaseTool, ToolResult\n"
    "from backend.core.tools.registry import register\n"
    "@register\n"
    "class HelloTool(BaseTool):\n"
    "    name = 'hello_plugin'\n"
    "    description = 'hello'\n"
    "    args_schema = {}\n"
    "    def run(self, **kwargs):\n"
    "        return ToolResult(success=True, data={'hello': kwargs.get('name')})\n"
)


def test_discover_plugins_loads_compliant_tool(tmp_path, clean_registry):
    _write_plugin(tmp_path, "hello_tool.py", _SIMPLE_TOOL_BODY)
    n = discover_plugins(tmp_path)
    assert n == 1
    cls = get_tool("hello_plugin")
    assert cls is not None
    tool = cls(Settings())
    res = tool.run(name="world")
    assert res.success is True
    assert res.data == {"hello": "world"}


def test_discover_plugins_recursive_subdirectory(tmp_path, clean_registry):
    sub = tmp_path / "nested"
    sub.mkdir(parents=True)
    body = (
        "from backend.core.tools.base import BaseTool, ToolResult\n"
        "from backend.core.tools.registry import register\n"
        "@register\n"
        "class NestedTool(BaseTool):\n"
        "    name = 'nested_plugin'\n"
        "    description = 'nested'\n"
        "    args_schema = {}\n"
        "    def run(self, **kwargs):\n"
        "        return ToolResult(success=True, data={})\n"
    )
    _write_plugin(sub, "nested_tool.py", body)
    assert discover_plugins(tmp_path) == 1
    assert get_tool("nested_plugin") is not None


def test_discover_plugins_is_idempotent(tmp_path, clean_registry):
    _write_plugin(tmp_path, "hello_tool.py", _SIMPLE_TOOL_BODY)
    assert discover_plugins(tmp_path) == 1
    assert discover_plugins(tmp_path) == 0  # already imported


def test_discover_plugins_skips_missing_dir(clean_registry):
    assert discover_plugins("/definitely/not/a/real/dir") == 0


def test_discover_plugins_build_tools_includes_plugin(tmp_path, clean_registry):
    _write_plugin(tmp_path, "build_tool.py", _SIMPLE_TOOL_BODY.replace("hello_plugin", "build_plugin"))
    discover_plugins(tmp_path)
    names = {t.name for t in build_tools(Settings())}
    assert "build_plugin" in names


def test_discover_plugins_broken_module_does_not_abort(tmp_path, clean_registry):
    _write_plugin(tmp_path, "bad_tool.py", "this is not valid python ((")
    _write_plugin(tmp_path, "good_tool.py", _SIMPLE_TOOL_BODY)
    # Broken plugin is skipped; the valid one still loads.
    discover_plugins(tmp_path)
    assert get_tool("hello_plugin") is not None


def test_register_keeps_first_on_conflict(clean_registry, caplog):
    class FirstTool(BaseTool):
        name = "dup_tool"
        description = "first"
        args_schema = {}

        def run(self, **kwargs) -> ToolResult:
            return ToolResult(success=True, data={"which": "first"})

    class SecondTool(BaseTool):
        name = "dup_tool"
        description = "second"
        args_schema = {}

        def run(self, **kwargs) -> ToolResult:
            return ToolResult(success=True, data={"which": "second"})

    register(FirstTool)
    register(SecondTool)

    cls = get_tool("dup_tool")
    assert cls is FirstTool
    assert any("already registered" in r.message for r in caplog.records)


def test_make_openapi_tool_returns_tools_for_valid_spec():
    """P1 item 6: the placeholder is replaced by a real generator."""
    from backend.core.tools.openapi_tool import OpenAPISpecError

    spec = {
        "openapi": "3.0.0",
        "info": {"title": "t", "version": "1.0"},
        "paths": {
            "/pets": {
                "get": {"operationId": "listPets", "responses": {"200": {}}},
                "post": {"operationId": "createPet", "responses": {"201": {}}},
            }
        },
    }
    tools = registry.make_openapi_tool(spec)
    assert len(tools) == 2
    assert {t.name for t in tools} == {"listPets", "createPet"}


def test_make_openapi_tool_raises_spec_error_for_invalid_spec():
    from backend.core.tools.openapi_tool import OpenAPISpecError

    with pytest.raises(OpenAPISpecError):
        registry.make_openapi_tool({})
