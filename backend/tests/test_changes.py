"""#343: per-round write/edit change capture, storage and review API.

The contract is ``docs/specs/workspace-files-changes-ui.md`` §5.1–5.3. These
tests exercise the real tool path (``AgentRuntime.tool_node`` through a
scripted mock LLM) plus the store seam directly for round-end behaviour and
the caps, and the HTTP faces of both read-only endpoints.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.core.llm.client import MockLLMClient
from backend.main import app
from backend.services.auth import TokenIssuer
from backend.services.changes import ChangesRecorder, ChangesStore
from backend.tests.conftest import make_manager, make_settings


def _wait(tm: Any, task_id: str, timeout: float = 15.0) -> Any:
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = tm.get_task(task_id)
        if task and task.status.value in ("INTERRUPTED", "FAILED"):
            return task
        time.sleep(0.05)
    return tm.get_task(task_id)


def _run_task(tmp_path: Path, tool_calls: list[dict], workspace: Path, **settings_overrides):
    settings = make_settings(tmp_path, **settings_overrides)
    mock = MockLLMClient(tool_calls=tool_calls, final_answer="done")
    tm = make_manager(settings, mock)
    task_id = tm.create_task("changes", "do it", workspace=str(workspace))
    task = _wait(tm, task_id)
    assert task is not None
    return settings, tm, task_id, task


# ── capture through the real tool path ──
def test_write_round_is_captured_and_readable(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    settings, tm, task_id, task = _run_task(
        tmp_path,
        [
            {
                "id": "call_1",
                "name": "write",
                "arguments": {"path": "notes/a.txt", "content": "one\ntwo\n"},
            }
        ],
        ws,
    )
    assert task.status.value == "INTERRUPTED"

    summary = tm.changes.read_round(task_id, 1)
    assert summary["round"] == 1
    assert summary["total"] == 1
    assert summary["truncated"] is False
    assert summary["added"] == 2
    assert summary["deleted"] == 0

    entry = summary["files"][0]
    assert entry["path"] == "notes/a.txt"
    assert entry["display"] == "notes/a.txt"
    assert entry["status"] == "added"
    assert entry["binary"] is False
    assert entry["oversized"] is False

    detail = tm.changes.read_detail(task_id, 1, entry["index"])
    assert detail is not None
    assert detail["status"] == "added"
    assert detail["hunks"] == [
        {
            "oldStart": 0,
            "oldLines": 0,
            "newStart": 1,
            "newLines": 2,
            "lines": ["+one", "+two"],
        }
    ]

    round_dir = settings.changes_path / task_id / "1"
    record_dir = next(p for p in round_dir.iterdir() if p.is_dir())
    assert (record_dir / "meta.json").is_file()
    assert (record_dir / "after").read_text(encoding="utf-8") == "one\ntwo\n"
    assert not (record_dir / "before").exists()


def test_repeated_edits_merge_into_one_net_change(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "f.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    settings, tm, task_id, task = _run_task(
        tmp_path,
        [
            {
                "id": "call_1",
                "name": "edit",
                "arguments": {"path": "f.txt", "old_string": "alpha", "new_string": "ALPHA"},
            },
            {
                "id": "call_2",
                "name": "edit",
                "arguments": {"path": "f.txt", "old_string": "beta", "new_string": "BETA"},
            },
        ],
        ws,
    )
    assert task.status.value == "INTERRUPTED"

    summary = tm.changes.read_round(task_id, 1)
    assert summary["total"] == 1
    entry = summary["files"][0]
    assert entry["path"] == "f.txt"
    assert entry["status"] == "modified"
    assert entry["added"] == 2
    assert entry["deleted"] == 2

    detail = tm.changes.read_detail(task_id, 1, entry["index"])
    assert detail is not None
    flat = [line for hunk in detail["hunks"] for line in hunk["lines"]]
    assert "-alpha" in flat and "+ALPHA" in flat
    assert "-beta" in flat and "+BETA" in flat
    assert "-gamma" not in flat and "+gamma" not in flat


def test_first_touch_before_and_round_end_after_include_later_changes(tmp_path: Path) -> None:
    """The first success saves the original; finalize re-reads the current file.

    A later shell write is not itself listed, but its bytes are part of the
    touched file's round-end ``after`` — the UI must say the set is not a full
    workspace diff.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "f.txt").write_text("one\ntwo\n", encoding="utf-8")
    settings = make_settings(tmp_path)
    store = ChangesStore(settings)
    recorder = ChangesRecorder(store, "task-1", ws)

    handle = recorder.begin(
        1, "edit", {"path": "f.txt"}, "call_1"
    )
    assert handle is not None
    (ws / "f.txt").write_text("ONE\ntwo\n", encoding="utf-8")
    recorder.commit(handle)

    # The agent's round ends later; an out-of-band writer (shell) appended a
    # line in between. Finalize must include it, and the other new file must
    # stay unlisted (pure shell writes are not captured).
    (ws / "f.txt").write_text("ONE\ntwo\nthree\n", encoding="utf-8")
    (ws / "from-shell.txt").write_text("not captured\n", encoding="utf-8")
    store.finalize_round("task-1", 1, ws)

    summary = store.read_round("task-1", 1)
    assert summary["total"] == 1
    assert [f["path"] for f in summary["files"]] == ["f.txt"]
    # Net diff is original -> current: the edit plus the later out-of-band line.
    assert summary["added"] == 2
    assert summary["deleted"] == 1

    detail = store.read_detail("task-1", 1, summary["files"][0]["index"])
    assert detail is not None
    flat = [line for hunk in detail["hunks"] for line in hunk["lines"]]
    assert "-one" in flat and "+ONE" in flat
    assert "+three" in flat


