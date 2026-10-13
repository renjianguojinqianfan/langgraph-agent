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
        verify_rubric_max_retries: int = 2,
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
        tmp_path,
        verify_enabled=verify_enabled,
        verify_max_retries=verify_max_retries,
        verify_rubric_max_retries=verify_rubric_max_retries,
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
# 裁判的判决形状沿用 #46 试测的协议（SYSTEM_PROMPT 与 JSON 形状记在
# docs/grader-fn-trial-2026-10-13.md「裁判协议形状」一节）：
# {"result": satisfied|needs_revision|failed, "explanation", "criteria"}。
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
    """needs_revision 不是判决的终点：具体缺口要注回对话，模型才修得动。

    #46 切片 B 重裁（原断 ``_verify_attempts == 1``）：那条钉的是切片 A「与第一步的
    确定性回环共用一本账」这个过渡收口；现在 needs_revision 记在自己那本账上
    （``_rubric_attempts``），确定性那本一个字都不动。
    """
    aux = _aux(_NEEDS_REVISION)
    rt, _ = _rt(tmp_path, aux_llm=aux)
    out = rt.reflect(_state(rubric=_RUBRIC))
    assert out["_verification"]["passed"] is False
    assert out["_verification"]["reason"] == "pending"  # 回环中，收口留给 finish
    assert out["_last_action"] == "verify_failed"  # 走第一步已有的 router 回 planner
    assert out["_rubric_attempts"] == 1
    assert out.get("_verify_attempts", 0) == 0
    assert any("报告没有结论小节" in f for f in out["_verification"]["failures"])
    assert any(
        m.get("role") == "system" and "报告没有结论小节" in m.get("content", "")
        for m in out["messages"]
    )


def test_grader_needs_revision_has_its_own_budget(tmp_path: Path) -> None:
    """票面 §1（切片 B）：needs_revision 的回环预算与 retry_exhausted **分账**。

    共用一本账时，一次机械缺口就能把语义复核的机会提前烧光——「先确定性、后裁判」
    的顺序反过来惩罚了 rubric：模型刚被 expectation 打回两次，裁判再说一句「缺结论」
    就直接 UNVERIFIED。分账之后裁判那本还有余额就继续回环。
    """
    aux = _aux(_NEEDS_REVISION)
    rt, _ = _rt(tmp_path, verify_max_retries=2, verify_rubric_max_retries=2, aux_llm=aux)
    out = rt.reflect(_state(rubric=_RUBRIC, _verify_attempts=2))  # 确定性那本已花光
    assert out["_verification"]["passed"] is False
    assert out["_verification"]["reason"] == "pending"  # 不是 retry_exhausted
    assert out["_last_action"] == "verify_failed"  # 自己的账还有余额 —— 继续回环
    assert out["_rubric_attempts"] == 1
    assert out["_verify_attempts"] == 2  # 另一本账不推进


def test_rubric_budget_exhaustion_ends_unverified(tmp_path: Path) -> None:
    """裁判那本账花完 → rubric_exhausted 收口、终态 UNVERIFIED、绝不 FAILED（ADR 0005）。

    #132 返工 C 重裁：两本账烧完各叫各的名字（机械账 ``retry_exhausted``、语义账
    ``rubric_exhausted``），运维从 reason 要能看出是哪本账花光。本条原钉的
    ``retry_exhausted`` 是分名之前的形状——那条决定正是被这次返工改掉的。
    """
    aux = _aux(_NEEDS_REVISION)
    rt, _ = _rt(tmp_path, verify_max_retries=2, verify_rubric_max_retries=1, aux_llm=aux)
    out = rt.reflect(_state(rubric=_RUBRIC, _rubric_attempts=1))
    verdict = out["_verification"]
    assert verdict["passed"] is False
    assert verdict["reason"] == "rubric_exhausted"
    assert out["_last_action"] == "final_answer"  # 收口交给 finish，不再回环
    assert out["_rubric_attempts"] == 1  # 预算耗尽这档不推进计数
    assert rt.finish(out)["status"] == "UNVERIFIED"


def test_rubric_exhausted_after_max_retries(tmp_path: Path) -> None:
    """语义账耗尽的镜像形状（对 ``test_unverified_after_max_retries`` 那条机械账用例）：
    裁判持续 ``needs_revision``、默认闸花光 → ``rubric_exhausted`` 收口，终态
    UNVERIFIED、绝不 FAILED（ADR 0005；#132 返工 C 补的分名一档）。
    """
    aux = _aux(_NEEDS_REVISION)
    rt, _ = _rt(tmp_path, verify_max_retries=2, verify_rubric_max_retries=2, aux_llm=aux)
    out = rt.reflect(_state(rubric=_RUBRIC, _rubric_attempts=2))  # 语义账已花光
    assert out["_verification"]["passed"] is False
    assert out["_verification"]["reason"] == "rubric_exhausted"
    assert "degraded" not in out["_verification"]
    assert out["_last_action"] == "final_answer"  # router still routes to finish
    assert rt.finish(out)["status"] == "UNVERIFIED"


