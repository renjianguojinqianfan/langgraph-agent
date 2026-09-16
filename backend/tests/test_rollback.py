"""P1-B rollback tests: per-write file-level before-images + whole-task restore.

Design contract under test (``docs/specs/p1-b-rollback.md``):

* capture happens *before* every sandbox file write (file_io write / edit) and
  lands **outside** the sandbox under ``<data_dir>/snapshots/<task_id>/``;
* the ledger (``ledger.jsonl`` + ``NNNN.bak`` files) is a reverse-replay undo
  log: restoring walks it backwards to the task's starting state;
* restore is decoupled from resume (never touches checkpoints / task state),
  idempotent on a second call, and refuses nothing that is not actively
  running (RUNNING/PENDING/live worker -> caller raises);
* every failure mode is fail-open: a broken snapshot must never break a tool
  call, a broken retention photo must never break a rollback.

Two layers, mirroring ``test_resume.py``: pure ``snapshots`` functions, then
the ``TaskManager``/REST seam with a scripted mock LLM. All offline.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.schemas import TaskStatus
from backend.core.agent.subagent import SubAgentExecutor, SubTaskSpec
from backend.core.llm.client import MockLLMClient
from backend.main import app
from backend.services import snapshots
from backend.services.event_bus import EventBus
from backend.services.persistence import Persistence
from backend.services.snapshots import (
    capture_before_image,
    cleanup_expired,
    get_current_task_id,
    restore_task,
    set_current_task_id,
)
from backend.tests.conftest import make_manager, make_settings


@pytest.fixture(autouse=True)
def _clear_task_ctx():
    """The contextvar is per-thread and pytest reuses one thread: reset it so
    no test inherits another's task id."""
    set_current_task_id(None)
    yield
    set_current_task_id(None)


def _s(tmp_path, **overrides):
    """Snapshot-enabled settings rooted at ``tmp_path`` (suite default is off)."""
    overrides.setdefault("snapshot_enabled", True)
    return make_settings(tmp_path, **overrides)


