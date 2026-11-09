"""Process runtime assembly shared by the web app and the terminal REPL.

D-48 (#20): the FastAPI lifespan and `qianfan repl` must build the SAME
EventBus / Persistence / TaskManager wiring; this module is that single seam.
It deliberately does not import `backend.main` (no FastAPI app, no routes), so
the REPL and its tests stay light and an embedded runner can reuse it too.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional

from .config import Settings
from .services.event_bus import EventBus
from .services.persistence import Persistence
from .services.plugins_store import EffectivePluginsConfig, resolve_effective_plugins
from .services.task_manager import TaskManager
from .utils.logging import get_logger
from .utils.net import normalize_no_proxy

logger = get_logger("runtime")


@dataclass
class Runtime:
    """One process's task runtime (EventBus + Persistence + TaskManager)."""

    settings: Settings
    event_bus: EventBus
    persistence: Persistence
    task_manager: TaskManager
    #: #67 P4-2: what this process actually assembled (saved file + CLI override).
    plugins: EffectivePluginsConfig


def build_runtime(
    settings: Settings, *, preset_override: Optional[str] = None
) -> Runtime:
    """Create the process runtime with the same wiring the web lifespan uses.

    D-54 (#67): resolve data/plugins.json (CLI override > file > env > full)
    before touching data/. An invalid file / preset fails here with a readable
    error naming the file path and the fix, for both the web and REPL entries.
    """
    # A bracketed IPv6 NO_PROXY entry makes every env-reading HTTP client
    # (OpenAI SDK included) raise while it is constructed. Normalize the
    # process env before the task manager can build one.
    if normalize_no_proxy():
        logger.info("NO_PROXY normalized: IPv6 brackets removed")
    plugins = resolve_effective_plugins(settings, preset_override=preset_override)
    settings.data_path.mkdir(parents=True, exist_ok=True)
    settings.artifacts_path.mkdir(parents=True, exist_ok=True)
    event_bus = EventBus()
    persistence = Persistence(settings)
    task_manager = TaskManager(
        settings,
        event_bus,
        persistence,
        default_workspace=Path.cwd(),
        plugins=plugins,
    )
    # #308 M1: expose the actually assembled tool face (legacy gates + dynamic
    # MCP tools included), not just the manifest's static package names.
    plugins = replace(plugins, tools=task_manager.assembled_tool_names)
    return Runtime(
        settings=settings,
        event_bus=event_bus,
        persistence=persistence,
        task_manager=task_manager,
        plugins=plugins,
    )


def model_key_is_missing(settings: Settings, store: Any) -> bool:
    """True when no model entry can serve a call (D-44 posture, logger-free).

    Mock mode never needs a key. Otherwise the default model must have a usable
    key source, or *some* entry must already have one; a missing default model
    with another keyed entry is not "no usable key".
    """
    if settings.use_mock_llm:
        return False
    snapshot = store.list_public()
    default_id = str(snapshot.get("default") or "")
    models = list(snapshot.get("models") or [])
    default = next((item for item in models if item.get("id") == default_id), None)
    default_source = str((default or {}).get("key_source") or "none")
    any_key = any(bool(item.get("key_set")) for item in models)
    return not (default_source != "none" or any_key)
