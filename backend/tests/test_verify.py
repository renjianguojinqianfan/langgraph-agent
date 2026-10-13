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

import pytest

from backend.api.schemas import Expectation, Task, TaskStatus
from backend.core.agent.nodes import AgentRuntime
from backend.core.llm.client import MockAuxLLMClient, MockLLMClient
from backend.core.tools.registry import build_tools
from backend.services.event_bus import EventBus
from backend.tests.conftest import make_manager, make_settings


def _rt(tmp_path: Path, *, verify_enabled: bool = True, verify_max_retries: int = 2,
        artifacts: Optional[List[Dict[str, Any]]] = None, aux_llm: Any = None,
        with_file_tools: bool = False):
    """Build an AgentRuntime over a fake tm whose _active_states holds artifacts.

    ``aux_llm`` is injected the way ``TaskManager`` injects it in production; left
    ``None`` the lazy property consults settings (aux disabled -> ``None``), which
    is exactly the degradation path the grader has to fail closed on.
    ``with_file_tools`` mounts the real file tools so the grader's authorship
    ledger has genuine ``registers_artifact`` producers to read.
    """
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
    tools = build_tools(settings) if with_file_tools else []
    rt = AgentRuntime("t1", tm, llm=SimpleNamespace(), tools=tools,
                      tool_schemas=[t.to_openai_schema() for t in tools], aux_llm=aux_llm)
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


def _call(tool_name: str, status: str) -> Dict[str, Any]:
    """One tool-call record carrying the name — expectation judgment needs it."""
    return {"tool_name": tool_name, "status": status}


def _exp(kind: str, target: str) -> Dict[str, Any]:
    return {"type": kind, "target": target}


def _round(*calls: Dict[str, Any]) -> Dict[str, Any]:
    return {"tool_calls": list(calls)}


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


