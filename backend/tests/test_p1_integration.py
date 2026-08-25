"""P1 integration tests — multiple P1 capabilities cooperating (offline).

These exercise the *combination* of the new nodes/events through the real
LangGraph loop with a scripted mock LLM: risk report events, subtask summary
events on the parent channel, knowledge-base auto-indexing, and the aux model
degradation path.
"""

from __future__ import annotations

import json

from backend.core.llm.client import LLMResponse, MockLLMClient
from backend.tests.conftest import make_manager, make_settings
from backend.tests.test_graph import _run_until_done


class _ResearchMock(MockLLMClient):
    """Parent final-answers; research subtask writes one file then ends."""

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
                            "id": "p1sub",
                            "name": "file_io",
                            "arguments": {
                                "action": "write",
                                "path": "p1_notes.txt",
                                "content": "P1 integration notes",
                            },
                        }
                    ],
                )
            return LLMResponse(content="研究完成")
        if "撰写" in user:
            return LLMResponse(content="写作完成")
        return LLMResponse(content="主任务完成")


def test_p1_events_in_main_channel(tmp_path, event_bus):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, _ResearchMock(), event_bus=event_bus)
    task_id = tm.create_task(title="p1", user_input="调研 RAG 最新进展并写报告")
    task = _run_until_done(tm, task_id)

    assert task.status.value == "COMPLETED"
    events = event_bus.replay(task_id)
    types = {e["type"] for e in events}
    # Risk scan ran (default enabled) and published its report.
    assert "risk_report" in types
    # Sub-agent summary events landed on the parent channel.
    assert "subtask_start" in types
    assert "subtask_result" in types
    # Parent artifacts include the subtask file via KB auto-index + artifact.
    assert any("p1_notes.txt" in a.filename for a in task.artifacts)


def test_p1_all_enabled_completes(tmp_path, event_bus):
    settings = make_settings(
        tmp_path,
        aux_llm_enabled=True,
        aux_llm_use_mock=True,
        risk_semantic_enabled=True,
        risk_policy="confirm",
        subagent_max_concurrency=2,
        kb_auto_index_artifacts=True,
        context_compress_strategy="truncate",
    )
    tm = make_manager(settings, _ResearchMock(), event_bus=event_bus)
    aux = tm._aux_llm
    assert aux is not None
    aux.role_responses["risk"] = "low: 常规调研任务"

    task_id = tm.create_task(title="all", user_input="调研 DeepAgent 架构并写报告")
    task = _run_until_done(tm, task_id)

    assert task.status.value == "COMPLETED"
    assert len(task.subtasks) == 2
    # KB auto-indexed the artifact produced by the research subtask.
    hits = tm._kb.retrieve("P1 integration notes", top_k=3)
    assert len(hits) >= 1


def test_p1_openapi_registration_does_not_break_startup(tmp_path, event_bus):
    """openapi_enabled with an invalid spec must warn and continue."""
    import logging

    settings = make_settings(
        tmp_path,
        openapi_enabled=True,
        openapi_spec_path=str(tmp_path / "missing_spec.yaml"),
    )
    # A missing file logs a warning but the manager still starts.
    tm = make_manager(settings, MockLLMClient(plan=["p"], final_answer="done"), event_bus=event_bus)
    assert tm is not None
    task_id = tm.create_task(title="t", user_input="hi")
    task = _run_until_done(tm, task_id)
    assert task.status.value == "COMPLETED"
