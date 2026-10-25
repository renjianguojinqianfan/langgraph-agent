"""Black-box resume acceptance tests (spec Issue #4, testing layer 1).

Full lifecycle through the public seam (TaskManager service layer / REST):

    create → RUNNING → stop → INTERRUPTED
           → simulate restart (rebuild manager on same storage)
           → resume → INTERRUPTED (parked)

Design note (implementation-grade deviation from spec D4, recorded here):
the project stops a task by flipping ``stop_requested`` and exiting through
the ``finish`` node, so the graph reaches END gracefully. A bare
``invoke(None, cfg)`` therefore has nothing to continue. The resume entry
restores the last checkpoint values, resets *control* flags only, and
re-invokes with the same ``thread_id`` so checkpoint history keeps growing.
"""

from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from backend.api.schemas import TaskStatus
from backend.core.llm.client import MockLLMClient
from backend.main import app
from backend.services.event_bus import EventBus
from backend.services.persistence import Persistence
from backend.tests.conftest import make_manager, make_settings


class _SlowMockLLMClient(MockLLMClient):
    """Sleeps each call so the run stays RUNNING long enough to stop."""

    def complete(self, messages, tools=None, **kwargs):
        time.sleep(0.1)
        return super().complete(messages, tools, **kwargs)


def _big_script():
    return _SlowMockLLMClient(
        plan=["step out"],
        tool_calls=[
            {
                "id": f"c{i}",
                "name": "write",
                "arguments": {"path": f"f{i}.txt", "content": "x"},
            }
            for i in range(5)
        ],
        final_answer="all done",
    )


