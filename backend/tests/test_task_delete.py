"""#241: delete task history (record + checkpoint + trace), never disk files."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.schemas import TaskStatus
from backend.core.llm.client import MockLLMClient
from backend.main import app
from backend.services import plan_file
from backend.tests.conftest import make_manager, make_settings


def _wait(tm, task_id, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = tm.get_task(task_id)
        if task and task.status.value in ("INTERRUPTED", "FAILED"):
            return task
        time.sleep(0.05)
    raise AssertionError("task never parked")


def _client(tm):
    client = TestClient(app, base_url="http://localhost")
    client.__enter__()
    getattr(client.app, "state").task_manager = tm
    return client


def _parked_task(tm, workspace: Path, tag: str = "t") -> str:
    workspace.mkdir(parents=True, exist_ok=True)
    task_id = tm.create_task(tag, "hi", workspace=str(workspace))
    _wait(tm, task_id)
    return task_id


def test_delete_removes_history_checkpoint_and_trace_but_not_files(tmp_path):
    settings = make_settings(tmp_path, checkpoint_enabled=True, trace_enabled=True)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    workspace = tmp_path / "ws"
    task_id = _parked_task(tm, workspace)

    assert tm._has_checkpoint(task_id) is True
    trace_file = settings.trace_path / f"{task_id}.jsonl"
    assert trace_file.is_file()
    plan_file.write_plan(workspace, task_id, "# Plan\n\nkeep me")
    marker = workspace / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    client = _client(tm)
    try:
        response = client.delete(f"/api/tasks/{task_id}")
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["ok"] is True
        assert data["cleanup"] == {"checkpoint": True, "trace": True}
    finally:
        client.__exit__(None, None, None)

    assert tm.get_task(task_id) is None
    stored = json.loads((settings.data_path / "tasks.json").read_text(encoding="utf-8"))
    assert all(item["id"] != task_id for item in stored)
    assert tm._has_checkpoint(task_id) is False
    assert not trace_file.exists()
    # Disk files are explicitly out of scope: the workspace and its plan stay.
    assert marker.read_text(encoding="utf-8") == "keep"
    assert plan_file.plan_path(workspace, task_id).is_file()
    assert tm.event_bus.replay(task_id) == []


def test_delete_unknown_task_returns_404(tmp_path):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    client = _client(tm)
    try:
        assert client.delete("/api/tasks/missing").status_code == 404
    finally:
        client.__exit__(None, None, None)


def test_delete_running_task_is_refused(tmp_path):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    task_id = _parked_task(tm, tmp_path / "ws")
    task = tm.get_task(task_id)
    task.status = TaskStatus.RUNNING
    tm.persistence.save_task(task)

    client = _client(tm)
    try:
        response = client.delete(f"/api/tasks/{task_id}")
        assert response.status_code == 409
        assert "still running" in response.json()["detail"]
    finally:
        client.__exit__(None, None, None)
    assert tm.get_task(task_id) is not None


def test_live_worker_thread_is_refused_even_when_the_record_says_parked(tmp_path):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    task_id = _parked_task(tm, tmp_path / "ws")
    release = threading.Event()
    worker = threading.Thread(target=lambda: release.wait(5), daemon=True)
    worker.start()
    tm._threads[task_id] = worker
    try:
        client = _client(tm)
        try:
            assert client.delete(f"/api/tasks/{task_id}").status_code == 409
        finally:
            client.__exit__(None, None, None)
    finally:
        release.set()
        worker.join(5)
    assert tm.get_task(task_id) is not None


def test_bulk_delete_is_scoped_to_one_workspace(tmp_path):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    workspace_a = tmp_path / "a"
    workspace_b = tmp_path / "b"
    removed = _parked_task(tm, workspace_a, "a")
    kept = _parked_task(tm, workspace_b, "b")

    client = _client(tm)
    try:
        response = client.delete("/api/tasks", params={"dir": str(workspace_a)})
        assert response.status_code == 200, response.text
        assert response.json()["data"]["deleted"] == 1
    finally:
        client.__exit__(None, None, None)

    assert tm.get_task(removed) is None
    assert tm.get_task(kept) is not None


def test_bulk_delete_refuses_the_whole_batch_when_any_task_is_live(tmp_path):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    workspace = tmp_path / "ws"
    parked = _parked_task(tm, workspace, "parked")
    live = _parked_task(tm, workspace, "live")
    task = tm.get_task(live)
    task.status = TaskStatus.RUNNING
    tm.persistence.save_task(task)

    client = _client(tm)
    try:
        response = client.delete("/api/tasks", params={"dir": str(workspace)})
        assert response.status_code == 409
        assert "still running" in response.json()["detail"]
    finally:
        client.__exit__(None, None, None)
    assert tm.get_task(parked) is not None
    assert tm.get_task(live) is not None


def test_bulk_delete_requires_a_local_directory(tmp_path):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    client = _client(tm)
    try:
        assert client.delete("/api/tasks").status_code == 400
        unc = client.delete("/api/tasks", params={"dir": r"\\attacker.example\share"})
        assert unc.status_code == 400
        assert "local path" in unc.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_delete_takes_the_record_before_artifact_cleanup(tmp_path, monkeypatch):
    """F1: a continuation racing the delete must not find a task to start.

    The record is removed under the manager lock *before* the expensive
    checkpoint/trace cleanup; a caller that loses the claim race sees "not
    found" instead of starting a worker that would resurrect the history.
    """
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    task_id = _parked_task(tm, tmp_path / "ws")

    entered = threading.Event()
    release = threading.Event()
    real_cleanup = tm._cleanup_task_artifacts

    def slow_cleanup(cleaned_id):
        entered.set()
        release.wait(5)
        return real_cleanup(cleaned_id)

    monkeypatch.setattr(tm, "_cleanup_task_artifacts", slow_cleanup)
    result = {}
    worker = threading.Thread(target=lambda: result.setdefault("result", tm.delete_task(task_id)))
    worker.start()
    try:
        assert entered.wait(5)
        with pytest.raises(RuntimeError, match="not found"):
            tm.send_message(task_id, "continue")
        assert tm.get_task(task_id) is None
    finally:
        release.set()
        worker.join(5)
    assert result["result"]["ok"] is True


def test_delete_persist_failure_keeps_record_checkpoint_and_trace(tmp_path, monkeypatch):
    """F1: a failed tasks.json write must not clean up the artifacts.

    Before: the API answered 200 and dropped checkpoint/trace anyway; after a
    restart the history reappeared, but the continuation data was gone. Now the
    in-memory removal is rolled back and the request fails with 500.
    """
    settings = make_settings(tmp_path, checkpoint_enabled=True, trace_enabled=True)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    task_id = _parked_task(tm, tmp_path / "ws")
    trace_file = settings.trace_path / f"{task_id}.jsonl"
    assert tm._has_checkpoint(task_id) is True
    assert trace_file.is_file()

    monkeypatch.setattr(tm.persistence, "_dump", lambda: False)
    client = _client(tm)
    try:
        response = client.delete(f"/api/tasks/{task_id}")
        assert response.status_code == 500, response.text
        assert "could not persist" in response.json()["detail"]
        # Assert while the client session is open: exiting TestClient shuts the
        # manager down, which closes (and unloads) the checkpoint store.
        assert tm.get_task(task_id) is not None
        assert tm._has_checkpoint(task_id) is True
    finally:
        client.__exit__(None, None, None)

    stored = json.loads((settings.data_path / "tasks.json").read_text(encoding="utf-8"))
    assert any(item["id"] == task_id for item in stored)
    assert trace_file.is_file()


def test_bulk_delete_persist_failure_keeps_every_record(tmp_path, monkeypatch):
    """F1 (bulk): a failed dump rolls back the whole batch, artifacts included."""
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    workspace = tmp_path / "ws"
    first = _parked_task(tm, workspace, "first")
    second = _parked_task(tm, workspace, "second")

    monkeypatch.setattr(tm.persistence, "_dump", lambda: False)
    client = _client(tm)
    try:
        response = client.delete("/api/tasks", params={"dir": str(workspace)})
        assert response.status_code == 500, response.text
        assert "could not persist" in response.json()["detail"]
    finally:
        client.__exit__(None, None, None)

    assert tm.get_task(first) is not None
    assert tm.get_task(second) is not None
    stored = json.loads((settings.data_path / "tasks.json").read_text(encoding="utf-8"))
    assert {item["id"] for item in stored} >= {first, second}


def test_trace_cleanup_closes_recorder_before_unlink(tmp_path, monkeypatch):
    """LOW: Windows refuses unlink while the JSONL handle is open.

    The worker pops ``_threads`` before closing its trace, so delete may win
    that window. The cleanup therefore closes the recorder first (idempotent)
    and only then unlinks; this test pins the call order.
    """
    settings = make_settings(tmp_path, trace_enabled=True)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    task_id = _parked_task(tm, tmp_path / "ws")

    calls = []

    class FakeTrace:
        def close(self, tid):
            calls.append(f"close:{tid}")

        def file_path(self, tid):
            class FakePath:
                def unlink(self, missing_ok=False):
                    calls.append(f"unlink:{tid}")

            return FakePath()

    monkeypatch.setattr(tm, "_trace", FakeTrace())
    result = tm.delete_task(task_id)
    assert result["cleanup"]["trace"] is True
    assert calls == [f"close:{task_id}", f"unlink:{task_id}"]
