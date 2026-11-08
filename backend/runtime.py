"""Process runtime assembly shared by the web app and the terminal REPL.

D-48 (#20): the FastAPI lifespan and `qianfan repl` must build the SAME
EventBus / Persistence / TaskManager wiring; this module is that single seam.
It deliberately does not import `backend.main` (no FastAPI app, no routes), so
the REPL and its tests stay light and an embedded runner can reuse it too.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .services.event_bus import EventBus
from .services.persistence import Persistence
from .services.task_manager import TaskManager


@dataclass
class Runtime:
    """One process's task runtime (EventBus + Persistence + TaskManager)."""

    settings: Settings
    event_bus: EventBus
    persistence: Persistence
    task_manager: TaskManager


def build_runtime(settings: Settings) -> Runtime:
    """Create the process runtime with the same wiring the web lifespan uses."""
    settings.data_path.mkdir(parents=True, exist_ok=True)
    settings.artifacts_path.mkdir(parents=True, exist_ok=True)
    event_bus = EventBus()
    persistence = Persistence(settings)
    task_manager = TaskManager(
        settings,
        event_bus,
        persistence,
        default_workspace=Path.cwd(),
    )
    return Runtime(
        settings=settings,
        event_bus=event_bus,
        persistence=persistence,
        task_manager=task_manager,
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
