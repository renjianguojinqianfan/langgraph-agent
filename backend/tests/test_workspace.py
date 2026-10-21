"""W1: the workspace belongs to the task (ADR 0010 decisions 1 / 2).

Two tasks in two directories run the same tool script; every tool (file
read/write/edit/ls/glob/grep, bash, load_skill) and the injection block must
see its own directory. Also covers directory filtering, legacy records and the
deleted-directory 409.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.agent.inject import build_inject_block
from backend.core.llm.client import MockLLMClient
from backend.core.runtime_context import (
    normalize_workspace,
    reset_runtime_context,
    set_runtime_context,
)
from backend.main import app
from backend.services.persistence import Persistence
from backend.tests.conftest import make_manager, make_settings


def _write_skill(workspace: Path, sentinel: str) -> None:
    skill = workspace / ".agents" / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: demo\ndescription: demo skill\n---\nBODY-{sentinel}\n",
        encoding="utf-8",
    )


def _script(tag: str) -> list[dict]:
    return [
        {"id": f"{tag}-w", "name": "write", "arguments": {"path": "probe.txt", "content": f"hello {tag}"}},
        {"id": f"{tag}-r", "name": "read", "arguments": {"path": "probe.txt"}},
        {
            "id": f"{tag}-e",
            "name": "edit",
            "arguments": {"path": "probe.txt", "old_string": f"hello {tag}", "new_string": f"bye {tag}"},
        },
        {"id": f"{tag}-l", "name": "ls", "arguments": {"path": "."}},
        {"id": f"{tag}-g", "name": "glob", "arguments": {"pattern": "*.txt"}},
        {"id": f"{tag}-gr", "name": "grep", "arguments": {"pattern": "bye", "path": "."}},
        {"id": f"{tag}-b", "name": "bash", "arguments": {"command": "pwd"}},
        {"id": f"{tag}-s", "name": "load_skill", "arguments": {"name": "demo"}},
    ]


def _wait_parked(client: TestClient, task_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = client.get(f"/api/tasks/{task_id}").json()["data"]
        if task["status"] in ("INTERRUPTED", "FAILED"):
            return task
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} never parked")


def _run_in_workspace(tmp_path: Path, workspace: Path, tag: str) -> dict:
    settings = make_settings(tmp_path)
    mock = MockLLMClient(tool_calls=_script(tag), final_answer=f"done {tag}")
    tm = make_manager(settings, mock)
    with TestClient(app, base_url="http://localhost") as client:
        getattr(client.app, "state").task_manager = tm
        r = client.post("/api/tasks", json={"input": f"work in {tag}", "workspace": str(workspace)})
        assert r.status_code == 200, r.text
        task = _wait_parked(client, r.json()["data"]["task_id"])
    return task


def _outputs(task: dict, tool: str) -> list[dict]:
    return [
        tc["output"]
        for step in task["steps"]
        for tc in step["tool_calls"]
        if tc["tool_name"] == tool
    ]


def test_every_tool_and_skill_follow_its_own_workspace(tmp_path):
    a = tmp_path / "project-a"
    b = tmp_path / "project-b"
    for workspace, sentinel in ((a, "SENTINEL-A"), (b, "SENTINEL-B")):
        workspace.mkdir()
        _write_skill(workspace, sentinel)

    task_a = _run_in_workspace(tmp_path, a, "A")
    task_b = _run_in_workspace(tmp_path, b, "B")

    assert task_a["workspace"] == normalize_workspace(a)
    assert task_b["workspace"] == normalize_workspace(b)
    assert (a / "probe.txt").read_text(encoding="utf-8").strip() == "bye A"
    assert (b / "probe.txt").read_text(encoding="utf-8").strip() == "bye B"

    for task, tag in ((task_a, "A"), (task_b, "B")):
        assert json.dumps(_outputs(task, "read"), ensure_ascii=False).find(f"hello {tag}") >= 0
        assert "probe.txt" in json.dumps(_outputs(task, "ls"), ensure_ascii=False)
        assert "probe.txt" in json.dumps(_outputs(task, "glob"), ensure_ascii=False)
        assert f"bye {tag}" in json.dumps(_outputs(task, "grep"), ensure_ascii=False)
        bash = _outputs(task, "bash")[0]
        assert os.path.normcase(bash["cwd"]) == os.path.normcase(normalize_workspace(a if tag == "A" else b))
        assert f"BODY-SENTINEL-{tag}" in json.dumps(_outputs(task, "load_skill"), ensure_ascii=False)

    assert "SENTINEL-B" not in json.dumps(task_a, ensure_ascii=False)
    assert "SENTINEL-A" not in json.dumps(task_b, ensure_ascii=False)


def test_injection_reads_the_task_workspace(tmp_path):
    settings = make_settings(tmp_path)
    a = tmp_path / "inj-a"
    b = tmp_path / "inj-b"
    a.mkdir()
    b.mkdir()
    (a / "AGENTS.md").write_text("SENTINEL-INJECT-A", encoding="utf-8")
    (b / "AGENTS.md").write_text("SENTINEL-INJECT-B", encoding="utf-8")

    tokens = set_runtime_context("task-a", a)
    try:
        block = build_inject_block(settings, home_dir=tmp_path / "home")
    finally:
        reset_runtime_context(tokens)

    assert "SENTINEL-INJECT-A" in block
    assert "SENTINEL-INJECT-B" not in block


def _api_client(tmp_path):
    settings = make_settings(tmp_path)
    mock = MockLLMClient(final_answer="ok")
    tm = make_manager(settings, mock)
    client = TestClient(app, base_url="http://localhost")
    client.__enter__()
    getattr(client.app, "state").task_manager = tm
    return client, tm, settings


def _create(tm, workspace: str | None, tag: str) -> str:
    return tm.create_task(tag, f"task {tag}", workspace=workspace)


def _wait_manager(tm, task_id, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = tm.get_task(task_id)
        if task and task.status.value in ("INTERRUPTED", "FAILED"):
            return task
        time.sleep(0.05)
    raise AssertionError("task never parked")


def test_list_filters_by_directory_and_all(tmp_path):
    a = tmp_path / "list-a"
    b = tmp_path / "list-b"
    a.mkdir()
    b.mkdir()
    client, tm, settings = _api_client(tmp_path)
    try:
        id_a = _create(tm, str(a), "A")
        id_b = _create(tm, str(b), "B")
        id_default = _create(tm, None, "D")
        for task_id in (id_a, id_b, id_default):
            _wait_manager(tm, task_id)

        only_a = client.get("/api/tasks", params={"dir": str(a)}).json()["data"]["tasks"]
        assert [t["id"] for t in only_a] == [id_a]

        spellings = [
            str(a) + os.sep,
            str(a) + os.sep + ".",
            str(a) + os.sep + "nested" + os.sep + "..",
            str(a).replace(os.sep, "/"),
        ]
        if os.name == "nt":
            spellings.append(str(a).upper())
        for spelling in spellings:
            tasks = client.get("/api/tasks", params={"dir": spelling}).json()["data"]["tasks"]
            assert [t["id"] for t in tasks] == [id_a], spelling

        all_tasks = client.get("/api/tasks", params={"dir": "all"}).json()["data"]["tasks"]
        assert {t["id"] for t in all_tasks} == {id_a, id_b, id_default}

        default_tasks = client.get("/api/tasks").json()["data"]["tasks"]
        assert [t["id"] for t in default_tasks] == [id_default]
        assert default_tasks[0]["workspace"] == normalize_workspace(settings.artifacts_path)
    finally:
        client.__exit__(None, None, None)


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(r"\\?\C:\tmp", id="device-namespace"),
        pytest.param(r"\\.\pipe\qianfan", id="device-pipe"),
        pytest.param("bad\x00path", id="nul-byte"),
        pytest.param(r"\\server\share", id="unc-backslash"),
        pytest.param("//server/share", id="unc-forward-slash"),
    ],
)
def test_list_tasks_rejects_non_local_workspace_paths(tmp_path, bad):
    """``?dir=`` must be rejected before ``resolve()`` can dial an SMB server."""
    client, _tm, _settings = _api_client(tmp_path)
    try:
        r = client.get("/api/tasks", params={"dir": bad})
        assert r.status_code == 400, r.text
    finally:
        client.__exit__(None, None, None)


def test_legacy_task_gets_the_old_sandbox_workspace(tmp_path):
    settings = make_settings(tmp_path)
    settings.data_path.mkdir(parents=True, exist_ok=True)
    legacy = [
        {
            "id": "old-1",
            "title": "legacy",
            "user_input": "legacy",
            "status": "INTERRUPTED",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    ]
    (settings.data_path / "tasks.json").write_text(json.dumps(legacy), encoding="utf-8")

    task = Persistence(settings).load_task("old-1")

    assert task is not None
    assert task.workspace == normalize_workspace(settings.artifacts_path)


def test_send_message_to_a_deleted_workspace_returns_409(tmp_path):
    settings = make_settings(tmp_path, checkpoint_enabled=True)
    workspace = tmp_path / "gone"
    workspace.mkdir()
    mock = MockLLMClient(final_answer="ok")
    tm = make_manager(settings, mock)
    client = TestClient(app, base_url="http://localhost")
    client.__enter__()
    getattr(client.app, "state").task_manager = tm
    try:
        task_id = tm.create_task("gone", "do something", workspace=str(workspace))
        _wait_manager(tm, task_id)
        shutil.rmtree(workspace)

        r = client.post(f"/api/tasks/{task_id}/messages", json={"input": "again"})
        assert r.status_code == 409
        assert "workspace" in r.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_continuation_writes_into_the_original_workspace(tmp_path):
    settings = make_settings(tmp_path, checkpoint_enabled=True)
    workspace = tmp_path / "continued"
    workspace.mkdir()
    mock = MockLLMClient(final_answer="ok")
    tm = make_manager(settings, mock)
    client = TestClient(app, base_url="http://localhost")
    client.__enter__()
    getattr(client.app, "state").task_manager = tm
    try:
        task_id = tm.create_task("continued", "first round", workspace=str(workspace))
        _wait_manager(tm, task_id)
        assert not (workspace / "continued.txt").exists()

        mock.tool_calls = [
            {"id": "w", "name": "write", "arguments": {"path": "continued.txt", "content": "x"}}
        ]
        mock._turn = 0
        r = client.post(f"/api/tasks/{task_id}/messages", json={"input": "write it now"})
        assert r.status_code == 200, r.text
        _wait_manager(tm, task_id)

        assert (workspace / "continued.txt").read_text(encoding="utf-8") == "x"
    finally:
        client.__exit__(None, None, None)


def test_first_run_does_not_recreate_a_deleted_workspace(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    workspace = tmp_path / "first-run-gone"
    workspace.mkdir()
    mock = MockLLMClient(
        tool_calls=[
            {"id": "w", "name": "write", "arguments": {"path": "probe.txt", "content": "x"}}
        ],
        final_answer="done",
    )
    tm = make_manager(settings, mock)
    monkeypatch.setattr(tm, "_start_run", lambda task_id: None)
    task_id = tm.create_task("gone", "write a file", workspace=str(workspace))
    shutil.rmtree(workspace)

    tm.run(task_id)

    task = tm.get_task(task_id)
    assert task is not None
    assert task.status.value == "FAILED"
    assert "workspace" in (task.error or "")
    assert not workspace.exists(), "the first run recreated the deleted task workspace"


def test_injection_is_built_from_the_runtime_workspace(tmp_path):
    """The runtime seats the workspace before AgentRuntime builds the block."""
    settings = make_settings(tmp_path, context_inject_enabled=True)
    workspace = tmp_path / "inject-run"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("SENTINEL-RUNTIME", encoding="utf-8")

    class _RecordingMock(MockLLMClient):
        def __init__(self):
            super().__init__(final_answer="ok")
            self.seen: list = []

        def complete(self, messages, tools=None, **kwargs):
            self.seen.append(messages)
            return super().complete(messages, tools=tools, **kwargs)

    mock = _RecordingMock()
    tm = make_manager(settings, mock)
    with TestClient(app, base_url="http://localhost") as client:
        getattr(client.app, "state").task_manager = tm
        r = client.post("/api/tasks", json={"input": "hi", "workspace": str(workspace)})
        _wait_parked(client, r.json()["data"]["task_id"])

    assert "SENTINEL-RUNTIME" in json.dumps(mock.seen[0], ensure_ascii=False)


def test_malformed_workspace_does_not_quarantine_history(tmp_path):
    settings = make_settings(tmp_path)
    settings.data_path.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "id": "good",
            "title": "good",
            "user_input": "good",
            "status": "INTERRUPTED",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
        {
            "id": "bad",
            "title": "bad",
            "user_input": "bad",
            "workspace": "bad\u0000path",
            "status": "INTERRUPTED",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
    ]
    (settings.data_path / "tasks.json").write_text(json.dumps(records), encoding="utf-8")

    persistence = Persistence(settings)

    assert persistence.load_task("good") is not None
    assert persistence.load_task("bad") is None
    assert not list(settings.data_path.glob("tasks.json.corrupt-*"))


def test_subagent_inherits_the_parent_workspace(tmp_path):
    settings = make_settings(tmp_path)
    workspace = tmp_path / "parent-ws"
    workspace.mkdir()
    mock = MockLLMClient(
        tool_calls=[{"id": "w", "name": "write", "arguments": {"path": "sub.txt", "content": "from sub"}}],
        final_answer="done",
    )
    tm = make_manager(settings, mock)
    from backend.core.agent.subagent import SubAgentExecutor, SubTaskSpec

    executor = SubAgentExecutor(tm, settings)
    tokens = set_runtime_context("parent", workspace)
    try:
        result = executor.run_subtask(
            SubTaskSpec(subtask_id="sub-1", name="s", instruction="write sub.txt", parent_task_id="parent")
        )
    finally:
        reset_runtime_context(tokens)

    assert result.status == "completed", result.error
    assert (workspace / "sub.txt").read_text(encoding="utf-8").strip() == "from sub"