# ── Q3/Q4: bounded retries, then UNVERIFIED (never FAILED) ───────────────────
# #46 重裁：原名 test_degrade_after_max_retries，钉的是旧决定「预算耗尽降级
# COMPLETED + degraded」。终态换成 UNVERIFIED（ADR 0005），degraded 布尔换成
# reason 枚举，并补上旧用例漏掉的那一半——只断 `_last_action` 而没有把 reflect
# 的输出喂给 finish，所以「声称完成但未验过」到底落成什么终态无人看管。
def test_unverified_after_max_retries(tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    rt, _ = _rt(tmp_path, verify_max_retries=2, artifacts=[_art(empty)])
    out = rt.reflect(_state(_verify_attempts=2))  # budget already exhausted
    assert out["_verification"]["passed"] is False
    assert out["_verification"]["reason"] == "retry_exhausted"
    assert "degraded" not in out["_verification"]
    assert out["_last_action"] == "final_answer"  # router still routes to finish
    assert rt.finish(out)["status"] == "UNVERIFIED"


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


# ── #46 第二步前的载荷形状：reason 取代 degraded ─────────────────────────────
def test_pass_payload_carries_empty_reason(tmp_path: Path) -> None:
    rt, _ = _rt(tmp_path, artifacts=[])
    out = rt.reflect(_state(steps=[_step("success")]))
    assert out["_verification"]["passed"] is True
    assert out["_verification"]["reason"] == ""
    assert out["_verification"]["failures"] == []


def test_loop_back_payload_marks_reason_pending(tmp_path: Path) -> None:
    """回环中的 reason 是 `pending`——`max_steps` 兜底认的就是这个未收口值。"""
    rt, _ = _rt(tmp_path, artifacts=[_art(tmp_path / "gone.txt")])
    out = rt.reflect(_state())
    assert out["_verification"]["reason"] == "pending"
    assert out["_last_action"] == "verify_failed"


# ── #46 第一步：expectation（调用方声明的确定性验收）────────────────────────
def test_expectation_artifact_missing_fails_even_with_successes(tmp_path: Path) -> None:
    """#46 的原始现象：一轮成功工具调用 + 声称完成，今天放行，声明过就该拦。"""
    rt, _ = _rt(tmp_path, artifacts=[])
    out = rt.reflect(
        _state(
            expectations=[_exp("artifact", "report.md")],
            steps=[_round(_call("read", "success"))],
        )
    )
    assert out["_verification"]["passed"] is False
    assert any("report.md" in f for f in out["_verification"]["failures"])
    assert out["_last_action"] == "verify_failed"


def test_expectation_artifact_present_and_nonempty_passes(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    (root / "report.md").write_text("hello", encoding="utf-8")
    rt, _ = _rt(tmp_path, artifacts=[])
    out = rt.reflect(
        _state(
            expectations=[_exp("artifact", "report.md")],
            steps=[_round(_call("write", "success"))],
        )
    )
    assert out["_verification"]["passed"] is True
    assert out["_last_action"] == "final_answer"


def test_expectation_artifact_empty_fails(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    (root / "report.md").write_text("", encoding="utf-8")
    rt, _ = _rt(tmp_path, artifacts=[])
    out = rt.reflect(_state(expectations=[_exp("artifact", "report.md")]))
    assert out["_verification"]["passed"] is False
    assert any("report.md" in f for f in out["_verification"]["failures"])


def test_expectation_resolves_against_sandbox_root_not_cwd(tmp_path: Path) -> None:
    """声明的是沙箱内相对路径：越出沙箱根的声明不合法，判失败而不是去读宿主文件。"""
    rt, _ = _rt(tmp_path, artifacts=[])
    out = rt.reflect(_state(expectations=[_exp("artifact", "../../Windows/win.ini")]))
    assert out["_verification"]["passed"] is False
    assert any("越出沙箱" in f for f in out["_verification"]["failures"])


def test_expectation_tool_success_named_passes(tmp_path: Path) -> None:
    rt, _ = _rt(tmp_path, artifacts=[])
    out = rt.reflect(
        _state(
            expectations=[_exp("tool_success", "write")],
            steps=[_round(_call("read", "success"), _call("write", "success"))],
        )
    )
    assert out["_verification"]["passed"] is True


def test_expectation_tool_success_named_missing_fails(tmp_path: Path) -> None:
    rt, _ = _rt(tmp_path, artifacts=[])
    out = rt.reflect(
        _state(
            expectations=[_exp("tool_success", "write")],
            steps=[_round(_call("read", "success"))],
        )
    )
    assert out["_verification"]["passed"] is False
    assert any("write" in f for f in out["_verification"]["failures"])


def test_expectation_tool_success_any_tool(tmp_path: Path) -> None:
    rt, _ = _rt(tmp_path, artifacts=[])
    ok = rt.reflect(
        _state(
            expectations=[_exp("tool_success", "*")],
            steps=[_round(_call("read", "success"))],
        )
    )
    assert ok["_verification"]["passed"] is True
    bad = rt.reflect(
        _state(expectations=[_exp("tool_success", "*")], steps=[_round()])
    )
    assert bad["_verification"]["passed"] is False


def test_expectation_artifact_directory_fails(tmp_path: Path) -> None:
    """目录不算交付物（ADR 0004 给产物定的判据，这里对声明复用）。

    只查「存在且非空」会放过它：Windows 上一个非空目录的 ``st_size`` 是 4096。
    """
    (tmp_path / "artifacts" / "docs").mkdir(parents=True)
    rt, _ = _rt(tmp_path, artifacts=[])
    out = rt.reflect(_state(expectations=[_exp("artifact", "docs")]))
    assert out["_verification"]["passed"] is False
    assert any("不是文件" in f for f in out["_verification"]["failures"])


def test_expectation_unjudgable_kind_fails_closed(tmp_path: Path) -> None:
    """判不动的声明算未通过：一条没人能验的合格线，不该被读成「过了」。"""
    rt, _ = _rt(tmp_path, artifacts=[])
    out = rt.reflect(_state(expectations=[_exp("sha256", "report.md")]))
    assert out["_verification"]["passed"] is False
    assert any("sha256" in f for f in out["_verification"]["failures"])


def test_expectation_target_is_mandatory_and_nonempty() -> None:
    """target 是声明的正文。放过空 target，tool_success 就悄悄变成「任一工具」——
    正是这份契约要消除的那种松弛；REST 侧因此收 422 而不是收下一条没人能验的条件。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Expectation(type="tool_success", target="")


def test_no_expectation_leaves_informational_task_alone(tmp_path: Path) -> None:
    """零声明 = 信息型任务：Q9 no-op PASS 原样保留（纯问答不被骚扰）。"""
    rt, _ = _rt(tmp_path, artifacts=[])
    out = rt.reflect(_state(steps=[_round(_call("read", "success"))], expectations=[]))
    assert out["_verification"]["passed"] is True


# ── #46 第一步：finish 的终态映射（崩了 / 没验过 / 被停 必须可分辨）──────────
def _rt_bare(tmp_path: Path):
    return _rt(tmp_path, artifacts=[])[0]


def test_finish_completed_when_verification_passed(tmp_path: Path) -> None:
    rt = _rt_bare(tmp_path)
    out = rt.finish(
        _state(_verification={"passed": True, "failures": [], "attempts": 1, "reason": ""})
    )
    assert out["status"] == "COMPLETED"


def test_finish_unverified_when_verification_failed(tmp_path: Path) -> None:
    rt = _rt_bare(tmp_path)
    out = rt.finish(
        _state(
            _verification={
                "passed": False,
                "failures": ["声明产物缺失：report.md"],
                "attempts": 2,
                "reason": "retry_exhausted",
            }
        )
    )
    assert out["status"] == "UNVERIFIED"


def test_finish_completed_when_verify_disabled(tmp_path: Path) -> None:
    """零回归：闸门关掉后 `_verification` 缺席，仍 COMPLETED（也覆盖史前 checkpoint）。"""
    rt = _rt_bare(tmp_path)
    out = rt.finish(_state())
    assert out["status"] == "COMPLETED"
    assert "_verification" not in out


def test_finish_failed_on_error_beats_verification(tmp_path: Path) -> None:
    rt = _rt_bare(tmp_path)
    out = rt.finish(
        _state(error="Executor LLM error: boom", _verification={"passed": False, "reason": ""})
    )
    assert out["status"] == "FAILED"


def test_finish_interrupted_beats_verification(tmp_path: Path) -> None:
    rt = _rt_bare(tmp_path)
    out = rt.finish(
        _state(stop_requested=True, _verification={"passed": False, "reason": ""})
    )
    assert out["status"] == "INTERRUPTED"


def test_finish_failed_when_nothing_claimed(tmp_path: Path) -> None:
    rt = _rt_bare(tmp_path)
    out = rt.finish(_state(final_answer="", _verification={"passed": False, "reason": ""}))
    assert out["status"] == "FAILED"


# ── #46 第一步：终态落库与事件（§7 事件面）───────────────────────────────────
def test_finalize_unverified_publishes_event_and_max_steps_reason(tmp_path: Path) -> None:
    """max_steps 抢占的回环（reason=pending）落库时兜底改写成 max_steps，
    且终态事件是 task_unverified——COMPLETED 不再被伪发。"""
    settings = make_settings(tmp_path, risk_scan_enabled=False, subagent_enabled=False)
    bus = EventBus()
    events: List[Dict[str, Any]] = []
    bus.subscribe("t1", lambda e: events.append(e))
    tm = make_manager(settings, MockLLMClient(final_answer="done"), event_bus=bus)
    task = Task(id="t1", title="t", user_input="u", status=TaskStatus.RUNNING)
    final: Dict[str, Any] = {
        "status": "UNVERIFIED",
        "final_answer": "done",
        "steps": [],
        "plan": [],
        "artifacts": [],
        "risk_report": [],
        "subtasks": [],
        "_verification": {
            "passed": False,
            "failures": ["声明产物缺失：report.md"],
            "attempts": 1,
            "reason": "pending",
        },
    }
    tm._finalize_terminal(task, final)
    assert task.status is TaskStatus.UNVERIFIED
    assert task.verification is not None
    assert task.verification["reason"] == "max_steps"
    assert "degraded" not in task.verification
    unverified = [e for e in events if e["type"] == "task_unverified"]
    assert unverified and unverified[0]["data"]["reason"] == "max_steps"
    assert not any(e["type"] == "task_completed" for e in events)


# ── 主 seam（集成）：提交带 expectation 的任务 → 终态 + verification ──────────
def test_expectation_unverified_end_to_end(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        risk_scan_enabled=False,
        subagent_enabled=False,
        verify_max_retries=0,
    )
    tm = make_manager(settings, MockLLMClient(final_answer="报告已经写好了"), event_bus=EventBus())
    tid = tm.create_task(
        title="deliver",
        user_input="把调研写成 report.md",
        expectations=[_exp("artifact", "report.md")],
    )
    deadline = time.time() + 15
    task = None
    while time.time() < deadline:
        task = tm.get_task(tid)
        if task and task.status.value in ("COMPLETED", "FAILED", "INTERRUPTED", "UNVERIFIED"):
            break
        time.sleep(0.05)
    assert task is not None
    assert task.status is TaskStatus.UNVERIFIED
    assert task.verification is not None
    assert task.verification["passed"] is False
    assert task.verification["reason"] == "retry_exhausted"
    assert any("report.md" in f for f in task.verification["failures"])


# ── #46 第二步：grader 三值判定（节点 seam：reflect）───────────────────────────
# 裁判的判决形状沿用 #46 试测的协议（data/grader-trial/run_grader.py 的
# SYSTEM_PROMPT）：{"result": satisfied|needs_revision|failed, "explanation", "criteria"}。
# 试测量出假阴性 0/24、0/23 用的就是这一份，换形状等于换掉了那组数字的依据。
_GRADER_ROLE = "grader"

_SATISFIED = '{"result": "satisfied", "explanation": "报告含结论小节", ' \
             '"criteria": [{"name": "结论小节", "passed": true}]}'
_NEEDS_REVISION = '{"result": "needs_revision", "explanation": "缺结论", ' \
                  '"criteria": [{"name": "结论小节", "passed": false, "gap": "报告没有结论小节"}]}'
_RUBRIC = "报告含结论小节，且引用至少两个来源"


class _RaisingAux(MockAuxLLMClient):
    """裁判自己出错（网络 / 鉴权 / 超时）——grader_error 那一档的注入点。"""

    def complete(self, messages, tools=None, **kwargs):
        raise RuntimeError("aux endpoint returned 500")


def _aux(verdict: str) -> MockAuxLLMClient:
    aux = MockAuxLLMClient()
    aux.role_responses[_GRADER_ROLE] = verdict
    return aux


def test_grader_satisfied_completes_the_task(tmp_path: Path) -> None:
    aux = _aux(_SATISFIED)
    rt, events = _rt(tmp_path, aux_llm=aux)
    out = rt.reflect(_state(rubric=_RUBRIC))
    assert out["_verification"]["passed"] is True
    assert out["_verification"]["reason"] == ""
    assert out["_last_action"] == "final_answer"  # -> finish -> COMPLETED
    assert rt.finish(out)["status"] == "COMPLETED"
    # 裁判真的被独立客户端跑过一次（票面用户故事 20：验证者独立于声明者）
    assert _GRADER_ROLE in aux.roles_called
    assert any(e["type"] == "verification" for e in events)


def test_grader_needs_revision_loops_back_with_the_gap(tmp_path: Path) -> None:
    """needs_revision 不是判决的终点：具体缺口要注回对话，模型才修得动。"""
    aux = _aux(_NEEDS_REVISION)
    rt, _ = _rt(tmp_path, aux_llm=aux)
    out = rt.reflect(_state(rubric=_RUBRIC))
    assert out["_verification"]["passed"] is False
    assert out["_verification"]["reason"] == "pending"  # 回环中，收口留给 finish
    assert out["_last_action"] == "verify_failed"  # 走第一步已有的 router 回 planner
    assert out["_verify_attempts"] == 1
    assert any("报告没有结论小节" in f for f in out["_verification"]["failures"])
    assert any(
        m.get("role") == "system" and "报告没有结论小节" in m.get("content", "")
        for m in out["messages"]
    )


def test_grader_needs_revision_shares_the_verify_budget(tmp_path: Path) -> None:
    """切片 A 的收口方式（票面 §4）：三值照判，needs_revision 走第一步已有的回环，
    预算与 retry_exhausted 共用——不是另起一本账。"""
    aux = _aux(_NEEDS_REVISION)
    rt, _ = _rt(tmp_path, verify_max_retries=2, aux_llm=aux)
    out = rt.reflect(_state(rubric=_RUBRIC, _verify_attempts=2))
    assert out["_verification"]["passed"] is False
    assert out["_verification"]["reason"] == "retry_exhausted"
    assert out["_last_action"] == "final_answer"  # 收口交给 finish，不再回环
    assert out["_verify_attempts"] == 2  # 预算耗尽这档不推进计数
    assert rt.finish(out)["status"] == "UNVERIFIED"


def test_grader_failed_verdict_is_grader_error_and_not_a_death_sentence(tmp_path: Path) -> None:
    """``failed`` 专指 rubric 本身无法评估：回环修不动调用方写的卷子，所以不回环、
    不烧预算，直接 UNVERIFIED(grader_error)——裁判的故障不许毁掉一个可能真完成的任务。"""
    aux = _aux('{"result": "failed", "explanation": "两条判据互相矛盾", "criteria": []}')
    rt, _ = _rt(tmp_path, verify_max_retries=2, aux_llm=aux)
    out = rt.reflect(_state(rubric=_RUBRIC))
    verdict = out["_verification"]
    assert verdict["passed"] is False
    assert verdict["reason"] == "grader_error"
    assert out["_last_action"] == "final_answer"
    assert out.get("_verify_attempts", 0) == 0
    assert rt.finish(out)["status"] == "UNVERIFIED"
    assert aux.call_count == 1  # 一次都不多花


def test_grader_unparseable_output_is_grader_error(tmp_path: Path) -> None:
    aux = _aux("我认为这份交付挺好的，不需要改。")
    rt, _ = _rt(tmp_path, aux_llm=aux)
    out = rt.reflect(_state(rubric=_RUBRIC))
    assert out["_verification"]["reason"] == "grader_error"
    assert rt.finish(out)["status"] == "UNVERIFIED"


def test_grader_client_exception_is_grader_error(tmp_path: Path) -> None:
    aux = _RaisingAux()
    rt, _ = _rt(tmp_path, aux_llm=aux)
    out = rt.reflect(_state(rubric=_RUBRIC))
    assert out["_verification"]["reason"] == "grader_error"
    assert out["_last_action"] == "final_answer"
    assert rt.finish(out)["status"] == "UNVERIFIED"


def test_rubric_declared_without_a_judge_fails_closed(tmp_path: Path) -> None:
    """声明了 rubric 却没有独立裁判（aux 未配置）：一条没人能验的合格线不该被读成
    交付——与 ``_expectation_failures`` 的「判不动即未过」同调，落 grader_error 不判死。"""
    rt, _ = _rt(tmp_path)  # aux_llm=None + settings aux disabled -> runtime.aux_llm is None
    assert rt.aux_llm is None
    out = rt.reflect(_state(rubric=_RUBRIC))
    assert out["_verification"]["passed"] is False
    assert out["_verification"]["reason"] == "grader_error"
    assert rt.finish(out)["status"] == "UNVERIFIED"


def test_mechanical_expectation_runs_before_the_grader(tmp_path: Path) -> None:
    """判定顺序（票面 §3）：确定性条件没过就带失败项回环，一次裁判都不叫——
    能确定性解决的问题不花裁判的钱。"""
    aux = _aux(_SATISFIED)
    rt, _ = _rt(tmp_path, aux_llm=aux)
    out = rt.reflect(
        _state(rubric=_RUBRIC, expectations=[_exp("artifact", "report.md")])
    )
    assert out["_verification"]["passed"] is False
    assert any("report.md" in f for f in out["_verification"]["failures"])
    assert aux.call_count == 0


def test_no_rubric_never_summons_the_judge(tmp_path: Path) -> None:
    """向后兼容的根钉子：没声明 rubric 的任务，行为与第一步逐字相同、零额外调用。"""
    aux = _aux(_NEEDS_REVISION)
    rt, _ = _rt(tmp_path, aux_llm=aux)
    out = rt.reflect(_state())
    assert out["_verification"]["passed"] is True
    assert aux.call_count == 0


def test_verify_disabled_with_rubric_is_zero_regression(tmp_path: Path) -> None:
    """``verify_enabled=false`` 的零回归锚必须对新字段同样成立：闸门关掉 = 今天，
    不判定、不叫裁判、仍 COMPLETED（票面 §8「验证关闭：等价今天」）。"""
    aux = _aux(_NEEDS_REVISION)
    rt, _ = _rt(tmp_path, verify_enabled=False, aux_llm=aux)
    out = rt.reflect(_state(rubric=_RUBRIC))
    assert "_verification" not in out
    assert out["_last_action"] == "final_answer"
    assert aux.call_count == 0
    assert rt.finish(out)["status"] == "COMPLETED"


def test_grader_prompt_carries_the_rubric_verbatim(tmp_path: Path) -> None:
    """rubric 是判据正文：到裁判手里时必须原样在场，不是又被模型转述过一遍的东西。"""
    aux = _aux(_SATISFIED)
    rt, _ = _rt(tmp_path, aux_llm=aux)
    rt.reflect(_state(rubric=_RUBRIC))
    sent = " ".join(str(m.get("content") or "") for m in aux.requests[-1])
    assert _RUBRIC in sent
