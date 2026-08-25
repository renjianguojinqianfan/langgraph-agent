"""Shared pytest fixtures and path bootstrap for the offline test suite.

All tests run fully offline: they build a :class:`MockLLMClient` (or otherwise
mock the network) and use throwaway temporary directories for data / artifacts.

Environment isolation: the suite must be deterministic regardless of a local
``.env`` file (a developer may have configured a live DashScope/OpenAI endpoint
for ``scripts/live_e2e.py``). Since pydantic-settings resolves *environment
variables* before ``.env``, we neutralise the live-provider keys here so the
process-wide :func:`get_settings` singleton (used by the smoke test) never
picks up a real endpoint.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ── Offline isolation (MUST run before any `backend.config` import) ──
os.environ.setdefault("USE_MOCK_LLM", "true")
os.environ.setdefault("LLM_BASE_URL", "")  # neutralise .env live endpoint
os.environ.setdefault("LLM_API_KEY", "")
os.environ.setdefault("AUX_LLM_ENABLED", "false")
os.environ.setdefault("AUTH_ENABLED", "false")
os.environ.setdefault("OPENAPI_ENABLED", "false")
os.environ.setdefault("MCP_ENABLED", "false")  # P2: no MCP subprocesses by default
os.environ.setdefault("GIT_ENABLED", "false")  # P2: no Git tools by default

# Ensure `import backend...` works regardless of the current working directory.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from backend.config import Settings  # noqa: E402
from backend.core.llm.client import MockLLMClient  # noqa: E402
from backend.core.tools.registry import build_tools  # noqa: E402
from backend.services.event_bus import EventBus  # noqa: E402
from backend.services.persistence import Persistence  # noqa: E402
from backend.services.task_manager import TaskManager  # noqa: E402


def make_settings(tmp_path: Path, **overrides) -> Settings:
    """Build an isolated :class:`Settings` rooted at ``tmp_path``.

    Explicitly sets ``llm_base_url=""`` so the offline suite is isolated from
    any local ``.env`` (e.g. a live DashScope/OpenAI endpoint): with an empty
    value the provider factory falls back to its built-in preset, keeping the
    assertions deterministic regardless of the developer's local environment.
    """
    base = dict(
        data_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        trace_dir=str(tmp_path / "traces"),
        kb_dir=str(tmp_path / "kb"),
        llm_base_url="",
        max_steps=50,
        sandbox_timeout=5,
        use_mock_llm=True,
        # P2: disable subprocess-spawning features by default so the offline
        # suite never starts MCP children or depends on git binaries. The
        # P2-specific tests opt in explicitly.
        mcp_enabled=False,
        git_enabled=False,
    )
    base.update(overrides)
    return Settings(**base)


def make_manager(
    settings: Settings,
    mock: MockLLMClient,
    event_bus: EventBus | None = None,
    persistence: Persistence | None = None,
) -> TaskManager:
    """Construct a :class:`TaskManager` wired with a scripted mock LLM."""
    eb = event_bus or EventBus()
    persistence = persistence or Persistence(settings)
    tools = build_tools(settings)
    return TaskManager(
        settings,
        eb,
        persistence,
        llm_client=mock,
        tools=tools,
    )


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def persistence(settings) -> Persistence:
    return Persistence(settings)
