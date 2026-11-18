"""#332 S6: the terminal's local REST/SSE client (fake HTTP transport).

The seam under test is the HTTP boundary: every case builds an
``httpx.MockTransport`` that answers like the real backend, so the assertions
read the requests the client actually makes (method, path, body, headers) and
the errors it actually classifies — never a private helper.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx
import pytest

from backend import server_lock
from backend.api_client import (
    AdmissionRejected,
    ApiClient,
    ApiError,
    AuthExpired,
    BackendUnreachable,
    resolve_base_url,
)
from backend.tests.conftest import make_settings


def _empty() -> httpx.Response:
    return httpx.Response(200, json={"code": 0, "data": {}, "message": "ok"})


class _Recorder:
    """Records requests and answers via a per-test handler map."""

    def __init__(self, *responses: Any) -> None:
        self.requests: List[httpx.Request] = []
        self.responses = list(responses)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.responses:
            return _empty()
        return self.responses.pop(0)


def _client(*responses: Any, token: Optional[str] = None) -> tuple[ApiClient, _Recorder]:
    recorder = _Recorder(*responses)
    client = ApiClient(
        "http://127.0.0.1:9123",
        token=token,
        transport=httpx.MockTransport(recorder.handler),
    )
    return client, recorder


# ── base URL ────────────────────────────────────────────────────────────────
def test_base_url_prefers_the_registry_port(tmp_path):
    settings = make_settings(tmp_path, port=8000)
    assert resolve_base_url(settings) == "http://127.0.0.1:8000"

    server_lock.claim_lock(
        server_lock.lock_path(settings),
        server_lock.ServerLock(
            pid=__import__("os").getpid(),
            port=9123,
            started_at=server_lock.now_iso(),
            instance="live",
            mode="serve",
        ),
    )
    assert resolve_base_url(settings) == "http://127.0.0.1:9123"


def test_base_url_falls_back_to_the_settings_port_for_a_dead_record(tmp_path):
    settings = make_settings(tmp_path, port=8000)
    server_lock.claim_lock(
        server_lock.lock_path(settings),
        server_lock.ServerLock(
            pid=999_999,
            port=9123,
            started_at=server_lock.now_iso(),
            instance="dead",
            mode="serve",
        ),
    )
    assert resolve_base_url(settings) == "http://127.0.0.1:8000"


# ── task helpers ────────────────────────────────────────────────────────────
def test_create_task_posts_the_workspace_and_returns_the_id():
    client, recorder = _client(
        httpx.Response(200, json={"code": 0, "data": {"task_id": "t1"}})
    )
    data = client.create_task("hello", workspace="E:/work/dir")

    assert data == {"task_id": "t1"}
    request = recorder.requests[0]
    assert request.method == "POST"
    assert request.url.path == "/api/tasks"
    assert json.loads(request.content) == {"input": "hello", "workspace": "E:/work/dir"}


def test_task_helpers_route_to_the_documented_endpoints():
    client, recorder = _client(
        httpx.Response(200, json={"code": 0, "data": {"id": "t1", "status": "INTERRUPTED"}}),
        httpx.Response(200, json={"code": 0, "data": {"tasks": [{"id": "t1"}]}}),
        httpx.Response(200, json={"code": 0, "data": {"ok": True}}),
        httpx.Response(200, json={"code": 0, "data": {"ok": True}}),
        httpx.Response(200, json={"code": 0, "data": {"mode": "plan"}}),
        httpx.Response(200, json={"code": 0, "data": {"model": "m1"}}),
    )

    assert client.get_task("t1")["status"] == "INTERRUPTED"
    assert client.list_tasks(limit=7, directory="all")["tasks"] == [{"id": "t1"}]
    client.send_message("t1", "more")
    client.stop_task("t1")
    client.set_mode("t1", "plan")
    client.set_model("t1", "m1")

    seen = [(r.method, r.url.path) for r in recorder.requests]
    assert seen == [
        ("GET", "/api/tasks/t1"),
        ("GET", "/api/tasks"),
        ("POST", "/api/tasks/t1/messages"),
        ("POST", "/api/tasks/t1/stop"),
        ("PUT", "/api/tasks/t1/mode"),
        ("PUT", "/api/tasks/t1/model"),
    ]
    assert recorder.requests[1].url.params["limit"] == "7"
    assert recorder.requests[1].url.params["dir"] == "all"
    assert json.loads(recorder.requests[2].content) == {"input": "more"}


def test_resume_targets_the_resume_endpoint():
    client, recorder = _client(
        httpx.Response(200, json={"code": 0, "data": {"ok": True}})
    )
    client.resume_task("t1")
    assert recorder.requests[0].method == "POST"
    assert recorder.requests[0].url.path == "/api/tasks/t1/resume"


def test_resolve_task_accepts_an_exact_id_or_a_unique_prefix():
    client, recorder = _client(
        httpx.Response(
            404, json={"detail": "task not found"}
        ),
        httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "tasks": [
                        {"id": "abc12345deadbeef", "status": "INTERRUPTED"},
                        {"id": "ffff0000deadbeef", "status": "RUNNING"},
                    ]
                },
            },
        ),
        httpx.Response(
            200, json={"code": 0, "data": {"id": "abc12345deadbeef", "status": "INTERRUPTED"}}
        ),
    )

    task = client.resolve_task("abc12345")

    assert task["id"] == "abc12345deadbeef"
    assert [r.url.path for r in recorder.requests] == [
        "/api/tasks/abc12345",
        "/api/tasks",
        "/api/tasks/abc12345deadbeef",
    ]


def test_resolve_task_reports_an_ambiguous_prefix():
    client, _ = _client(
        httpx.Response(404, json={"detail": "task not found"}),
        httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "tasks": [
                        {"id": "aaaa1111deadbeef"},
                        {"id": "aaaa2222deadbeef"},
                    ]
                },
            },
        ),
    )
    with pytest.raises(ApiError) as exc:
        client.resolve_task("aaaa")
    assert "多个" in str(exc.value)


# ── auth ────────────────────────────────────────────────────────────────────
def test_login_keeps_the_token_in_the_header_only():
    client, recorder = _client(
        httpx.Response(200, json={"code": 0, "data": {"token": "secret-token"}}),
        httpx.Response(200, json={"code": 0, "data": {"id": "t1"}}),
    )
    client.login("local-secret")
    client.get_task("t1")

    login_request, task_request = recorder.requests
    assert login_request.url.path == "/api/auth/token"
    assert "secret-token" not in str(login_request.url)
    assert "local-secret" not in str(login_request.url)
    assert task_request.headers["Authorization"] == "Bearer secret-token"
    assert "secret-token" not in str(task_request.url)


def test_login_with_a_rejected_secret_is_auth_expired():
    client, _ = _client(httpx.Response(401, json={"detail": "invalid token"}))
    with pytest.raises(AuthExpired) as exc:
        client.login("wrong")
    assert "wrong" not in str(exc.value)


def test_a_401_on_a_task_call_is_auth_expired():
    client, _ = _client(httpx.Response(401, json={"detail": "invalid token"}))
    with pytest.raises(AuthExpired) as exc:
        client.get_task("t1")
    assert "401" in str(exc.value)


# ── error classification ────────────────────────────────────────────────────
def test_a_409_is_an_admission_rejection_with_the_server_reason():
    client, _ = _client(
        httpx.Response(409, json={"detail": "task is RUNNING; only a parked task can continue"})
    )
    with pytest.raises(AdmissionRejected) as exc:
        client.send_message("t1", "again")
    assert "RUNNING" in str(exc.value)
    assert isinstance(exc.value, ApiError)


def test_a_connection_refusal_is_backend_unreachable():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = ApiClient("http://127.0.0.1:9", transport=httpx.MockTransport(boom))
    with pytest.raises(BackendUnreachable) as exc:
        client.get_task("t1")
    assert "127.0.0.1:9" in str(exc.value)


def test_a_500_is_a_plain_api_error():
    client, _ = _client(httpx.Response(500, json={"detail": "tasks.json write failed"}))
    with pytest.raises(ApiError) as exc:
        client.stop_task("t1")
    assert "500" in str(exc.value) and "write failed" in str(exc.value)


# ── SSE ─────────────────────────────────────────────────────────────────────
def _sse(*frames: str) -> httpx.Response:
    return httpx.Response(
        200,
        content=iter([frame.encode("utf-8") for frame in frames]),
        headers={"Content-Type": "text/event-stream"},
    )


def test_events_parse_sse_frames_into_type_and_data():
    client, recorder = _client(
        _sse(
            'event: task_created\ndata: {"task_id": "t1"}\n\n',
            ': keep-alive\n\n',
            'event: heartbeat\ndata: {}\n\n',
            'event: task_interrupted\ndata: {"status": "INTERRUPTED"}\n\n',
        )
    )

    events = list(client.events("t1"))

    # The client is transport: every frame is yielded verbatim; which types the
    # terminal renders is the session's decision (the small face).
    assert events == [
        {"type": "task_created", "data": {"task_id": "t1"}},
        {"type": "heartbeat", "data": {}},
        {"type": "task_interrupted", "data": {"status": "INTERRUPTED"}},
    ]
    assert recorder.requests[0].url.path == "/api/tasks/t1/events"
    assert recorder.requests[0].headers["Accept"] == "text/event-stream"


def test_events_carry_the_bearer_header_when_authenticated():
    client, recorder = _client(_sse(), token="tok")
    assert list(client.events("t1")) == []
    assert recorder.requests[0].headers["Authorization"] == "Bearer tok"


def test_events_401_is_auth_expired():
    client, _ = _client(httpx.Response(401, json={"detail": "invalid token"}))
    with pytest.raises(AuthExpired):
        list(client.events("t1"))


def test_a_broken_event_stream_is_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        def body():
            yield b'event: task_created\ndata: {"task_id": "t1"}\n\n'
            raise httpx.ReadError("connection dropped", request=request)

        return httpx.Response(200, content=body())

    client = ApiClient("http://127.0.0.1:9123", transport=httpx.MockTransport(handler))
    seen: List[Dict[str, Any]] = []
    with pytest.raises(BackendUnreachable):
        for event in client.events("t1"):
            seen.append(event)

    assert seen == [{"type": "task_created", "data": {"task_id": "t1"}}]
