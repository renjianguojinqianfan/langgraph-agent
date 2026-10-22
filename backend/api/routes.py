"""REST + SSE API routes.

All responses use the unified envelope ``{code, data, message}``. Long-running
progress is delivered through ``GET /api/tasks/{id}/events`` as SSE frames.

P1 additions:

* ``POST /api/auth/token`` — login issuance (keeps the ``{ok:true}`` shape
  when ``auth_enabled=false``);
* protected endpoints opt into ``Depends(verify_token)`` (``sse.py`` stays
  untouched — the SSE route validates ``?token=`` in the route layer before
  building the streaming response).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from ..services.auth import TokenIssuer, verify_token
from ..services.event_bus import EventBus
from ..services.task_manager import TaskManager
from ..utils.logging import get_logger
from .schemas import (
    ApiResponse,
    AuthTokenRequest,
    CreateTaskRequest,
    McpServerInfo,
    SendMessageRequest,
    UpdateModeRequest,
    UpdatePlanRequest,
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
    try:
        task_id = _tm(request).create_task(
            payload.title,
            payload.input.strip(),
            mode=payload.mode.value,
            workspace=payload.workspace,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _envelope(data={"task_id": task_id})


@router.get("/tasks")
def list_tasks(
    request: Request,
    limit: int = 50,
    dir: str = "current",
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    """History filtered by task directory (ADR 0010 decision 2).

    ``dir=all`` returns every task; ``dir=current`` (default) or ``dir=<path>``
    filters by the normalized workspace, so different spellings of one
    directory land on the same list.
    """
    manager = _tm(request)
    if dir == "all":
        tasks = manager.list_all_tasks(limit=limit)
    else:
        workspace = None if dir in ("", "current") else dir
        try:
            tasks = manager.list_tasks(limit=limit, workspace=workspace)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
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


@router.post("/tasks/{task_id}/resume")
def resume_task(
    task_id: str,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    """Continue an INTERRUPTED task from its last durable checkpoint (Issue #4)."""
    if _tm(request).get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        result = _tm(request).resume(task_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _envelope(data=result)


@router.put("/tasks/{task_id}/mode")
def update_mode(
    task_id: str,
    payload: UpdateModeRequest,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    """Flip the sticky plan/build switch (ADR 0008 decision 3).

    Takes effect on the next round; the current one keeps the face it started
    with. Unknown task -> 404, unknown mode is rejected by the request model.
    """
    if _tm(request).get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        result = _tm(request).set_mode(task_id, payload.mode.value)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _envelope(data=result)


@router.put("/tasks/{task_id}/plan")
def update_plan(
    task_id: str,
    payload: UpdatePlanRequest,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    """Replace a parked task's plan; the next message runs against the edit."""
    if _tm(request).get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        result = _tm(request).set_plan(
            task_id, [p.model_dump() for p in payload.plan]
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _envelope(data=result)


@router.post("/tasks/{task_id}/messages")
def send_message(
    task_id: str,
    payload: SendMessageRequest,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    """Say one more thing to a parked task and continue the same conversation.

    This is the multi-turn entry (ADR 0008 decision 1). ``resume`` remains the
    no-new-message path for a stopped run; both need a durable checkpoint and
    both answer 409 when the task cannot continue.
    """
    if _tm(request).get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    if not payload.input or not payload.input.strip():
        raise HTTPException(status_code=400, detail="`input` is required.")
    try:
        result = _tm(request).send_message(task_id, payload.input)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _envelope(data=result)


@router.post("/tasks/{task_id}/rollback")
def rollback_task(
    task_id: str,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    """Restore the sandbox to its pre-task state from the before-image ledger.

    P1-B (docs/specs/p1-b-rollback.md): file-only — checkpoints and the task
    record are untouched, so this is decoupled from resume. A task still active
    (RUNNING/PENDING/unwinding worker) is refused synchronously with 409, the
    same style as resume; an unknown task is 404.
    """
    if _tm(request).get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        result = _tm(request).rollback(task_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
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


@router.get("/tasks/{task_id}/trace", response_model=None)
def task_trace(
    task_id: str,
    request: Request,
    format: str = "ndjson",
    _auth: str = Depends(verify_token),
) -> ApiResponse | Response:
    """Return the persisted JSONL trace for a task.

    Default: raw NDJSON (``application/x-ndjson``) so the frontend can replay
    it line by line. ``?format=json`` returns the unified envelope
    ``{code, data:[Event,...], message}``. Missing file / disabled trace -> 404.

    The real return type is the union below (envelope for ``?format=json``, raw
    NDJSON ``Response`` otherwise). ``response_model=None`` is stated explicitly
    so FastAPI keeps skipping model inference exactly as it did when the
    annotation was the bare ``Response`` subclass — the OpenAPI document and both
    branches' serialisation are unchanged.
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


# ── P1 item 5: auth (public) ──
@router.post("/auth/token")
def auth_token(payload: AuthTokenRequest, request: Request) -> ApiResponse:
    settings = request.app.state.settings
    if not settings.auth_enabled:
        return _envelope(data={"ok": True, "note": "auth disabled"})
    if payload.token != settings.auth_token:
        raise HTTPException(status_code=401, detail="invalid token")
    issuer: TokenIssuer | None = getattr(request.app.state, "auth", None)
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
