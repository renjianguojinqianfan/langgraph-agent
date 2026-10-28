"""Failure-path tests for #253 (persistence + lifecycle containment).

These pin the behaviours the HIGH/MEDIUM findings asked for without starting a
real model: a dump failure is visible, a bad checkpoint path degrades instead
of crashing startup, shutdown never closes sqlite under a live worker, and a
trace-file error cannot leave a RUNNING task with no worker.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from backend.api.schemas import ChatTurn, Task, TaskStatus
from backend.core.llm.client import MockLLMClient
from backend.services.persistence import Persistence, PersistenceError
from backend.tests.conftest import make_manager, make_settings


def _parked_task(settings, task_id: str = "t-parked") -> Task:
    now = datetime.now(timezone.utc).isoformat()
    persistence = Persistence(settings)
    task = Task(
        id=task_id,
        title="parked",
        user_input="hi",
        workspace=str(settings.artifacts_path),
        status=TaskStatus.INTERRUPTED,
        conversation=[ChatTurn(role="user", content="hi")],
        created_at=now,
        updated_at=now,
    )
    assert persistence.save_task(task)
    return task


def _allow_resume(tm, monkeypatch) -> None:
    monkeypatch.setattr(tm, "_has_checkpoint", lambda task_id: True)


def _trace_denied(*args, **kwargs):
    raise OSError("trace directory denied")


class _AliveThread:
    def is_alive(self) -> bool:
        return True

    def join(self, timeout=None) -> None:
        pass


class _Conn:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_create_task_save_failure_aborts_without_worker(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient())
    monkeypatch.setattr(tm.persistence, "save_task", lambda task: False)

    with pytest.raises(PersistenceError):
        tm.create_task(title="x", user_input="hello")

    assert tm.list_tasks() == []
    assert tm._threads == {}
    assert not list(settings.trace_path.glob("*.jsonl"))


def test_checkpoint_dir_that_is_a_file_degrades(tmp_path):
    blocker = tmp_path / "checkpoint-file"
    blocker.write_text("not a directory", encoding="utf-8")
    settings = make_settings(
        tmp_path, checkpoint_enabled=True, checkpoint_dir=str(blocker)
    )

    tm = make_manager(settings, MockLLMClient())

    assert tm._checkpointer is None
    assert tm.checkpoint_degraded_reason is not None
    assert str(blocker) in tm.checkpoint_degraded_reason
    assert "do NOT delete snapshots" in tm.checkpoint_degraded_reason


def test_checkpoint_connect_failure_degrades(tmp_path, monkeypatch):
    real_connect = sqlite3.connect

    def fake_connect(*args, **kwargs):
        if kwargs.get("check_same_thread") is False:
            raise sqlite3.OperationalError("database is locked")
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", fake_connect)
    settings = make_settings(tmp_path, checkpoint_enabled=True)

    tm = make_manager(settings, MockLLMClient())

    assert tm._checkpointer is None
    assert "could not be opened" in (tm.checkpoint_degraded_reason or "")


def test_resume_and_continue_report_the_checkpoint_degradation(tmp_path):
    """#253 AC2: the 409 quotes the startup cause, not a contradictory hint."""
    blocker = tmp_path / "checkpoint-file"
    blocker.write_text("not a directory", encoding="utf-8")
    settings = make_settings(
        tmp_path, checkpoint_enabled=True, checkpoint_dir=str(blocker)
    )
    _parked_task(settings)
    tm = make_manager(settings, MockLLMClient())

    with pytest.raises(RuntimeError, match="checkpointing is degraded"):
        tm.resume("t-parked")
    with pytest.raises(RuntimeError, match="checkpointing is degraded"):
        tm.send_message("t-parked", "next")


def test_resume_without_checkpoint_keeps_the_generic_remedy(tmp_path):
    settings = make_settings(tmp_path)  # checkpointing switched off
    _parked_task(settings)
    tm = make_manager(settings, MockLLMClient())

    with pytest.raises(RuntimeError, match="checkpoints disabled"):
        tm.resume("t-parked")
    assert tm.checkpoint_degraded_reason is None


def test_shutdown_keeps_connection_when_worker_is_alive(tmp_path):
    tm = make_manager(make_settings(tmp_path), MockLLMClient())
    conn = _Conn()
    tm._checkpoint_conn = conn
    tm._threads["t-live"] = _AliveThread()
    tm._known_secrets.add("sk-live")
    tm._round_models["t-live"] = ("profile", object())

    tm.shutdown()

    assert conn.closed is False
    assert tm._checkpoint_conn is conn
    # Credential hygiene must not be skipped by the early return.
    assert tm._known_secrets == set()
    assert tm._round_models == {}