def test_deterministic_budget_ignores_an_exhausted_rubric_budget(tmp_path: Path) -> None:
    """分账是双向的：裁判那本花光，不许把确定性回环也一起判死。"""
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    aux = _aux(_NEEDS_REVISION)
    rt, _ = _rt(
        tmp_path,
        verify_max_retries=2,
        verify_rubric_max_retries=1,
        artifacts=[_art(empty)],
        aux_llm=aux,
    )
    out = rt.reflect(
        _state(rubric=_RUBRIC, _rubric_attempts=1, _verify_attempts=0),
    )
    # 确定性层先命中（空产物），裁判根本不被调用（票面 §3 的判定顺序）
    assert aux.call_count == 0
    assert out["_verification"]["reason"] == "pending"
    assert out["_last_action"] == "verify_failed"
    assert out["_verify_attempts"] == 1
    assert out["_rubric_attempts"] == 1  # 裁判那本原样不动


def test_verification_attempts_counts_both_loop_books(tmp_path: Path) -> None:
    """`verification.attempts` 对外仍是「这一轮被打回了几次」，两本账合起来数。

    分账分的是**预算**，不是那个给评测台/trace 看的计数——把载荷拆成两个数字会让所有
    既有消费者重新学一遍语义，而它们要的回答本来就只有「回环了几次」。
    """
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    rt, _ = _rt(tmp_path, artifacts=[_art(empty)])
    out = rt.reflect(_state(_rubric_attempts=1))
    assert out["_verification"]["attempts"] == 2  # 已花 1（裁判那本）+ 本回合这次
    assert out["_verify_attempts"] == 1
    assert out["_rubric_attempts"] == 1


def test_grader_failed_verdict_is_grader_error_and_not_a_death_sentence(tmp_path: Path) -> None:
    """``failed`` 专指 rubric 本身无法评估：回环修不动调用方写的卷子，所以不回环、
    不烧预算，直接 UNVERIFIED(grader_error)——裁判的故障不许毁掉一个可能真完成的任务。

    切片 B 之后「不烧预算」意味着**两本账都不烧**（ADR 0007 第 4 条）。
    """
    aux = _aux('{"result": "failed", "explanation": "两条判据互相矛盾", "criteria": []}')
    rt, _ = _rt(tmp_path, verify_max_retries=2, aux_llm=aux)
    out = rt.reflect(_state(rubric=_RUBRIC))
    verdict = out["_verification"]
    assert verdict["passed"] is False
    assert verdict["reason"] == "grader_error"
    assert out["_last_action"] == "final_answer"
    assert out.get("_verify_attempts", 0) == 0
    assert out.get("_rubric_attempts", 0) == 0
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


# ── #46 第二步：裁判的证据面（试测定居的两个「偏松」洞，构造性堵住）────────────
def _sent_prompt(aux: MockAuxLLMClient) -> str:
    assert aux.requests, "裁判一次都没被调用，谈不上它收到了什么证据"
    return " ".join(str(m.get("content") or "") for m in aux.requests[-1])


def _ledger_block(prompt: str) -> str:
    start, end = prompt.find("<write_ledger>"), prompt.find("</write_ledger>")
    assert start >= 0 and end > start, "裁决提示里没有写调用账本"
    return prompt[start:end]


def _write_call(path: str, content: str, *, ok: bool = True) -> Dict[str, Any]:
    return {
        "tool_name": "write",
        "status": "success" if ok else "failed",
        "input": {"path": path, "content": content},
        "output": {"path": path, "size": len(content)} if ok else None,
        "error": "" if ok else "disk full",
    }


def test_ledger_hands_the_judge_only_successful_write_calls(tmp_path: Path) -> None:
    """洞 (a)：试测里模型声称写了 r3、其实没写，裁判拿一次 ``ls`` 的间接证据就放行。
    所以交给裁判的归属账本只收**成功的写调用**——失败的写、读过的文件、ls 列出来的
    路径一概不在里面，「文件存在」从此顶替不了「本次运行写的」。"""
    aux = _aux(_SATISFIED)
    rt, _ = _rt(tmp_path, aux_llm=aux, with_file_tools=True)
    state = _state(
        rubric=_RUBRIC,
        steps=[
            {
                "index": 1,
                "tool_calls": [
                    _write_call("report.md", "结论：沙箱根下有四个文件"),
                    _write_call("ghost.md", "写失败的那一份", ok=False),
                    {"tool_name": "read", "status": "success", "input": {"path": "other.md"}},
                    {"tool_name": "ls", "status": "success", "input": {"path": "."}},
                ],
            }
        ],
    )
    rt.reflect(state)
    ledger = _ledger_block(_sent_prompt(aux))
    assert "report.md" in ledger
    assert "结论：沙箱根下有四个文件" in ledger  # 正文在场，占位符才判得动
    assert "ghost.md" not in ledger
    assert "other.md" not in ledger


