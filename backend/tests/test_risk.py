"""P1 item 1 — planning-phase risk scan tests (fully offline).

Covers keyword hits / misses, the optional semantic path, the confirm-policy
end-to-end flow (high-risk tool calls require human confirmation before
execution), and the pause policy (plan-level blocking confirmation).
"""

from __future__ import annotations

import json
import threading
import time

from backend.config import Settings
from backend.core.agent.risk import (
    DANGER_KEYWORDS,
    RiskScanner,
    scan_keywords,
    scan_semantic,
    scan_tool_output,
)
from backend.core.llm.client import LLMResponse, MockAuxLLMClient, MockLLMClient
from backend.tests.conftest import make_manager
from backend.tests.test_graph import _run_until_done


def _wait_event(events, event_type, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for ev in events:
            if ev["type"] == event_type:
                return ev
        time.sleep(0.01)
    return None


def _auto_confirm_in_background(tm, event_bus, task_id, approved, tool_call_id=None, timeout=10):
    """Auto-confirm any pending confirmation (specific id or first found)."""

    def _watcher():
        deadline = time.time() + timeout
        while time.time() < deadline:
            for ev in event_bus.replay(task_id):
                if ev["type"] == "human_confirm_required":
                    tid = ev["data"]["tool_call_id"]
                    if tool_call_id is None or tid == tool_call_id:
                        tm.confirm(task_id, tid, approved)
                        return
            time.sleep(0.01)

    t = threading.Thread(target=_watcher, daemon=True)
    t.start()
    return t


# ── keyword scan ──
def test_scan_keywords_hits_high_for_rm_rf():
    plan = [{"description": "删除服务器上所有文件 (rm -rf /data)"}, {"description": "写报告"}]
    items = scan_keywords(plan)
    assert items[0]["level"] == "high"
    assert "rm -rf" in items[0]["matched_keywords"] or "删除" in items[0]["matched_keywords"]
    assert items[0]["action"] == "confirm"
    assert items[0]["suggestion"]
    assert items[1]["level"] == "none"


def test_scan_keywords_case_insensitive():
    plan = ["Run DROP TABLE users", "Search the web"]
    items = scan_keywords(plan)
    assert items[0]["level"] == "high"
    assert "drop table" in items[0]["matched_keywords"]
    assert items[1]["level"] == "none"


def test_scan_keywords_no_hit():
    plan = ["搜索资料", "写一份报告文件"]
    items = scan_keywords(plan)
    assert all(it["level"] == "none" for it in items)
    assert all(it["matched_keywords"] == [] for it in items)


def test_scan_keywords_custom_override():
    items = scan_keywords([{"description": "清理所有缓存"}], extra_keywords=["清理"])
    assert items[0]["level"] == "high"
    assert "清理" in items[0]["matched_keywords"]


def test_danger_keywords_covers_five_categories():
    assert set(DANGER_KEYWORDS.keys()) >= {"delete", "database", "financial", "privacy", "system"}


# ── semantic scan (aux) ──
def test_scan_semantic_none_when_no_llm():
    step = {"description": "清理服务器上所有数据"}
    assert scan_semantic(None, step) is None


def test_scan_semantic_uses_aux():
    aux = MockAuxLLMClient(role_responses={"risk": "high: 该操作会清空服务器数据"})
    step = {"description": "清理服务器上所有数据"}
    res = scan_semantic(aux, step)
    assert res is not None
    assert res["level"] == "high"
    assert aux.call_count == 1


def test_risk_scanner_semantic_lifts_benign_step():
    settings = Settings(
        risk_semantic_enabled=True,
        kb_dir="data/kb",
        data_dir="data",
    )
    aux = MockAuxLLMClient(role_responses={"risk": "high: 清理服务器数据属于高危操作"})
    scanner = RiskScanner(settings, aux_llm=aux, semantic_enabled=True)
    plan = [{"description": "清理服务器上所有数据"}]
    items = scanner.scan(plan)
    assert items[0]["level"] == "high"
    assert aux.call_count == 1  # only the non-keyword step consumed aux


# ── tool output skeleton ──
def test_scan_tool_output_is_skeleton():
    assert scan_tool_output({"data": "secret=abc"}) == []


# ── end-to-end confirm policy ──
def test_risk_confirm_flow_blocks_until_approved(settings, event_bus):
    mock = MockLLMClient(
        plan=["删除所有临时文件并清理"],
        tool_calls=[
            {
                "id": "r1",
                "name": "file_io",
                "arguments": {"action": "write", "path": "out.txt", "content": "x"},
            }
        ],
        final_answer="done after confirmation.",
    )
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="risk", user_input="清理临时文件")
    _auto_confirm_in_background(tm, event_bus, task_id, approved=True)
    task = _run_until_done(tm, task_id)

    assert task.status.value == "COMPLETED"
    events = event_bus.replay(task_id)
    types = {e["type"] for e in events}
    assert "risk_report" in types
    assert "risk_found" in types
    assert "human_confirm_required" in types
    # The tool executed only after approval.
    tool_results = [e["data"] for e in events if e["type"] == "tool_result"]
    assert any(r.get("tool_name") == "file_io" and r.get("status") == "success" for r in tool_results)
    # risk_report persisted to the task.
    assert any(it.level == "high" for it in task.risk_report)


