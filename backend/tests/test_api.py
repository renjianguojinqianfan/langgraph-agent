"""API-layer tests using FastAPI's ``TestClient``.

The default :class:`TaskManager` is replaced with one driven by a
:class:`MockLLMClient` so every endpoint runs fully offline. Covers:

* ``POST /api/tasks`` create + unified envelope;
* ``GET /api/tasks`` list + ``GET /api/tasks/{id}`` query;
* ``POST /api/tasks/{id}/stop`` interrupt;
* ``GET /api/tasks/{id}/events`` SSE stream emitting events (tested via the
  ``sse_response`` generator the route uses, with a disconnected request so it
  terminates immediately after replaying buffered events — no network, no hang);
* ``POST /api/tasks/{id}/confirm`` envelope + 404 handling.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from backend.api.sse import sse_response
from backend.core.llm.client import MockLLMClient
from backend.core.tools.registry import build_tools
from backend.main import app
from backend.services.event_bus import EventBus
from backend.services.persistence import Persistence
from backend.services.task_manager import TaskManager
from backend.tests.conftest import make_settings


class _FakeRequest:
    """A request that reports itself disconnected so the SSE generator exits
    right after the replay buffer (no 15s idle wait on the client side)."""

    async def is_disconnected(self):
        return True


@pytest.fixture
def client(tmp_path):
    settings = make_settings(tmp_path)
    eb = EventBus()
    persistence = Persistence(settings)
    mock = MockLLMClient(
        plan=["Create a file"],
        tool_calls=[
            {
                "id": "c1",
                "name": "file_io",
                "arguments": {"action": "write", "path": "api_out.txt", "content": "via api"},
            }
        ],
        final_answer="Created api_out.txt.",
    )
    tm = TaskManager(settings, eb, persistence, llm_client=mock, tools=build_tools(settings))

    with TestClient(app) as test_client:
        # Inject offline, deterministic components.
        test_client.app.state.settings = settings
        test_client.app.state.event_bus = eb
        test_client.app.state.persistence = persistence
        test_client.app.state.task_manager = tm
        yield test_client


def _wait_done(test_client, task_id, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = test_client.get(f"/api/tasks/{task_id}")
        status = r.json()["data"]["status"]
        if status in ("COMPLETED", "FAILED", "INTERRUPTED"):
            return status
        time.sleep(0.05)
    return "TIMEOUT"


def test_create_task_returns_envelope_with_task_id(client):
    r = client.post("/api/tasks", json={"input": "write a file via api"})
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert "task_id" in body["data"]
    assert body["message"] == "ok"


def test_create_task_rejects_empty_input(client):
    r = client.post("/api/tasks", json={"input": "   "})
    assert r.status_code == 400


def test_get_task_returns_full_task(client):
    tid = client.post("/api/tasks", json={"input": "write a file via api"}).json()["data"]["task_id"]
    r = client.get(f"/api/tasks/{tid}")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["id"] == tid
    assert "status" in body["data"]


def test_get_unknown_task_returns_404(client):
    r = client.get("/api/tasks/nope")
    assert r.status_code == 404


def test_list_tasks_envelope(client):
    client.post("/api/tasks", json={"input": "task one"})
    r = client.get("/api/tasks")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert isinstance(body["data"]["tasks"], list)


def test_stop_task_interrupts(client):
    tid = client.post("/api/tasks", json={"input": "long task"}).json()["data"]["task_id"]
    time.sleep(0.2)
    r = client.post(f"/api/tasks/{tid}/stop")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["ok"] is True
    assert body["data"]["status"] in ("INTERRUPTED", "COMPLETED", "FAILED")


def test_stop_unknown_task_returns_404(client):
    r = client.post("/api/tasks/nope/stop")
    assert r.status_code == 404


def test_sse_response_emits_replayed_event_frames(client):
    tid = client.post("/api/tasks", json={"input": "write a file via api"}).json()["data"]["task_id"]
    assert _wait_done(client, tid) == "COMPLETED"

    eb = client.app.state.event_bus

    # The TestClient fixture leaves a running event loop in the main thread, so
    # calling asyncio.run() here raises "cannot be called from a running event
    # loop". Drive the async SSE generator from a dedicated worker thread that
    # owns its own loop instead. sse_response is also constructed inside that
    # thread so its internal asyncio.Queue binds to the same loop.
    import asyncio as _asyncio
    import threading as _threading

    result: dict = {}

    def _run() -> None:
        async def _collect():
            frames = []
            # sse_response() returns a StreamingResponse; iterate its async
            # body_iterator to pull the SSE frames.
            agen = sse_response(tid, eb, _FakeRequest()).body_iterator
            for _ in range(100):
                try:
                    frame = await agen.__anext__()
                except StopAsyncIteration:
                    break
                frames.append(frame)
                if "final_answer" in frame or "task_completed" in frame:
                    break
            return frames

        result["frames"] = _asyncio.run(_collect())

    t = _threading.Thread(target=_run)
    t.start()
    t.join(timeout=30)
    assert not t.is_alive(), "SSE frame collection timed out"
    frames = result["frames"]

    joined = "".join(frames)
    assert "event: task_created" in joined
    assert "event: final_answer" in joined or "event: task_completed" in joined
    # Frames must follow the SSE wire format.
    assert joined.startswith("event: ")


def test_sse_route_is_registered(client):
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/tasks/{task_id}/events" in paths


def test_confirm_endpoint_returns_envelope(client):
    tid = client.post("/api/tasks", json={"input": "write a file via api"}).json()["data"]["task_id"]
    r = client.post(
        f"/api/tasks/{tid}/confirm",
        json={"tool_call_id": "cc1", "approved": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert "ok" in body["data"]


def test_health_endpoint_reports_status(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
