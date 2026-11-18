"""#332 S5: ``POST /api/server/stop`` — explicit shared-backend shutdown.

The endpoint is the only way a client may stop the registered backend. It
verifies the caller's instance id, refuses new work (503), parks live tasks,
waits for their workers, releases process resources and the single-instance
record, then asks uvicorn to exit. An incomplete shutdown is reported as such
(504) and never claims success.
"""

from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend import server_lock
from backend.api.schemas import ChatTurn, Task, TaskStatus
from backend.core.llm.client import MockLLMClient
from backend.main import create_app
from backend.server_lock import ServerLock
from backend.services.persistence import Persistence
from backend.tests.conftest import make_manager, make_settings


class FakeTaskManager:
    """Just enough of TaskManager for the stop orchestration."""

    def __init__(self, tasks=(), *, joins=True, shutdown_ok=True):
        self.persistence = SimpleNamespace(list_all=lambda: list(tasks))
        self.stopped: list[str] = []
        self.shutdown_calls = 0
        self._joins = joins
        self._shutdown_ok = shutdown_ok

    def stop(self, task_id: str):
        self.stopped.append(task_id)
        for task in self.persistence.list_all():
            if task.id == task_id:
                task.status = TaskStatus.INTERRUPTED
        return {"ok": True, "status": TaskStatus.INTERRUPTED.value}

    def kill_background(self, task_id: str) -> int:
        return 0

    def list_tasks(self, limit: int = 50, workspace=None):
        return list(self.persistence.list_all())[:limit]

    def wait_for_idle(self, timeout: float = 0.0) -> bool:
        return self._joins

    def shutdown(self) -> bool:
        self.shutdown_calls += 1
        return self._shutdown_ok


def _app(tmp_path, monkeypatch, instance="inst-1"):
    settings = make_settings(tmp_path)
    monkeypatch.setenv("QIANFAN_INSTANCE", instance)
    app = create_app(settings)
    exits: list[bool] = []
    app.state.request_server_exit = lambda: exits.append(True)
    return settings, app, exits


def _claim(settings, instance="inst-1", port=9123):
    lock_file = server_lock.lock_path(settings)
    assert server_lock.claim_lock(
        lock_file,
        ServerLock(
            pid=os.getpid(),
            port=port,
            started_at=server_lock.now_iso(),
            instance=instance,
            mode="serve",
        ),
    )
    return lock_file


def test_stop_rejects_a_mismatched_instance_and_does_not_touch_tasks(tmp_path, monkeypatch):
    settings, app, exits = _app(tmp_path, monkeypatch)
    lock_file = _claim(settings)
    tm = FakeTaskManager([SimpleNamespace(id="t1", status=TaskStatus.RUNNING)])
    with TestClient(app, base_url="http://localhost") as client:
        app.state.task_manager = tm
        response = client.post("/api/server/stop", json={"instance": "someone-else"})

        assert response.status_code == 409
        assert "instance" in response.json()["detail"]
        assert tm.stopped == []
        assert exits == []
        assert server_lock.read_lock(lock_file) is not None
        # The backend is still serving: a rejected stop changes nothing.
        assert client.get("/api/plugins").status_code == 200


def test_stop_refuses_when_no_instance_is_registered(tmp_path, monkeypatch):
    _settings, app, exits = _app(tmp_path, monkeypatch, instance="")
    with TestClient(app, base_url="http://localhost") as client:
        response = client.post("/api/server/stop", json={"instance": "inst-1"})

    assert response.status_code == 409
    assert exits == []


def test_stop_requires_an_instance_field(tmp_path, monkeypatch):
    _settings, app, _exits = _app(tmp_path, monkeypatch)
    with TestClient(app, base_url="http://localhost") as client:
        response = client.post("/api/server/stop", json={})

    assert response.status_code == 422


def test_stop_reports_completion_releases_the_lock_and_rejects_new_work(tmp_path, monkeypatch):
    settings, app, exits = _app(tmp_path, monkeypatch)
    lock_file = _claim(settings)
    tm = FakeTaskManager([SimpleNamespace(id="t1", status=TaskStatus.RUNNING)])
    with TestClient(app, base_url="http://localhost") as client:
        app.state.task_manager = tm
        response = client.post("/api/server/stop", json={"instance": "inst-1"})

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["stopped_tasks"] == ["t1"]
        assert data["workers_joined"] is True
        assert data["shutdown_complete"] is True
        assert data["lock_released"] is True
        assert data["exit_requested"] is True
        assert tm.stopped == ["t1"]
        assert tm.shutdown_calls == 1
        assert exits == [True]
        assert server_lock.read_lock(lock_file) is None
        # 拒绝新运行：the stop request itself completed, everything after is 503.
        assert client.get("/api/tasks").status_code == 503
        # The readiness probe still answers while the process finishes exiting.
        assert client.get("/health").status_code == 200


def test_stop_reports_an_incomplete_shutdown_without_claiming_success(tmp_path, monkeypatch):
    from backend.services import server_control

    monkeypatch.setattr(server_control, "STOP_TIMEOUT_SEC", 0.2)
    settings, app, exits = _app(tmp_path, monkeypatch)
    lock_file = _claim(settings)
    tm = FakeTaskManager(
        [SimpleNamespace(id="t1", status=TaskStatus.RUNNING)],
        joins=False,
        shutdown_ok=False,
    )
    with TestClient(app, base_url="http://localhost") as client:
        app.state.task_manager = tm
        response = client.post("/api/server/stop", json={"instance": "inst-1"})

        assert response.status_code == 504
        data = response.json()["data"]
        assert data["stopped_tasks"] == ["t1"]
        assert data["workers_joined"] is False
        assert data["shutdown_complete"] is False
        assert data["lock_released"] is False
        assert data["exit_requested"] is False
        assert exits == []
        # A live worker may still write tasks.json: the record must stay ours.
        assert server_lock.read_lock(lock_file) is not None


def _running_task(settings, task_id="t1"):
    now = server_lock.now_iso()
    task = Task(
        id=task_id,
        title="live",
        user_input="hi",
        workspace=str(settings.artifacts_path),
        status=TaskStatus.RUNNING,
        conversation=[ChatTurn(role="user", content="hi")],
        created_at=now,
        updated_at=now,
    )
    assert Persistence(settings).save_task(task)
    return task


def test_stop_parks_a_running_task_and_waits_for_its_worker(tmp_path, monkeypatch):
    settings, app, exits = _app(tmp_path, monkeypatch)
    lock_file = _claim(settings)
    # Build the manager first: its startup reconciliation parks orphaned
    # RUNNING records, so the live one is registered afterwards (as a running
    # worker would own it in the serving process).
    tm = make_manager(settings, MockLLMClient())
    task = _running_task(settings)
    assert tm.persistence.save_task(task)
    finished = threading.Event()

    def slow_worker():
        time.sleep(0.2)
        finished.set()

    worker = threading.Thread(target=slow_worker, name="slow-worker")
    tm._threads[task.id] = worker
    worker.start()

    with TestClient(app, base_url="http://localhost") as client:
        app.state.task_manager = tm
        response = client.post("/api/server/stop", json={"instance": "inst-1"})

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["stopped_tasks"] == [task.id]
        assert data["workers_joined"] is True
        assert data["shutdown_complete"] is True
        assert finished.is_set()
        assert not worker.is_alive()
        assert exits == [True]

    persisted = Persistence(settings).load_task(task.id)
    assert persisted is not None and persisted.status is TaskStatus.INTERRUPTED
    assert server_lock.read_lock(lock_file) is None
