"""Artifact registration: source and dedupe (issue #72).

``产物 = 本任务（含子任务）写出的文件``. Two contracts, both on the
:class:`~backend.services.task_manager.TaskManager` / graph seam:

* only a tool declaring ``registers_artifact`` is a source of products, so a
  ``read`` of a file the task already wrote registers nothing;
* :meth:`TaskManager.add_artifact` is deduplicated per task by **resolved** path,
  so a re-write of the same file lands as one record, one ``artifact_created``
  event and one knowledge-base ingest call.

The KB ingest count is asserted because the issue's acceptance list requires the
event and ingest counts to track the registration count — that is the contract,
not an implementation detail.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from backend.core.llm.client import MockLLMClient
from backend.core.tools.mcp_tool import McpTool
from backend.core.tools.openapi_tool import OpenAPITool
from backend.core.tools.registry import build_tools
from backend.services.event_bus import EventBus
from backend.tests.conftest import make_manager, make_settings
from backend.tests.test_graph import _run_until_done


def _tool_call(call_id: str, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": call_id, "name": name, "arguments": arguments}


def _outcome(task: Any) -> List[Tuple[str, str]]:
    """``(tool_name, status)`` per tool call the run actually made — the guard
    that keeps a "one artifact" assertion from passing on a call that never ran.
    Attribute access because the persisted record is a ``ToolCallRecord`` model."""
    return [
        (tc.tool_name, tc.status) for step in task.steps for tc in (step.tool_calls or [])
    ]


def _manager(tmp_path, tool_calls: List[Dict[str, Any]], event_bus: EventBus, monkeypatch):
    """A manager over a scripted mock, plus a counting wrapper on the KB."""
    mock = MockLLMClient(
        plan=["produce the file"],
        tool_calls=tool_calls,
        final_answer="done",
    )
    settings = make_settings(tmp_path)
    tm = make_manager(settings, mock, event_bus=event_bus)
    ingest: List[str] = []
    original = tm._kb.add_document

    def _counting_add_document(path: Any) -> Any:
        ingest.append(str(Path(path).resolve()))
        return original(path)

    monkeypatch.setattr(tm._kb, "add_document", _counting_add_document)
    return tm, settings, ingest, mock


def test_write_then_read_back_registers_one_artifact(tmp_path, event_bus, monkeypatch):
    """The read is not a product: after write + read of the same file the task
    still owns exactly one artifact."""
    tm, settings, ingest, _ = _manager(
        tmp_path,
        [
            _tool_call("w1", "write", {"path": "report.md", "content": "body"}),
            _tool_call("r1", "read", {"path": "report.md"}),
        ],
        event_bus,
        monkeypatch,
    )
    task_id = tm.create_task(title="write and re-read", user_input="write then read")
    task = _run_until_done(tm, task_id)

    assert task is not None and task.status.value == "COMPLETED"
    assert [name for name, _ in _outcome(task)] == ["write", "read"]
    assert all(status == "success" for _, status in _outcome(task))
    assert [a.filename for a in task.artifacts] == ["report.md"]
    events = [e for e in event_bus.replay(task_id) if e["type"] == "artifact_created"]
    assert len(events) == 1
    assert ingest == [str((settings.artifacts_path / "report.md").resolve())]


def test_same_file_written_twice_registers_once(tmp_path, event_bus, monkeypatch):
    """A rewrite of the same path is one product, not two."""
    tm, settings, ingest, _ = _manager(
        tmp_path,
        [
            _tool_call("w1", "write", {"path": "report.md", "content": "first"}),
            _tool_call("w2", "write", {"path": "report.md", "content": "second"}),
        ],
        event_bus,
        monkeypatch,
    )
    task_id = tm.create_task(title="write twice", user_input="overwrite the file")
    task = _run_until_done(tm, task_id)

    assert task is not None and task.status.value == "COMPLETED"
    assert [name for name, _ in _outcome(task)] == ["write", "write"]
    assert all(status == "success" for _, status in _outcome(task))
    assert (settings.artifacts_path / "report.md").read_text(encoding="utf-8") == "second"
    assert [a.filename for a in task.artifacts] == ["report.md"]
    events = [e for e in event_bus.replay(task_id) if e["type"] == "artifact_created"]
    assert len(events) == 1
    assert ingest == [str((settings.artifacts_path / "report.md").resolve())]


def test_add_artifact_dedupes_by_resolved_path(tmp_path, event_bus):
    """Two spellings of the same file are one registration: the dedupe key is
    the resolved path, not the string the caller happened to pass.

    ``sub/../out.txt`` is the cross-platform half of that claim — the lexical
    collapse ``resolve()`` performs on every OS. What resolve adds on one
    platform only (Windows case folding, symlink / junction targets) is not
    asserted here, since a test that passes on one OS and fails on the other is
    worse than a narrower lock.

    Driven on a hand-seated active state rather than ``create_task``, because
    ``create_task`` starts a background run that owns (and at the end drops)
    that very slot — here the subject is ``add_artifact`` itself.
    """
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(plan=["unused"], tool_calls=[]), event_bus=event_bus)
    task_id = "t_dedupe"
    tm._active_states[task_id] = {"artifacts": []}

    target = settings.artifacts_path / "out.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("body", encoding="utf-8")
    spelled = target.parent / "sub" / ".." / "out.txt"

    first = tm.add_artifact(task_id, target)
    second = tm.add_artifact(task_id, spelled)

    events = [e for e in event_bus.replay(task_id) if e["type"] == "artifact_created"]
    assert second.id == first.id
    assert len(tm._active_states[task_id]["artifacts"]) == 1
    assert len(events) == 1


def test_only_the_sandbox_write_tools_are_product_sources(tmp_path):
    """The registration face is exactly ``write`` + ``edit``.

    Every other mounted tool — the read/ls/glob/grep siblings, code execution,
    HTTP, search, KB, skills, the sub-agent spawner — keeps the ``False``
    default, and so do the wrappers that are appended dynamically rather than
    registered (MCP, OpenAPI, plugins): a product is a file the task wrote, so
    a wrapper that wants its output registered has to say so on its class.

    Why the dynamic half is locked on the declaration rather than on a run:
    ``McpTool`` / ``OpenAPITool`` never put a top-level ``path`` in their result
    data, so a payload-driven test through them would pass with or without #72
    and prove nothing. The behavioural half — a tool whose result *does* carry
    ``path`` while declaring nothing — is locked in
    ``test_qa_nodes.test_read_like_result_on_a_real_file_registers_nothing``.
    """
    settings = make_settings(tmp_path)
    mounted = build_tools(settings)
    assert sorted(t.name for t in mounted if t.registers_artifact) == ["edit", "write"]

    assert McpTool.registers_artifact is False
    assert OpenAPITool.registers_artifact is False


def test_registration_is_per_task(tmp_path, event_bus, monkeypatch):
    """Dedupe is scoped to a task: two tasks touching the same file each own it,
    which is what lets a subtask's file be handed back to the parent."""
    tm, settings, _, mock = _manager(
        tmp_path,
        [_tool_call("w1", "write", {"path": "shared.txt", "content": "body"})],
        event_bus,
        monkeypatch,
    )
    first_id = tm.create_task(title="first", user_input="write shared.txt")
    first = _run_until_done(tm, first_id)
    mock.reset()
    second_id = tm.create_task(title="second", user_input="write shared.txt")
    second = _run_until_done(tm, second_id)

    assert first is not None and second is not None
    assert [a.filename for a in first.artifacts] == ["shared.txt"]
    assert [a.filename for a in second.artifacts] == ["shared.txt"]
    assert first.artifacts[0].id != second.artifacts[0].id
    assert first.artifacts[0].path == second.artifacts[0].path
