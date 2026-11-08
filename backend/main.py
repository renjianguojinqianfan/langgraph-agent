"""FastAPI application entry point.

Wires configuration, the event bus, persistence and the task manager into
``app.state`` and exposes the REST + SSE API under ``/api``.

``create_app(settings)`` is the assembly seam (D-16): the module-level ``app``
is built from the process settings so ``uvicorn backend.main:app`` keeps
working, while tests can build a throwaway app with their own ``Settings`` and
assert against the real middleware stack (CORS / TrustedHost / lifespan /
routes) instead of a hand-built probe.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

from . import server_lock
from .api.routes import router
from .config import Settings, ensure_loopback_host, get_settings
from .core.capability_packages import resolve_packages
from .runtime import build_runtime, model_key_is_missing
from .services.auth import (
    TokenIssuer,
    auth_state_message,
    ensure_startup_auth_configured,
)
from .services.persistence import PersistenceError
from .utils.logging import get_logger, install_access_log_redaction

logger = get_logger("main")

# D-47 (#271): redact `?token=...` from uvicorn access-log records. Installed at
# import time so both entries are covered — in-process `uvicorn.run` (`cli`) and
# `python -m uvicorn backend.main:app` (including the --reload child, which
# imports this module fresh). The filter is idempotent and only touches the
# `uvicorn.access` logger.
install_access_log_redaction()

settings = get_settings()

# Security guard (#254 AC1): refuse to silently serve with auth enabled but no
# usable secret, and always state the auth posture once at startup.
ensure_startup_auth_configured(settings)
logger.info(auth_state_message(settings))

# DNS-rebinding guard (ADR 0009 decision 5): the service is opened as localhost,
# so any other Host name is refused before routing. Independent of
# ``settings.host`` on purpose — this chapter does not support remote access.
# Starlette's TrustedHostMiddleware compares the parsed host with brackets kept
# around IPv6 literals ("[::1]"), so the IPv6 loopback entry is bracketed (M2).
TRUSTED_HOSTS = ["127.0.0.1", "localhost", "::1", "[::1]"]


def warn_if_no_usable_model_key(settings: Settings, store: Any) -> bool:
    """D-44 (#259): warn once when the startup posture has no usable model key.

    The old warning lived in the client constructor and keyed off the global
    `llm_api_key`, so it fired even when a pasted key existed (and told people
    to write `.env`). The real source of truth is the model store: the default
    model's `key_source` (pasted / env / none) plus whether *any* entry has a
    key. Warn only when nothing can serve a call, and point at the settings
    page / provider env var -- never at `.env`. Returns whether it warned.
    """
    if not model_key_is_missing(settings, store):
        return False
    default_id = str(store.list_public().get("default") or "")
    logger.warning(
        "没有可用的模型 Key：默认模型 %r 的来源是 none，且没有任何模型条目配过 Key。"
        "请在设置页给对应 Provider 粘贴 Key，或设置它配置的环境变量"
        "（如 LLM_API_KEY）；任务在真正调用模型时才会失败，启动不会代填。",
        default_id or "(无默认模型)",
    )
    return True


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


def create_app(settings: Settings) -> FastAPI:
    """Assemble the HTTP app around ``settings`` (D-16 test seam).

    Production calls this once with the process settings; tests pass their own
    throwaway ``Settings`` and get the same middleware order and routes:
    CORSMiddleware is added first, TrustedHost last (= outermost).
    """

    # D-43 (#259): refuse a non-loopback bind before any side effect. This
    # also covers `uvicorn backend.main:app`, because the module-level app is
    # built through this function at import time.
    ensure_loopback_host(settings)
    # D-45 (#271): the entrypoint guard is reusable (cli / REPL call it too);
    # calling it here keeps the create_app test seam on the same contract.
    ensure_startup_auth_configured(settings)
    # D-55 (#67): unknown preset / bad capability config fails at startup.
    resolve_packages(settings.plugins_preset)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # D-42 (#259): single-backend guard before touching data/. The launcher
        # passes its identity through `QIANFAN_INSTANCE`, which is what stops
        # this guard from "waiting on ourselves". A hand-started
        # `uvicorn backend.main:app` has no id and is refused outright (H2):
        # only the launchers wire up the host / key guards, so a manual start
        # would silently skip them.
        if settings.single_backend_guard_enabled:
            own_instance = os.environ.get("QIANFAN_INSTANCE", "")
            try:
                server_lock.ensure_single_backend(settings)
            except RuntimeError as exc:
                logger.error("%s", exc)
                raise
            # M1 (#259): start.py runs `uvicorn --reload`; its spawned process
            # is the reload *supervisor* and stays alive across reloads, so the
            # lock keeps the supervisor pid. Transferring this worker pid would
            # make the record look stale on the next reload while the
            # supervisor is still serving.
            keep_supervisor_pid = os.environ.get(
                "QIANFAN_LOCK_KEEP_PID", ""
            ).strip().lower() in {"1", "true", "yes"}
            if own_instance and not keep_supervisor_pid:
                if not server_lock.update_lock_pid(
                    server_lock.lock_path(settings), own_instance, os.getpid()
                ):
                    # The launcher's record vanished mid-boot: keep serving, but
                    # say so -- a second launcher would otherwise think nobody
                    # is home.
                    logger.warning(
                        "单实例记录已被接管：本次启动未登记 pid（instance=%s）",
                        own_instance,
                    )
        # D-48 (#20): the EventBus / Persistence / TaskManager wiring is the
        # same seam the terminal REPL uses (backend/runtime.py). Auth issuance
        # stays here: the REPL never serves tokens and must not couple to it.
        runtime = build_runtime(settings)
        app.state.settings = settings
        app.state.event_bus = runtime.event_bus
        app.state.persistence = runtime.persistence
        # P1 item 5 / D-45: the issuer exists only when auth is enabled; the
        # dependency returns "local" before it ever reads app.state.auth.
        app.state.auth = (
            TokenIssuer(settings.auth_token, settings.auth_token_ttl_sec)
            if settings.auth_enabled
            else None
        )
        app.state.task_manager = runtime.task_manager
        # D-44 (#259): startup posture, not client construction -- warn only
        # when no model entry can actually serve a call.
        warn_if_no_usable_model_key(settings, runtime.task_manager.models)
        yield
        # P2 item 1: gracefully shut down MCP child processes on app exit so no
        # stdio subprocess survives the server. #260: the shutdown path itself
        # logs each step (TaskManager.shutdown); a failure here must reach the log
        # file as well, not only stdout.
        logger.info("app shutdown: releasing task manager resources")
        try:
            runtime.task_manager.shutdown()
        except Exception:  # pragma: no cover - defensive
            logger.exception("task_manager.shutdown failed")

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
    # Added last = outermost.
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

    mount_frontend(app, settings.frontend_dist_path)
    return app


app = create_app(settings)
