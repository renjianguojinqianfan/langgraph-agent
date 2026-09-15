"""P0-B completion-verification tests (Issue #25).

Unit level: drive :meth:`AgentRuntime.reflect` directly with crafted states and
a fake ``tm`` exposing ``_active_states`` (the artifact registry) — fully
deterministic, no graph, no LLM, no network. Plus one integration test through
``make_manager`` for end-to-end ``Task.verification`` population, and the
headless result-key contract (review 漏洞2).
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from backend.core.agent.nodes import AgentRuntime
from backend.core.llm.client import MockLLMClient
from backend.services.event_bus import EventBus
from backend.tests.conftest import make_manager, make_settings


def _rt(tmp_path: Path, *, verify_enabled: bool = True, verify_max_retries: int = 2,
        artifacts: Optional[List[Dict[str, Any]]] = None):
    """Build an AgentRuntime over a fake tm whose _active_states holds artifacts."""
    settings = make_settings(
        tmp_path, verify_enabled=verify_enabled, verify_max_retries=verify_max_retries
    )
    bus = EventBus()
    events: List[Dict[str, Any]] = []
    bus.subscribe("t1", lambda e: events.append(e))
    tm = SimpleNamespace(
        settings=settings,
        event_bus=bus,
        add_artifact=lambda *a, **k: None,
        _active_states={"t1": {"artifacts": list(artifacts or [])}},
    )
    rt = AgentRuntime("t1", tm, llm=SimpleNamespace(), tools=[], tool_schemas=[])
    return rt, events


def _state(**kw: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "_last_action": "final_answer",
        "final_answer": "done",
        "steps": [],
        "messages": [],
    }
    base.update(kw)
    return base


def _art(path: Path) -> Dict[str, Any]:
    return {"path": str(path), "filename": path.name}


def _step(*statuses: str) -> Dict[str, Any]:
    return {"tool_calls": [{"status": s} for s in statuses]}


# ── S1: artifact integrity ───────────────────────────────────────────────────
def test_pass_when_artifact_present_and_nonempty(tmp_path: Path) -> None:
    good = tmp_path / "good.txt"
    good.write_text("content", encoding="utf-8")
    rt, events = _rt(tmp_path, artifacts=[_art(good)])
    out = rt.reflect(_state())
    assert out["_verification"]["passed"] is True
    assert out["_last_action"] == "final_answer"  # proceeds to finish
    assert any(e["type"] == "verification" for e in events)


def test_fail_on_empty_artifact_loops_back(tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    rt, _ = _rt(tmp_path, artifacts=[_art(empty)])
    out = rt.reflect(_state())
    assert out["_verification"]["passed"] is False
    assert out["_last_action"] == "verify_failed"  # loops back to planner
    assert out["_verify_attempts"] == 1
    assert any("empty.txt" in f for f in out["_verification"]["failures"])
    # Q10: feedback message injected carrying the concrete failure
    assert any(
        m.get("role") == "system" and "empty.txt" in m.get("content", "")
        for m in out["messages"]
    )


def test_fail_on_missing_artifact(tmp_path: Path) -> None:
    rt, _ = _rt(tmp_path, artifacts=[_art(tmp_path / "gone.txt")])
    out = rt.reflect(_state())
    assert out["_verification"]["passed"] is False
    assert out["_last_action"] == "verify_failed"


# ── S2: all-attempts-failed gate (triple-narrowed) ───────────────────────────
def test_s2_fails_when_all_tools_failed_and_no_product(tmp_path: Path) -> None:
    rt, _ = _rt(tmp_path, artifacts=[])
    out = rt.reflect(_state(steps=[_step("failed", "failed")]))
    assert out["_verification"]["passed"] is False
    assert out["_last_action"] == "verify_failed"


def test_s2_narrowing_passes_when_a_tool_succeeded(tmp_path: Path) -> None:
    # Pure Q&A: one successful search + one flaky failure + zero artifacts.
    rt, _ = _rt(tmp_path, artifacts=[])
    out = rt.reflect(_state(steps=[_step("success", "failed")]))
    assert out["_verification"]["passed"] is True
    assert out["_last_action"] == "final_answer"


# ── Q9: no-op pass ───────────────────────────────────────────────────────────
def test_noop_pass_no_artifacts_no_failures(tmp_path: Path) -> None:
    rt, _ = _rt(tmp_path, artifacts=[])
    out = rt.reflect(_state(steps=[_step("success")]))
    assert out["_verification"]["passed"] is True
    assert out.get("_verify_attempts", 0) == 0


# ── error-path guard (review 漏洞1) ──────────────────────────────────────────
def test_error_path_skips_verification(tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    rt, events = _rt(tmp_path, artifacts=[_art(empty)])
    out = rt.reflect(_state(error="Executor LLM error: boom"))
    # would have failed on the empty artifact, but the error guard skips verify
    assert "_verification" not in out
    assert out["_last_action"] == "final_answer"  # finish judges FAILED
    assert not any(e["type"] == "verification" for e in events)


# ── Q5: disabled = zero regression ───────────────────────────────────────────
def test_disabled_is_zero_regression(tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    rt, events = _rt(tmp_path, verify_enabled=False, artifacts=[_art(empty)])
    out = rt.reflect(_state())
    assert "_verification" not in out
    assert out["_last_action"] == "final_answer"
    assert not any(e["type"] == "verification" for e in events)


# ── Q3/Q4: bounded retries then degrade (never FAILED) ───────────────────────
def test_degrade_after_max_retries(tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    rt, _ = _rt(tmp_path, verify_max_retries=2, artifacts=[_art(empty)])
    out = rt.reflect(_state(_verify_attempts=2))  # budget already exhausted
    assert out["_verification"]["passed"] is False
    assert out["_verification"]["degraded"] is True
    assert out["_last_action"] == "final_answer"  # finish -> COMPLETED (degraded)


# ── Q12: integration — Task.verification populated end-to-end ────────────────
def test_task_verification_populated_end_to_end(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, risk_scan_enabled=False, subagent_enabled=False)
    tm = make_manager(settings, MockLLMClient(final_answer="all done"), event_bus=EventBus())
    tid = tm.create_task(title="qa", user_input="just answer")
    deadline = time.time() + 15
    while time.time() < deadline:
        t = tm.get_task(tid)
        if t and t.status.value in ("COMPLETED", "FAILED", "INTERRUPTED"):
            break
        time.sleep(0.05)
    task = tm.get_task(tid)
    assert task is not None and task.status.value == "COMPLETED"
    # pure Q&A (no artifacts, no failed tools) -> verification no-op PASS
    assert task.verification is not None
    assert task.verification["passed"] is True


# ── review 漏洞2: headless result carries verification ───────────────────────
def test_headless_result_keys_include_verification() -> None:
    from backend.headless import _RESULT_KEYS

    assert "verification" in _RESULT_KEYS
