"""Tests for persistent trace recording (P0 item 4).

Covers the TraceRecorder (event order preserved, unknown tasks ignored,
unsubscribe after close, idempotent attach) and the ``GET /api/tasks/{id}/trace``
REST endpoint (raw NDJSON, ``?format=json`` envelope, 404 for missing file,
404 when trace is disabled). Runs fully offline with a scripted MockLLMClient.

#260 adds the failure surface: a trace write that fails must be counted and
logged (never silent — ``EventBus.publish`` swallows subscriber exceptions by
contract), and the read endpoint must refuse oversized / malformed / unreadable
files with a located error instead of reading them whole into memory.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from backend.api import routes
from backend.config import Settings
from backend.core.llm.client import MockLLMClient
from backend.core.tools.registry import build_tools
from backend.main import app
from backend.services.event_bus import Event, EventBus
from backend.services.model_store import ModelStore
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
        if status in ("INTERRUPTED", "FAILED"):
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
    bus.publish("t1", "tool_result", {"tool_name": "write"})
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


class _CloseOnSubscribeBus:
    """Lands a ``close()`` in the middle of ``attach()``'s subscribe call."""

    def __init__(self, rec: TraceRecorder) -> None:
        self._rec = rec
        self.subscribed: list[Callable[[Event], None]] = []

    def subscribe(self, task_id, callback):
        self._rec.close(task_id)
        self.subscribed.append(callback)
        return []

    def unsubscribe(self, task_id, callback):
        if callback in self.subscribed:
            self.subscribed.remove(callback)


def test_attach_does_not_leak_callback_when_close_races_subscribe(tmp_path):
    """#213: close() during attach() must not leave a live callback behind.

    A leaked callback is not merely memory: after a later re-attach it writes
    every event into the new era's trace a second time.
    """
    rec = TraceRecorder(make_settings(tmp_path))
    bus = _CloseOnSubscribeBus(rec)

    rec.attach(bus, "t1")  # must not raise

    assert bus.subscribed == []


