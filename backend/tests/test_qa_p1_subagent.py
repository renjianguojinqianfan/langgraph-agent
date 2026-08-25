"""QA independent boundary tests — P1 item 2 (sub-agent collaboration).

Focuses on contracts the engineer's tests touch only lightly:

* ``spawn_subagent`` tool must be ``requires_confirm=True`` (the spawn gate);
* parent ``messages`` contain only the folded summary — never subtask
  internal assistant/tool messages (isolation, arch §2.4);
* subtask internal events live on the subtask's own channel, not the parent's;
* a crashing subtask publishes ``subtask_failed`` on the parent channel and
  does not crash the parent;
* ``subagent_enabled=False`` -> no split, zero regression.
"""

from __future__ import annotations

import json

from backend.core.agent.graph import build_graph
from backend.core.agent.nodes import AgentRuntime
from backend.core.agent.subagent import SubAgentExecutor
from backend.core.llm.client import LLMResponse, MockLLMClient
from backend.tests.conftest import make_manager, make_settings
from backend.tests.test_graph import _run_until_done


class _ResearchMock(MockLLMClient):
    """Parent final-answers immediately; research subtask calls file_io once;
    writing subtask final-answers directly."""

    def __init__(self):
        super().__init__(plan=["调研", "写作"], final_answer="父任务完成")

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
                            "id": "qasub",
                            "name": "file_io",
                            "arguments": {
                                "action": "write",
                                "path": "qa_sub_notes.txt",
                                "content": "QA sub notes",
                            },
                        }
                    ],
                )
            return LLMResponse(content="研究完成")
        if "撰写" in user:
            return LLMResponse(content="报告已完成")
        return LLMResponse(content="父任务完成")


def _main_state(user_input):
    return {
        "task_id": "qa-main",
        "messages": [{"role": "user", "content": user_input}],
        "plan": [],
        "steps": [],
        "artifacts": [],
        "status": "RUNNING",
        "stop_requested": False,
        "pending_confirm": {},
        "step_index": 0,
        "final_answer": "",
        "error": "",
        "_last_action": "",
        "_current_tool_calls": [],
        "_confirmed_ids": [],
        "_rejected_ids": [],
        "_needs_confirm": False,
        "risk_report": [],
        "_risk_blocked": False,
        "subtasks": [],
        "_is_subtask": False,
    }


def test_spawn_subagent_tool_requires_confirm(tmp_path):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(plan=["p"], final_answer="d"))
    tool = next(t for t in tm._tools if t.name == "spawn_subagent")
    assert tool.requires_confirm is True


def test_parent_messages_contain_only_folded_summary(tmp_path):
    """The parent state messages must not contain subtask internal
    assistant/tool messages — only the folded summary assistant turn."""
    settings = make_settings(tmp_path)
    tm = make_manager(settings, _ResearchMock())
    runtime = AgentRuntime(
        task_id="qa-main",
        task_manager=tm,
        llm=tm._llm,
        tools=tm._tools,
        tool_schemas=tm._tool_schemas,
        aux_llm=tm._aux_llm,
        subagent_executor=tm._subagent,
        confirm_enabled=True,
    )
    graph = build_graph(runtime, mode="main")
    final = graph.invoke(_main_state("调研 RAG 最新进展并写报告"))

    assert final["status"] == "COMPLETED"
    assert len(final["subtasks"]) == 2

    roles = [m.get("role") for m in final["messages"]]
    assert "tool" not in roles, "subtask internal tool messages leaked into parent messages"
    assistant_turns = [
        m.get("content", "") for m in final["messages"] if m.get("role") == "assistant"
    ]
    folded = [c for c in assistant_turns if "子任务" in c]
    assert folded, "no folded subtask summary in parent messages"
    # The folded block is a bounded summary, not the subtask's full internal log.
    assert all(len(c) <= 1000 for c in folded)


def test_subtask_internal_events_stay_on_subtask_channel(tmp_path, event_bus):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, _ResearchMock(), event_bus=event_bus)
    task_id = tm.create_task(title="qa-iso", user_input="调研 RAG 最新进展并写报告")
    task = _run_until_done(tm, task_id)
    assert task.status.value == "COMPLETED"
    assert len(task.subtasks) == 2

    sub_id = task.subtasks[0].subtask_id
    parent_types = {e["type"] for e in event_bus.replay(task_id)}
    sub_events = event_bus.replay(sub_id)
    sub_types = {e["type"] for e in sub_events}

    # The subtask's own channel carries its internal plan/tool events...
    assert "plan_update" in sub_types
    assert "tool_call" in sub_types
    # ...and the parent channel only saw the summary events.
    assert "subtask_start" in parent_types
    assert "subtask_result" in parent_types
    assert "tool_call" not in parent_types or all(
        e["data"].get("tool_name") != "file_io"
        for e in event_bus.replay(task_id)
        if e["type"] == "tool_call"
    )


def test_subtask_failure_publishes_failed_event_and_keeps_parent_alive(
    tmp_path, event_bus, monkeypatch
):
    settings = make_settings(tmp_path)
    tm = make_manager(
        settings,
        MockLLMClient(plan=["调研"], final_answer="done"),
        event_bus=event_bus,
    )
    ex = SubAgentExecutor(tm, settings)
    published = []

    def _boom(spec, publish=None):
        raise RuntimeError("subtask worker crashed")

    monkeypatch.setattr(ex, "_exec_one", _boom)
    results = ex.run_plan_with_subtasks(
        "parent", "调研 X 并写报告", [], publish=lambda t, d: published.append((t, d))
    )
    assert len(results) == 2
    assert all(r.status == "failed" for r in results)
    failed = [d for t, d in published if t == "subtask_failed"]
    assert len(failed) == 2
    assert all("error" in d for d in failed)
    # The parent channel sees the summary events without crashing.
    types = {t for t, _ in published}
    assert "subtask_start" in types
    assert "subtask_failed" in types


def test_subagent_disabled_does_not_split(tmp_path, event_bus):
    settings = make_settings(tmp_path, subagent_enabled=False)
    tm = make_manager(settings, _ResearchMock(), event_bus=event_bus)
    task_id = tm.create_task(title="qa-off", user_input="调研 RAG 最新进展并写报告")
    task = _run_until_done(tm, task_id)
    assert task.status.value == "COMPLETED"
    assert task.subtasks == []
    types = {e["type"] for e in event_bus.replay(task_id)}
    assert "subtask_start" not in types
    assert "subtask_result" not in types