def test_shutdown_clears_secrets_without_a_checkpoint_connection(tmp_path):
    tm = make_manager(make_settings(tmp_path), MockLLMClient())
    tm._known_secrets.add("sk-idle")
    tm._round_models["t-idle"] = ("profile", object())

    tm.shutdown()

    assert tm._known_secrets == set()
    assert tm._round_models == {}


def test_shutdown_closes_connection_when_idle(tmp_path):
    settings = make_settings(tmp_path, checkpoint_enabled=True)
    tm = make_manager(settings, MockLLMClient())
    assert tm._checkpoint_conn is not None

    tm.shutdown()

    assert tm._checkpoint_conn is None
    assert tm._checkpointer is None


def test_resume_trace_failure_leaves_task_interrupted(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    before = _parked_task(settings)
    tm = make_manager(settings, MockLLMClient())
    _allow_resume(tm, monkeypatch)
    monkeypatch.setattr(tm._trace, "attach", _trace_denied)

    with pytest.raises(RuntimeError, match="trace is unavailable"):
        tm.resume("t-parked")

    task = tm.get_task("t-parked")
    assert task.status == TaskStatus.INTERRUPTED
    assert task.updated_at == before.updated_at
    assert "t-parked" not in tm._threads


def test_resume_save_failure_rolls_back_to_parked(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    _parked_task(settings)
    tm = make_manager(settings, MockLLMClient())
    _allow_resume(tm, monkeypatch)
    monkeypatch.setattr(tm.persistence, "save_task", lambda task: False)

    with pytest.raises(PersistenceError):
        tm.resume("t-parked")

    task = tm.get_task("t-parked")
    assert task.status == TaskStatus.INTERRUPTED
    assert "t-parked" not in tm._threads


def test_continue_trace_failure_keeps_turn_out(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    _parked_task(settings)
    tm = make_manager(settings, MockLLMClient())
    _allow_resume(tm, monkeypatch)
    monkeypatch.setattr(tm._trace, "attach", _trace_denied)

    with pytest.raises(RuntimeError, match="trace is unavailable"):
        tm.send_message("t-parked", "next")

    task = tm.get_task("t-parked")
    assert task.status == TaskStatus.INTERRUPTED
    assert [turn.content for turn in task.conversation] == ["hi"]
    assert "t-parked" not in tm._threads


def test_continue_save_failure_rolls_back_user_turn(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    _parked_task(settings)
    tm = make_manager(settings, MockLLMClient())
    _allow_resume(tm, monkeypatch)
    monkeypatch.setattr(tm.persistence, "save_task", lambda task: False)

    with pytest.raises(PersistenceError):
        tm.send_message("t-parked", "next")

    task = tm.get_task("t-parked")
    assert task.status == TaskStatus.INTERRUPTED
    assert [turn.content for turn in task.conversation] == ["hi"]
    assert "t-parked" not in tm._threads


def test_set_mode_save_failure_rolls_back(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    _parked_task(settings)
    tm = make_manager(settings, MockLLMClient())
    monkeypatch.setattr(tm.persistence, "save_task", lambda task: False)

    with pytest.raises(PersistenceError):
        tm.set_mode("t-parked", "plan")

    assert tm.get_task("t-parked").mode.value == "build"


def test_stop_save_failure_rolls_back_status(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient())
    now = datetime.now(timezone.utc).isoformat()
    task = Task(
        id="t-running",
        title="running",
        user_input="hi",
        workspace=str(settings.artifacts_path),
        status=TaskStatus.RUNNING,
        created_at=now,
        updated_at=now,
    )
    assert tm.persistence.save_task(task)
    monkeypatch.setattr(tm.persistence, "save_task", lambda candidate: False)

    with pytest.raises(PersistenceError):
        tm.stop("t-running")

    assert tm.get_task("t-running").status == TaskStatus.RUNNING
    assert tm._stop_flags.get("t-running") is True


def test_create_api_surfaces_persistence_failure(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.main import app

    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient())
    with TestClient(app, base_url="http://localhost") as client:
        client.app.state.settings = settings
        client.app.state.task_manager = tm
        client.app.state.persistence = tm.persistence
        monkeypatch.setattr(tm.persistence, "save_task", lambda task: False)

        response = client.post("/api/tasks", json={"input": "hello"})

    assert response.status_code == 500
    assert "could not persist" in response.json()["detail"]