def test_risk_confirm_flow_skips_on_rejection(settings, event_bus):
    mock = MockLLMClient(
        plan=["删除临时文件"],
        tool_calls=[
            {
                "id": "r2",
                "name": "file_io",
                "arguments": {"action": "write", "path": "out2.txt", "content": "x"},
            }
        ],
        final_answer="skipped.",
    )
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="risk", user_input="清理文件")
    _auto_confirm_in_background(tm, event_bus, task_id, approved=False)
    task = _run_until_done(tm, task_id)

    assert task.status.value == "COMPLETED"
    events = event_bus.replay(task_id)
    tool_results = [e["data"] for e in events if e["type"] == "tool_result"]
    assert any(r.get("status") == "skipped" for r in tool_results)


def test_risk_disabled_is_zero_regression(settings, event_bus):
    settings.risk_scan_enabled = False
    mock = MockLLMClient(
        plan=["写一个文件"],
        tool_calls=[
            {
                "id": "r3",
                "name": "file_io",
                "arguments": {"action": "write", "path": "n.txt", "content": "ok"},
            }
        ],
        final_answer="done.",
    )
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="no-risk", user_input="write a file")
    task = _run_until_done(tm, task_id)
    assert task.status.value == "COMPLETED"
    assert "risk_report" not in {e["type"] for e in event_bus.replay(task_id)}


# ── pause policy ──
class _PauseMock(MockLLMClient):
    """Returns a high-risk plan on the first planner call, then a benign one
    so pause-mode blocking happens exactly once per round."""

    def __init__(self, tool_calls, final_answer="done."):
        super().__init__(plan=["删除临时文件"], tool_calls=tool_calls, final_answer=final_answer)
        self._planner_calls = 0

    def complete(self, messages, tools=None, **kwargs):
        if not tools:
            self._planner_calls += 1
            if self._planner_calls >= 2:
                return LLMResponse(content=json.dumps(["写总结"]))
            return LLMResponse(content=json.dumps(self.plan))
        return super().complete(messages, tools, **kwargs)


def test_risk_pause_policy_approves_at_plan_level(settings, event_bus):
    settings.risk_policy = "pause"
    mock = _PauseMock(
        tool_calls=[
            {
                "id": "r4",
                "name": "file_io",
                "arguments": {"action": "write", "path": "p.txt", "content": "x"},
            }
        ],
    )
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="pause", user_input="清理")
    _auto_confirm_in_background(tm, event_bus, task_id, approved=True, tool_call_id="risk_plan")
    task = _run_until_done(tm, task_id)

    assert task.status.value == "COMPLETED"
    events = event_bus.replay(task_id)
    confirm_events = [e for e in events if e["type"] == "human_confirm_required"]
    assert any(e["data"]["tool_call_id"] == "risk_plan" for e in confirm_events)
    # After plan approval the tool call runs without per-call confirmation.
    tool_results = [e["data"] for e in events if e["type"] == "tool_result"]
    assert any(r.get("status") == "success" for r in tool_results)


def test_risk_pause_policy_rejects_and_skips(settings, event_bus):
    settings.risk_policy = "pause"
    mock = _PauseMock(
        tool_calls=[
            {
                "id": "r5",
                "name": "file_io",
                "arguments": {"action": "write", "path": "q.txt", "content": "x"},
            }
        ],
    )
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="pause", user_input="清理")
    _auto_confirm_in_background(tm, event_bus, task_id, approved=False, tool_call_id="risk_plan")
    task = _run_until_done(tm, task_id)
    assert task.status.value == "COMPLETED"