class _GateBus:
    """Holds the first ``subscribe`` until the test releases it, then fans out."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self._calls = 0
        self.callbacks: list[Callable[[Event], None]] = []

    def subscribe(self, task_id, callback):
        self._calls += 1
        if self._calls == 1:
            self.entered.set()
            assert self.release.wait(5), "test never released the first subscribe"
        self.callbacks.append(callback)
        return []

    def unsubscribe(self, task_id, callback):
        if callback in self.callbacks:
            self.callbacks.remove(callback)

    def publish(self, task_id, event_type, data):
        event = {"type": event_type, "data": data, "ts": time.time()}
        for callback in list(self.callbacks):
            callback(event)
        return event


def test_late_attach_does_not_duplicate_events_after_reattach(tmp_path):
    """#213: an attach that lost to close+re-attach must not double-write.

    The first attach is paused inside subscribe; close() and a second attach
    then finish. When the first attach finally registers, it is stale and must
    be removed instead of sharing the new era's callback set.
    """
    rec = TraceRecorder(make_settings(tmp_path))
    bus = _GateBus()
    errors = []

    def slow_attach() -> None:
        try:
            rec.attach(bus, "t1")
        except BaseException as exc:  # surfaced by the assertion below
            errors.append(exc)

    thread = threading.Thread(target=slow_attach, daemon=True)
    thread.start()
    assert bus.entered.wait(5), "first attach never reached subscribe"

    rec.close("t1")  # era 1 closes while attach 1 is mid-subscribe
    rec.attach(bus, "t1")  # era 2 registers immediately
    bus.release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert errors == []

    bus.publish("t1", "step_start", {})
    rec.close("t1")

    events = [json.loads(ln) for ln in _read_lines(rec.file_path("t1"))]
    assert [e["type"] for e in events].count("step_start") == 1


# ── #260: write failures are visible ────────────────────────────
class _BrokenHandle:
    """A file handle whose writes fail like a full disk."""

    def write(self, _line: str) -> int:
        raise OSError("no space left on device")

    def flush(self) -> None:
        raise OSError("no space left on device")

    def close(self) -> None:
        pass


def test_trace_write_failure_is_counted_and_logged(tmp_path, caplog):
    """#260 AC2: a failed write is counted and logged — never silent.

    ``EventBus.publish`` swallows subscriber exceptions by contract ("a bad
    subscriber must not break the publisher"), so without the recorder's own
    signal a disk-full trace would lose every event invisibly. The publisher
    must also stay unbroken: both ``publish`` calls return normally.
    """
    log = logging.getLogger("agent.trace")
    log.addHandler(caplog.handler)  # the app logger has propagate=False
    try:
        with caplog.at_level(logging.WARNING):
            settings = make_settings(tmp_path)
            bus = EventBus()
            rec = TraceRecorder(settings)
            rec.attach(bus, "t1")
            rec._files["t1"] = _BrokenHandle()  # swap in the failing handle
            bus.publish("t1", "a", {"n": 1})
            bus.publish("t1", "b", {"n": 2})
            # Observable signal: the loss is counted per task, live.
            assert rec.write_failures == {"t1": 2}
            rec.close("t1")
    finally:
        log.removeHandler(caplog.handler)

    # First failure logged with traceback; later ones counted, not repeated —
    # a disk-full run must not flood the log one line per event.
    assert caplog.text.count("trace write failed for t1") == 1
    assert "no space left on device" in caplog.text
    # The end marker failure and the end-of-era loss summary are both visible.
    assert "trace_end write failed for t1" in caplog.text
    assert "lost 2 event(s) to write failures" in caplog.text
    # close() pops the counter: the next era starts from a clean signal.
    assert rec.write_failures == {}


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
                "name": "write",
                "arguments": {"path": "t.txt", "content": "hi"},
            }
        ],
        final_answer="done.",
    )
    tm = TaskManager(
        settings,
        eb,
        persistence,
        llm_client=mock,
        tools=build_tools(settings),
        model_store=ModelStore(settings, home_dir=settings.data_path / "home"),
    )
    with TestClient(app, base_url="http://localhost") as test_client:
        test_client.app.state.settings = settings
        test_client.app.state.event_bus = eb
        test_client.app.state.persistence = persistence
        test_client.app.state.task_manager = tm
        yield test_client, settings


def test_trace_api_ndjson(trace_client):
    client, settings = trace_client
    tid = client.post("/api/tasks", json={"input": "write a file"}).json()["data"]["task_id"]
    assert _wait_done(client, tid) == "INTERRUPTED"
    trace_file = settings.trace_path / f"{tid}.jsonl"
    assert _wait_trace_closed(trace_file)

    r = client.get(f"/api/tasks/{tid}/trace")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(ln) for ln in r.text.splitlines() if ln.strip()]
    types = [e["type"] for e in events]
    assert "task_created" in types
    assert "tool_result" in types
    assert "final_answer" in types or "task_interrupted" in types
    assert types[-1] == "trace_end"


def test_trace_api_json_format(trace_client):
    client, settings = trace_client
    tid = client.post("/api/tasks", json={"input": "write a file"}).json()["data"]["task_id"]
    assert _wait_done(client, tid) == "INTERRUPTED"
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


def test_trace_api_rejects_illegal_task_id_before_touching_disk(trace_client, monkeypatch):
    """#254 AC3: ``task_id`` is a filename component, so it is validated first.

    A backslash is not a URL path separator, so ``..\\secret`` used to reach the
    route and be concatenated into ``<trace_dir>\\..\\secret.jsonl`` — a
    filesystem escape on Windows. Illegal ids must be refused with 400 before
    any file exists/read call.
    """
    client, settings = trace_client
    # A real file the traversal would hit if the segment were concatenated.
    outside = settings.trace_path.parent / "secret.jsonl"
    outside.write_text('{"type":"leaked"}\n', encoding="utf-8")

    def _must_not_touch(*args, **kwargs):
        raise AssertionError("the trace route touched the filesystem for an illegal task_id")

    monkeypatch.setattr(Path, "read_text", _must_not_touch)
    monkeypatch.setattr(Path, "exists", _must_not_touch)
    for bad in (r"..\secret", "..\\..\\secret", "a" * 65):
        r = client.get(f"/api/tasks/{quote(bad, safe='')}/trace")
        assert r.status_code == 400, (bad, r.text)
        assert "task_id" in r.json()["detail"].lower()


def test_trace_api_unknown_task_never_serves_a_stale_file(trace_client):
    """A leftover trace file must not leak through for an unknown task.

    Without the record lookup, a file left by a deleted task (or a crashed
    record write) is served as if it belonged to a live task. This 404 is only
    reachable through the task-existence branch.
    """
    client, settings = trace_client
    stale = settings.trace_path / f"{'a' * 32}.jsonl"
    stale.write_text('{"type":"leaked"}\n', encoding="utf-8")

    r = client.get(f"/api/tasks/{'a' * 32}/trace")
    assert r.status_code == 404
    assert "trace" in r.json()["detail"].lower()


def test_trace_api_existing_task_without_trace_file_returns_404(trace_client):
    """The file-missing branch stays a 404 for a live task with trace enabled."""
    client, settings = trace_client
    tid = client.post("/api/tasks", json={"input": "write a file"}).json()["data"]["task_id"]
    assert _wait_done(client, tid) == "INTERRUPTED"
    trace_file = settings.trace_path / f"{tid}.jsonl"
    assert _wait_trace_closed(trace_file)
    trace_file.unlink()

    r = client.get(f"/api/tasks/{tid}/trace")
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
    tm = TaskManager(
        settings,
        eb,
        persistence,
        llm_client=mock,
        tools=build_tools(settings),
        model_store=ModelStore(settings, home_dir=settings.data_path / "home"),
    )
    with TestClient(app, base_url="http://localhost") as test_client:
        test_client.app.state.settings = settings
        test_client.app.state.event_bus = eb
        test_client.app.state.persistence = persistence
        test_client.app.state.task_manager = tm
        r = test_client.get("/api/tasks/abc/trace")
        assert r.status_code == 404
        assert "trace" in r.json()["detail"].lower()


# ── #260: read failures, size cap, format validation ───────────
def _finished_task_with_trace(client, settings) -> tuple[str, Path]:
    """One parked task whose trace file is closed (no more writes)."""
    tid = client.post("/api/tasks", json={"input": "write a file"}).json()["data"]["task_id"]
    assert _wait_done(client, tid) == "INTERRUPTED"
    trace_file = settings.trace_path / f"{tid}.jsonl"
    assert _wait_trace_closed(trace_file)
    return tid, trace_file


def test_trace_api_json_format_rejects_malformed_line(trace_client):
    """#260 AC3: a malformed line is a located 422, not a silently short list.

    The raw NDJSON branch stays a byte-for-byte passthrough: the replay face
    skips malformed lines by design and export identity depends on the bytes.
    """
    client, settings = trace_client
    tid, trace_file = _finished_task_with_trace(client, settings)

    lines = trace_file.read_text(encoding="utf-8").splitlines()
    lines[1] = "{not json"
    trace_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    r = client.get(f"/api/tasks/{tid}/trace?format=json")
    assert r.status_code == 422
    assert "line 2" in r.json()["detail"]

    raw = client.get(f"/api/tasks/{tid}/trace")
    assert raw.status_code == 200
    assert raw.text.splitlines()[1] == "{not json"


def test_trace_api_json_format_accepts_unicode_line_separators(trace_client):
    """#260 rework (S1): JSONL's separator is ``\\n``; U+2028/U+2029/NEL are payload.

    The recorder writes with ``ensure_ascii=False``, so those characters stay
    raw inside a legal line. ``str.splitlines()`` would cut the line in half
    and 422 a file the backend itself wrote correctly — a false alarm on a
    perfectly good trace.
    """
    client, settings = trace_client
    tid, trace_file = _finished_task_with_trace(client, settings)
    lines = trace_file.read_text(encoding="utf-8").split("\n")
    lines[0] = json.dumps(
        {"type": "final_answer", "data": {"answer": "a\u2028b\u2029c\x85d"}, "ts": 1.5},
        ensure_ascii=False,
    )
    trace_file.write_text("\n".join(lines), encoding="utf-8")

    r = client.get(f"/api/tasks/{tid}/trace?format=json")
    assert r.status_code == 200
    body = r.json()
    assert body["data"][0]["data"]["answer"] == "a\u2028b\u2029c\x85d"
    # The separators did not split the line: same event count as the file.
    assert len(body["data"]) == len([ln for ln in lines if ln.strip()])


def test_trace_api_json_format_rejects_non_object_and_missing_fields(trace_client):
    """#260 AC3: the event shape is validated, with the line located."""
    client, settings = trace_client
    tid, trace_file = _finished_task_with_trace(client, settings)
    original = trace_file.read_text(encoding="utf-8").splitlines()

    for bad, expected in (
        ("[1, 2, 3]", "not a JSON object"),
        ('{"foo": 1}', "missing string field 'type'"),
        ('{"type": "x"}', "missing object field 'data'"),
        ('{"type": "x", "data": {}}', "missing numeric field 'ts'"),
    ):
        lines = list(original)
        lines[0] = bad
        trace_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        r = client.get(f"/api/tasks/{tid}/trace?format=json")
        assert r.status_code == 422, bad
        assert "line 1" in r.json()["detail"]
        assert expected in r.json()["detail"]


def test_trace_api_rejects_oversized_trace(trace_client, monkeypatch):
    """#260 AC3: the file is never read whole into memory — over cap is a 413."""
    client, settings = trace_client
    tid, _ = _finished_task_with_trace(client, settings)
    monkeypatch.setattr(routes, "TRACE_MAX_BYTES", 16)

    r = client.get(f"/api/tasks/{tid}/trace")
    assert r.status_code == 413
    assert "byte limit" in r.json()["detail"]

    r_json = client.get(f"/api/tasks/{tid}/trace?format=json")
    assert r_json.status_code == 413


def test_trace_api_rejects_non_utf8_trace(trace_client):
    """#260 AC3: a non-UTF-8 file is a clear 422, not an unhandled decode error."""
    client, settings = trace_client
    tid, trace_file = _finished_task_with_trace(client, settings)
    trace_file.write_bytes(
        b'{"type":"a","data":{},"ts":1}\n\xff\xfe not utf-8\n'
    )

    r = client.get(f"/api/tasks/{tid}/trace")
    assert r.status_code == 422
    assert "UTF-8" in r.json()["detail"]


def test_trace_api_read_failure_is_logged_and_returns_500(trace_client, monkeypatch, caplog):
    """#260 AC2: a read failure is a logged 500, not an unhandled traceback."""
    client, settings = trace_client
    tid, _ = _finished_task_with_trace(client, settings)

    def _locked_open(self, *args, **kwargs):
        raise OSError("file is locked by another process")

    monkeypatch.setattr(Path, "open", _locked_open)
    log = logging.getLogger("agent.api.routes")
    log.addHandler(caplog.handler)  # the app logger has propagate=False
    try:
        with caplog.at_level(logging.ERROR):
            r = client.get(f"/api/tasks/{tid}/trace")
    finally:
        log.removeHandler(caplog.handler)

    assert r.status_code == 500
    assert "could not be read" in r.json()["detail"]
    assert "file is locked" in r.json()["detail"]
    assert f"trace read failed for task {tid}" in caplog.text
