"""QA independent P1 end-to-end integration scenarios (fully offline).

Two realistic user journeys combining multiple P1 capabilities:

* **Journey A — risk gate + KB memory**: submit a task whose plan contains a
  danger keyword -> risk scan flags it -> human confirm -> tool executes ->
  artifact auto-indexed into the KB -> task completes with a persisted
  ``risk_report``.
* **Journey B — sub-agent delegation + summary + artifact hand-back**: the
  parent asks for an isolated subtask, a human approves the spawn, the subtask
  writes its notes inside the sandbox, and the file is registered as a parent
  artifact *and* indexed into the KB (issue #56: delegation is an approved tool
  call, never a keyword-triggered split).
* **Journey B′ — the same journey with the gate refused**: nothing runs, nothing
  is written, nothing is indexed.
"""

from __future__ import annotations

import json
import threading
import time

from backend.core.llm.client import LLMResponse, MockLLMClient
from backend.tests.conftest import make_manager, make_settings
from backend.tests.test_graph import _auto_confirm_in_background, _run_until_done


def _auto_confirm(tm, event_bus, task_id, approved, timeout=10):
    def _watcher():
        deadline = time.time() + timeout
        while time.time() < deadline:
            for ev in event_bus.replay(task_id):
                if ev["type"] == "human_confirm_required":
                    tm.confirm(task_id, ev["data"]["tool_call_id"], approved)
                    return
            time.sleep(0.01)

    threading.Thread(target=_watcher, daemon=True).start()


# ── Journey A: risk gate + artifact auto-index ──
def test_journey_risk_confirm_execute_kb_index(tmp_path, event_bus):
    settings = make_settings(tmp_path)
    mock = MockLLMClient(
        plan=["删除服务器临时文件并清理"],
        tool_calls=[
            {
                "id": "qaA1",
                "name": "file_io",
                "arguments": {
                    "action": "write",
                    "path": "risk_report_artifact.txt",
                    "content": "风险任务产物内容：安全策略说明",
                },
            }
        ],
        final_answer="风险操作已确认并执行完毕。",
    )
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="qa-journey-a", user_input="删除服务器临时文件")
    _auto_confirm(tm, event_bus, task_id, approved=True)
    task = _run_until_done(tm, task_id)

    assert task.status.value == "COMPLETED"
    # risk_report persisted (item 1)
    assert any(it.level == "high" for it in task.risk_report)
    # human-confirm gate fired before execution (item 1)
    types = {e["type"] for e in event_bus.replay(task_id)}
    assert "human_confirm_required" in types
    # artifact registered and auto-indexed into the KB (item 3)
    assert any("risk_report_artifact.txt" in a.filename for a in task.artifacts)
    hits = tm._kb.retrieve("风险任务产物内容", top_k=5)
    assert len(hits) >= 1
    assert hits[0].path.endswith("risk_report_artifact.txt")


# ── Journey B: approved sub-agent delegation + summary + artifact hand-back ──
class _ResearchMock(MockLLMClient):
    """Parent delegates the research leg (on approval); the subtask writes notes.

    Issue #56 removed the keyword-triggered split, so this wording now runs the
    parent's own plan and the delegation is an ordinary, gated tool call. One
    client instance serves both graphs; the subtask turn is recognised by the
    marker in its instruction.
    """

    INSTRUCTION = "检索素材并记下要点"

    def __init__(self):
        super().__init__(plan=["调研", "写作"], final_answer="主任务完成")
        self.subtask_turns = 0

    def complete(self, messages, tools=None, **kwargs):
        user = " ".join(
            str(m.get("content") or "") for m in messages if m.get("role") == "user"
        )
        if not tools:
            return LLMResponse(content=json.dumps(self.plan))
        if self.INSTRUCTION in user:
            self.subtask_turns += 1
            if self.subtask_turns == 1:
                return LLMResponse(
                    content="",
                    tool_calls=[
                        {
                            "id": "qaB1",
                            "name": "write",
                            "arguments": {
                                "path": "qa_report_notes.md",
                                "content": "QA integration notes for RAG",
                            },
                        }
                    ],
                )
            return LLMResponse(content="研究完成")
        if self._executor_turn == 0:
            self._executor_turn += 1
            return LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "qaB0",
                        "name": "spawn_subagent",
                        "arguments": {
                            "name": "研究子任务",
                            "instruction": self.INSTRUCTION,
                        },
                    }
                ],
            )
        return LLMResponse(content=self.final_answer)


def _spawn_results(events):
    return [
        e["data"]
        for e in events
        if e["type"] == "tool_result" and e["data"]["tool_name"] == "spawn_subagent"
    ]


def test_journey_subagent_spawn_summary_artifact_kb(tmp_path, event_bus):
    settings = make_settings(tmp_path)
    mock = _ResearchMock()
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="qa-journey-b", user_input="调研 RAG 最新进展并写报告")
    _auto_confirm_in_background(tm, event_bus, task_id, approved=True)
    task = _run_until_done(tm, task_id)

    assert task.status.value == "COMPLETED"
    types = {e["type"] for e in event_bus.replay(task_id)}
    # The delegation was gated (item 2) and it actually ran.
    assert "human_confirm_required" in types
    assert mock.subtask_turns >= 2
    spawn = _spawn_results(event_bus.replay(task_id))
    assert spawn and spawn[0]["status"] == "success"
    # The folded summary rides back as the tool result, with the artifacts list.
    assert spawn[0]["output"]["status"] == "completed"
    assert spawn[0]["output"]["summary"]
    # The deleted split entry leaves no event vocabulary behind (#56).
    assert types.isdisjoint({"subtask_start", "subtask_result", "subtask_failed"})
    assert task.subtasks == []
    # Subtask-produced file handed back to the parent artifacts (item 2)...
    assert any("qa_report_notes.md" in a.filename for a in task.artifacts)
    # ...while its own tool call stayed on the subtask channel.
    sub_events = event_bus.replay(spawn[0]["output"]["subtask_id"])
    assert any(
        e["type"] == "tool_call" and e["data"]["tool_name"] == "write" for e in sub_events
    )
    assert not [
        e
        for e in event_bus.replay(task_id)
        if e["type"] in ("tool_call", "tool_result") and e["data"]["tool_name"] == "write"
    ]
    # ...and auto-indexed into the KB for the next session (item 3).
    hits = tm._kb.retrieve("integration notes", top_k=5)
    assert len(hits) >= 1
    assert any(h.path.endswith("qa_report_notes.md") for h in hits)


def test_journey_refused_gate_spawns_nothing(tmp_path, event_bus):
    """拒绝派生：子任务零轮次，沙箱与 KB 里什么都不留下。"""
    settings = make_settings(tmp_path)
    mock = _ResearchMock()
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="qa-journey-b-no", user_input="调研 RAG 最新进展并写报告")
    _auto_confirm_in_background(tm, event_bus, task_id, approved=False)
    task = _run_until_done(tm, task_id)

    assert task.status.value == "COMPLETED"
    assert mock.subtask_turns == 0
    spawn = _spawn_results(event_bus.replay(task_id))
    assert spawn and spawn[0]["status"] == "skipped"
    assert task.subtasks == []
    assert not (settings.artifacts_path / "qa_report_notes.md").exists()
    assert tm._kb.retrieve("integration notes", top_k=5) == []
