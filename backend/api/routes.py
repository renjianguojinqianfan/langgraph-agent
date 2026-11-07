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
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from ..core.runtime_context import normalize_workspace
from ..core.workspace_files import resolve_in_workspace, workspace_files
from ..services.auth import TokenIssuer, verify_token
from ..services.event_bus import EventBus
from ..services.model_store import (
    PROTOCOL_OPENAI,
    CredentialsError,
    ProviderDiscoveryError,
    require_implemented_protocol,
)
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
    ProviderCreateRequest,
    ProviderModelCreateRequest,
    ProviderModelUpdateRequest,
    ProviderUpdateRequest,
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


@router.get("/workspaces/validate")
def validate_workspace_path(
    path: str = "",
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    """Resolve a directory for the page's workspace switcher (#242).

    Read-only: no listing, no creation. The same normalization the task layer
    uses rejects UNC / device / NUL paths before any filesystem call, and the
    caller gets the canonical spelling back so the URL stays shareable.
    """
    if not path.strip():
        raise HTTPException(status_code=400, detail="`path` is required.")
    try:
        normalized = normalize_workspace(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    target = Path(normalized)
    if not target.exists():
        raise HTTPException(status_code=400, detail=f"directory does not exist: {normalized}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"not a directory: {normalized}")
    return _envelope(data={"path": normalized})


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
    except PlanError as exc:
        # The plan pre-check inside the resume refused (#279 AC3): same
        # sanitised face as GET /plan — the cause goes to the log only.
        raise _plan_read_failed(task_id, exc) from None
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
    """Switch a task's model; the next message uses it (ADR 0010 decision 7).

    D-34 accepts either an existing model entry id (``{model}``) or a raw
    ``{provider, model}`` pair; the latter creates a ``source: manual`` entry
    on demand. Both forms return the updated task.
    """
    if _tm(request).get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    if payload.provider:
        try:
            result = _tm(request).set_task_model(
                task_id, provider=payload.provider, model=payload.model or ""
            )
        except KeyError:
            raise HTTPException(
                status_code=404, detail=f"provider not found: {payload.provider}"
            ) from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
    else:
        try:
            result = _tm(request).set_model(task_id, payload.model or "")
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
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
    except CredentialsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
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
    except CredentialsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
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


# ── providers + model catalog (#243 P2-2 / D-34) ──
@router.get("/providers")
def list_providers(
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    """Provider cards plus the global default model entry id.

    Credentials never leave the process: each card reports only key_set /
    key_source.
    """
    store = _tm(request).models
    return _envelope(
        data={"providers": store.list_providers(), "default": store.default_id}
    )


@router.post("/providers")
def create_provider(
    payload: ProviderCreateRequest,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    protocol = (payload.protocol or PROTOCOL_OPENAI).strip()
    try:
        # D-35: both supported protocols have clients; an unknown one is a 400.
        require_implemented_protocol(protocol)
        created = _tm(request).add_provider(
            id=payload.id,
            name=payload.name,
            protocol=protocol,
            base_url=payload.base_url,
            api_key_env=payload.api_key_env,
            key=payload.key,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            thinking=payload.thinking,
            reasoning_effort=payload.reasoning_effort,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except CredentialsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return _envelope(data=created)


@router.put("/providers/{provider_id}")
def update_provider(
    provider_id: str,
    payload: ProviderUpdateRequest,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    """Partial edit; the provider id is immutable (D-34).

    A protocol change is validated against the supported set (unknown → 400);
    the D-36 sampling defaults are editable here as well.
    """
    fields = payload.model_dump(exclude_none=True)
    if "protocol" in fields:
        fields["protocol"] = str(fields["protocol"]).strip() or PROTOCOL_OPENAI
    try:
        if "protocol" in fields:
            require_implemented_protocol(fields["protocol"])
        updated = _tm(request).update_provider(provider_id, **fields)
    except KeyError:
        raise HTTPException(status_code=404, detail="provider not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except CredentialsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return _envelope(data=updated)


@router.delete("/providers/{provider_id}")
def delete_provider(
    provider_id: str,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    """Delete a provider and its model entries; the last model is refused."""
    try:
        _tm(request).delete_provider(provider_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="provider not found") from None
    except CredentialsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return _envelope(data={"ok": True})


@router.get("/providers/{provider_id}/models")
def list_provider_models(
    provider_id: str,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    """One provider's catalog plus the global default model entry id."""
    store = _tm(request).models
    try:
        models = store.list_models(provider_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="provider not found") from None
    return _envelope(data={"models": models, "default": store.default_id})


@router.post("/providers/{provider_id}/models")
def create_provider_model(
    provider_id: str,
    payload: ProviderModelCreateRequest,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    try:
        created = _tm(request).add_model_entry(
            provider_id,
            model=payload.model,
            name=payload.name,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            thinking=payload.thinking,
            reasoning_effort=payload.reasoning_effort,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="provider not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _envelope(data=created)


@router.put("/providers/{provider_id}/models/{model_id}")
def update_provider_model(
    provider_id: str,
    model_id: str,
    payload: ProviderModelUpdateRequest,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    """Partial edit of one catalog entry (D-36 sampling overrides live here)."""
    try:
        updated = _tm(request).update_model_entry(
            provider_id, model_id, **payload.model_dump(exclude_none=True)
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="model not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _envelope(data=updated)


@router.delete("/providers/{provider_id}/models/{model_id}")
def delete_provider_model(
    provider_id: str,
    model_id: str,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    try:
        _tm(request).delete_model_entry(provider_id, model_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="model not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return _envelope(data={"ok": True})


@router.post("/providers/{provider_id}/models/discover")
def discover_provider_models(
    provider_id: str,
    request: Request,
    _auth: str = Depends(verify_token),
) -> ApiResponse:
    """Preview the provider's available models; never persisted (D-34).

    Bounded time / response size, redirects refused, and the error text is
    redacted so a key can never reach the caller.
    """
    manager = _tm(request)
    try:
        candidates = manager.models.discover_models(provider_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="provider not found") from None
    except ProviderDiscoveryError as exc:
        raise HTTPException(status_code=400, detail=manager.redact(str(exc))) from None
    return _envelope(data={"models": candidates})


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
        raise _plan_read_failed(task_id, exc) from None
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
    except PlanError as exc:
        # The plan pre-check inside the continuation refused (#279 AC3): same
        # sanitised face as GET /plan — the cause goes to the log only.
        raise _plan_read_failed(task_id, exc) from None
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _envelope(data=result)


FILES_MAX = 2000
PREVIEW_MAX_BYTES = 200_000

#: A task id is a single safe filename component (``uuid4().hex``; legacy ids
#: may carry ``-`` / ``_``). The record lookup in the route also means reserved
#: device names (``CON``) cannot reach a trace path — nothing is registered
#: under them.
_TASK_ID_RE = re.compile(r"[0-9A-Za-z_-]{1,64}")


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


#: Upper bound for reading one trace file into memory (#260). It guards the
#: ``?format=json`` branch only, which must parse every line; the raw NDJSON
#: branch streams instead (see the route). The route reads one byte past this
#: cap and refuses the file (413) instead of silently serving a truncated list.
TRACE_MAX_BYTES = 8_388_608  # 8 MiB

#: What the client is told when the trace file cannot be read at all (#260).
#: Deliberately generic: the OSError text (Windows: absolute paths, lock
#: holders) goes to the server log, never to the response body.
_TRACE_READ_ERROR_DETAIL = "trace file could not be read; see the server log"

#: Same discipline for the plan file (#279 AC3 / #260 rework): ``PlanError``
#: carries the absolute plan path and the OS error, and the frontend renders
#: this detail verbatim — so the full cause goes to the log only.
_PLAN_READ_ERROR_DETAIL = "plan file cannot be read; see the server log"

#: Chunk size for streaming the raw trace prefix (#260 rework).
_TRACE_CHUNK_BYTES = 64 * 1024


def _trace_line_error(lineno: int, why: str) -> HTTPException:
    """A malformed trace line is a located, client-visible 422 (#260)."""
    return HTTPException(
        status_code=422,
        detail=f"trace line {lineno} is malformed: {why}",
    )


def _trace_read_failed(task_id: str, exc: OSError) -> HTTPException:
    """Log the full cause, answer with the safe detail (#260)."""
    logger.error(
        "trace read failed for task %s (%s: %s)",
        task_id,
        type(exc).__name__,
        exc,
    )
    return HTTPException(status_code=500, detail=_TRACE_READ_ERROR_DETAIL)


def _plan_read_failed(task_id: str, exc: PlanError) -> HTTPException:
    """Log the full cause, answer with the safe detail (#260 rework)."""
    logger.error("plan read failed for task %s: %s", task_id, exc)
    return HTTPException(status_code=409, detail=_PLAN_READ_ERROR_DETAIL)


async def _trace_prefix_body(path: Path, size: int) -> AsyncIterator[bytes]:
    """Yield at most ``size`` bytes of ``path`` — the stat-time prefix snapshot.

    Stopping at the stat-time size is what makes the stream consistent: the
    trace keeps growing while its task runs, so a stream that ran to EOF would
    send more bytes than any length declared up front (``FileResponse``
    declares ``content-length`` from ``os.stat`` and reads to EOF — h11
    ``too much data`` on the client). A file shorter than ``size`` (replaced /
    truncated) simply ends early. Nothing is declared, so neither direction
    can violate the protocol; the semantic is "raw = the prefix snapshot at
    request time" (#260 rework).
    """
    remaining = size
    async with await anyio.open_file(str(path), "rb") as handle:
        while remaining > 0:
            chunk = await handle.read(min(remaining, _TRACE_CHUNK_BYTES))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@router.get("/tasks/{task_id}/trace", response_model=None)
def task_trace(
    task_id: str,
    request: Request,
    format: str = "ndjson",
    _auth: str = Depends(verify_token),
) -> ApiResponse | Response:
    """Return the persisted JSONL trace for a task.

    Default: raw NDJSON (``application/x-ndjson``) so the frontend can replay
    it line by line — byte-for-byte the on-disk file, malformed lines included
    (the replay face skips those by design, and export identity depends on
    them). It is **streamed**, never buffered and never capped: this is the
    frontend's only trace consumer (replay + export), so a long trace must
    stay replayable. The body is the **prefix snapshot at request time** — the
    file keeps growing while its task runs, and the stream stops at the size
    ``stat`` saw (see :func:`_trace_prefix_body`); the size bound applies to
    the parsing branch only.

    ``?format=json`` returns the unified envelope
    ``{code, data:[Event,...], message}`` and validates every line, so a
    malformed trace is a located 422 instead of a silently shortened list.

    An illegal ``task_id`` is a 400 before any filesystem access; an unknown
    task, a task without a trace file, or a disabled trace is a 404; a
    ``?format=json`` file past :data:`TRACE_MAX_BYTES` is a 413; an unreadable
    file (either branch) is a logged 500 with a generic detail.

    The real return type is the union below (envelope for ``?format=json``,
    raw NDJSON response otherwise). ``response_model=None`` is stated explicitly
    so FastAPI keeps skipping model inference exactly as it did when the
    annotation was the bare ``Response`` subclass — the OpenAPI document and both
    branches' serialisation are unchanged.
    """
    settings = request.app.state.settings
    # Validate the segment before it is used as a filename: illegal ids are a
    # client error and must not touch the filesystem (#254).
    if not _TASK_ID_RE.fullmatch(task_id):
        raise HTTPException(status_code=400, detail="invalid task_id")
    if not settings.trace_enabled:
        raise HTTPException(status_code=404, detail="task trace not enabled")
    task = _tm(request).get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task trace not found")
    trace_file = settings.trace_path / f"{task.id}.jsonl"
    if not trace_file.exists():
        raise HTTPException(status_code=404, detail="task trace not found")

    if format != "json":
        # Stream the stat-time PREFIX (#260 rework): the file keeps growing
        # while the task runs, and a "read to EOF" stream (FileResponse) would
        # send more bytes than the length it declares up front — the client
        # sees h11 ``too much data`` or a silently truncated body. The old
        # ``read_text`` was a consistent snapshot; this restores that property
        # without the memory cost. The response is chunked (no declared
        # length), so neither growth nor a shrinking/replaced file can violate
        # the protocol, and the semantic is "raw = the prefix snapshot at
        # request time".
        # The probe only NARROWS the window: a file deleted or locked between
        # the probe and the stream fails mid-response (server-logged, client
        # sees a broken body) instead of as a clean 500. The realistic cases
        # — a parked file, or a running task appending — are covered.
        try:
            size = trace_file.stat().st_size
            with trace_file.open("rb"):
                pass
        except OSError as exc:
            raise _trace_read_failed(task.id, exc) from None
        return StreamingResponse(
            _trace_prefix_body(trace_file, size),
            media_type="application/x-ndjson",
        )

    # Bounded read (#260): the parsing branch must not pull a whole unbounded
    # file into memory.
    try:
        with trace_file.open("rb") as handle:
            raw_bytes = handle.read(TRACE_MAX_BYTES + 1)
    except OSError as exc:
        raise _trace_read_failed(task.id, exc) from None
    if len(raw_bytes) > TRACE_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"trace file exceeds the {TRACE_MAX_BYTES} byte limit; "
                "the raw NDJSON form has no limit"
            ),
        )
    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=422, detail="trace file is not valid UTF-8"
        ) from None

    events = []
    # Split on "\n" only (#260): JSONL's separator is the newline, and the
    # recorder writes with ``ensure_ascii=False``, so U+2028 / U+2029 / NEL
    # stay raw inside a legal line — ``str.splitlines()`` would cut such a
    # line in half and 422 a file the backend itself wrote correctly.
    for lineno, line in enumerate(raw.split("\n"), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _trace_line_error(lineno, f"not valid JSON ({exc.msg})") from None
        if not isinstance(event, dict):
            raise _trace_line_error(lineno, "not a JSON object")
        if not isinstance(event.get("type"), str):
            raise _trace_line_error(lineno, "missing string field 'type'")
        if not isinstance(event.get("data"), dict):
            raise _trace_line_error(lineno, "missing object field 'data'")
        if not isinstance(event.get("ts"), (int, float)):
            raise _trace_line_error(lineno, "missing numeric field 'ts'")
        events.append(event)
    return _envelope(data=events)


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