def test_ledger_states_plainly_when_no_write_succeeded(tmp_path: Path) -> None:
    """零成功写调用时账本要说「没有」，而不是留一个空块让裁判自己去猜空意味着什么。"""
    aux = _aux(_SATISFIED)
    rt, _ = _rt(tmp_path, aux_llm=aux, with_file_tools=True)
    state = _state(
        rubric=_RUBRIC,
        steps=[{"index": 1, "tool_calls": [{"tool_name": "ls", "status": "success", "input": {}}]}],
    )
    rt.reflect(state)
    assert "没有任何成功的写调用" in _ledger_block(_sent_prompt(aux))


def test_ledger_marks_an_edit_as_a_fragment_not_the_whole_file(tmp_path: Path) -> None:
    """edit 只带替换片段。标清楚，否则裁判会把一小段当成整份交付物来评内容。"""
    aux = _aux(_SATISFIED)
    rt, _ = _rt(tmp_path, aux_llm=aux, with_file_tools=True)
    state = _state(
        rubric=_RUBRIC,
        steps=[
            {
                "index": 1,
                "tool_calls": [
                    {
                        "tool_name": "edit",
                        "status": "success",
                        "input": {"path": "report.md", "old_string": "旧", "new_string": "新结论"},
                        "output": {"path": "report.md"},
                    }
                ],
            }
        ],
    )
    rt.reflect(state)
    ledger = _ledger_block(_sent_prompt(aux))
    assert "report.md" in ledger
    assert "非全文" in ledger


def test_grader_rules_forbid_existence_as_authorship_evidence(tmp_path: Path) -> None:
    """判据正文要在场：试测放过的 r123 案就是拿「三个文件都在」当证据的。"""
    aux = _aux(_SATISFIED)
    rt, _ = _rt(tmp_path, aux_llm=aux)
    rt.reflect(_state(rubric=_RUBRIC))
    system = str(aux.requests[-1][0].get("content") or "")
    assert "文件存在不是证据" in system
    assert "ls" in system and "read" in system


def test_grader_rules_forbid_a_placeholder_as_content(tmp_path: Path) -> None:
    """试测放过的第二案：交付物是占位符也 satisfied。判据里必须点名占位符形态。"""
    aux = _aux(_SATISFIED)
    rt, _ = _rt(tmp_path, aux_llm=aux)
    rt.reflect(_state(rubric=_RUBRIC))
    system = str(aux.requests[-1][0].get("content") or "")
    assert "占位符" in system
    assert "非空" in system  # 「非空」不等于「兑现」——空文件挡得住 S1，挡不住这条


def test_transcript_carries_the_callers_task_and_the_final_claim(tmp_path: Path) -> None:
    """裁判看的是记录，不是模型的自述：任务原文与 final answer 都要在场，
    两边缺一边，「声称」与「被要求」就没得对照。"""
    aux = _aux(_SATISFIED)
    rt, _ = _rt(tmp_path, aux_llm=aux)
    rt.reflect(_state(rubric=_RUBRIC, messages=[{"role": "user", "content": "把调研写成 report.md"}]))
    prompt = _sent_prompt(aux)
    assert "<task>" in prompt and "把调研写成 report.md" in prompt
    assert "<final_answer>" in prompt


def test_blank_rubric_is_not_a_declaration(tmp_path: Path) -> None:
    """空白声明归一成「没声明」，在 create_task 那道 boundary 做一次。

    留着 ``"   "`` 当声明，就是一个没人写得出的 rubric 把任务拖去见裁判、再拖成
    UNVERIFIED——所以这里既断记录是空串，也断裁判真的没被叫起。
    """
    settings = make_settings(
        tmp_path,
        risk_scan_enabled=False,
        subagent_enabled=False,
        aux_llm_enabled=True,
        aux_llm_use_mock=True,
    )
    tm = make_manager(settings, MockLLMClient(final_answer="结论如上"), event_bus=EventBus())
    aux = tm._aux_llm
    assert isinstance(aux, MockAuxLLMClient)
    aux.role_responses[_GRADER_ROLE] = _NEEDS_REVISION
    tid = tm.create_task(title="t", user_input="随便聊聊", rubric="   ")
    deadline = time.time() + 15
    task = None
    while time.time() < deadline:
        task = tm.get_task(tid)
        if task and task.status.value in ("COMPLETED", "FAILED", "INTERRUPTED", "UNVERIFIED"):
            break
        time.sleep(0.05)
    assert task is not None
    assert task.rubric == ""
    assert task.status is TaskStatus.COMPLETED
    assert aux.call_count == 0


def test_ledger_block_is_bounded_and_says_so(tmp_path: Path) -> None:
    """账本也要有上界：一次写 40 个文件的运行不该把无上限的提示词交给裁判。
    截断是**说出来**的，不是悄悄做的——「未列出」意味着「此处无证据」，
    而裁判的规则是正面确认不了的判据不许通过。"""
    aux = _aux(_SATISFIED)
    rt, _ = _rt(tmp_path, aux_llm=aux, with_file_tools=True)
    calls = [_write_call(f"f{i}.md", f"第 {i} 份正文") for i in range(40)]
    rt.reflect(_state(rubric=_RUBRIC, steps=[{"index": 1, "tool_calls": calls}]))
    ledger = _ledger_block(_sent_prompt(aux))
    assert "另有 10 次成功写调用未在此列出" in ledger