def _write(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _ledger(settings, task_id: str) -> list[dict]:
    f = settings.snapshots_path / task_id / snapshots.LEDGER_NAME
    if not f.exists():
        return []
    return [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]


# ───────────────────── pure layer: capture ─────────────────────
def test_capture_without_task_id_is_a_noop(tmp_path):
    settings = _s(tmp_path)
    target = _write(settings.artifacts_path, "a.txt", "hello")
    assert capture_before_image(target, settings=settings) is False
    assert not settings.snapshots_path.exists()


def test_capture_disabled_by_switch(tmp_path):
    settings = _s(tmp_path, snapshot_enabled=False)
    set_current_task_id("t-off")
    target = _write(settings.artifacts_path, "a.txt", "hello")
    assert capture_before_image(target, settings=settings) is False
    assert not settings.snapshots_path.exists()


def test_capture_new_file_records_creation(tmp_path):
    settings = _s(tmp_path)
    set_current_task_id("t-new")
    target = settings.artifacts_path / "fresh.txt"  # does not exist yet
    assert capture_before_image(target, settings=settings) is True
    lines = _ledger(settings, "t-new")
    assert len(lines) == 1
    assert lines[0]["path"] == "fresh.txt"
    assert lines[0]["existed"] is False
    assert lines[0]["backup"] is None
    task_dir = settings.snapshots_path / "t-new"
    assert not list(task_dir.glob("*.bak"))


def test_capture_existing_file_stores_before_image(tmp_path):
    settings = _s(tmp_path)
    set_current_task_id("t-mod")
    target = _write(settings.artifacts_path, "sub/a.txt", "old version")
    assert capture_before_image(target, settings=settings) is True
    lines = _ledger(settings, "t-mod")
    assert lines[0]["path"] == "sub/a.txt"
    assert lines[0]["existed"] is True
    assert lines[0]["backup"] == "0001.bak"
    assert (settings.snapshots_path / "t-mod" / "0001.bak").read_text(encoding="utf-8") == "old version"


def test_capture_outside_sandbox_is_skipped(tmp_path):
    settings = _s(tmp_path)
    set_current_task_id("t-esc")
    outside = tmp_path / "outside.txt"
    outside.write_text("nope", encoding="utf-8")
    assert capture_before_image(outside, settings=settings) is False
    assert _ledger(settings, "t-esc") == []


def test_capture_is_fail_open_when_store_unwritable(tmp_path):
    """A blocked snapshot store must degrade to False, never raise."""
    blocker = tmp_path / "blocked"
    blocker.write_text("i am a file, not a directory", encoding="utf-8")
    settings = _s(tmp_path, snapshot_dir=str(blocker))
    set_current_task_id("t-boom")
    target = _write(settings.artifacts_path, "a.txt", "hello")
    assert capture_before_image(target, settings=settings) is False  # no exception


def test_capture_sequence_is_stable(tmp_path):
    """Two captures of the same path get distinct seq numbers / backups."""
    settings = _s(tmp_path)
    set_current_task_id("t-seq")
    target = _write(settings.artifacts_path, "a.txt", "v1")
    capture_before_image(target, settings=settings)
    target.write_text("v2", encoding="utf-8")
    capture_before_image(target, settings=settings)
    lines = _ledger(settings, "t-seq")
    assert [line["seq"] for line in lines] == [1, 2]
    assert lines[0]["backup"] != lines[1]["backup"]


# ───────────────────── pure layer: restore ────────────────────
def test_restore_without_ledger_is_a_noop(tmp_path):
    settings = _s(tmp_path)
    result = restore_task("t-none", settings=settings)
    assert result == {"ok": True, "files": [], "already_original": True}


def test_restore_deletes_file_created_during_task(tmp_path):
    settings = _s(tmp_path)
    set_current_task_id("t-c")
    target = settings.artifacts_path / "made.txt"
    capture_before_image(target, settings=settings)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("agent output", encoding="utf-8")

    result = restore_task("t-c", settings=settings)
    assert result["ok"] is True
    assert result["files"] == ["made.txt"]
    assert result["already_original"] is False
    assert not target.exists()


def test_restore_reverts_modified_file_to_before_image(tmp_path):
    settings = _s(tmp_path)
    set_current_task_id("t-m")
    target = _write(settings.artifacts_path, "a.txt", "original")
    capture_before_image(target, settings=settings)
    target.write_text("agent rewrite", encoding="utf-8")

    result = restore_task("t-m", settings=settings)
    assert result["files"] == ["a.txt"]
    assert target.read_text(encoding="utf-8") == "original"


def test_restore_same_file_twice_returns_to_oldest_version(tmp_path):
    settings = _s(tmp_path)
    set_current_task_id("t-2x")
    target = _write(settings.artifacts_path, "a.txt", "v1")
    capture_before_image(target, settings=settings)
    target.write_text("v2", encoding="utf-8")
    capture_before_image(target, settings=settings)
    target.write_text("v3", encoding="utf-8")

    restore_task("t-2x", settings=settings)
    assert target.read_text(encoding="utf-8") == "v1"  # whole-task semantics


def test_restore_multiple_files_including_subdirs(tmp_path):
    settings = _s(tmp_path)
    set_current_task_id("t-multi")
    kept = _write(settings.artifacts_path, "keep.txt", "before")
    added = settings.artifacts_path / "sub" / "new.txt"
    capture_before_image(kept, settings=settings)
    capture_before_image(added, settings=settings)
    kept.write_text("after", encoding="utf-8")
    added.parent.mkdir(parents=True, exist_ok=True)
    added.write_text("fresh", encoding="utf-8")

    result = restore_task("t-multi", settings=settings)
    assert sorted(result["files"]) == ["keep.txt", "sub/new.txt"]
    assert kept.read_text(encoding="utf-8") == "before"
    assert not added.exists()


def test_restore_is_idempotent_on_second_call(tmp_path):
    settings = _s(tmp_path)
    set_current_task_id("t-idem")
    target = _write(settings.artifacts_path, "a.txt", "original")
    capture_before_image(target, settings=settings)
    target.write_text("changed", encoding="utf-8")

    first = restore_task("t-idem", settings=settings)
    second = restore_task("t-idem", settings=settings)
    assert first["files"] == ["a.txt"]
    assert second["files"] == []
    assert second["already_original"] is True


def test_restore_keeps_retention_photo_of_current_state(tmp_path):
    settings = _s(tmp_path)
    set_current_task_id("t-ret")
    target = _write(settings.artifacts_path, "a.txt", "original")
    capture_before_image(target, settings=settings)
    target.write_text("current state", encoding="utf-8")

    restore_task("t-ret", settings=settings)
    retention = settings.snapshots_path / "t-ret" / snapshots.RETENTION_DIR
    entries = [json.loads(line) for line in (retention / snapshots.RETENTION_LEDGER).read_text(encoding="utf-8").splitlines() if line.strip()]
    assert entries and entries[0]["path"] == "a.txt"
    assert (retention / entries[0]["backup"]).read_text(encoding="utf-8") == "current state"


def test_restore_skips_corrupt_ledger_line(tmp_path):
    settings = _s(tmp_path)
    set_current_task_id("t-corrupt")
    target = _write(settings.artifacts_path, "a.txt", "original")
    capture_before_image(target, settings=settings)
    target.write_text("changed", encoding="utf-8")
    ledger = settings.snapshots_path / "t-corrupt" / snapshots.LEDGER_NAME
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write("{not json at all\n")

    result = restore_task("t-corrupt", settings=settings)
    assert result["files"] == ["a.txt"]
    assert target.read_text(encoding="utf-8") == "original"


def test_restore_refuses_ledger_path_escaping_sandbox(tmp_path):
    settings = _s(tmp_path)
    task_dir = settings.snapshots_path / "t-escape"
    task_dir.mkdir(parents=True, exist_ok=True)
    evil = tmp_path / "evil.txt"
    evil.write_text("untouched", encoding="utf-8")
    (task_dir / snapshots.LEDGER_NAME).write_text(
        json.dumps(
            {"seq": 1, "path": "../evil.txt", "existed": True, "backup": "0001.bak",
             "chars": 0, "ts": "2026-09-17T00:00:00+00:00"}
        )
        + "\n",
        encoding="utf-8",
    )
    (task_dir / "0001.bak").write_text("pwned", encoding="utf-8")

    result = restore_task("t-escape", settings=settings)
    assert result["files"] == []
    assert evil.read_text(encoding="utf-8") == "untouched"


def test_cleanup_expired_removes_only_old_task_dirs(tmp_path):
    settings = _s(tmp_path)
    old_dir = settings.snapshots_path / "old-task"
    new_dir = settings.snapshots_path / "new-task"
    for d in (old_dir, new_dir):
        d.mkdir(parents=True, exist_ok=True)
        (d / snapshots.LEDGER_NAME).write_text("", encoding="utf-8")
    stale = time.time() - 40 * 86400
    for p in (old_dir, old_dir / snapshots.LEDGER_NAME):
        os.utime(p, (stale, stale))

    removed = cleanup_expired(settings=settings)
    assert removed == 1
    assert not old_dir.exists()
    assert new_dir.exists()


def test_cleanup_keeps_dir_whose_ledger_was_touched_recently(tmp_path):
    """Appending does not bump the dir mtime — the ledger is the age reference."""
    settings = _s(tmp_path)
    d = settings.snapshots_path / "still-writing"
    d.mkdir(parents=True, exist_ok=True)
    ledger = d / snapshots.LEDGER_NAME
    ledger.write_text("", encoding="utf-8")
    stale = time.time() - 60 * 86400
    os.utime(d, (stale, stale))  # dir looks ancient, ledger looks fresh

    assert cleanup_expired(settings=settings) == 0
    assert d.exists()


def test_cleanup_disabled_when_retention_not_positive(tmp_path):
    settings = _s(tmp_path, snapshot_retention_days=0)
    d = settings.snapshots_path / "old-task"
    d.mkdir(parents=True, exist_ok=True)
    stale = time.time() - 400 * 86400
    os.utime(d, (stale, stale))
    assert cleanup_expired(settings=settings) == 0
    assert d.exists()


def test_cleanup_disabled_by_master_switch(tmp_path):
    """A disabled feature must not delete data it no longer owns."""
    settings_before = _s(tmp_path)
    d = settings_before.snapshots_path / "old-task"
    d.mkdir(parents=True, exist_ok=True)
    (d / snapshots.LEDGER_NAME).write_text("", encoding="utf-8")
    stale = time.time() - 400 * 86400
    for p in (d, d / snapshots.LEDGER_NAME):
        os.utime(p, (stale, stale))

    settings_off = _s(tmp_path, snapshot_enabled=False)
    assert cleanup_expired(settings=settings_off) == 0
    assert d.exists()


# ───────────────────── seam: tools capture, manager restores ─────────────────────
def _writer_mock(path: str, content: str) -> MockLLMClient:
    return MockLLMClient(
        plan=["write one file"],
        tool_calls=[
            {"id": "w1", "name": "file_io", "arguments": {"action": "write", "path": path, "content": content}}
        ],
        final_answer="done",
    )


def _wait(tm, task_id, statuses=("COMPLETED", "FAILED", "INTERRUPTED"), timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = tm.get_task(task_id)
        if t and t.status.value in statuses:
            return t
        time.sleep(0.05)
    return tm.get_task(task_id)


def _wait_unwound(tm, task_id, timeout=10.0):
    """Wait for run()'s teardown to pop the worker thread.

    A terminal status is persisted a beat before ``_threads`` is popped, and
    rollback correctly refuses a task whose worker is still unwinding — so
    tests that roll back a settled task must first let that window close.
    """
    deadline = time.time() + timeout
    while time.time() < deadline and tm._threads.get(task_id) is not None:
        time.sleep(0.02)


def _run_to_completion(settings, path: str, content: str, event_bus=None):
    tm = make_manager(settings, _writer_mock(path, content), event_bus=event_bus)
    task_id = tm.create_task(title="writer", user_input="write a file")
    task = _wait(tm, task_id)
    assert task.status == TaskStatus.COMPLETED
    _wait_unwound(tm, task_id)
    return tm, task_id


def test_run_writes_ledger_and_rollback_restores(tmp_path):
    settings = _s(tmp_path)
    bus = EventBus()
    original = _write(settings.artifacts_path, "report.txt", "hand-written")
    tm, task_id = _run_to_completion(settings, "report.txt", "agent version", event_bus=bus)
    assert (settings.artifacts_path / "report.txt").read_text(encoding="utf-8") == "agent version"
    assert len(_ledger(settings, task_id)) == 1

    result = tm.rollback(task_id)
    assert result["ok"] is True
    assert result["files"] == ["report.txt"]
    assert (settings.artifacts_path / "report.txt").read_text(encoding="utf-8") == "hand-written"
    assert original.exists()
    events = [e for e in bus.replay(task_id) if e["type"] == "task_rollback"]
    assert len(events) == 1
    assert events[0]["data"]["files"] == ["report.txt"]


def test_rollback_does_not_touch_task_state_or_checkpoints(tmp_path):
    settings = _s(tmp_path)
    tm, task_id = _run_to_completion(settings, "a.txt", "x")
    before = tm.get_task(task_id).model_dump()
    tm.rollback(task_id)
    after = tm.get_task(task_id).model_dump()
    assert after == before  # rollback is file-only: decoupled from resume/checkpoints


def test_rollback_does_not_open_a_resume_path(tmp_path):
    """Decoupled recovery planes: a rollback never makes resume admissible."""
    settings = _s(tmp_path, checkpoint_enabled=True)
    tm, task_id = _run_to_completion(settings, "a.txt", "x")
    tm.rollback(task_id)
    # COMPLETED stays COMPLETED — the three resume 409s are untouched.
    with pytest.raises(RuntimeError):
        tm.resume(task_id)


def test_rollback_rejected_while_task_is_active(tmp_path):
    settings = _s(tmp_path)
    tm, task_id = _run_to_completion(settings, "a.txt", "x")
    # Flip the persisted record back to RUNNING: the status alone is the gate
    # (防重放与活跃写交错), even with no live worker thread.
    t = tm.get_task(task_id)
    t.status = TaskStatus.RUNNING
    tm.persistence.save_task(t)
    with pytest.raises(RuntimeError):
        tm.rollback(task_id)
    assert (settings.artifacts_path / "a.txt").read_text(encoding="utf-8") == "x"


def test_rollback_rejected_while_worker_thread_still_alive(tmp_path):
    settings = _s(tmp_path)
    tm, task_id = _run_to_completion(settings, "a.txt", "x")
    # A settled task whose worker is still unwinding (stop() window) must not
    # be rolled back underneath the final writes.
    gate = threading.Event()
    worker = threading.Thread(target=gate.wait, daemon=True)
    worker.start()
    tm._threads[task_id] = worker
    try:
        with pytest.raises(RuntimeError):
            tm.rollback(task_id)
    finally:
        tm._threads.pop(task_id, None)
        gate.set()
        worker.join(timeout=5)


def test_rollback_missing_task_raises(tmp_path):
    settings = _s(tmp_path)
    tm = make_manager(settings, _writer_mock("unused.txt", "x"))
    with pytest.raises(RuntimeError):
        tm.rollback("no-such-task")


def test_subtask_writes_land_in_parent_ledger(tmp_path):
    """Pool threads get a fresh context — _exec_one must re-seat the parent id."""
    settings = _s(tmp_path, subagent_max_concurrency=2)
    tm = make_manager(settings, _writer_mock("sub_out.txt", "from subtask"))
    executor = SubAgentExecutor(tm, settings)
    spec = SubTaskSpec(
        subtask_id="s1", name="writer", instruction="write the file",
        parent_task_id="parent-task",
    )
    executor._pool.submit(executor._exec_one, spec).result(timeout=30)

    lines = _ledger(settings, "parent-task")
    assert len(lines) == 1
    assert lines[0]["path"] == "sub_out.txt"
    assert get_current_task_id() is None  # pool thread's context died with it


def test_disabled_switch_is_zero_regression(tmp_path):
    settings = _s(tmp_path, snapshot_enabled=False)
    tm, task_id = _run_to_completion(settings, "a.txt", "written")
    assert (settings.artifacts_path / "a.txt").exists()
    assert not settings.snapshots_path.exists()
    # Rollback without a ledger is a 200-style no-op, not an error.
    assert tm.rollback(task_id) == {"ok": True, "files": [], "already_original": True}


def test_startup_cleanup_runs_from_manager_construction(tmp_path):
    settings = _s(tmp_path)
    stale_dir = settings.snapshots_path / "ancient-task"
    stale_dir.mkdir(parents=True, exist_ok=True)
    ledger = stale_dir / snapshots.LEDGER_NAME
    ledger.write_text("", encoding="utf-8")
    old = time.time() - 90 * 86400
    for p in (stale_dir, ledger):
        os.utime(p, (old, old))

    make_manager(settings, MockLLMClient(plan=[], final_answer="noop"))
    assert not stale_dir.exists()


# ───────────────────── REST seam ─────────────────────
@pytest.fixture
def client(tmp_path):
    settings = _s(tmp_path)
    eb = EventBus()
    persistence = Persistence(settings)
    mock = _writer_mock("api.txt", "via api")
    tm = make_manager(settings, mock, event_bus=eb, persistence=persistence)

    with TestClient(app) as test_client:
        test_client.app.state.settings = settings
        test_client.app.state.event_bus = eb
        test_client.app.state.persistence = persistence
        test_client.app.state.task_manager = tm
        yield test_client, settings, tm


def test_rollback_endpoint_restores_files(client):
    test_client, settings, tm = client
    tid = test_client.post("/api/tasks", json={"input": "write api.txt"}).json()["data"]["task_id"]
    _wait(tm, tid)
    _wait_unwound(tm, tid)
    r = test_client.post(f"/api/tasks/{tid}/rollback")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["files"] == ["api.txt"]
    assert body["data"]["already_original"] is False
    assert not (settings.artifacts_path / "api.txt").exists()


def test_rollback_endpoint_404_for_unknown_task(client):
    test_client, _, _ = client
    r = test_client.post("/api/tasks/ghost/rollback")
    assert r.status_code == 404


def test_rollback_endpoint_409_for_active_task(client):
    test_client, settings, tm = client
    tid = test_client.post("/api/tasks", json={"input": "write api.txt"}).json()["data"]["task_id"]
    t = tm.get_task(tid)
    t.status = TaskStatus.RUNNING
    tm.persistence.save_task(t)
    r = test_client.post(f"/api/tasks/{tid}/rollback")
    assert r.status_code == 409
