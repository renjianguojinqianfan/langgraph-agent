"""Live end-to-end verification against a real OpenAI-compatible LLM host
(validated with the QianWen / DashScope qwen3.7-flash-2026-07-15 model).

Run (Linux/macOS bash or Git Bash):
    LLM_API_KEY="$DASHSCOPE_API_KEY" python scripts/live_e2e.py
or, after putting llm_api_key into .env:
    python scripts/live_e2e.py

Requirements:
  - An OpenAI-compatible endpoint + API key (env LLM_API_KEY or .env llm_api_key)
  - .env configured with llm_base_url (e.g. https://dashscope.aliyuncs.com/compatible-mode/v1)
    and llm_model (e.g. qwen3.7-flash-2026-07-15), use_mock_llm=false
  - The chosen model must have quota on the account

This script drives the REAL LangGraph kernel end-to-end with a natural-language
task, verifying planner -> executor -> tool -> reflect -> final_answer and that
the write tool actually writes an artifact.

It then exercises the Issue #4 resume path: a second task is stopped mid-run,
the TaskManager is rebuilt (simulating a process restart), and the task is
resumed from its durable checkpoint to completion.

A real model can reach for a dangerous (``requires_confirm``) tool on the way.
The gate is not disabled here — the script plays the human instead, answering
each request through the same ``TaskManager.confirm`` the UI calls (Issue #68),
so the run tests resume rather than parking until ``confirm_timeout_sec`` (1800s)
elapses. See ``attach_confirm_proxy``.

Offline smoke mode (no key, no network — safe for CI):
    python scripts/live_e2e.py --check
Forces USE_MOCK_LLM=true, redirects every writable path (data/artifacts/
traces/repos/kb) into a throw-away temp dir, and verifies the dependency
chain loads: settings -> tools -> mock LLM client -> TaskManager lifecycle.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # scripts/ -> project root
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("USE_MOCK_LLM", "false")

from backend.config import get_settings  # noqa: E402
from backend.core.llm.openai_compat import create_llm_client  # noqa: E402
from backend.core.tools.registry import build_tools  # noqa: E402
from backend.services.event_bus import EventBus  # noqa: E402
from backend.services.persistence import Persistence  # noqa: E402
from backend.services.task_manager import TaskManager  # noqa: E402


def _wait_terminal(tm, task_id, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = tm.get_task(task_id)
        if task and task.status.value in ("COMPLETED", "FAILED", "INTERRUPTED"):
            return task
        time.sleep(0.1)
    return tm.get_task(task_id)


def attach_confirm_proxy(tm: TaskManager, task_id: str) -> list[dict]:
    """Play the human for the confirmation gate, so the run keeps moving (#68).

    Scenario 2 claims to test stop -> rebuild -> resume, not the gate. A real
    model can still pick a ``requires_confirm`` tool mid-run (observed: it loops
    back after a failed verification and reaches for ``code_exec``), and
    ``human_confirm_node`` then waits ``confirm_timeout_sec`` (default 1800s) for
    a verdict nobody sends — while the script only waits 180s. The run always
    times out, and the timeout reads like a kernel bug.

    The answer goes through the client the gate already exposes to humans: the
    same ``TaskManager.confirm`` the REST route ``POST /tasks/{id}/confirm``
    calls. Deliberately NOT ``auto_approve`` — that is the evaluation-mode switch
    of ``backend/headless.py``, which never asks. Here the gate computes its
    verdict, parks, publishes ``human_confirm_required`` and only continues
    because an approval landed, so the ask/reply pair and the ``approved``
    terminal state stay on the record: the gate is still being tested, the
    script just stopped being absent.

    Both publishers of that event get an answer, since both wait on the same
    request registry: the per-call gate above, and ``risk_policy=pause``'s
    plan-level confirmation — which has no timeout at all, so an absent human
    parks that run forever rather than for 1800s.

    Returns the requests this proxy answered — diagnostics only; the evidence
    check reads the events (see :func:`count_gate_verdicts`).
    """
    answered: list[dict] = []
    logged: set[tuple[str, float]] = set()

    def _on_event(event: dict) -> None:
        if event.get("type") != "human_confirm_required":
            return
        data = event.get("data") or {}
        call_id = str(data.get("tool_call_id") or "")
        if not call_id:
            return
        # Answer first, log second: ``confirm`` is what knows whether a request
        # is pending (False otherwise), and one call id can legitimately be
        # asked twice in a run — a later round re-generating it, or the catch-up
        # below re-delivering an event that was also delivered live.
        ok = bool(tm.confirm(task_id, call_id, True))
        key = (call_id, float(event.get("ts") or 0.0))
        if key in logged:
            return
        logged.add(key)
        answered.append(
            {"tool_call_id": call_id, "tool_name": str(data.get("tool_name") or ""), "ok": ok}
        )
        print(
            f"  [CONFIRM] approved {data.get('tool_name')} ({call_id})"
            + ("" if ok else " — NO REQUEST PENDING, this run will park")
        )

    tm.event_bus.subscribe(task_id, _on_event)
    # create_task() starts its worker inline, so a gate can already be waiting
    # by the time we get here: replay the per-task buffer and answer whatever
    # landed before the subscription (same catch-up the SSE endpoint relies on).
    for event in tm.event_bus.replay(task_id):
        _on_event(event)
    return answered


def count_gate_verdicts(events: list[dict]) -> tuple[int, int]:
    """``(asks the gate published, asks closed by a human approval)`` in one stream.

    Counting only ``approved`` — never ``auto_approved`` — is the point: it says
    the run advanced because somebody answered, which is what tells this apart
    from disabling the gate (#68 裁定 A). A run where the gate never fires is a
    fine outcome (a read-only plan needs no verdict), so the pair is compared,
    never required.

    Pairing is by count, not by call id: one ask gets exactly one
    ``human_confirm_resolved``, while a call id can be reused across rounds. The
    plan-level ask (``risk_policy=pause``, key ``risk_plan``) is left out of the
    ask side on purpose — that confirmation has no tool-call record to annotate,
    so it resolves as a bare bool and never emits ``human_confirm_resolved``
    (nodes.py ``_plan_confirm``; README「分档与上界都只在 per-call 闸门这一层」).
    Counting it would turn a legitimately answered plan-level pause into a FAIL.
    """
    asks = sum(
        1
        for e in events
        if e["type"] == "human_confirm_required" and (e.get("data") or {}).get("tool_call_id") != "risk_plan"
    )
    approvals = sum(
        1
        for e in events
        if e["type"] == "human_confirm_resolved" and (e.get("data") or {}).get("outcome") == "approved"
    )
    return asks, approvals


def run_check() -> int:
    """Offline smoke of the live_e2e wiring: no LLM calls, no network, no key.

    Forces the mock LLM and redirects ALL writable paths into a temp dir —
    artifacts/trace/git/kb are independent settings fields that resolve
    against PROJECT_ROOT and do NOT follow data_dir, so overriding DATA_DIR
    alone would still let TaskManager construction touch data/.
    """
    tmp = Path(tempfile.mkdtemp(prefix="live_e2e_check_"))
    # Override (not setdefault) so a local .env cannot leak in; note the
    # module-level setdefault("USE_MOCK_LLM", "false") above is thus beaten.
    os.environ["USE_MOCK_LLM"] = "true"
    os.environ["DATA_DIR"] = str(tmp)
    os.environ["ARTIFACTS_DIR"] = str(tmp / "artifacts")
    os.environ["TRACE_DIR"] = str(tmp / "traces")
    os.environ["GIT_REPO_DIR"] = str(tmp / "repos")
    os.environ["KB_DIR"] = str(tmp / "kb")
    get_settings.cache_clear()  # env must win over any cached Settings

    print(f"=== OFFLINE SMOKE (--check) | temp root: {tmp} ===")
    results: list[tuple[str, bool, str]] = []
    settings = tools = llm = tm = None

    def step(name, fn):
        nonlocal settings, tools, llm, tm
        try:
            fn()
            results.append((name, True, ""))
        except Exception as exc:  # report-and-continue: print every failure
            results.append((name, False, repr(exc)))

    def _settings():
        nonlocal settings
        settings = get_settings()
        assert settings.use_mock_llm, "USE_MOCK_LLM override did not take effect"
        assert str(settings.data_path).startswith(str(tmp)), "DATA_DIR redirect did not take effect"

    def _tools():
        nonlocal tools
        tools = build_tools(settings)
        assert tools, "build_tools returned an empty tool set"

    def _llm():
        nonlocal llm
        llm = create_llm_client(settings)
        assert llm is not None

    def _tm():
        nonlocal tm
        tm = TaskManager(settings, EventBus(), Persistence(settings),
                         llm_client=llm, tools=tools)

    def _shutdown():
        if tm is None:
            raise AssertionError("TaskManager never constructed")
        tm.shutdown()

    step("get_settings() resolves (mock forced, paths in tmp)", _settings)
    step("build_tools() returns non-empty tool set", _tools)
    step("create_llm_client() (mock path)", _llm)
    step("TaskManager constructs over temp storage", _tm)
    step("TaskManager.shutdown() completes", _shutdown)

    ok = True
    for name, passed, err in results:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" — {err}" if err else ""))
        ok = ok and passed
    print("=== RESULT:", "PASS" if ok else "FAIL", "===")
    return 0 if ok else 1


def main() -> int:
    settings = get_settings()
    print(f"LLM config: provider={settings.llm_provider} model={settings.llm_model} base_url={settings.llm_base_url}")
    print(f"use_mock_llm={settings.use_mock_llm} key_set={'yes' if settings.llm_api_key else 'NO'}")

    if settings.use_mock_llm or not settings.llm_api_key:
        print("ABORT: use_mock_llm must be false and llm_api_key must be set (via LLM_API_KEY env or .env).")
        return 2

    eb = EventBus()
    persistence = Persistence(settings)
    llm = create_llm_client(settings)
    tools = build_tools(settings)
    tm = TaskManager(settings, eb, persistence, llm_client=llm, tools=tools)

    task_id = tm.create_task(
        title="live-qianwen-e2e",
        user_input="Write a short summary of your own capabilities into a file named agent_summary.txt, "
        "then tell me what you did.",
    )
    print(f"task_id: {task_id}\n")
    # The script is the human on this path (#68): a mid-run gate gets an answer
    # instead of a timeout. Same client the UI/REST uses, so the gate stays armed.
    attach_confirm_proxy(tm, task_id)

    # Budget expressed in LLM rounds, so raising llm_request_timeout_sec can never
    # silently shrink this window (#13).
    budget_sec = max(90, int(settings.llm_request_timeout_sec) * 4)
    for _ in range(budget_sec * 10):  # 0.1s poll interval
        time.sleep(0.1)
        task = tm.get_task(task_id)
        if task and task.status.value in ("COMPLETED", "FAILED", "INTERRUPTED"):
            break

    task = tm.get_task(task_id)
    events = eb.replay(task_id)
    event_types = {e["type"] for e in events}

    print("\n=== LIVE E2E REPORT ===")
    print("final status:", task.status.value if task else "MISSING")
    print("event types :", sorted(event_types))
    asks1, approved1 = count_gate_verdicts(events)
    print(f"confirm gates: {approved1}/{asks1} ask(s) closed by an approval")

    artifacts = [a.filename for a in (task.artifacts if task else [])]
    print("artifacts   :", artifacts)

    final_answers = [e for e in events if e["type"] == "final_answer"]
    if final_answers:
        print("\n--- final_answer (truncated) ---")
        print(str(final_answers[-1]["data"])[:500])

    # Verify the artifact actually landed.
    artifact = None
    for a in (task.artifacts if task else []):
        if a.filename == "agent_summary.txt":
            artifact = a
            break
    file_ok = False
    if artifact:
        p = settings.artifacts_path / artifact.filename
        file_ok = p.exists() and p.read_text(encoding="utf-8").strip() != ""

    checks = {
        "task persisted": task is not None,
        "status COMPLETED": task is not None and task.status.value == "COMPLETED",
        "plan_update event": "plan_update" in event_types,
        "tool_call event": "tool_call" in event_types,
        "tool_result event": "tool_result" in event_types,
        "artifact_created event": "artifact_created" in event_types,
        "final_answer event": "final_answer" in event_types,
        "task_completed event": "task_completed" in event_types,
        "agent_summary.txt written": file_ok,
    }

    ok = True
    for name, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed

    print("=== SCENARIO 1 RESULT:", "PASS" if ok else "FAIL", "===\n")

    # ── Scenario 2 (Issue #4): stop -> restart simulation -> resume ──
    print("=== SCENARIO 2: stop / rebuild / resume ===")
    if not settings.checkpoint_enabled:
        print("SKIP: checkpoint_enabled=false; cannot exercise the resume path.")
        return 0 if ok else 1

    llm2 = create_llm_client(settings)
    eb2 = EventBus()
    tm_stop = TaskManager(settings, eb2, persistence, llm_client=llm2, tools=tools)
    rid = tm_stop.create_task(
        title="live-resume-e2e",
        user_input="Create three text files r1.txt r2.txt r3.txt in artifacts, "
        "each containing its own name, then summarize what you created.",
    )
    # Before the stop, too: a gate parked here would write ``pending_confirm``,
    # and ``resume()`` refuses a snapshot parked on the gate — the scenario would
    # then fail on "resume accepted" and say nothing about the resume path.
    attach_confirm_proxy(tm_stop, rid)

    # Stop as soon as it is RUNNING so an INTERRUPTED record exists.
    stopped = False
    interrupted_observed = False
    deadline = time.time() + 60
    while time.time() < deadline:
        t = tm_stop.get_task(rid)
        if t and t.status.value == "RUNNING":
            tm_stop.stop(rid)
            stopped = True
            # Sample IMMEDIATELY: stop() publishes INTERRUPTED synchronously,
            # but later phases (resume) flip the shared record to RUNNING, so
            # this is the only moment the interrupted state is observable.
            tt = tm_stop.get_task(rid)
            interrupted_observed = bool(tt) and tt.status.value == "INTERRUPTED"
            break
        if t and t.status.value not in ("PENDING", "RUNNING"):
            break
        time.sleep(0.05)

    task_r = _wait_terminal(tm_stop, rid)
    steps_before = len(task_r.steps) if task_r else 0
    print(f"stopped={stopped} status={task_r.status.value if task_r else '?'} steps_so_far={steps_before}")

    # Simulate a process restart: brand-new manager over the same storage.
    tm_resume = TaskManager(settings, EventBus(), persistence,
                            llm_client=create_llm_client(settings), tools=build_tools(settings))
    tm_stop.shutdown()

    # The rebuilt manager owns a new bus and a new pending-request registry, so
    # the proxy has to re-attach there before resume(): the continued run answers
    # the same way the pre-restart one did (#68).
    attach_confirm_proxy(tm_resume, rid)

    resumed = False
    try:
        res = tm_resume.resume(rid)
        resumed = bool(res.get("ok"))
    except RuntimeError as exc:
        print("resume rejected:", exc)
    print(f"resume accepted: {resumed}")

    task_final = _wait_terminal(tm_resume, rid, timeout=180)
    # In-process restart shares one Persistence instance, so tm_stop's
    # finalization races are visible through the same object; the resumed
    # worker may also legitimately still be RUNNING when the model is slow —
    # poll via a fresh persistence read to avoid any cached-state artifact.
    fresh = Persistence(settings).load_task(rid)
    if (task_final is None or task_final.status.value == "RUNNING") and fresh:
        task_final = fresh
    # Gate evidence spans both phases: two buses, one story. An ask the proxy
    # could not answer is what #68 actually was, so it fails here rather than
    # showing up as an unexplained 180s timeout.
    asks2, approved2 = count_gate_verdicts(eb2.replay(rid) + tm_resume.event_bus.replay(rid))
    print(f"confirm gates: {approved2}/{asks2} ask(s) closed by an approval")
    final_checks = {
        "task was interrupted before stop": stopped and interrupted_observed,
        "resume accepted": resumed,
        "resumed run reached terminal state": bool(task_final) and task_final.status.value in ("COMPLETED", "FAILED", "INTERRUPTED"),
        "resumed run completed": bool(task_final) and task_final.status.value == "COMPLETED",
        "steps continued (not restarted)": bool(task_final) and len(task_final.steps) >= max(steps_before, 1),
        "final answer present": bool(task_final and task_final.final_answer),
        # Vacuous when the model never reached for a gated tool (asks2 == 0); it
        # is the gate *not being bypassed* that has to hold whenever it fires.
        "no per-call gate left unanswered": asks2 == approved2,
    }
    for name, passed in final_checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed

    tm_resume.shutdown()
    print("=== SCENARIO 2 RESULT:", "PASS" if all(final_checks.values()) else "FAIL", "===")
    print("=== FINAL RESULT:", "PASS" if ok else "FAIL", "===\n")
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live E2E against a real LLM host")
    parser.add_argument("--check", action="store_true",
                        help="offline smoke of the wiring only: no LLM/network/key needed")
    args = parser.parse_args()
    raise SystemExit(run_check() if args.check else main())
