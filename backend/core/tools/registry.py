"""Tool registry and auto-discovery.

Tools register their *class* (keyed by ``name``) via the :func:`register`
decorator. :func:`build_tools` instantiates every registered tool with the
current :class:`~backend.config.Settings`, which is what the kernel uses.
Adding a new tool is therefore a matter of writing a ``BaseTool`` subclass and
decorating it with ``@register`` — the Agent picks it up automatically.

P0 plugin support (:func:`discover_plugins`) extends this to external tool
modules: a compliant ``BaseTool`` dropped into the plugins directory (or a
subdirectory) is imported at startup and registers through the *same*
``_REGISTRY`` as the built-in tools. Name conflicts keep the first registered
tool and log a warning — never a silent overwrite.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from ...config import Settings
from ...utils.logging import get_logger
from .base import BaseTool

logger = get_logger("tool.registry")

_REGISTRY: Dict[str, Type[BaseTool]] = {}

# Resolved plugin paths already imported — makes discovery idempotent so
# repeated TaskManager construction does not re-import / re-register plugins
# (which would otherwise spam "already registered" warnings).
_IMPORTED_PLUGINS: set = set()


def register(cls: Type[BaseTool]) -> Type[BaseTool]:
    """Class decorator that registers a tool by its ``name``.

    If another tool with the same ``name`` is already registered, the first one
    wins and a warning is logged (the new class is still returned so chained
    usage keeps working, but it is *not* installed into the registry).
    """
    if not cls.name:
        raise ValueError(f"Tool {cls.__name__} must define a non-empty `name`.")
    existing = _REGISTRY.get(cls.name)
    if existing is not None:
        logger.warning(
            "Tool name %r already registered by %s; keeping the existing one "
            "(new %s ignored).",
            cls.name,
            existing.__name__,
            cls.__name__,
        )
        return cls
    _REGISTRY[cls.name] = cls
    return cls


def _import_module_from_path(path: Path):
    """Import a single ``.py`` file as a standalone module.

    Works for single-file modules that are not part of a package (no
    ``__init__.py`` required). Import errors are caught and logged — a broken
    plugin must never abort startup.
    """
    path = Path(path)
    # Unique module name per path (hash is stable within the process).
    module_name = f"_plugin_{path.stem}_{abs(hash(str(path.resolve()))) & 0xFFFFFFFF:x}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            logger.warning("Could not create import spec for %s", path)
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to import plugin %s: %s", path, exc)
        sys.modules.pop(module_name, None)
        return None


def discover_plugins(plugins_dir) -> int:
    """Recursively scan ``plugins_dir`` for ``*.py`` and import them.

    Importing triggers any ``@register`` decorators so plugin tools land in the
    shared ``_REGISTRY``. A missing directory is skipped silently (zero
    regression). Returns the number of modules imported.
    """
    root = Path(plugins_dir)
    if not root.exists() or not root.is_dir():
        logger.info("Plugin directory %s does not exist; skipping discovery.", root)
        return 0

    count = 0
    for path in sorted(root.rglob("*.py")):
        resolved = str(path.resolve())
        if resolved in _IMPORTED_PLUGINS:
            continue  # already imported by an earlier discovery pass
        if _import_module_from_path(path) is not None:
            _IMPORTED_PLUGINS.add(resolved)
            count += 1
    logger.info("discover_plugins scanned %s (%d new module(s))", root, count)
    return count


def make_openapi_tool(
    spec: Dict[str, Any],
    settings: Optional[Settings] = None,
) -> List[BaseTool]:
    """P1 item 6: generate one :class:`BaseTool` per OpenAPI operation.

    Delegates to :func:`backend.core.tools.openapi_tool.build_tools_from_spec`
    and returns the generated instances (a spec maps to *several* tools).
    Invalid specs raise :class:`OpenAPISpecError` — callers (TaskManager)
    catch it, log a warning and continue startup.
    """
    from .openapi_tool import build_tools_from_spec

    return build_tools_from_spec(spec, settings=settings)


def get_tool(name: str) -> BaseTool | None:
    cls = _REGISTRY.get(name)
    return cls


def list_tools() -> List[Type[BaseTool]]:
    return list(_REGISTRY.values())


def build_tools(settings: Settings) -> List[BaseTool]:
    """Instantiate all registered tools with the given settings."""
    return [cls(settings) for cls in _REGISTRY.values()]


def build_tool_schemas(settings: Settings) -> List[Dict[str, Any]]:
    """OpenAI-compatible function schemas for every registered tool."""
    return [tool.to_openai_schema() for tool in build_tools(settings)]