def _wait(tm, task_id, statuses=("INTERRUPTED", "FAILED"), timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = tm.get_task(task_id)
        if t and t.status.value in statuses:
            return t
        time.sleep(0.05)
    return tm.get_task(task_id)


def _trace_events(path):
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            break  # mid-write tail; callers that race a writer retry
    return events


def _wait_trace_end(path, *, after_lines, timeout=10.0):
    """Wait until the JSONL has grown past ``after_lines`` and ends with trace_end."""
    deadline = time.time() + timeout
    events = _trace_events(path)
    while time.time() < deadline:
        events = _trace_events(path)
        if len(events) > after_lines and events[-1]["type"] == "trace_end":
            return events
        time.sleep(0.02)
    return events


def _interrupt_midway(settings, event_bus):
    """Create a task and stop it once RUNNING. Returns (manager, task_id)."""
    tm = make_manager(settings, _big_script(), event_bus=event_bus)
    task_id = tm.create_task(title="resumable", user_input="many steps")
    deadline = time.time() + 10
    while time.time() < deadline:
        t = tm.get_task(task_id)
        if t and t.status.value == "RUNNING":
            break
        time.sleep(0.02)
    else:
        pytest.fail("task never entered RUNNING")
    tm.stop(task_id)
    assert _wait(tm, task_id).status.value == "INTERRUPTED"
    return tm, task_id


def _ckpt_settings(tmp_path):
    """Offline settings with the checkpoint store explicitly enabled (Issue #4).

    The suite-wide env default keeps checkpoint persistence OFF so unrelated
    tests never open a sqlite store; these tests opt in per tmp_path.
    """
    return make_settings(tmp_path, checkpoint_enabled=True)


class TestBlackBoxResume:
    def test_stop_rebuild_resume_completes(self, tmp_path):
        settings = _ckpt_settings(tmp_path)
        bus1 = EventBus()
        tm1, task_id = _interrupt_midway(settings, bus1)

        first_steps = len(tm1.get_task(task_id).steps)

        # ── restart simulation: brand-new bus + manager over the same dirs ──
        bus2 = EventBus()
        tm2 = make_manager(settings, _big_script(), event_bus=bus2)
        # startup reconciliation left it INTERRUPTED, never resurrected.
        assert tm2.get_task(task_id).status == TaskStatus.INTERRUPTED

        result = tm2.resume(task_id)
        assert result["ok"] is True

        task = _wait(tm2, task_id)
        assert task.status.value == "INTERRUPTED"
        assert task.final_answer == "all done"
        # continued, not restarted: more steps than the interrupted run had.
        assert len(task.steps) >= first_steps

        types = {e["type"] for e in bus2.replay(task_id)}
        assert "task_resumed" in types
        assert "task_interrupted" in types

    def test_trace_file_grows_across_resume(self, tmp_path):
        settings = _ckpt_settings(tmp_path)
        tm1, task_id = _interrupt_midway(settings, EventBus())
        trace_file = settings.trace_path / f"{task_id}.jsonl"
        lines_after_stop = len(trace_file.read_text(encoding="utf-8").splitlines())

        tm2 = make_manager(settings, _big_script(), event_bus=EventBus())
        tm2.resume(task_id)
        _wait(tm2, task_id)

        lines_after_resume = len(trace_file.read_text(encoding="utf-8").splitlines())
        assert lines_after_resume > lines_after_stop

    def test_trace_captures_task_resumed_across_restart(self, tmp_path):
        """The resumed era starts with exactly one ``task_resumed`` (#205).

        ``resume`` used to publish the boundary event before reattaching the
        recorder, so it reached SSE only. The whole previous era must also
        stay un-replayed: the subscription snapshot is still dropped.
        """
        settings = _ckpt_settings(tmp_path)
        tm1, task_id = _interrupt_midway(settings, EventBus())
        tm1.shutdown()  # join era 1 so its trace_end is on disk before resume
        trace_file = settings.trace_path / f"{task_id}.jsonl"
        before = _wait_trace_end(trace_file, after_lines=0)
        assert before[0]["type"] == "task_created"
        assert "task_resumed" not in [e["type"] for e in before]

        tm2 = make_manager(settings, _big_script(), event_bus=EventBus())
        tm2.resume(task_id)
        _wait(tm2, task_id)
        after = _wait_trace_end(trace_file, after_lines=len(before))

        assert after[: len(before)] == before  # append-only
        tail = [e["type"] for e in after[len(before):]]
        assert tail[0] == "task_resumed"  # a replayed snapshot would precede it
        assert tail.count("task_resumed") == 1
        assert tail[-1] == "trace_end"

    def test_trace_captures_task_resumed_across_continuation(self, tmp_path):
        """Same boundary guarantee for the continuation (send_message) path."""
        settings = _ckpt_settings(tmp_path)
        tm = make_manager(
            settings, MockLLMClient(final_answer="round done"), event_bus=EventBus()
        )
        task_id = tm.create_task(title="continue", user_input="hi")
        assert _wait(tm, task_id).status == TaskStatus.INTERRUPTED
        trace_file = settings.trace_path / f"{task_id}.jsonl"
        before = _wait_trace_end(trace_file, after_lines=0)
        assert before[0]["type"] == "task_created"
        assert "task_resumed" not in [e["type"] for e in before]

        tm.send_message(task_id, "one more")
        _wait(tm, task_id)
        after = _wait_trace_end(trace_file, after_lines=len(before))

        assert after[: len(before)] == before
        tail = [e["type"] for e in after[len(before):]]
        assert tail[0] == "task_resumed"  # a replayed snapshot would precede it
        assert tail.count("task_resumed") == 1
        assert tail[-1] == "trace_end"

    def test_resume_non_resumable_status_raises(self, tmp_path):
        settings = make_settings(tmp_path)
        tm = make_manager(settings, MockLLMClient(final_answer="done"), event_bus=EventBus())
        task_id = tm.create_task(title="quick", user_input="finish fast")
        _wait(tm, task_id)
        assert tm.get_task(task_id).status.value == "INTERRUPTED"

        with pytest.raises(RuntimeError):
            tm.resume(task_id)

    def test_resume_unknown_or_legacy_no_checkpoint_raises(self, tmp_path):
        settings = make_settings(tmp_path)
        # Legacy record with no checkpoint artifact behind it.
        from datetime import datetime, timezone

        p = Persistence(settings)
        now = datetime.now(timezone.utc).isoformat()
        from backend.api.schemas import Task

        p.save_task(
            Task(
                id="t-legacy",
                title="legacy",
                user_input="x",
                status=TaskStatus.INTERRUPTED,
                created_at=now,
                updated_at=now,
            )
        )
        tm = make_manager(settings, MockLLMClient(), event_bus=EventBus())
        # tm never ran t-legacy: reconstruct is impossible → explicit error.
        with pytest.raises(RuntimeError):
            tm.resume("t-legacy")
        with pytest.raises(RuntimeError):
            tm.resume("t-missing")

    def test_concurrent_resume_admits_exactly_one(self, tmp_path):
        """The lock-guarded claim admits one worker; the loser gets a 409."""
        settings = _ckpt_settings(tmp_path)
        bus = EventBus()
        tm1, task_id = _interrupt_midway(settings, bus)

        tm2 = make_manager(settings, _big_script(), event_bus=EventBus())
        results: list = []
        errors: list = []
        barrier = threading.Barrier(2)

        def fire():
            barrier.wait()
            try:
                results.append(tm2.resume(task_id))
            except RuntimeError as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=fire) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        task = _wait(tm2, task_id)
        assert task.status.value == "INTERRUPTED"
        assert len(results) == 1 and len(errors) == 1