def test_failed_write_is_not_recorded(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    settings, tm, task_id, task = _run_task(
        tmp_path,
        [
            {
                "id": "call_bad",
                "name": "write",
                "arguments": {"path": "../escape.txt", "content": "nope"},
            }
        ],
        ws,
    )
    assert task.status.value == "INTERRUPTED"
    assert tm.changes.read_round(task_id, 1)["total"] == 0
    assert not (tmp_path / "escape.txt").exists()


def test_blocked_write_is_not_recorded(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    policy = '[{"tool":"write","pattern":"forbidden","reason":"blocked by test"}]'
    settings, tm, task_id, task = _run_task(
        tmp_path,
        [
            {
                "id": "call_blocked",
                "name": "write",
                "arguments": {"path": "forbidden.txt", "content": "nope"},
            }
        ],
        ws,
        policy_rules=policy,
    )
    assert task.status.value == "INTERRUPTED"
    assert tm.changes.read_round(task_id, 1)["total"] == 0
    assert not (ws / "forbidden.txt").exists()


def test_round_without_changes_is_empty(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    settings, tm, task_id, task = _run_task(tmp_path, [], ws)
    assert tm.changes.read_round(task_id, 1) == {
        "round": 1,
        "total": 0,
        "truncated": False,
        "added": 0,
        "deleted": 0,
        "files": [],
    }


# ── caps, binary and coarse diffs ──
def _record(
    store: ChangesStore,
    task_id: str = "task-1",
    round_no: int = 1,
    path: str = "f.txt",
    *,
    before: bytes | None = None,
    after: bytes | None = None,
    tool_call_id: str = "call_1",
) -> None:
    store.record(
        task_id,
        round_no,
        path,
        before_existed=before is not None,
        before=before,
        after=after,
        tool_call_id=tool_call_id,
    )


def test_binary_write_is_flagged_and_stores_no_content(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    settings, tm, task_id, _task = _run_task(
        tmp_path,
        [
            {
                "id": "call_bin",
                "name": "write",
                "arguments": {"path": "bin.dat", "content": "a\x00b"},
            }
        ],
        ws,
    )
    summary = tm.changes.read_round(task_id, 1)
    assert summary["files"][0]["binary"] is True
    detail = tm.changes.read_detail(task_id, 1, summary["files"][0]["index"])
    assert detail is not None
    assert detail["binary"] is True
    assert detail["hunks"] == []
    record_dir = next(
        p for p in (settings.changes_path / task_id / "1").iterdir() if p.is_dir()
    )
    assert not (record_dir / "after").exists()
    assert not (record_dir / "before").exists()


def test_invalid_utf8_before_is_binary(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "f.txt").write_bytes(b"\xff\xfe")
    store = ChangesStore(make_settings(tmp_path))
    _record(store, before=b"\xff\xfe", after=b"\xff\xfe")
    summary = store.read_round("task-1", 1)
    assert summary["files"][0]["binary"] is True
    detail = store.read_detail("task-1", 1, summary["files"][0]["index"])
    assert detail is not None and detail["hunks"] == []


def test_oversized_file_stores_no_content_and_no_diff(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = ChangesStore(settings, max_file_bytes=4)
    _record(store, after=b"0123456789")
    summary = store.read_round("task-1", 1)
    entry = summary["files"][0]
    assert entry["oversized"] is True
    assert entry["added"] == 0 and entry["deleted"] == 0
    detail = store.read_detail("task-1", 1, entry["index"])
    assert detail is not None
    assert detail["oversized"] is True
    assert detail["hunks"] == []
    record_dir = next(p for p in (settings.changes_path / "task-1" / "1").iterdir() if p.is_dir())
    assert not (record_dir / "after").exists()


def test_round_byte_cap_marks_the_overflowing_file(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = ChangesStore(settings, max_file_bytes=100, max_round_bytes=10)
    _record(store, path="a.txt", after=b"123456")
    _record(store, path="b.txt", after=b"123456")
    summary = store.read_round("task-1", 1)
    by_path = {entry["path"]: entry for entry in summary["files"]}
    assert by_path["a.txt"]["oversized"] is False
    assert by_path["b.txt"]["oversized"] is True


def test_max_files_reports_full_total_and_truncates_the_list(tmp_path: Path) -> None:
    from backend.services.changes import CHANGES_MAX_FILES

    settings = make_settings(tmp_path)
    store = ChangesStore(settings)
    for i in range(CHANGES_MAX_FILES + 5):
        _record(store, path=f"f{i:04d}.txt", after=b"x\n")
    summary = store.read_round("task-1", 1)
    assert summary["total"] == CHANGES_MAX_FILES + 5
    assert summary["truncated"] is True
    assert len(summary["files"]) == CHANGES_MAX_FILES
    assert summary["files"][0]["path"] == "f0000.txt"
    assert summary["files"][-1]["path"] == f"f{CHANGES_MAX_FILES - 1:04d}.txt"


def test_diff_timeout_degrades_to_one_coarse_hunk(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = ChangesStore(settings, diff_timeout_sec=0.0)
    _record(
        store, before=b"one\ntwo\n", after=b"ONE\ntwo\nthree\n"
    )
    summary = store.read_round("task-1", 1)
    assert summary["added"] == 3
    assert summary["deleted"] == 2
    detail = store.read_detail("task-1", 1, summary["files"][0]["index"])
    assert detail is not None
    assert detail["coarse"] is True
    assert len(detail["hunks"]) == 1
    hunk = detail["hunks"][0]
    assert hunk["oldStart"] == 1 and hunk["oldLines"] == 2
    assert hunk["newStart"] == 1 and hunk["newLines"] == 3
    assert hunk["lines"] == ["-one", "-two", "+ONE", "+two", "+three"]


def test_rendered_lines_are_capped_and_marked_truncated(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = ChangesStore(settings, max_rendered_lines=4)
    _record(store, after=b"1\n2\n3\n4\n5\n6\n")
    summary = store.read_round("task-1", 1)
    detail = store.read_detail("task-1", 1, summary["files"][0]["index"])
    assert detail is not None
    assert detail["truncated"] is True
    assert sum(len(h["lines"]) for h in detail["hunks"]) == 4


# ── round lifecycle ──
def test_finalize_never_overwrites_a_finalized_round(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "f.txt").write_text("one\n", encoding="utf-8")
    store = ChangesStore(make_settings(tmp_path))
    _record(store, before=b"one\n", after=b"two\n")
    store.finalize_round("task-1", 1, ws)
    first = store.read_detail("task-1", 1, 0)
    assert first is not None

    (ws / "f.txt").write_text("completely different\n", encoding="utf-8")
    store.finalize_round("task-1", 1, ws)
    assert store.read_detail("task-1", 1, 0) == first


def test_a_new_round_does_not_touch_the_previous_round(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    store = ChangesStore(make_settings(tmp_path))
    _record(store, round_no=1, path="a.txt", after=b"one\n")
    store.finalize_round("task-1", 1, ws)
    round_one = store.read_detail("task-1", 1, 0)
    assert round_one is not None

    _record(store, round_no=2, path="a.txt", before=b"one\n", after=b"two\n")
    store.finalize_round("task-1", 2, ws)
    assert store.read_detail("task-1", 1, 0) == round_one
    round_two = store.read_round("task-1", 2)
    assert round_two["total"] == 1
    assert store.read_round("task-1", 1)["total"] == 1


def test_missing_after_with_a_before_is_deleted(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "gone.txt").write_text("one\ntwo\n", encoding="utf-8")
    store = ChangesStore(make_settings(tmp_path))
    handle = ChangesRecorder(store, "task-1", ws).begin(
        1, "edit", {"path": "gone.txt"}, "call_1"
    )
    assert handle is not None
    (ws / "gone.txt").write_text("one\nTWO\n", encoding="utf-8")
    ChangesRecorder(store, "task-1", ws).commit(handle)
    (ws / "gone.txt").unlink()
    store.finalize_round("task-1", 1, ws)
    summary = store.read_round("task-1", 1)
    assert summary["files"][0]["status"] == "deleted"
    assert summary["deleted"] == 2


def test_task_cleanup_removes_the_round_directory(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    settings, tm, task_id, _task = _run_task(
        tmp_path,
        [
            {
                "id": "call_1",
                "name": "write",
                "arguments": {"path": "a.txt", "content": "x\n"},
            }
        ],
        ws,
    )
    assert (settings.changes_path / task_id).is_dir()
    result = tm.delete_task(task_id)
    assert result["cleanup"]["changes"] is True
    assert not (settings.changes_path / task_id).exists()
    assert tm.changes.read_round(task_id, 1)["total"] == 0


def test_startup_removes_orphan_task_dirs_and_keeps_real_rounds(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    settings, tm, task_id, _task = _run_task(
        tmp_path,
        [
            {
                "id": "call_1",
                "name": "write",
                "arguments": {"path": "a.txt", "content": "x\n"},
            }
        ],
        ws,
    )
    orphan = settings.changes_path / "orphan-task" / "1" / "0"
    orphan.mkdir(parents=True)
    (orphan / "meta.json").write_text('{"path":"x.txt"}', encoding="utf-8")
    tm.shutdown()

    tm2 = make_manager(settings, MockLLMClient(final_answer="done"))
    assert not (settings.changes_path / "orphan-task").exists()
    assert (settings.changes_path / task_id).is_dir()
    assert tm2.changes.read_round(task_id, 1)["total"] == 1


def test_restart_still_reads_old_rounds(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    settings, tm, task_id, _task = _run_task(
        tmp_path,
        [
            {
                "id": "call_1",
                "name": "write",
                "arguments": {"path": "a.txt", "content": "one\ntwo\n"},
            }
        ],
        ws,
    )
    tm.shutdown()

    tm2 = make_manager(settings, MockLLMClient(final_answer="done"))
    summary = tm2.changes.read_round(task_id, 1)
    assert summary["total"] == 1
    detail = tm2.changes.read_detail(task_id, 1, summary["files"][0]["index"])
    assert detail is not None
    assert [line for h in detail["hunks"] for line in h["lines"]] == ["+one", "+two"]


def test_tampered_meta_with_an_absolute_path_is_ignored(tmp_path: Path) -> None:
    """A hand-edited sidecar must not turn an absolute path into an API payload."""
    settings = make_settings(tmp_path)
    store = ChangesStore(settings)
    _record(store, after=b"x\n")
    summary = store.read_round("task-1", 1)
    record_dir = next(p for p in (settings.changes_path / "task-1" / "1").iterdir() if p.is_dir())
    meta = json.loads((record_dir / "meta.json").read_text(encoding="utf-8"))
    meta["path"] = "C:\\secret\\outside.txt"
    meta["display"] = "C:\\secret\\outside.txt"
    (record_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    assert store.read_round("task-1", 1)["total"] == 0
    assert store.read_detail("task-1", 1, summary["files"][0]["index"]) is None


def test_crashed_reindex_is_recovered(tmp_path: Path) -> None:
    """A kill between reindex renames must not hide captured records."""
    settings = make_settings(tmp_path)
    store = ChangesStore(settings)
    _record(store, path="a.txt", after=b"one\n")
    round_dir = settings.changes_path / "task-1" / "1"
    original = next(p for p in round_dir.iterdir() if p.is_dir())
    original.rename(round_dir / ".reindex-0")

    summary = store.read_round("task-1", 1)
    assert summary["total"] == 1
    assert summary["files"][0]["path"] == "a.txt"
    detail = store.read_detail("task-1", 1, summary["files"][0]["index"])
    assert detail is not None
    assert detail["hunks"][0]["lines"] == ["+one"]


def test_concurrent_tasks_keep_separate_task_directories(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = ChangesStore(settings)

    def worker(task_id: str) -> None:
        for i in range(12):
            _record(store, task_id=task_id, path=f"{task_id}-{i}.txt", after=b"x\n")

    threads = [threading.Thread(target=worker, args=(f"task{n}",)) for n in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    for n in range(3):
        summary = store.read_round(f"task{n}", 1)
        assert summary["total"] == 12
        assert all(entry["path"].startswith(f"task{n}-") for entry in summary["files"])


# ── read-only API ──
def _api_client(tmp_path: Path, *, auth: bool = False):
    ws = tmp_path / "ws"
    ws.mkdir()
    settings = make_settings(
        tmp_path,
        auth_enabled=auth,
        auth_token="secret" if auth else "changeme",
    )
    mock = MockLLMClient(
        tool_calls=[
            {
                "id": "call_1",
                "name": "write",
                "arguments": {"path": "a.txt", "content": "one\ntwo\n"},
            },
            {
                "id": "call_2",
                "name": "write",
                "arguments": {"path": "b.txt", "content": "bee\n"},
            },
        ],
        final_answer="done",
    )
    tm = make_manager(settings, mock)
    task_id = tm.create_task("api", "write files", workspace=str(ws))
    assert _wait(tm, task_id).status.value == "INTERRUPTED"
    client = TestClient(app, base_url="http://localhost")
    client.__enter__()
    state = getattr(client.app, "state")
    state.task_manager = tm
    state.settings = settings
    if auth:
        state.auth = TokenIssuer(settings.auth_token, settings.auth_token_ttl_sec)
    return client, tm, task_id, settings


def test_changes_api_lists_and_reads_one_diff(tmp_path: Path) -> None:
    client, _tm, task_id, _settings = _api_client(tmp_path)
    try:
        r = client.get(f"/api/tasks/{task_id}/changes", params={"round": 1})
        assert r.status_code == 200
        body = r.json()["data"]
        assert body["round"] == 1
        assert body["total"] == 2
        assert body["truncated"] is False
        assert body["added"] == 3
        assert [f["path"] for f in body["files"]] == ["a.txt", "b.txt"]
        assert body["files"][0]["status"] == "added"

        index = body["files"][0]["index"]
        d = client.get(f"/api/tasks/{task_id}/changes/1/{index}")
        assert d.status_code == 200
        detail = d.json()["data"]
        assert detail["path"] == "a.txt"
        assert detail["coarse"] is False
        assert detail["hunks"][0]["lines"] == ["+one", "+two"]

        empty = client.get(f"/api/tasks/{task_id}/changes", params={"round": 9})
        assert empty.status_code == 200
        assert empty.json()["data"] == {
            "round": 9,
            "total": 0,
            "truncated": False,
            "added": 0,
            "deleted": 0,
            "files": [],
        }
    finally:
        client.__exit__(None, None, None)


@pytest.mark.parametrize("round_value", [None, "", "0", "-2", "abc", "1.5"])
def test_changes_api_rejects_an_invalid_round_as_400(
    tmp_path: Path, round_value: str | None
) -> None:
    client, _tm, task_id, _settings = _api_client(tmp_path)
    try:
        params = {} if round_value is None else {"round": round_value}
        r = client.get(f"/api/tasks/{task_id}/changes", params=params)
        assert r.status_code == 400, r.text
        d = client.get(f"/api/tasks/{task_id}/changes/{round_value or 'x'}/0")
        assert d.status_code == 400, d.text
    finally:
        client.__exit__(None, None, None)


def test_changes_api_unknown_task_and_index(tmp_path: Path) -> None:
    client, _tm, task_id, _settings = _api_client(tmp_path)
    try:
        assert client.get("/api/tasks/nope/changes", params={"round": 1}).status_code == 404
        assert client.get("/api/tasks/nope/changes/1/0").status_code == 404
        r = client.get(f"/api/tasks/{task_id}/changes/1/999")
        assert r.status_code == 404
        r = client.get(f"/api/tasks/{task_id}/changes/1/abc")
        assert r.status_code == 400
    finally:
        client.__exit__(None, None, None)


def test_changes_api_never_leaks_a_storage_path(tmp_path: Path) -> None:
    client, _tm, task_id, settings = _api_client(tmp_path)
    try:
        responses = [
            client.get(f"/api/tasks/{task_id}/changes", params={"round": 1}),
            client.get(f"/api/tasks/{task_id}/changes/1/0"),
        ]
        for response in responses:
            text = response.text
            assert str(settings.changes_path) not in text
            assert str(tmp_path) not in text
            assert settings.changes_path.as_posix() not in text
    finally:
        client.__exit__(None, None, None)


def test_changes_api_requires_auth(tmp_path: Path) -> None:
    client, _tm, task_id, _settings = _api_client(tmp_path, auth=True)
    try:
        assert client.get(f"/api/tasks/{task_id}/changes", params={"round": 1}).status_code == 401
        assert client.get(f"/api/tasks/{task_id}/changes/1/0").status_code == 401
        issuer = getattr(client.app, "state").auth
        token, _ = issuer.issue()
        headers = {"Authorization": f"Bearer {token}"}
        assert (
            client.get(
                f"/api/tasks/{task_id}/changes", params={"round": 1}, headers=headers
            ).status_code
            == 200
        )
        assert (
            client.get(f"/api/tasks/{task_id}/changes/1/0", headers=headers).status_code
            == 200
        )
    finally:
        client.__exit__(None, None, None)


def test_changes_api_rejects_query_token_on_rest_reads(tmp_path: Path) -> None:
    client, _tm, task_id, _settings = _api_client(tmp_path, auth=True)
    try:
        issuer = getattr(client.app, "state").auth
        token, _ = issuer.issue()
        # Only /events and /files/download accept ?token=; reads are header-only.
        assert (
            client.get(
                f"/api/tasks/{task_id}/changes", params={"round": 1, "token": token}
            ).status_code
            == 401
        )
    finally:
        client.__exit__(None, None, None)
