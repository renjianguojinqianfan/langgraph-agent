"""QA independent edge-case tests — persistent trace recording (P0 item 4).

Reviewer-perspective coverage: failed and interrupted tasks both write a
complete JSONL ending in ``trace_end``; every NDJSON line is valid JSON and the
``?format=json`` envelope matches the raw NDJSON order/content exactly; close is
idempotent. Fully offline via scripted/mock LLMs.
"""

from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from backend.core.llm.client import MockLLMClient
from backend.core.tools.registry import build_tools
from backend.main import app
from backend.services.event_bus import EventBus
from backend.services.persistence import Persistence
from backend.services.task_manager import TaskManager
from backend.services.trace import TraceRecorder
from backend.tests.conftest import make_settings


def _read(path) -> list:
    if not path.exists():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _wait_status(tm: TaskManager, tid: str, timeout: float = 15) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = tm.get_task(tid)
        if task and task.status.value in ("COMPLETED", "FAILED", "INTERRUPTED"):
            return task.status.value
        time.sleep(0.05)
    return "TIMEOUT"


def _wait_trace_end(path, timeout: float = 10) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        lines = _read(path)
        if lines:
            try:
                if json.loads(lines[-1]).get("type") == "trace_end":
                    return True
            except json.JSONDecodeError:
                pass
        time.sleep(0.02)
    return False


class _ExecutorBoomLLM(MockLLMClient):
    """Planner works; every executor call raises -> task FAILED."""

    def complete(self, messages, tools=None, **kwargs):
        if not tools:
            return super().complete(messages, tools=None)
        raise RuntimeError("executor exploded")


class _SlowPlannerLLM(MockLLMClient):
    """Planner blocks briefly so a stop() can land mid-run."""

    def complete(self, messages, tools=None, **kwargs):
        if not tools:
            time.sleep(0.5)
        return super().complete(messages, tools)


# ── failed / interrupted tasks write complete traces ──────────
def test_trace_records_failed_task(tmp_path):
    settings = make_settings(tmp_path)
    eb = EventBus()
    persistence = Persistence(settings)
    tm = TaskManager(
        settings, eb, persistence,
        llm_client=_ExecutorBoomLLM(),
        tools=build_tools(settings),
    )
    tid = tm.create_task(None, "do the thing")
    assert _wait_status(tm, tid) == "FAILED"

    trace_file = settings.trace_path / f"{tid}.jsonl"
    assert _wait_trace_end(trace_file)
    events = [json.loads(ln) for ln in _read(trace_file)]
    types = [e["type"] for e in events]
    assert "task_created" in types
    assert "task_failed" in types
    assert types[-1] == "trace_end"


def test_trace_records_interrupted_task(tmp_path):
    settings = make_settings(tmp_path)
    eb = EventBus()
    persistence = Persistence(settings)
    tm = TaskManager(
        settings, eb, persistence,
        llm_client=_SlowPlannerLLM(),
        tools=build_tools(settings),
    )
    tid = tm.create_task(None, "long task")
    time.sleep(0.3)  # let the run thread reach the slow planner call
    tm.stop(tid)
    assert _wait_status(tm, tid) == "INTERRUPTED"

    trace_file = settings.trace_path / f"{tid}.jsonl"
    assert _wait_trace_end(trace_file)
    events = [json.loads(ln) for ln in _read(trace_file)]
    types = [e["type"] for e in events]
    assert "task_interrupted" in types
    assert types[-1] == "trace_end"


# ── NDJSON validity & ?format=json equivalence ────────────────
def test_trace_ndjson_valid_and_matches_json_format(tmp_path):
    settings = make_settings(tmp_path)
    eb = EventBus()
    persistence = Persistence(settings)
    mock = MockLLMClient(
        plan=["Write a file"],
        tool_calls=[
            {
                "id": "qa1",
                "name": "file_io",
                "arguments": {"action": "write", "path": "qa.txt", "content": "hello"},
            }
        ],
        final_answer="wrote it",
    )
    tm = TaskManager(settings, eb, persistence, llm_client=mock, tools=build_tools(settings))
    with TestClient(app) as client:
        client.app.state.settings = settings
        client.app.state.event_bus = eb
        client.app.state.persistence = persistence
        client.app.state.task_manager = tm

        tid = client.post("/api/tasks", json={"input": "write a file"}).json()["data"]["task_id"]
        assert _wait_status(tm, tid) == "COMPLETED"
        trace_file = settings.trace_path / f"{tid}.jsonl"
        assert _wait_trace_end(trace_file)

        r = client.get(f"/api/tasks/{tid}/trace")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/x-ndjson")
        lines = [ln for ln in r.text.splitlines() if ln.strip()]
        ndjson_events = [json.loads(ln) for ln in lines]  # every line parses
        assert ndjson_events[-1]["type"] == "trace_end"

        r2 = client.get(f"/api/tasks/{tid}/trace?format=json")
        env = r2.json()
        assert env["code"] == 0
        assert env["message"] == "ok"
        assert isinstance(env["data"], list)
        # JSON envelope matches the raw NDJSON event-for-event (order/content).
        assert env["data"] == ndjson_events


# ── TraceRecorder lifecycle edges ─────────────────────────────
def test_trace_recorder_close_idempotent(tmp_path):
    settings = make_settings(tmp_path)
    bus = EventBus()
    rec = TraceRecorder(settings)
    rec.attach(bus, "t9")
    bus.publish("t9", "a", {})
    rec.close("t9")
    rec.close("t9")  # second close must not raise / duplicate trace_end
    events = [json.loads(ln) for ln in _read(rec.file_path("t9"))]
    assert [e["type"] for e in events] == ["a", "trace_end"]


def test_trace_recorder_attach_after_close_reopens(tmp_path):
    settings = make_settings(tmp_path)
    bus = EventBus()
    rec = TraceRecorder(settings)
    rec.attach(bus, "t10")
    bus.publish("t10", "a", {})
    rec.close("t10")
    # Re-attaching the same id opens a fresh subscription + file.
    rec.attach(bus, "t10")
    bus.publish("t10", "b", {})
    rec.close("t10")
    events = [json.loads(ln) for ln in _read(rec.file_path("t10"))]
    assert [e["type"] for e in events] == ["a", "trace_end", "b", "trace_end"]
