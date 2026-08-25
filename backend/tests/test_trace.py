"""Tests for persistent trace recording (P0 item 4).

Covers the TraceRecorder (event order preserved, unknown tasks ignored,
unsubscribe after close, idempotent attach) and the ``GET /api/tasks/{id}/trace``
REST endpoint (raw NDJSON, ``?format=json`` envelope, 404 for missing file,
404 when trace is disabled). Runs fully offline with a scripted MockLLMClient.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.core.llm.client import MockLLMClient
from backend.core.tools.registry import build_tools
from backend.main import app
from backend.services.event_bus import EventBus
from backend.services.persistence import Persistence
from backend.services.task_manager import TaskManager
from backend.services.trace import TraceRecorder
from backend.tests.conftest import make_settings


def _read_lines(path) -> list:
    if not path.exists():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _wait_done(client, task_id, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/tasks/{task_id}")
        status = r.json()["data"]["status"]
        if status in ("COMPLETED", "FAILED", "INTERRUPTED"):
            return status
        time.sleep(0.05)
    return "TIMEOUT"


def _wait_trace_closed(path, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        lines = _read_lines(path)
        if lines:
            try:
                if json.loads(lines[-1]).get("type") == "trace_end":
                    return True
            except json.JSONDecodeError:
                pass
        time.sleep(0.02)
    return False


# ── TraceRecorder unit behaviour ──────────────────────────────
def test_trace_recorder_writes_events_in_order(tmp_path):
    settings = make_settings(tmp_path)
    bus = EventBus()
    rec = TraceRecorder(settings)
    rec.attach(bus, "t1")
    bus.publish("t1", "task_created", {"task_id": "t1"})
    bus.publish("t1", "step_start", {"index": 1})
    bus.publish("t1", "tool_result", {"tool_name": "file_io"})
    rec.close("t1")

    events = [json.loads(ln) for ln in _read_lines(rec.file_path("t1"))]
    assert [e["type"] for e in events] == [
        "task_created",
        "step_start",
        "tool_result",
        "trace_end",
    ]
    assert events[0]["data"]["task_id"] == "t1"
    assert events[-1]["type"] == "trace_end"
    assert all("ts" in e for e in events)


def test_trace_recorder_ignores_unknown_task(tmp_path):
    settings = make_settings(tmp_path)
    bus = EventBus()
    rec = TraceRecorder(settings)
    rec.attach(bus, "t1")
    bus.publish("t2", "task_created", {"task_id": "t2"})  # not attached
    rec.close("t1")
    events = [json.loads(ln) for ln in _read_lines(rec.file_path("t1"))]
    assert [e["type"] for e in events] == ["trace_end"]


def test_trace_recorder_close_unsubscribes(tmp_path):
    settings = make_settings(tmp_path)
    bus = EventBus()
    rec = TraceRecorder(settings)
    rec.attach(bus, "t1")
    bus.publish("t1", "a", {"n": 1})
    rec.close("t1")
    bus.publish("t1", "b", {"n": 2})  # after close — must not be appended
    events = [json.loads(ln) for ln in _read_lines(rec.file_path("t1"))]
    assert [e["type"] for e in events] == ["a", "trace_end"]


def test_trace_recorder_attach_is_idempotent(tmp_path):
    settings = make_settings(tmp_path)
    bus = EventBus()
    rec = TraceRecorder(settings)
    rec.attach(bus, "t1")
    rec.attach(bus, "t1")  # second attach must be a no-op
    bus.publish("t1", "x", {})
    rec.close("t1")
    events = [json.loads(ln) for ln in _read_lines(rec.file_path("t1"))]
    assert [e["type"] for e in events] == ["x", "trace_end"]


# ── GET /api/tasks/{id}/trace ─────────────────────────────────
@pytest.fixture
def trace_client(tmp_path):
    settings = make_settings(tmp_path)
    eb = EventBus()
    persistence = Persistence(settings)
    mock = MockLLMClient(
        plan=["Write a file"],
        tool_calls=[
            {
                "id": "c1",
                "name": "file_io",
                "arguments": {"action": "write", "path": "t.txt", "content": "hi"},
            }
        ],
        final_answer="done.",
    )
    tm = TaskManager(settings, eb, persistence, llm_client=mock, tools=build_tools(settings))
    with TestClient(app) as test_client:
        test_client.app.state.settings = settings
        test_client.app.state.event_bus = eb
        test_client.app.state.persistence = persistence
        test_client.app.state.task_manager = tm
        yield test_client, settings


def test_trace_api_ndjson(trace_client):
    client, settings = trace_client
    tid = client.post("/api/tasks", json={"input": "write a file"}).json()["data"]["task_id"]
    assert _wait_done(client, tid) == "COMPLETED"
    trace_file = settings.trace_path / f"{tid}.jsonl"
    assert _wait_trace_closed(trace_file)

    r = client.get(f"/api/tasks/{tid}/trace")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(ln) for ln in r.text.splitlines() if ln.strip()]
    types = [e["type"] for e in events]
    assert "task_created" in types
    assert "tool_result" in types
    assert "final_answer" in types or "task_completed" in types
    assert types[-1] == "trace_end"


def test_trace_api_json_format(trace_client):
    client, settings = trace_client
    tid = client.post("/api/tasks", json={"input": "write a file"}).json()["data"]["task_id"]
    assert _wait_done(client, tid) == "COMPLETED"
    trace_file = settings.trace_path / f"{tid}.jsonl"
    assert _wait_trace_closed(trace_file)

    r = client.get(f"/api/tasks/{tid}/trace?format=json")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["message"] == "ok"
    assert isinstance(body["data"], list)
    assert body["data"][-1]["type"] == "trace_end"


def test_trace_api_missing_returns_404(trace_client):
    client, _ = trace_client
    r = client.get("/api/tasks/does_not_exist/trace")
    assert r.status_code == 404
    assert "trace" in r.json()["detail"].lower()


def test_trace_api_disabled_returns_404(tmp_path):
    settings = Settings(
        data_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        trace_dir=str(tmp_path / "traces"),
        trace_enabled=False,
        max_steps=50,
        use_mock_llm=True,
    )
    eb = EventBus()
    persistence = Persistence(settings)
    mock = MockLLMClient()
    tm = TaskManager(settings, eb, persistence, llm_client=mock, tools=build_tools(settings))
    with TestClient(app) as test_client:
        test_client.app.state.settings = settings
        test_client.app.state.event_bus = eb
        test_client.app.state.persistence = persistence
        test_client.app.state.task_manager = tm
        r = test_client.get("/api/tasks/abc/trace")
        assert r.status_code == 404
        assert "trace" in r.json()["detail"].lower()
