"""FastAPI application entry point.

Wires configuration, the event bus, persistence and the task manager into
``app.state`` and exposes the REST + SSE API under ``/api``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .config import get_settings
from .services.auth import TokenIssuer
from .services.event_bus import EventBus
from .services.persistence import Persistence
from .services.task_manager import TaskManager

settings = get_settings()


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
        settings, app.state.event_bus, app.state.persistence
    )
    yield
    # P2 item 1: gracefully shut down MCP child processes on app exit so no
    # stdio subprocess survives the server.
    try:
        app.state.task_manager.shutdown()
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[lifespan] task_manager.shutdown failed: {exc}")


app = FastAPI(title="LangGraph Autonomous Task Agent", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/health")
def health(request: Request) -> dict:
    s = getattr(request.app.state, "settings", settings)
    return {
        "status": "ok",
        "mock_llm": s.use_mock_llm,
        "auth_enabled": s.auth_enabled,  # P1 item 5: frontend login gate
    }
