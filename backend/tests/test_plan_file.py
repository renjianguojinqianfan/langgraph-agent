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


def test_a_non_plan_reply_keeps_the_previous_plan_note(tmp_path):
    class _QuestionLLM:
        def complete(self, messages, tools=None, **kwargs):
            from backend.core.llm.client import LLMResponse

            return LLMResponse(content="Which folder should I use?")

    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    task_id = tm.create_task("p", "hi", workspace=str(settings.artifacts_path))
    _wait(tm, task_id)
    workspace = Path(tm.get_task(task_id).workspace)
    text = "# Plan\n\n1. existing"
    plan_file.write_plan(workspace, task_id, text)

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
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "计划已更新到第 1 版。"},
        ],
        "plan_text": text,
        "plan_version": 1,
        "plan_hash": plan_file.plan_hash(text),
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

    notes = [
        m
        for m in state["messages"]
        if m.get("role") == "assistant"
        and str(m.get("content") or "").startswith("计划已更新到")
    ]
    assert len(notes) == 1
    assert notes[0]["content"] == "计划已更新到第 1 版。"
    assert state["messages"][-1]["content"] == "Which folder should I use?"


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
    conversation = tm.get_task(task_id).conversation
    notes = [
        t
        for t in conversation
        if t.role == "assistant" and t.content.startswith("计划已更新到")
    ]
    assert len(notes) == 1
    assert "第 1 版" in notes[0].content


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


def test_compression_replaces_an_old_plan_message_with_the_latest(tmp_path):
    settings = make_settings(tmp_path, context_token_budget=1, context_keep_recent=1)
    llm = _RecordingLLM(final_answer="done")
    tm = make_manager(settings, llm)
    task_id = tm.create_task("p", "hi", workspace=str(settings.artifacts_path))
    task = _wait(tm, task_id)
    workspace = Path(task.workspace)
    plan_file.write_plan(workspace, task_id, "# Plan\n\n1. latest")

    from backend.core.agent.nodes import AgentRuntime

    runtime = AgentRuntime(task_id, tm, llm=llm, tools=[], tool_schemas=[])
    state = {
        "task_id": task_id,
        "mode": "build",
        "messages": [
            {"role": "user", "content": plan_file.render_plan_message("# Plan\n\n1. stale")},
            *[{"role": "user", "content": "x" * 2000} for _ in range(3)],
            {"role": "assistant", "content": "y" * 2000},
        ],
        "plan_text": "",
        "plan_version": 1,
        "plan_hash": plan_file.plan_hash("# Plan\n\n1. stale"),
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
    built = runtime._build_messages(state, "system")
    plans = [m for m in built if plan_file.is_plan_message(m.get("content"))]
    assert len(plans) == 1
    assert "latest" in plans[0]["content"]
    assert "stale" not in plans[0]["content"]


def test_compression_refreshes_a_mid_round_plan(tmp_path):
    settings = make_settings(tmp_path, context_token_budget=1, context_keep_recent=1)
    llm = _RecordingLLM(final_answer="done")
    tm = make_manager(settings, llm)
    task_id = tm.create_task("p", "hi", workspace=str(settings.artifacts_path))
    task = _wait(tm, task_id)
    workspace = Path(task.workspace)
    first = "# Plan\n\n1. first"
    plan_file.write_plan(workspace, task_id, first)
    plan_file.write_plan(workspace, task_id, "# Plan\n\n1. second")

    from backend.core.agent.nodes import AgentRuntime

    runtime = AgentRuntime(task_id, tm, llm=llm, tools=[], tool_schemas=[])
    state = {
        "task_id": task_id,
        "mode": "build",
        "messages": [
            {"role": "user", "content": plan_file.render_plan_message(first)},
            *[{"role": "user", "content": "x" * 2000} for _ in range(3)],
            {"role": "assistant", "content": "y" * 2000},
        ],
        "plan_text": first,
        "plan_version": 1,
        "plan_hash": plan_file.plan_hash(first),
        "steps": [],
        "status": "RUNNING",
        "stop_requested": False,
        "step_index": 0,
        "round_steps": 5,  # mid-round: only compression may refresh the file
        "final_answer": "",
        "error": "",
        "_last_action": "",
        "_current_tool_calls": [],
        "subtasks": [],
        "_is_subtask": False,
    }
    built = runtime._build_messages(state, "system")

    plans = [m for m in built if plan_file.is_plan_message(m.get("content"))]
    assert len(plans) == 1
    assert "second" in plans[0]["content"]
    assert state["plan_version"] == 2
    notes = [
        m
        for m in built
        if m.get("role") == "assistant"
        and str(m.get("content") or "").startswith("计划已更新到")
    ]
    assert len(notes) == 1
    assert "第 2 版" in notes[0]["content"]


def test_a_user_message_that_merely_starts_with_the_prefix_is_kept(tmp_path):
    messages = [{"role": "user", "content": "[qianfan-plan] I typed this"}]
    plan_file.sync_plan_message(messages, "# Plan\n\n1. real")
    contents = [m["content"] for m in messages]
    assert "[qianfan-plan] I typed this" in contents
    assert len([c for c in contents if plan_file.is_plan_message(c)]) == 1


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


def test_model_write_to_the_plan_file_is_used_next_round(tmp_path):
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
    runtime.agent(state)  # same round: the edit must NOT be visible yet

    assert _plan_messages(llm.transcripts[-1]) == []
    assert not any(
        m.get("role") == "assistant"
        and str(m.get("content") or "").startswith("计划已更新到")
        for m in state["messages"]
    )

    state["round_steps"] = 0  # a new round starts (manager resets the budget)
    runtime.agent(state)  # next round: the file is read and wins

    assert "model edit" in _plan_messages(llm.transcripts[-1])[0]
    notes = [
        m
        for m in state["messages"]
        if m.get("role") == "assistant"
        and str(m.get("content") or "").startswith("计划已更新到")
    ]
    assert len(notes) == 1
    assert "第 1 版" in notes[0]["content"]
    assert (workspace / ".qianfan" / ".gitignore").read_text(encoding="utf-8") == "*\n"


def test_put_plan_requires_text(tmp_path):
    settings = make_settings(tmp_path, checkpoint_enabled=True)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    task_id = tm.create_task("p", "hi", workspace=str(settings.artifacts_path))
    task = _wait(tm, task_id)
    workspace = Path(task.workspace)

    client = TestClient(app, base_url="http://localhost")
    client.__enter__()
    getattr(client.app, "state").task_manager = tm
    try:
        r = client.put(f"/api/tasks/{task_id}/plan", json={})
        assert r.status_code == 422
        assert not plan_file.plan_path(workspace, task_id).exists()
    finally:
        client.__exit__(None, None, None)


def test_build_mode_does_not_create_a_plan_file(tmp_path):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(final_answer="done"))
    task_id = tm.create_task("p", "hi", workspace=str(settings.artifacts_path))
    task = _wait(tm, task_id)

    assert task.status.value == "INTERRUPTED"
    assert task.plan_version == 0
    assert not plan_file.plan_path(Path(task.workspace), task_id).exists()


