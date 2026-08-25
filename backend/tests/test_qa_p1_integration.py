"""QA independent P1 end-to-end integration scenarios (fully offline).

Two realistic user journeys combining multiple P1 capabilities:

* **Journey A — risk gate + KB memory**: submit a task whose plan contains a
  danger keyword -> risk scan flags it -> human confirm -> tool executes ->
  artifact auto-indexed into the KB -> task completes with a persisted
  ``risk_report``.
* **Journey B — sub-agent split + summary + artifact hand-back**: a
  "research + report" task is split into two isolated subtasks, their folded
  summaries land in the parent context, the subtask-produced file is
  registered as a parent artifact *and* indexed into the KB.
"""

from __future__ import annotations

import json
import threading
import time

from backend.core.llm.client import LLMResponse, MockLLMClient
from backend.tests.conftest import make_manager, make_settings
from backend.tests.test_graph import _run_until_done


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


# ── Journey B: sub-agent split + summary + artifact hand-back ──
class _ResearchMock(MockLLMClient):
    def __init__(self):
        super().__init__(plan=["调研", "写作"], final_answer="主任务完成")

    def complete(self, messages, tools=None, **kwargs):
        user = " ".join(
            str(m.get("content") or "") for m in messages if m.get("role") == "user"
        )
        if not tools:
            return LLMResponse(content=json.dumps(self.plan))
        if "检索" in user:
            if self._executor_turn == 0:
                self._executor_turn += 1
                return LLMResponse(
                    content="",
                    tool_calls=[
                        {
                            "id": "qaB1",
                            "name": "file_io",
                            "arguments": {
                                "action": "write",
                                "path": "qa_report_notes.md",
                                "content": "QA integration notes for RAG",
                            },
                        }
                    ],
                )
            return LLMResponse(content="研究完成")
        if "撰写" in user:
            return LLMResponse(content="报告已完成")
        return LLMResponse(content="主任务完成")


def test_journey_subagent_split_summary_artifact_kb(tmp_path, event_bus):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, _ResearchMock(), event_bus=event_bus)
    task_id = tm.create_task(title="qa-journey-b", user_input="调研 RAG 最新进展并写报告")
    task = _run_until_done(tm, task_id)

    assert task.status.value == "COMPLETED"
    # Two isolated subtasks with folded results (item 2)
    assert len(task.subtasks) == 2
    assert all(st.status == "completed" for st in task.subtasks)
    assert all(st.summary for st in task.subtasks)
    # Parent channel got the summary events (item 2 observability)
    types = {e["type"] for e in event_bus.replay(task_id)}
    assert {"subtask_start", "subtask_result"} <= types
    # Subtask-produced file handed back to the parent artifacts (item 2)
    assert any("qa_report_notes.md" in a.filename for a in task.artifacts)
    # ...and auto-indexed into the KB for the next session (item 3)
    hits = tm._kb.retrieve("integration notes", top_k=5)
    assert len(hits) >= 1
