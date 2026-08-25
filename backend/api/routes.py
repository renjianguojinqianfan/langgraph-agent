"""REST + SSE API routes.

All responses use the unified envelope ``{code, data, message}``. Long-running
progress is delivered through ``GET /api/tasks/{id}/events`` as SSE frames.

P1 additions:

* ``POST /api/auth/token`` — login issuance (keeps the ``{ok:true}`` shape
  when ``auth_enabled=false``);
* protected endpoints opt into ``Depends(verify_token)`` (``sse.py`` stays
  untouched — the SSE route validates ``?token=`` in the route layer before
  building the streaming response);
* knowledge-base management: ``GET /api/kb`` / ``POST /api/kb/rebuild`` /
  ``DELETE /api/kb/{doc_id}``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from ..core.kb.knowledge_base import get_kb_instance
from ..services.auth import TokenIssuer, verify_token
from ..services.event_bus import EventBus
from ..services.task_manager import TaskManager
from ..utils.logging import get_logger
from .schemas import (
    ApiResponse,
    AuthTokenRequest,
    ConfirmRequest,
    CreateTaskRequest,
    McpServerInfo,
)
from .sse import sse_response

logger = get_logger("api.routes")

router = APIRouter()


def _tm(request: Request) -> TaskManager:
    return request.app.state.task_manager


def _eb(request: Request) -> EventBus:
    return request.app.state.event_bus


def _envelope(data: object = None, code: int = 0, message: str = "ok") -> ApiResponse:
    return ApiResponse(code=code, data=data, message=message)


@router.post("/tasks")
def create_task(
    payload: CreateTaskRequest,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    if not payload.input or not payload.input.strip():
        raise HTTPException(status_code=400, detail="`input` is required.")
    task_id = _tm(request).create_task(payload.title, payload.input.strip())
    return _envelope(data={"task_id": task_id})


@router.get("/tasks")
def list_tasks(
    request: Request,
    limit: int = 50,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    tasks = _tm(request).list_tasks(limit=limit)
    return _envelope(data={"tasks": [t.model_dump(mode="json") for t in tasks]})


@router.get("/tasks/{task_id}")
def get_task(
    task_id: str,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    task = _tm(request).get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return _envelope(data=task.model_dump(mode="json"))


@router.post("/tasks/{task_id}/stop")
def stop_task(
    task_id: str,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    if _tm(request).get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    result = _tm(request).stop(task_id)
    return _envelope(data=result)


@router.get("/tasks/{task_id}/events")
def task_events(
    task_id: str,
    request: Request,
    _auth: str = Depends(verify_token),
):
    if _tm(request).get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    return sse_response(task_id, _eb(request), request)


@router.get("/tasks/{task_id}/trace")
def task_trace(
    task_id: str,
    request: Request,
    format: str = "ndjson",
    _auth: str = Depends(verify_token),
) -> Response:
    """Return the persisted JSONL trace for a task.

    Default: raw NDJSON (``application/x-ndjson``) so the frontend can replay
    it line by line. ``?format=json`` returns the unified envelope
    ``{code, data:[Event,...], message}``. Missing file / disabled trace -> 404.
    """
    settings = request.app.state.settings
    if not settings.trace_enabled:
        raise HTTPException(status_code=404, detail="task trace not enabled")
    trace_file = settings.trace_path / f"{task_id}.jsonl"
    if not trace_file.exists():
        raise HTTPException(status_code=404, detail="task trace not found")

    raw = trace_file.read_text(encoding="utf-8")
    if format == "json":
        events = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:  # pragma: no cover - defensive
                continue
        return _envelope(data=events)
    return Response(content=raw, media_type="application/x-ndjson")


@router.get("/tasks/{task_id}/artifacts/{artifact_id}")
def download_artifact(
    task_id: str,
    artifact_id: str,
    request: Request,
    _auth: str = Depends(verify_token),
) -> Response:
    task = _tm(request).get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    art = request.app.state.persistence.find_artifact(task_id, artifact_id)
    if art is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    data = _tm(request).get_artifact(task_id, artifact_id)
    if data is None:
        raise HTTPException(status_code=404, detail="artifact file missing")
    return Response(content=data, media_type=art.mime, headers={"Content-Disposition": f'attachment; filename="{art.filename}"'})


@router.get("/tasks/{task_id}/artifacts/{artifact_id}/preview")
def preview_artifact(
    task_id: str,
    artifact_id: str,
    request: Request,
    _auth: str = Depends(verify_token),
) -> Response:
    task = _tm(request).get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    art = request.app.state.persistence.find_artifact(task_id, artifact_id)
    if art is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    data = _tm(request).get_artifact(task_id, artifact_id)
    if data is None:
        raise HTTPException(status_code=404, detail="artifact file missing")
    if art.mime.startswith("text/") or art.mime in ("application/json",):
        return Response(content=data, media_type=art.mime)
    # Images / binaries: return raw bytes for <img>/preview.
    return Response(content=data, media_type=art.mime)


@router.post("/tasks/{task_id}/confirm")
def confirm_task(
    task_id: str,
    payload: ConfirmRequest,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    if _tm(request).get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    ok = _tm(request).confirm(task_id, payload.tool_call_id, payload.approved)
    return _envelope(data={"ok": ok})


# ── P1 item 5: auth (public) ──
@router.post("/auth/token")
def auth_token(payload: AuthTokenRequest, request: Request) -> ApiResponse:
    settings = request.app.state.settings
    if not settings.auth_enabled:
        return _envelope(data={"ok": True, "note": "auth disabled"})
    if payload.token != settings.auth_token:
        raise HTTPException(status_code=401, detail="invalid token")
    issuer: TokenIssuer = getattr(request.app.state, "auth", None)
    if issuer is None:
        raise HTTPException(status_code=500, detail="auth not configured")
    token, expires_at = issuer.issue()
    return _envelope(
        data={
            "token": token,
            "expires_at": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
            "ok": True,
        }
    )


# ── P1 item 3: knowledge base management ──
@router.get("/kb")
def kb_list(request: Request, _auth: str = Depends(verify_token)) -> ApiResponse:
    kb = getattr(_tm(request), "_kb", None) or get_kb_instance()
    if kb is None:
        return _envelope(data={"docs": []})
    docs = kb.list_docs()
    return _envelope(data={"docs": [d.to_dict() for d in docs]})


@router.post("/kb/rebuild")
def kb_rebuild(request: Request, _auth: str = Depends(verify_token)) -> ApiResponse:
    kb = getattr(_tm(request), "_kb", None) or get_kb_instance()
    if kb is None:
        return _envelope(data={"ok": True, "indexed": 0})
    count = kb.rebuild()
    return _envelope(data={"ok": True, "indexed": count})


@router.delete("/kb/{doc_id}")
def kb_delete(
    doc_id: str,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    kb = getattr(_tm(request), "_kb", None) or get_kb_instance()
    if kb is None:
        return _envelope(data={"ok": False}, code=1, message="kb not available")
    ok = kb.remove(doc_id)
    return _envelope(data={"ok": ok}, code=0 if ok else 1, message="ok" if ok else "doc not found")


# ── P2 item 1: MCP server diagnostics ──
@router.get("/mcp/servers")
def mcp_servers(request: Request, _auth: str = Depends(verify_token)) -> ApiResponse:
    """Return the connection status of every configured MCP server.

    Disabled (``mcp_enabled=false`` or empty ``mcp_servers``) -> ``servers: []``
    so the response is a zero-regression empty list.
    """
    tm = _tm(request)
    manager = getattr(tm, "_mcp", None)
    if manager is None:
        return _envelope(data={"servers": []})
    try:
        statuses = manager.status_list()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("mcp status_list failed: %s", exc)
        return _envelope(data={"servers": []})
    return _envelope(
        data={
            "servers": [
                McpServerInfo(
                    name=s.name,
                    transport=s.transport,
                    status=s.status,
                    tools_count=s.tools_count,
                    error=s.error,
                ).model_dump()
                for s in statuses
            ]
        }
    )
