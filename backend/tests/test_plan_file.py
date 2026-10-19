"""C6: the plan is one Markdown file in the task workspace (ADR 0010 decision 4).

Covers the #153 regressions: an edited file is what the next round sends, the
plan message survives compression and appears exactly once, a deleted file
means no plan, and a model write to the file is picked up next call.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.llm.client import MockLLMClient
from backend.main import app
from backend.services import plan_file
from backend.tests.conftest import make_manager, make_settings


class _RecordingLLM(MockLLMClient):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.transcripts: list[list[dict]] = []

    def complete(self, messages, tools=None, **kwargs):
        self.transcripts.append([dict(m) for m in messages])
        return super().complete(messages, tools=tools, **kwargs)


def _wait(tm, task_id, timeout=20.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = tm.get_task(task_id)
        if task and task.status.value in ("INTERRUPTED", "FAILED"):
            return task
        time.sleep(0.05)
    raise AssertionError("task never parked")


def _plan_messages(transcript):
    return [
        m["content"]
        for m in transcript
        if m.get("role") == "user" and plan_file.is_plan_message(m.get("content"))
    ]


def test_plan_mode_writes_the_file_and_git_ignores_it(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is required")
    settings = make_settings(tmp_path)
    workspace = settings.artifacts_path
    workspace.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True, capture_output=True)
    tm = make_manager(settings, MockLLMClient(plan=["first step"], final_answer="ok"))

    task_id = tm.create_task("p", "plan it", mode="plan", workspace=str(workspace))
    task = _wait(tm, task_id)

    path = plan_file.plan_path(workspace, task_id)
    assert path.is_file()
    assert "first step" in path.read_text(encoding="utf-8")
    assert (workspace / ".qianfan" / ".gitignore").read_text(encoding="utf-8") == "*\n"
    assert task.plan_version == 1
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=workspace, check=True, capture_output=True, text=True
    ).stdout
    assert ".qianfan" not in status


def test_a_non_plan_reply_does_not_create_the_file(tmp_path):
    class _QuestionLLM:
        def complete(self, messages, tools=None, **kwargs):
            from backend.core.llm.client import LLMResponse

            return LLMResponse(content="Which folder should I use?")

    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    task_id = tm.create_task("p", "hi", workspace=str(settings.artifacts_path))
    _wait(tm, task_id)

    from backend.core.agent.nodes import AgentRuntime

    runtime = AgentRuntime(
        task_id,
        tm,
        llm=_QuestionLLM(),
        tools=[],
        tool_schemas=[],
    )
    state = {
        "task_id": task_id,
        "mode": "plan",
        "messages": [{"role": "user", "content": "hi"}],
        "plan_text": "",
        "plan_version": 0,
        "plan_hash": "",
        "steps": [],
        "status": "RUNNING",
        "stop_requested": False,
        "step_index": 0,
        "round_steps": 0,
        "final_answer": "",
        "error": "",
        "_last_action": "",
        "_current_tool_calls": [],
        "subtasks": [],
        "_is_subtask": False,
    }
    runtime.agent(state)

    assert state["final_answer"] == "Which folder should I use?"
    assert not plan_file.plan_path(Path(tm.get_task(task_id).workspace), task_id).exists()


def test_edited_file_is_what_the_next_round_sends(tmp_path):
    settings = make_settings(tmp_path, checkpoint_enabled=True)
    llm = _RecordingLLM(final_answer="done")
    tm = make_manager(settings, llm)
    task_id = tm.create_task("p", "hi", workspace=str(settings.artifacts_path))
    task = _wait(tm, task_id)
    workspace = Path(task.workspace)

    plan_file.write_plan(workspace, task_id, "# Plan\n\n1. edited externally")
    llm.transcripts.clear()
    tm.send_message(task_id, "go")
    _wait(tm, task_id)

    texts = _plan_messages(llm.transcripts[0])
    assert len(texts) == 1
    assert "edited externally" in texts[0]
    assert tm.get_task(task_id).plan_version == 1


def test_exactly_one_plan_message_even_after_compression(tmp_path):
    settings = make_settings(
        tmp_path,
        checkpoint_enabled=True,
        context_token_budget=1,
        context_keep_recent=1,
    )
    llm = _RecordingLLM(final_answer="done")
    tm = make_manager(settings, llm)
    task_id = tm.create_task("p", "hi", workspace=str(settings.artifacts_path))
    task = _wait(tm, task_id)
    workspace = Path(task.workspace)
    plan_file.write_plan(workspace, task_id, "# Plan\n\n1. keep me")

    llm.transcripts.clear()
    tm.send_message(task_id, "go")
    _wait(tm, task_id)

    assert llm.transcripts
    for transcript in llm.transcripts:
        texts = _plan_messages(transcript)
        assert len(texts) == 1
        assert "keep me" in texts[0]


def test_deleted_file_means_no_plan(tmp_path):
    settings = make_settings(tmp_path, checkpoint_enabled=True)
    llm = _RecordingLLM(final_answer="done")
    tm = make_manager(settings, llm)
    task_id = tm.create_task("p", "hi", workspace=str(settings.artifacts_path))
    task = _wait(tm, task_id)
    workspace = Path(task.workspace)
    plan_file.write_plan(workspace, task_id, "# Plan\n\n1. first")

    tm.send_message(task_id, "go")
    _wait(tm, task_id)
    plan_file.plan_path(workspace, task_id).unlink()

    llm.transcripts.clear()
    tm.send_message(task_id, "again")
    _wait(tm, task_id)
    for transcript in llm.transcripts:
        assert _plan_messages(transcript) == []


def test_model_write_to_the_plan_file_is_used_next_call(tmp_path):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    task_id = tm.create_task("p", "hi", workspace=str(settings.artifacts_path))
    _wait(tm, task_id)
    workspace = Path(tm.get_task(task_id).workspace)
    rel = f".qianfan/plans/{task_id}.md"
    llm = _RecordingLLM(
        tool_calls=[
            {
                "id": "w1",
                "name": "write",
                "arguments": {"path": rel, "content": "# Plan\n\n1. model edit"},
            }
        ],
        final_answer="done",
    )
    # Replace the manager's client for this direct runtime below.
    from backend.core.agent.nodes import AgentRuntime

    runtime = AgentRuntime(
        task_id,
        tm,
        llm=llm,
        tools=tm._tools,
        tool_schemas=tm._tool_schemas,
    )
    state = {
        "task_id": task_id,
        "mode": "build",
        "messages": [{"role": "user", "content": "write the plan file"}],
        "plan_text": "",
        "plan_version": 0,
        "plan_hash": "",
        "steps": [],
        "status": "RUNNING",
        "stop_requested": False,
        "step_index": 0,
        "round_steps": 0,
        "final_answer": "",
        "error": "",
        "_last_action": "",
        "_current_tool_calls": [],
        "subtasks": [],
        "_is_subtask": False,
    }
    runtime.agent(state)  # tool round
    runtime.tool_node(state)  # execute the write
    runtime.agent(state)  # next call reads the file

    assert llm.transcripts
    assert "model edit" in _plan_messages(llm.transcripts[-1])[0]
    assert (workspace / ".qianfan" / ".gitignore").read_text(encoding="utf-8") == "*\n"


def test_rest_plan_get_and_put(tmp_path):
    settings = make_settings(tmp_path, checkpoint_enabled=True)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    task_id = tm.create_task("p", "hi", workspace=str(settings.artifacts_path))
    _wait(tm, task_id)

    client = TestClient(app, base_url="http://localhost")
    client.__enter__()
    getattr(client.app, "state").task_manager = tm
    try:
        empty = client.get(f"/api/tasks/{task_id}/plan").json()["data"]
        assert empty == {"text": "", "version": 0}
        saved = client.put(f"/api/tasks/{task_id}/plan", json={"text": "# Plan\n\n1. api"})
        assert saved.status_code == 200
        assert saved.json()["data"]["version"] == 1
        loaded = client.get(f"/api/tasks/{task_id}/plan").json()["data"]
        assert loaded["text"] == "# Plan\n\n1. api"
    finally:
        client.__exit__(None, None, None)
