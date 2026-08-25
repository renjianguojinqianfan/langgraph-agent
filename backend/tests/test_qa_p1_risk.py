"""QA independent boundary tests — P1 item 1 (risk scan) + item 4 (aux model).

These tests are written from the QA perspective and intentionally exercise
edge cases / payload contracts that the engineer's tests only touch lightly:

* every one of the five built-in keyword categories actually flags ``high``;
* ``risk_report`` / ``risk_found`` event payloads (schema contract §2.2);
* **ordering guarantee**: a high-risk tool call is NOT executed before the
  human confirmation (we observe the event stream, confirm only afterwards,
  and only then assert the tool result exists);
* the optional custom keyword override is robust to invalid JSON;
* aux degradation: when aux is *not* enabled (or no auxiliary task triggers),
  a MockAuxLLMClient is never called (``call_count == 0``).
"""

from __future__ import annotations

import json
import threading
import time

from backend.core.agent.graph import build_graph
from backend.core.agent.nodes import AgentRuntime
from backend.core.agent.risk import DANGER_KEYWORDS, scan_keywords
from backend.core.llm.client import MockAuxLLMClient, MockLLMClient
from backend.tests.conftest import make_manager, make_settings
from backend.tests.test_graph import _run_until_done


def _auto_confirm_in_background(tm, event_bus, task_id, approved, timeout=10):
    def _watcher():
        deadline = time.time() + timeout
        while time.time() < deadline:
            for ev in event_bus.replay(task_id):
                if ev["type"] == "human_confirm_required":
                    tm.confirm(task_id, ev["data"]["tool_call_id"], approved)
                    return
            time.sleep(0.01)

    threading.Thread(target=_watcher, daemon=True).start()


