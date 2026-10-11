"""Artifact registration: source and dedupe (issue #72).

``产物 = 本任务（含子任务）写出的文件``. Two contracts, both on the
:class:`~backend.services.task_manager.TaskManager` / graph seam:

* only a tool declaring ``registers_artifact`` is a source of products, so a
  ``read`` of a file the task already wrote registers nothing;
* :meth:`TaskManager.add_artifact` is deduplicated per task by **resolved** path,
  so a re-write of the same file lands as one record, one ``artifact_created``
  event and one knowledge-base ingest call.

Issue #76 adds the subtask half of the same contract: a subtask runs the same
``tool_node``, but it never has an ``_active_states`` entry, so the registration
the tool node used to make under the subtask's own id landed in no ledger at all
— its whole cost was a phantom event on the subtask channel plus a redundant KB
ingest. One subtask write must now mean one record (parent ledger), one event
(parent channel) and one ingest, with the subtask channel registering nothing.

The KB ingest count is asserted because the issue's acceptance list requires the
event and ingest counts to track the registration count — that is the contract,
not an implementation detail.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from backend.core.agent.subagent import SubTaskSpec
from backend.core.llm.client import LLMResponse, MockLLMClient
from backend.core.tools.mcp_tool import McpTool
from backend.core.tools.openapi_tool import OpenAPITool
from backend.core.tools.registry import build_tools
from backend.services.event_bus import EventBus
from backend.tests.conftest import make_manager, make_settings
from backend.tests.test_checkpointer import _LogCollector
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


def test_registration_of_one_path_is_atomic_across_threads(tmp_path, event_bus, monkeypatch):
    """#72 review F1: two threads registering the same path for one task still
    land exactly one record and one event.

    The interleaving is forced rather than hoped for: the first registration
    parks inside ``register_artifact`` waiting for a second thread to join it,
    which can only happen if the lookup and the append are NOT one atomic step.
    With the lock in place the second thread cannot be in there at all, so the
    barrier breaks and the test costs ~1.5s instead of milliseconds.
    """
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(plan=["unused"], tool_calls=[]), event_bus=event_bus)
    task_id = "t_concurrent"
    tm._active_states[task_id] = {"artifacts": []}

    target = settings.artifacts_path / "shared.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("body", encoding="utf-8")

    gate = threading.Barrier(2)
    original = tm.persistence.register_artifact

    def _parking_register(path: Path) -> Any:
        try:
            gate.wait(timeout=1.5)
        except threading.BrokenBarrierError:
            pass
        return original(path)

    monkeypatch.setattr(tm.persistence, "register_artifact", _parking_register)

    threads = [
        threading.Thread(target=tm.add_artifact, args=(task_id, target)) for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert len(tm._active_states[task_id]["artifacts"]) == 1
    events = [e for e in event_bus.replay(task_id) if e["type"] == "artifact_created"]
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


def test_subtask_write_registers_once_on_the_parent_channel(
    tmp_path, event_bus, monkeypatch
):
    """#76 A: a subtask's write is registered by the hand-back under the PARENT
    id only — the tool node no longer registers it under the subtask's own id.

    A subtask has no ``_active_states`` entry, so the registration the tool node
    used to make there landed in no ledger at all: its whole cost was one
    phantom ``artifact_created`` on the subtask channel plus one redundant KB
    ingest (the record itself was minted and dropped). One write must now mean
    one record on the parent ledger, one event on the parent channel, one KB
    ingest — and nothing at all on the subtask channel.

    Driven through ``run_subtask`` because that is exactly the entry
    ``spawn_subagent`` uses (``SpawnSubagentTool.run`` hands it the spec), and
    the parent ledger is hand-seated the same way
    ``test_add_artifact_dedupes_by_resolved_path`` seats one: the subject is the
    registration route, not a parent run.
    """
    tm, settings, ingest, _ = _manager(
        tmp_path,
        [_tool_call("s1", "write", {"path": "notes.txt", "content": "sub notes"})],
        event_bus,
        monkeypatch,
    )
    parent_id = "t-parent-live"
    tm._active_states[parent_id] = {"artifacts": []}

    res = tm._subagent.run_subtask(
        SubTaskSpec(
            subtask_id="sub:writes",
            name="writer",
            instruction="把笔记写进 notes.txt",
            parent_task_id=parent_id,
        )
    )

    # Guard: the write really ran inside the subtask — a run that never wrote
    # must not be able to satisfy any of the counts below.
    written = settings.artifacts_path / "notes.txt"
    assert res.status == "completed"
    assert [Path(p).resolve() for p in res.artifacts] == [written.resolve()]
    assert written.read_text(encoding="utf-8") == "sub notes"

    # The contract: one record on the parent ledger, one event on the parent
    # channel, one KB ingest — and the subtask channel registers nothing.
    ledger = tm._active_states[parent_id]["artifacts"]
    assert [a["filename"] for a in ledger] == ["notes.txt"]
    parent_events = [e for e in event_bus.replay(parent_id) if e["type"] == "artifact_created"]
    assert len(parent_events) == 1
    sub_events = [e for e in event_bus.replay("sub:writes") if e["type"] == "artifact_created"]
    assert sub_events == []
    assert ingest == [str(written.resolve())]


#: Only a subtask ever sees this; the parent's own input must not contain it.
SUBTASK_MARKER = "笔记"


class _ParentThenSubtaskMock(MockLLMClient):
    """The parent final-answers on sight; the subtask writes one file first.

    The two sides are told apart by the user text each graph carries — the
    parent's ``user_input`` versus the subtask instruction — so a test observes
    that the write really came from the subtask instead of assuming it.
    """

    def __init__(self) -> None:
        super().__init__(plan=["produce"], final_answer="done")
        self.subtask_turns = 0

    def complete(self, messages, tools=None, **kwargs):
        user = " ".join(
            str(m.get("content") or "") for m in messages if m.get("role") == "user"
        )
        if not tools:
            return LLMResponse(content=json.dumps(self.plan))
        if SUBTASK_MARKER in user:
            self.subtask_turns += 1
            if self.subtask_turns == 1:
                return LLMResponse(
                    content="",
                    tool_calls=[
                        _tool_call("s1", "write", {"path": "notes.txt", "content": "sub notes"})
                    ],
                )
            return LLMResponse(content=self.final_answer)
        return LLMResponse(content=self.final_answer)


@pytest.fixture
def subagent_logs():
    """Collect ``agent.subagent`` records for the duration of one test.

    ``get_logger()`` sets ``propagate = False``, so pytest's caplog never sees
    them; the handler is attached to the target logger directly (same shape as
    ``test_checkpointer.tm_logs``).
    """
    collector = _LogCollector()
    logging.getLogger("agent.agent.subagent").addHandler(collector)
    try:
        yield collector
    finally:
        logging.getLogger("agent.agent.subagent").removeHandler(collector)


def test_late_hand_back_is_refused_loudly_not_dropped_silently(
    tmp_path, event_bus, subagent_logs
):
    """#76 C: a hand-back that arrives after the parent's run is over is refused
    and traced, not silently dropped.

    ``add_artifact`` keeps its ledger in ``_active_states``; ``run()``'s finally
    pops it, so a subtask finishing after its parent (parent stopped, subtask
    overran the timeout window — #71's window) used to mint a record that
    landed nowhere and fire one ``artifact_created`` that promised a product the
    task did not own. The refusal must be loud instead: no event, no record, and
    a warning naming the parent, the subtask and the path.

    Landing the record is the follow-up ticket's (#106, 裁 B) job; this ticket
    only turns the silent drop into an attributable one.

    The parent runs to completion for real (``_run_until_done``) so the ledger
    teardown under test is the production one, not a hand-emptied dict.
    """
    settings = make_settings(tmp_path)
    tm = make_manager(settings, _ParentThenSubtaskMock(), event_bus=event_bus)
    parent_id = tm.create_task(title="parent", user_input="父任务直接收尾")
    _run_until_done(tm, parent_id)

    res = tm._subagent.run_subtask(
        SubTaskSpec(
            subtask_id="sub:late",
            name="writer",
            instruction="把" + SUBTASK_MARKER + "写进 notes.txt",
            parent_task_id=parent_id,
        )
    )

    # Guard: the write really ran — the refusal below is about what happens to
    # the file, not about a subtask that did nothing.
    written = settings.artifacts_path / "notes.txt"
    assert res.status == "completed"
    assert [Path(p).resolve() for p in res.artifacts] == [written.resolve()]
    assert written.read_text(encoding="utf-8") == "sub notes"

    # The refusal: no record on the (reloaded) task, no event promising one.
    reloaded = tm.persistence.load_task(parent_id)
    assert reloaded is not None and reloaded.artifacts == []
    parent_events = [e for e in event_bus.replay(parent_id) if e["type"] == "artifact_created"]
    assert parent_events == []

    # The trace: a warning naming the parent task, the subtask and the path.
    traces = subagent_logs.messages(min_level=logging.WARNING)
    assert any(
        parent_id in m and "notes.txt" in m and "sub:late" in m for m in traces
    ), traces
