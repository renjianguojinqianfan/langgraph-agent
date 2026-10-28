"""Shared pytest fixtures and path bootstrap for the offline test suite.

All tests run fully offline: they build a :class:`MockLLMClient` (or otherwise
mock the network) and use throwaway temporary directories for data / artifacts.

Environment isolation: the suite must be deterministic regardless of a local
``.env`` file (a developer may have configured a live DashScope/OpenAI
endpoint for manual runs). Since pydantic-settings resolves *environment
variables* before ``.env``, we neutralise the live-provider keys here so the
process-wide :func:`get_settings` singleton (used by the smoke test) never
picks up a real endpoint. LangSmith is neutralised the same way and more
strictly (issue #84): the SDK's own tracing switches are *removed*, so a shell
that exports them cannot make the suite dial out.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# ── Throwaway runtime directories (MUST run before any `backend.config` import) ──
# ``backend.main`` builds a module-level app whose lifespan enters the
# production TaskManager; tests use that app directly. Relative paths resolve
# against PROJECT_ROOT, so without this block every such TestClient writes the
# checkout's ``data/`` (finding TEST-01 / #252). Assign, not setdefault: a
# developer shell that exported DATA_DIR must not move the suite back onto real
# state. ``make_settings`` still gives individual tests their own tmp_path.
#
# Prefer an absolute TEMP/TMP or LOCALAPPDATA\Temp. A sandboxed interpreter can
# be denied mkdir there even though the shell may write files; probing with a
# single mkdir avoids ``tempfile.mkdtemp``'s 10k-name PermissionError loop.
# When OS temp is unavailable, a sibling of the checkout is the next safest
# place: writable, but outside both the repository and its ``data/``.
_TEST_ROOT_CANDIDATES: list[Path] = []
if os.name == "nt":
    for _env_name in ("TEMP", "TMP"):
        _raw = os.environ.get(_env_name, "")
        if _raw and Path(_raw).is_absolute():
            _TEST_ROOT_CANDIDATES.append(Path(_raw))
    _local_app_data = os.environ.get("LOCALAPPDATA")
    if _local_app_data:
        _TEST_ROOT_CANDIDATES.append(Path(_local_app_data) / "Temp")
else:
    _TEST_ROOT_CANDIDATES.append(Path(tempfile.gettempdir()))
_TEST_ROOT_CANDIDATES.append(_PROJECT_ROOT.parent)


def _try_runtime_root(parent: Path) -> Optional[Path]:
    try:
        parent.mkdir(parents=True, exist_ok=True)
        root = parent / f"qianfan-tests-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        os.mkdir(root, 0o700)
    except OSError:
        return None
    return root


_TEST_RUNTIME_ROOT: Optional[Path] = None
for _candidate in _TEST_ROOT_CANDIDATES:
    _TEST_RUNTIME_ROOT = _try_runtime_root(_candidate)
    if _TEST_RUNTIME_ROOT is not None:
        break
if _TEST_RUNTIME_ROOT is None:
    raise RuntimeError(
        "could not create an isolated test runtime directory outside the checkout"
    )
os.environ["DATA_DIR"] = str(_TEST_RUNTIME_ROOT / "data")
os.environ["ARTIFACTS_DIR"] = str(_TEST_RUNTIME_ROOT / "data" / "artifacts")
os.environ["TRACE_DIR"] = str(_TEST_RUNTIME_ROOT / "data" / "traces")
os.environ["QIANFAN_TEST_DATA_ROOT"] = str(_TEST_RUNTIME_ROOT)
atexit.register(shutil.rmtree, _TEST_RUNTIME_ROOT, ignore_errors=True)

# ── Offline isolation (MUST run before any `backend.config` import) ──
os.environ.setdefault("USE_MOCK_LLM", "true")
os.environ.setdefault("MOCK_LLM_SCRIPT_PATH", "")  # #108: a local .env script must not steer the suite
os.environ.setdefault("LLM_BASE_URL", "")  # neutralise .env live endpoint
os.environ.setdefault("LLM_API_KEY", "")
os.environ.setdefault("AUTH_ENABLED", "false")
os.environ.setdefault("MCP_ENABLED", "false")  # P2: no MCP subprocesses by default
os.environ.setdefault(
    "CHECKPOINT_ENABLED", "false"
)  # Issue #4: no sqlite checkpoint store unless a test opts in
os.environ.setdefault("CONTEXT_INJECT_ENABLED", "false")  # P1-A′: isolate the injection chain
os.environ.setdefault("CONTEXT_EVICT_ENABLED", "false")  # T1.4: isolate tool-result eviction
# C4: a developer's local frontend/dist must not change the app composition
# (CI has no build output). Tests that need a dist build their own app.
os.environ.setdefault("FRONTEND_DIST", "_no_frontend_dist_for_tests")

# ── LangSmith tracing is off for the offline suite (Issue #84) ──
# The block above zeroes *this repo's* settings; it cannot zero what the SDK
# reads for itself. langsmith/langchain-core resolve their own tracing switches
# from the process environment (langsmith.utils.tracing_is_enabled walks
# LANGSMITH_/LANGCHAIN_ x TRACING_V2/TRACING), so a developer shell exporting
# LANGSMITH_TRACING=true + a key makes this "offline" suite POST real runs to
# LangSmith — tests still pass, they just silently stop being offline (429 spam,
# slower runs, and any future "no network" assertion quietly bypassed). The
# repo's own ``trace_enabled`` gates backend/services/trace.py (JSONL recorder)
# and never reaches the SDK, so the switch has to leave the environment here —
# at conftest import, i.e. before the first callback manager is configured,
# which happens per graph run inside a test. pop, not setdefault: an inherited
# "true" must lose. Dropping the credentials along with the switches keeps the
# suite's claim (零网络、零 Key) literally true in the test process.
for _k in (
    "LANGSMITH_TRACING",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING",
    "LANGCHAIN_TRACING_V2",
    "LANGCHAIN_HANDLER",
    "LANGSMITH_API_KEY",
    "LANGCHAIN_API_KEY",
):
    os.environ.pop(_k, None)

# ── langgraph's own serde switch (Part of #212) ──
# langgraph.checkpoint.serde._msgpack reads LANGGRAPH_STRICT_MSGPACK **at
# import time**, so a per-test monkeypatch cannot undo it. Popping it here —
# before any backend import — keeps the serde sentinel on the permissive path
# that actually exercises the #9251 reconstruction fallback. pop, not
# setdefault: an inherited "true" must lose.
os.environ.pop("LANGGRAPH_STRICT_MSGPACK", None)

# Ensure `import backend...` works regardless of the current working directory.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from backend.config import Settings  # noqa: E402
from backend.core.llm.client import MockLLMClient  # noqa: E402
from backend.core.tools.registry import build_tools  # noqa: E402
from backend.services.event_bus import EventBus  # noqa: E402
from backend.services.model_store import ModelStore  # noqa: E402
from backend.services.persistence import Persistence  # noqa: E402
from backend.services.task_manager import TaskManager  # noqa: E402

_current_request: ContextVar[Optional["pytest.FixtureRequest"]] = ContextVar(
    "_current_request", default=None
)


@pytest.fixture(autouse=True)
def _capture_request(request):
    """Expose the active pytest request to make_manager for finalizer wiring."""
    token = _current_request.set(request)
    yield
    _current_request.reset(token)


def make_settings(tmp_path: Path, **overrides) -> Settings:
    """Build an isolated :class:`Settings` rooted at ``tmp_path``.

    Explicitly sets ``llm_base_url=""`` so the offline suite is isolated from
    any local ``.env`` (e.g. a live DashScope/OpenAI endpoint): with an empty
    value the provider factory falls back to its built-in preset, keeping the
    assertions deterministic regardless of the developer's local environment.

    Checkpoint persistence defaults OFF (env setdefault above); pass
    ``checkpoint_enabled=True`` to opt a test into the sqlite checkpoint
    store — it then lives under ``tmp_path / "checkpoints"``.
    """
    base: dict[str, Any] = dict(
        data_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        trace_dir=str(tmp_path / "traces"),
        # Explicit: a developer's CHECKPOINT_DIR must not leak one sqlite file
        # into every test that opts into checkpoints.
        checkpoint_dir="",
        llm_provider="deepseek",
        llm_base_url="",
        llm_model="deepseek-chat",
        max_steps=50,
        sandbox_timeout=5,
        use_mock_llm=True,
        # P2: disable subprocess-spawning MCP children by default so the
        # offline suite never starts them. The MCP tests opt in explicitly.
        mcp_enabled=False,
    )
    base.update(overrides)
    return Settings(**base)


def make_manager(
    settings: Settings,
    mock: MockLLMClient | None = None,
    event_bus: EventBus | None = None,
    persistence: Persistence | None = None,
    *,
    llm_factory: Any = None,
    model_store: Any = None,
) -> TaskManager:
    """Construct a :class:`TaskManager` wired with a scripted mock LLM.

    Registers an autouse teardown so every manager built through this helper
    releases its process-level resources (MCP children, checkpoint sqlite
    connection) even when a test forgets to call ``shutdown()``.
    """
    eb = event_bus or EventBus()
    persistence = persistence or Persistence(settings)
    tools = build_tools(settings)
    # Credentials stay under the test's tmp dir: a test must never write the
    # developer's real ~/.qianfan/credentials.json.
    store = model_store or ModelStore(settings, home_dir=settings.data_path / "home")
    tm = TaskManager(
        settings,
        eb,
        persistence,
        llm_client=mock,
        tools=tools,
        default_workspace=settings.artifacts_path,
        llm_factory=llm_factory,
        model_store=store,
    )

    _request = _current_request.get()
    if _request is not None:
        _request.addfinalizer(tm.shutdown)
    return tm


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def persistence(settings) -> Persistence:
    return Persistence(settings)
