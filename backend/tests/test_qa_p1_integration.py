"""QA independent P1 end-to-end integration scenarios (fully offline).

One realistic user journey combining P1 capabilities:

* **Sub-agent delegation + summary + artifact hand-back**: the parent asks for
  an isolated subtask, the subtask writes its notes inside the sandbox, and the
  file is registered as a parent artifact *and* indexed into the KB (issue #56:
  delegation is a tool call, never a keyword-triggered split).

Journey A (the planning-phase risk scan + the human confirm gate it fed) and
Journey B′ (the gate's refusal path) were removed with the gate in #138.
"""

from __future__ import annotations

import json

from backend.core.llm.client import LLMResponse, MockLLMClient
from backend.tests.conftest import make_manager, make_settings
from backend.tests.test_graph import _run_until_done


# ── Journey B: sub-agent delegation + summary + artifact hand-back ──
class _ResearchMock(MockLLMClient):
    """Parent delegates the research leg; the subtask writes notes.

    Issue #56 removed the keyword-triggered split, so this wording now runs the
    parent's own plan and the delegation is an ordinary tool call. One client
    instance serves both graphs; the subtask turn is recognised by the marker in
    its instruction.
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
        if self._turn == 0:
            self._turn += 1
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
    task = _run_until_done(tm, task_id)

    assert task.status.value == "INTERRUPTED"
    types = {e["type"] for e in event_bus.replay(task_id)}
    # The delegation actually ran.
    assert mock.subtask_turns >= 2
    spawn = _spawn_results(event_bus.replay(task_id))
    assert spawn and spawn[0]["status"] == "success"
    # The folded summary rides back as the tool result, with the artifacts list.
    assert spawn[0]["output"]["status"] == "completed"
    assert spawn[0]["output"]["summary"]
    # #70 裁定 A 重裁：汇总事件回到父频道。
    assert {"subtask_start", "subtask_result"} <= types
    assert "subtask_failed" not in types
    # #70 裁定 A 重裁：一次真实派生回登一条 completed 子任务。
    assert len(task.subtasks) == 1
    assert task.subtasks[0].status == "completed"
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
