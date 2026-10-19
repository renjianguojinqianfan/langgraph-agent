"""Registry semantics that outlived the plugin feature (#156 C1).

Three cases were deleted together with ``test_plugins.py`` / ``test_qa_plugins.py``
when plugin discovery went away, but they pin the still-live ``@register``
contract, not plugin loading:

* the first registration wins on a name conflict (with a warning);
* an empty tool name is rejected;
* ``build_tools`` instantiates exactly one class per registered name.

Each test isolates the global registry so it never leaks into another test.
"""

from __future__ import annotations

import pytest

import backend.core.tools.registry as registry
from backend.config import Settings
from backend.core.tools.base import BaseTool, ToolResult
from backend.core.tools.registry import build_tools, get_tool, register


@pytest.fixture
def clean_registry():
    saved = dict(registry._REGISTRY)
    yield
    registry._REGISTRY.clear()
    registry._REGISTRY.update(saved)


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
