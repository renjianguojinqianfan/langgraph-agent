"""#46 第二步（切片 B）——``UNVERIFIED`` 的人工复核出口。

Face under test: a **settled** ``UNVERIFIED`` task plus one human verdict. The
task moves to ``COMPLETED`` and carries an attributable record of the act, while
``verification`` keeps telling the machine's own truth — that is the ruling ADR
0005's「重开条件」asked for: 人工接受**不算**「验证通过」，它是终态的第三个来源，
不是把第一个来源改成绿。

So the assertions split in two, and both halves matter:

* what changed — ``status``、``review``（谁 / 何时 / 为何 / 推翻了哪一条 reason）、``task_accepted``
  事件、trace JSONL 里那一行留痕；
* what must **not** change — ``verification.passed`` 仍是 ``False``、``reason`` 仍是原值、
  ``task_completed`` 不被伪发第二遍、运行中的任务根本不接受复核（不能预先授权）。

Everything runs through the real :class:`TaskManager` + Persistence + EventBus +
TraceRecorder with a scripted mock LLM: no network, no key.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from backend.api.schemas import Task, TaskStatus
from backend.core.llm.client import MockLLMClient
from backend.services.event_bus import EventBus
from backend.services.task_manager import TaskManager
from backend.tests.conftest import make_manager, make_settings

_TERMINAL = ("COMPLETED", "FAILED", "INTERRUPTED", "UNVERIFIED")
_NOTE = "人工比对过 report.md，结论与引用都在，判它算交付"
_REVIEWER = "opencode"


def _wait_terminal(tm: TaskManager, task_id: str) -> Optional[Task]:
    deadline = time.time() + 15
    while time.time() < deadline:
        task = tm.get_task(task_id)
        if task is not None and task.status.value in _TERMINAL:
            return task
        time.sleep(0.05)
    return tm.get_task(task_id)


def _unverified_manager(
    tmp_path: Path, *, reason: str, **overrides: Any
) -> Tuple[TaskManager, EventBus, str]:
    """One real run that lands UNVERIFIED, for either cause.

    ``retry_exhausted`` — a declared ``artifact`` expectation the run never met, with
    the deterministic budget at zero. ``grader_error`` — a declared ``rubric`` with no
    judge configured (``aux_llm_enabled`` is off by default), which ADR 0007 makes
    fail closed. Both are the product's own paths, not a hand-made record.
    """
    settings = make_settings(
        tmp_path,
        risk_scan_enabled=False,
        subagent_enabled=False,
        **({"verify_max_retries": 0} if reason == "retry_exhausted" else {}),
        **overrides,
    )
    bus = EventBus()
    tm = make_manager(settings, MockLLMClient(final_answer="报告已经写好了"), event_bus=bus)
    tid = tm.create_task(
        title="deliver",
        user_input="把调研写成 report.md",
        expectations=(
            [{"type": "artifact", "target": "report.md"}]
            if reason == "retry_exhausted"
            else []
        ),
        rubric="报告含结论小节" if reason == "grader_error" else "",
    )
    task = _wait_terminal(tm, tid)
    assert task is not None and task.status is TaskStatus.UNVERIFIED, (
        f"fixture must land UNVERIFIED, got {task.status if task else None}"
    )
    assert (task.verification or {}).get("reason") == reason
    return tm, bus, tid


def _events(bus: EventBus, task_id: str, kind: str) -> List[Dict[str, Any]]:
    return [e for e in bus.replay(task_id) if e["type"] == kind]


def _require(tm: TaskManager, task_id: str) -> Task:
    """Load-or-fail: these tests care about the record's *fields*, so the None
    branch is a fixture bug, not a case."""
    task = tm.get_task(task_id)
    assert task is not None
    return task


# ── 接受本身 ─────────────────────────────────────────────────────────────────
def test_accept_moves_unverified_task_to_completed(tmp_path: Path) -> None:
    tm, _, tid = _unverified_manager(tmp_path, reason="retry_exhausted")

    result = tm.accept_unverified(tid, reviewer=_REVIEWER, note=_NOTE)

    task = tm.get_task(tid)
    assert task is not None
    assert task.status is TaskStatus.COMPLETED
    assert result["status"] == "COMPLETED"
    assert task.review is not None
    assert task.review.reviewer == _REVIEWER
    assert task.review.note == _NOTE
    assert task.review.overruled_reason == "retry_exhausted"
    assert task.review.at  # 留痕要有时间，不是「不知何时被改的」


def test_accept_leaves_the_machine_verdict_alone(tmp_path: Path) -> None:
    """被改写的是**终态**，不是判定。

    这条是整张票的铰链：一旦「接受」把 ``verification.passed`` 涂成 True，`UNVERIFIED`
    就退回去年的 `COMPLETED(degraded)`——章换了个位置盖而已。机器的账必须继续说实话。
    """
    tm, _, tid = _unverified_manager(tmp_path, reason="retry_exhausted")
    seeded = tm.get_task(tid)
    assert seeded is not None
    before = dict(seeded.verification or {})

    tm.accept_unverified(tid, reviewer=_REVIEWER, note=_NOTE)

    accepted = tm.get_task(tid)
    assert accepted is not None
    after = accepted.verification or {}
    assert after["passed"] is False
    assert after["reason"] == "retry_exhausted"
    assert after["failures"] == before["failures"]
    assert "review" not in after  # 人的动作不住在机器的判决里


def test_accept_publishes_task_accepted_and_never_a_second_completed(tmp_path: Path) -> None:
    """事件面上这件事必须自有名字：伪发一条 ``task_completed`` 会把「数终态事件」的消费方
    （trace 回放 / 前端时间线）引到「验证过了」的读法上去。"""
    tm, bus, tid = _unverified_manager(tmp_path, reason="retry_exhausted")

    tm.accept_unverified(tid, reviewer=_REVIEWER, note=_NOTE)

    accepted = _events(bus, tid, "task_accepted")
    assert len(accepted) == 1
    data = accepted[0]["data"]
    assert data["task_id"] == tid
    assert data["status"] == "COMPLETED"
    assert data["reason"] == "retry_exhausted"
    assert data["reviewer"] == _REVIEWER
    assert data["note"] == _NOTE
    assert _events(bus, tid, "task_completed") == []


def test_accept_records_which_verdict_it_overruled(tmp_path: Path) -> None:
    """``grader_error`` 与 ``retry_exhausted`` 都能被接受（不设门槛：人是最后的判决者，
    拦住某一档只会让黄灯永远亮着），但**接受的是哪一档要能数出来**——#46 试测要的
    「上线后持续采样」靠的就是这个字段（ADR 0007 的重开条件）。"""
    tm, _, tid = _unverified_manager(tmp_path, reason="grader_error")

    tm.accept_unverified(tid, reviewer=_REVIEWER, note="裁判没配，人工看过交付物")

    task = tm.get_task(tid)
    assert task is not None and task.status is TaskStatus.COMPLETED
    assert task.review is not None and task.review.overruled_reason == "grader_error"
    assert (task.verification or {}).get("reason") == "grader_error"


def test_accept_writes_the_audit_line_into_the_trace(tmp_path: Path) -> None:
    """留痕不能只在内存里：任务跑完后 trace 已 close，复核要像 ``rollback`` 那样把记录器
    重新接上，不然这件事进得了 SSE、进不了那份可回放的 JSONL。"""
    tm, _, tid = _unverified_manager(tmp_path, reason="retry_exhausted")
    trace_path = tm.settings.trace_path / f"{tid}.jsonl"

    tm.accept_unverified(tid, reviewer=_REVIEWER, note=_NOTE)

    lines = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    accepted = [e for e in lines if e.get("type") == "task_accepted"]
    assert len(accepted) == 1
    assert accepted[0]["data"]["note"] == _NOTE
    assert accepted[0]["data"]["reviewer"] == _REVIEWER


# ── 拒绝的形状：没 settled / 不是 UNVERIFIED / 已经接受过 ─────────────────────
def test_accept_refused_while_the_run_is_still_open(tmp_path: Path) -> None:
    """复核时任务还在跑怎么办：**不改写、不排队**。排队等于「预先授权一个还没看到的判决」，
    而这条出口的正当性恰恰在于人看过了这份结论。"""
    tm, bus, _ = _unverified_manager(tmp_path, reason="retry_exhausted")
    tm.persistence.save_task(
        Task(id="running1", title="t", user_input="u", status=TaskStatus.RUNNING)
    )

    with pytest.raises(RuntimeError):
        tm.accept_unverified("running1", reviewer=_REVIEWER, note=_NOTE)

    held = tm.get_task("running1")
    assert held is not None and held.status is TaskStatus.RUNNING
    assert _events(bus, "running1", "task_accepted") == []


def test_accept_refused_while_the_worker_thread_has_not_returned(tmp_path: Path) -> None:
    """记录已落 ``UNVERIFIED``、worker 线程却还没从 ``run()`` 返回——拒绝走的是
    ``is_alive()`` 那一半边，不是状态那一半边。

    这不是编出来的场景：``_finalize_terminal`` 先写终态记录、线程随后才收尾，两者之间
    就是有一段窗口。此刻接受等于赶在 worker 自己的收口之前改写它即将写下的东西，
    而这条出口的正当性恰恰在于「人已经看过一份不再变化的结论」。
    """
    tm, bus, tid = _unverified_manager(tmp_path, reason="retry_exhausted")
    assert tm.get_task(tid).status is TaskStatus.UNVERIFIED  # type: ignore[union-attr]

    gate = threading.Event()
    stalled = threading.Thread(target=gate.wait, args=(30,), daemon=True)
    stalled.start()
    tm._threads[tid] = stalled
    try:
        with pytest.raises(RuntimeError, match="settled run"):
            tm.accept_unverified(tid, reviewer=_REVIEWER, note=_NOTE)
        assert tm.get_task(tid).status is TaskStatus.UNVERIFIED  # type: ignore[union-attr]
        assert _events(bus, tid, "task_accepted") == []
    finally:
        gate.set()
        stalled.join(timeout=5)

    # 线程真返回了，同一份请求就放行——拒绝的对象是「没 settled」，不是这个任务。
    tm.accept_unverified(tid, reviewer=_REVIEWER, note=_NOTE)
    assert tm.get_task(tid).status is TaskStatus.COMPLETED  # type: ignore[union-attr]


def test_accept_refused_on_a_task_that_passed_verification(tmp_path: Path) -> None:
    """过了验收的任务没有「接受」这件事可做——放过去就等于给一条真·绿的任务
    也留一条「有人替它背书」的记录，把两种来源混成一种。"""
    settings = make_settings(tmp_path, risk_scan_enabled=False, subagent_enabled=False)
    tm = make_manager(settings, MockLLMClient(final_answer="答案在这儿"), event_bus=EventBus())
    tid = tm.create_task(title="qa", user_input="首都是哪里")
    task = _wait_terminal(tm, tid)
    assert task is not None and task.status is TaskStatus.COMPLETED

    with pytest.raises(RuntimeError):
        tm.accept_unverified(tid, reviewer=_REVIEWER, note=_NOTE)
    assert tm.get_task(tid).review is None  # type: ignore[union-attr]


def test_second_accept_is_refused_and_the_first_record_survives(tmp_path: Path) -> None:
    """一次接受 = 一条留痕。第二次要么是被拒、要么是改写理由——后者就不是审计了。"""
    tm, bus, tid = _unverified_manager(tmp_path, reason="retry_exhausted")
    tm.accept_unverified(tid, reviewer=_REVIEWER, note=_NOTE)

    with pytest.raises(RuntimeError):
        tm.accept_unverified(tid, reviewer="someone-else", note="换个理由再试一次")

    task = tm.get_task(tid)
    assert task is not None and task.review is not None
    assert task.review.note == _NOTE
    assert task.review.reviewer == _REVIEWER
    assert len(_events(bus, tid, "task_accepted")) == 1


def test_accept_refused_for_unknown_task(tmp_path: Path) -> None:
    tm, _, _ = _unverified_manager(tmp_path, reason="retry_exhausted")

    with pytest.raises(RuntimeError):
        tm.accept_unverified("nope", reviewer=_REVIEWER, note=_NOTE)


def test_accept_refused_without_a_reason(tmp_path: Path) -> None:
    """「理由」是这条出口存在的条件：没有理由的接受就是橡皮图章。"""
    tm, _, tid = _unverified_manager(tmp_path, reason="retry_exhausted")

    with pytest.raises(ValueError):
        tm.accept_unverified(tid, reviewer=_REVIEWER, note="   ")

    assert tm.get_task(tid).status is TaskStatus.UNVERIFIED  # type: ignore[union-attr]


# ── 落盘：复核的价值在「事后有人能重放」───────────────────────────────────────
def test_acceptance_survives_a_manager_restart(tmp_path: Path) -> None:
    """重启后这条记录还在不在。

    上面几条读的都是同一个进程里的对象；审计的意义恰恰在服务重启之后——所以断言跑在
    一个新的 :class:`TaskManager`（新的 :class:`Persistence`，从 ``tasks.json`` 重新
    装载）上。``review`` 是个嵌套模型，它能不能跟着 ``Task`` 一起序列化/反序列化，
    只有跨过这次重建才看得出来；``verification.passed`` 仍是 ``False`` 也一样，
    否则「机器判决不被改写」只在本进程成立。
    """
    tm, _, tid = _unverified_manager(tmp_path, reason="retry_exhausted")
    tm.accept_unverified(tid, reviewer=_REVIEWER, note=_NOTE)

    restarted = make_manager(
        tm.settings, MockLLMClient(final_answer="unused"), event_bus=EventBus()
    )
    task = restarted.get_task(tid)
    assert task is not None and task.status is TaskStatus.COMPLETED
    assert task.review is not None
    assert task.review.reviewer == _REVIEWER
    assert task.review.note == _NOTE
    assert task.review.overruled_reason == "retry_exhausted"
    assert task.review.at
    assert (task.verification or {}).get("passed") is False
    assert (task.verification or {}).get("reason") == "retry_exhausted"