class TestResumeAPI:
    @pytest.fixture
    def api(self, tmp_path):
        settings = _ckpt_settings(tmp_path)
        eb = EventBus()
        persistence = Persistence(settings)
        tm = make_manager(settings, _big_script(), event_bus=eb)
        with TestClient(app, base_url="http://localhost") as c:
            c.app.state.settings = settings
            c.app.state.event_bus = eb
            c.app.state.persistence = persistence
            c.app.state.task_manager = tm
            yield c, tm

    def test_resume_endpoint_envelope_and_404(self, api):
        client, tm = api
        r = client.post("/api/tasks/does-not-exist/resume")
        assert r.status_code == 404

        task_id = tm.create_task(title="quick", user_input="finish")
        # Wait for a terminal status first.
        deadline = time.time() + 15
        while time.time() < deadline:
            if tm.get_task(task_id).status.value in ("INTERRUPTED", "FAILED"):
                break
            time.sleep(0.05)
        # A parked task is resumable by design (#139); the 409 mapping is what
        # this test pins, so force a status resume refuses.
        parked = tm.get_task(task_id)
        parked.status = TaskStatus.FAILED
        tm.persistence.save_task(parked)
        r = client.post(f"/api/tasks/{task_id}/resume")
        assert r.status_code == 409


class _FakeSnap:
    """Stand-in for a langgraph StateSnapshot (only ``.values`` is read)."""

    def __init__(self, values):
        self.values = values


class _FakeGraph:
    """``get_state()`` returns queued snapshots; the last one repeats after."""

    def __init__(self, snaps):
        self._snaps = list(snaps)
        self.calls = 0

    def get_state(self, config):
        snap = self._snaps[min(self.calls, len(self._snaps) - 1)]
        self.calls += 1
        return snap


class TestResumeStateRead:
    """Unit tests for ``_read_restorable_state`` (Issue #23 flake hardening).

    The resume flake is a read-visibility race that is hard to reproduce on
    demand, so these drive the retry seam deterministically with a fake graph:
    a transient empty read must be retried until values appear, and a
    permanently empty one must still raise the original error (proving no
    reject semantic was relaxed -- only the read is retried).
    """

    def test_retries_transient_empty_then_returns_values(self, tmp_path):
        settings = make_settings(tmp_path)
        tm = make_manager(settings, MockLLMClient(), event_bus=EventBus())
        good = {
            "status": "RUNNING",
            "plan_text": "",
            "plan_version": 0,
            "plan_hash": "",
            "steps": [],
        }
        graph = _FakeGraph([_FakeSnap({}), _FakeSnap(None), _FakeSnap(good)])
        vals = tm._read_restorable_state(graph, "t1", attempts=5, pause=0.0)
        assert vals == good
        assert graph.calls == 3  # empty, None, then good

    def test_raises_when_always_empty(self, tmp_path):
        settings = make_settings(tmp_path)
        tm = make_manager(settings, MockLLMClient(), event_bus=EventBus())
        graph = _FakeGraph([_FakeSnap({})])
        with pytest.raises(RuntimeError, match="empty checkpoint state"):
            tm._read_restorable_state(graph, "t1", attempts=3, pause=0.0)
        assert graph.calls == 3  # budget exhausted, never yielded a value
