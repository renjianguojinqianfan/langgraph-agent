"""FastAPI application entry point.

Wires configuration, the event bus, persistence and the task manager into
``app.state`` and exposes the REST + SSE API under ``/api``.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

from .api.routes import router
from .config import get_settings
from .services.auth import TokenIssuer, auth_state_notice, ensure_startup_auth_safe
from .services.event_bus import EventBus
from .services.persistence import Persistence, PersistenceError
from .services.task_manager import TaskManager
from .utils.logging import get_logger

logger = get_logger("main")

settings = get_settings()

# Security guard (#254 AC1): refuse to silently serve with auth enabled but no
# usable secret, and always state the auth posture once at startup.
ensure_startup_auth_safe(settings)
if settings.auth_enabled:
    logger.info(auth_state_notice(settings))
else:
    logger.warning(auth_state_notice(settings))


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.data_path.mkdir(parents=True, exist_ok=True)
    settings.artifacts_path.mkdir(parents=True, exist_ok=True)
    app.state.settings = settings
    app.state.event_bus = EventBus()
    app.state.persistence = Persistence(settings)
    # P1 item 5: token issuer (used by verify_token when auth_enabled=True).
    app.state.auth = TokenIssuer(settings.auth_token, settings.auth_token_ttl_sec)
    app.state.task_manager = TaskManager(
        settings,
        app.state.event_bus,
        app.state.persistence,
        default_workspace=Path.cwd(),
    )
    yield
    # P2 item 1: gracefully shut down MCP child processes on app exit so no
    # stdio subprocess survives the server.
    try:
        app.state.task_manager.shutdown()
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[lifespan] task_manager.shutdown failed: {exc}")


app = FastAPI(title="LangGraph Autonomous Task Agent", version="0.1.0", lifespan=lifespan)


@app.exception_handler(PersistenceError)
async def _persistence_error_handler(
    _request: Request, exc: PersistenceError
) -> JSONResponse:
    """A failed tasks.json write is a server-side failure, not a silent 200."""
    return JSONResponse(status_code=500, content={"detail": str(exc)})

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# DNS-rebinding guard (ADR 0009 decision 5): the service is opened as localhost,
# so any other Host name is refused before routing. Independent of
# ``settings.host`` on purpose — this chapter does not support remote access.
# Added last = outermost.
TRUSTED_HOSTS = ["127.0.0.1", "localhost"]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=TRUSTED_HOSTS)

app.include_router(router, prefix="/api")


@app.get("/health")
def health(request: Request) -> dict:
    s = getattr(request.app.state, "settings", settings)
    return {
        "status": "ok",
        "mock_llm": s.use_mock_llm,
        "auth_enabled": s.auth_enabled,  # P1 item 5: frontend login gate
        # W2: the launcher's one-time identity, so a second launch can tell
        # this backend apart from an unrelated program on the same port.
        "instance": os.environ.get("QIANFAN_INSTANCE", ""),
    }


def mount_frontend(app: FastAPI, dist: Path) -> bool:
    """Serve the built frontend at ``/`` when it exists (ADR 0010).

    Registered after every API route so ``/api`` and ``/health`` keep
    precedence. Returns whether the build output was mounted.

    ``StaticFiles`` (starlette>=1.3.1) refuses leading-separator paths before
    ``os.path.realpath`` and keeps resolution inside ``dist``; this replaces the
    removed CVE-2026-48818 mitigation (see docs/security.md §6).
    """
    if not (dist / "index.html").is_file():
        return False
    app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")
    return True


mount_frontend(app, settings.frontend_dist_path)
