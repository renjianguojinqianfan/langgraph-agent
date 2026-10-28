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
import mimetypes
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response

from ..core.workspace_files import resolve_in_workspace, workspace_files
from ..services.auth import TokenIssuer, verify_token
from ..services.event_bus import EventBus
from ..services.persistence import PersistenceError
from ..services.plan_file import PlanError
from ..services.task_manager import TaskManager
from ..utils.logging import get_logger
from .schemas import (
    ApiResponse,
    AuthTokenRequest,
    CreateTaskRequest,
    McpServerInfo,
    ModelCreateRequest,
    ModelKeyRequest,
    ModelUpdateRequest,
    SendMessageRequest,
    TaskModelRequest,
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
            model=payload.model,
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


@router.delete("/tasks")
def delete_workspace_tasks(
    request: Request,
    dir: str = "",
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    """Delete one workspace's task history (#241); never touches disk files.

    ``dir`` is required: there is intentionally no "delete everything" spelling.
    Any live task in the directory refuses the whole batch (409, nothing deleted).
    Background processes still owned by the deleted tasks are reaped.
    """
    if not dir.strip():
        raise HTTPException(status_code=400, detail="`dir` is required.")
    try:
        deleted = _tm(request).delete_workspace_tasks(dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except PersistenceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from None
    return _envelope(data={"deleted": deleted})


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


@router.delete("/tasks/{task_id}")
def delete_task_history(
    task_id: str,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    """Delete one task's history + checkpoint + trace (#241).

    The worker of a live task keeps writing those artifacts, so PENDING/RUNNING
    (or a still-alive worker thread) is a 409. The record is removed under the
    manager lock *before* the expensive cleanup, so a continuation that loses
    the claim race finds no task instead of resurrecting a half-deleted one.
    Background processes the task still owns are reaped during cleanup. If the
    record cannot be persisted, nothing is cleaned up and the API answers 500.
    """
    if _tm(request).get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        result = _tm(request).delete_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="task not found") from None
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except PersistenceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from None
    return _envelope(data=result)


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


@router.put("/tasks/{task_id}/model")
def update_task_model(
    task_id: str,
    payload: TaskModelRequest,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    """Switch a task's model; the next message uses it (ADR 0010 decision 7)."""
    if _tm(request).get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        result = _tm(request).set_model(task_id, payload.model)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _envelope(data=result)


@router.get("/models")
def list_models(
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    return _envelope(data=_tm(request).models.list_public())


@router.post("/models")
def create_model(
    payload: ModelCreateRequest,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    try:
        profile = _tm(request).add_model_profile(
            name=payload.name,
            base_url=payload.base_url,
            model=payload.model,
            api_key_env=payload.api_key_env,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _envelope(data=_tm(request).models.public_profile(profile.id))


@router.put("/models/{model_id}")
def update_model(
    model_id: str,
    payload: ModelUpdateRequest,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    try:
        _tm(request).update_model_profile(model_id, **payload.model_dump(exclude_none=True))
    except KeyError:
        raise HTTPException(status_code=404, detail="model not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _envelope(data=_tm(request).models.public_profile(model_id))


@router.delete("/models/{model_id}")
def delete_model(
    model_id: str,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    try:
        _tm(request).delete_model_profile(model_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="model not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _envelope(data={"ok": True})


@router.post("/models/{model_id}/default")
def set_default_model(
    model_id: str,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    try:
        _tm(request).set_default_model_profile(model_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="model not found")
    return _envelope(data={"ok": True, "default": model_id})


@router.put("/models/{model_id}/key")
def set_model_key(
    model_id: str,
    payload: ModelKeyRequest,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    """Write-only: a key goes in, only ``key_set`` comes back."""
    try:
        _tm(request).set_model_key(model_id, payload.key)
    except KeyError:
        raise HTTPException(status_code=404, detail="model not found")
    store = _tm(request).models
    return _envelope(
        data={
            "ok": True,
            "key_set": store.key_set(model_id),
            "key_source": store.key_source(model_id),
        }
    )


@router.post("/models/{model_id}/test")
def test_model(
    model_id: str,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    try:
        result = _tm(request).test_model(model_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="model not found")
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


@router.get("/tasks/{task_id}/plan")
def get_plan(
    task_id: str,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    """The plan file's text and version (C6)."""
    if _tm(request).get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        result = _tm(request).get_plan(task_id)
    except PlanError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _envelope(data=result)


@router.put("/tasks/{task_id}/plan")
def update_plan(
    task_id: str,
    payload: UpdatePlanRequest,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    """Replace a parked task's plan file; the next message runs against it."""
    if _tm(request).get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        result = _tm(request).set_plan(task_id, payload.text)
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


FILES_MAX = 2000
PREVIEW_MAX_BYTES = 200_000


def _task_workspace(request: Request, task_id: str) -> Path:
    task = _tm(request).get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    root = Path(task.workspace)
    if not root.is_dir():
        raise HTTPException(
            status_code=409,
            detail=f"task workspace no longer exists: {task.workspace}",
        )
    return root.resolve()


def _file_target(request: Request, task_id: str, path: str) -> Path:
    root = _task_workspace(request, task_id)
    target = resolve_in_workspace(root, path)
    if target is None:
        raise HTTPException(status_code=400, detail="path escapes the task workspace")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return target


@router.get("/tasks/{task_id}/files")
def list_workspace_files(
    task_id: str,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    """Files in the task's workspace, using the glob/grep ignore rules (W3).

    The list is a visibility filter, not a boundary: a named preview/download
    still reaches any file inside the workspace (``read`` behaves the same).
    """
    root = _task_workspace(request, task_id)
    files = workspace_files(root)
    entries = []
    for path in files[:FILES_MAX]:
        try:
            stat = path.stat()
            entries.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": stat.st_size,
                }
            )
        except OSError:
            continue
    return _envelope(
        data={"files": entries, "truncated": len(files) > FILES_MAX}
    )


@router.get("/tasks/{task_id}/files/preview")
def preview_workspace_file(
    task_id: str,
    request: Request,
    path: str,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    """Text preview; binaries are refused and large files are truncated."""
    target = _file_target(request, task_id, path)
    size = target.stat().st_size
    with target.open("rb") as handle:
        chunk = handle.read(PREVIEW_MAX_BYTES + 1)
    truncated = len(chunk) > PREVIEW_MAX_BYTES
    chunk = chunk[:PREVIEW_MAX_BYTES]
    if b"\x00" in chunk:
        raise HTTPException(status_code=415, detail="binary file cannot be previewed")
    return _envelope(
        data={
            "path": path,
            "content": chunk.decode("utf-8", errors="replace"),
            "size": size,
            "truncated": truncated,
        }
    )


@router.get("/tasks/{task_id}/files/download")
def download_workspace_file(
    task_id: str,
    request: Request,
    path: str,
    _auth: str = Depends(verify_token),
) -> FileResponse:
    """Stream a workspace file (never loaded whole into memory)."""
    target = _file_target(request, task_id, path)
    media_type, _ = mimetypes.guess_type(target.name)
    return FileResponse(
        target,
        media_type=media_type or "application/octet-stream",
        filename=target.name,
    )


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