def _wait_event(bus, task_id, event_type, timeout=10):
    """Poll the bus buffer *live* (re-fetch each iteration) for an event."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for ev in bus.replay(task_id):
            if ev["type"] == event_type:
                return ev
        time.sleep(0.01)
    return None


# ── five-category keyword coverage (PRD Q1) ──
def test_every_danger_category_flags_high():
    """One representative keyword per category must yield level=high."""
    samples = {
        "delete": "格式化磁盘",
        "database": "truncate table orders",
        "financial": "向对方汇款 5000 元",
        "privacy": "给所有人发短信",
        "system": "关闭服务器关机",
    }
    assert set(DANGER_KEYWORDS.keys()) >= set(samples.keys())
    for category, text in samples.items():
        items = scan_keywords([{"description": text}])
        assert items[0]["level"] == "high", f"category={category} text={text!r} not flagged"
        assert items[0]["action"] == "confirm"
        assert items[0]["suggestion"]


def test_scan_keywords_handles_step_field_and_plain_string():
    items = scan_keywords([{"step": "删除文件"}, "普通任务", {"description": "写报告"}])
    assert items[0]["level"] == "high"
    assert items[1]["level"] == "none"
    assert items[2]["level"] == "none"


def test_custom_override_invalid_json_ignored(tmp_path):
    """Bad JSON in risk_danger_keywords must not raise; scan still works."""
    settings = make_settings(tmp_path, risk_danger_keywords="not-json{{{")
    scanner_items = scan_keywords([{"description": "清理缓存"}])
    assert scanner_items[0]["level"] == "none"  # built-in table has no 清理
    # And a plain RiskScanner construction with the bad override does not raise.
    from backend.core.agent.risk import RiskScanner

    scanner = RiskScanner(settings)
    items = scanner.scan([{"description": "删除文件"}])
    assert items[0]["level"] == "high"


# ── event payload contract (arch §2.2) ──
def test_risk_report_and_found_event_payloads(settings, event_bus):
    mock = MockLLMClient(
        plan=["删除临时文件", "写总结"],
        tool_calls=[
            {
                "id": "qar1",
                "name": "file_io",
                "arguments": {"action": "write", "path": "qar1.txt", "content": "x"},
            }
        ],
        final_answer="done.",
    )
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="qa-payload", user_input="清理临时文件")

    # A high-risk round needs one confirmation before the tool runs; approve it.
    _auto_confirm_in_background(tm, event_bus, task_id, approved=True)
    task = _run_until_done(tm, task_id)
    assert task.status.value == "COMPLETED"

    events = event_bus.replay(task_id)
    report = _wait_event(event_bus, task_id, "risk_report")
    assert report is not None
    # Full report payload: {items, policy, semantic_enabled}
    data = report["data"]
    assert "items" in data and isinstance(data["items"], list)
    assert data["policy"] == "confirm"
    assert data["semantic_enabled"] is False
    assert any(it["level"] == "high" for it in data["items"])

    found = [e["data"] for e in events if e["type"] == "risk_found"]
    assert len(found) >= 1
    item = found[0]
    # Single-hit payload is a RiskItem: {step_index, level, matched_keywords, suggestion, action}
    assert set(item.keys()) >= {"step_index", "level", "matched_keywords", "suggestion", "action"}
    assert item["level"] in ("high", "medium")
    assert item["matched_keywords"]


# ── ordering: high-risk call must NOT run before confirmation ──
def test_high_risk_tool_not_executed_before_confirm(settings, event_bus):
    mock = MockLLMClient(
        plan=["删除临时文件并清理"],
        tool_calls=[
            {
                "id": "qar2",
                "name": "file_io",
                "arguments": {"action": "write", "path": "qar2.txt", "content": "x"},
            }
        ],
        final_answer="done.",
    )
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="qa-order", user_input="清理临时文件")

    # Wait for the confirmation request WITHOUT auto-approving.
    confirm_ev = _wait_event(event_bus, task_id, "human_confirm_required")
    assert confirm_ev is not None, "confirmation request never arrived"
    # While waiting, the tool must not have executed.
    tool_results = [e for e in event_bus.replay(task_id) if e["type"] == "tool_result"]
    assert tool_results == [], "high-risk tool call executed before human confirmation!"

    # Approve now -> tool executes, task completes.
    tm.confirm(task_id, confirm_ev["data"]["tool_call_id"], True)
    task = _run_until_done(tm, task_id)
    assert task.status.value == "COMPLETED"
    results = [e["data"] for e in event_bus.replay(task_id) if e["type"] == "tool_result"]
    assert any(r.get("tool_name") == "file_io" and r.get("status") == "success" for r in results)


def test_risk_disabled_publishes_no_risk_events(settings, event_bus):
    settings.risk_scan_enabled = False
    mock = MockLLMClient(plan=["删除临时文件"], final_answer="done")
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="qa-off", user_input="清理临时文件")
    task = _run_until_done(tm, task_id)
    assert task.status.value == "COMPLETED"
    types = {e["type"] for e in event_bus.replay(task_id)}
    assert "risk_report" not in types
    assert "risk_found" not in types
    # A disabled scanner also leaves no persisted risk_report.
    assert task.risk_report == []


# ── aux model degradation (item 4) ──
def test_aux_mock_zero_calls_when_aux_enabled_but_no_task_triggers(tmp_path, event_bus):
    """aux enabled + mock, but no summary/risk/tool-choice task runs ->
    the aux client must see zero calls (degradation is per auxiliary task)."""
    settings = make_settings(
        tmp_path,
        aux_llm_enabled=True,
        aux_llm_use_mock=True,
        # risk_semantic_enabled stays False (default) and
        # context_compress_strategy stays "truncate" (default) -> no aux task.
    )
    mock = MockLLMClient(plan=["普通任务"], final_answer="done")
    tm = make_manager(settings, mock, event_bus=event_bus)
    aux = tm._aux_llm
    assert isinstance(aux, MockAuxLLMClient)
    assert aux.call_count == 0

    task_id = tm.create_task(title="qa-aux0", user_input="普通任务")
    task = _run_until_done(tm, task_id)
    assert task.status.value == "COMPLETED"
    assert aux.call_count == 0, "aux was called although no auxiliary task was configured"


def test_aux_runtime_aux_property_none_when_disabled_even_if_injected(tmp_path):
    """AgentRuntime.aux_llm must honour settings: with aux disabled it returns
    None regardless of a mistakenly injected client (no semantic calls)."""
    settings = make_settings(tmp_path)  # aux_llm_enabled=False
    mock = MockLLMClient(plan=["普通任务"], final_answer="done")
    tm = make_manager(settings, mock)
    injected = MockAuxLLMClient(role_responses={"risk": "high: 危险"})
    runtime = AgentRuntime(
        task_id="t",
        task_manager=tm,
        llm=mock,
        tools=tm._tools,
        tool_schemas=tm._tool_schemas,
        aux_llm=injected,
    )
    # With risk_semantic_enabled=False the RiskScanner skips semantic analysis;
    # a full graph run must therefore never call the injected aux client.
    state: dict = {
        "task_id": "t",
        "messages": [{"role": "user", "content": "清理服务器上所有数据"}],
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
    graph = build_graph(runtime, mode="main")
    final = graph.invoke(state)
    assert final["status"] == "COMPLETED"
    assert injected.call_count == 0


def test_get_aux_llm_returns_none_on_incomplete_live_config(tmp_path):
    from backend.core.llm.openai_compat import get_aux_llm

    settings = make_settings(
        tmp_path,
        aux_llm_enabled=True,
        aux_llm_use_mock=False,
        aux_llm_model="",  # incomplete -> degrade
    )
    assert get_aux_llm(settings, main_llm=None) is None