def test_non_utf8_plan_is_a_readable_conflict(tmp_path):
    settings = make_settings(tmp_path, checkpoint_enabled=True)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    task_id = tm.create_task("p", "hi", workspace=str(settings.artifacts_path))
    task = _wait(tm, task_id)
    path = plan_file.plan_path(Path(task.workspace), task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes("# 计划\n".encode("gbk"))

    client = TestClient(app, base_url="http://localhost")
    client.__enter__()
    getattr(client.app, "state").task_manager = tm
    try:
        got = client.get(f"/api/tasks/{task_id}/plan")
        assert got.status_code == 409
        assert "UTF-8" in got.json()["detail"]

        sent = client.post(f"/api/tasks/{task_id}/messages", json={"input": "go"})
        assert sent.status_code == 409
        assert "UTF-8" in sent.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_non_utf8_plan_fails_the_run(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    monkeypatch.setattr(tm, "_start_run", lambda task_id: None)
    task_id = tm.create_task("p", "hi", workspace=str(workspace))
    path = plan_file.plan_path(workspace, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes("# 计划\n".encode("gbk"))

    tm.run(task_id)

    task = tm.get_task(task_id)
    assert task is not None
    assert task.status.value == "FAILED"
    assert "UTF-8" in (task.error or "")


def test_old_plan_never_reaches_the_summarizer(tmp_path):
    settings = make_settings(
        tmp_path,
        context_token_budget=1,
        context_keep_recent=1,
        context_compress_strategy="summarize",
    )
    llm = _RecordingLLM(final_answer="summary")
    tm = make_manager(settings, llm)
    task_id = tm.create_task("p", "hi", workspace=str(settings.artifacts_path))
    _wait(tm, task_id)
    plan_file.write_plan(Path(settings.artifacts_path), task_id, "# Plan\n\n1. latest")

    from backend.core.agent.nodes import AgentRuntime

    runtime = AgentRuntime(task_id, tm, llm=llm, tools=[], tool_schemas=[])
    state = {
        "task_id": task_id,
        "mode": "build",
        "messages": [
            {"role": "user", "content": plan_file.render_plan_message("# Plan\n\n1. stale")},
            *[{"role": "user", "content": "x" * 2000} for _ in range(3)],
            {"role": "assistant", "content": "y" * 2000},
        ],
        "plan_text": "",
        "plan_version": 1,
        "plan_hash": plan_file.plan_hash("# Plan\n\n1. stale"),
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
    built = runtime._build_messages(state, "system")

    assert llm.transcripts, "the summarizer never ran"
    assert all("stale" not in str(m.get("content")) for m in llm.transcripts[0])
    assert "stale" not in str(built)
    plans = [m for m in built if plan_file.is_plan_message(m.get("content"))]
    assert len(plans) == 1
    assert "latest" in plans[0]["content"]


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
