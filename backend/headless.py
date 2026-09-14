"""Headless (unattended) single-task execution entry point (P0-C).

Runs ONE natural-language task to completion with no UI, then reports a
machine-readable result and exits. The invocation shape mirrors the other
agents the eval harness drives (pi / opencode / qoder)::

    python -m backend.headless -p "<prompt>" --dir <workdir> --auto-approve --output json

so a single command form can drive every contestant (roadmap-pawbench.md v2).
One process runs one task; batching is the eval harness's job (fresh workdir +
fresh thread_id per task, no cross-task bleed).

Two modes:

* **real run** — drives the real kernel through :class:`TaskManager`; the LLM
  config comes from env/.env (so the harness can point it at any model, e.g.
  ``deepseek-flash``, purely through ``LLM_*`` env vars — nothing hard-coded);
* **--check** — offline smoke (mock LLM, temp paths, no network / no key),
  safe for CI, mirroring ``scripts/live_e2e.py --check``.

Gate bypass (evaluation mode != production mode): ``--auto-approve`` builds
``TaskManager(auto_approve=True)`` -> ``AgentRuntime(confirm_enabled=False)``,
which the executor already honours to skip ``need_confirm`` for every tool
(nodes.py is untouched). The headless ``Settings`` also disables ``risk_scan``
so ``risk_policy=pause`` can never block on a human who is not there. The
FastAPI service path never sets ``auto_approve``, so there is no global
"disable the gate" switch.

Result contract: with ``--output json`` the parsed result object is the ONLY
thing on stdout (agent logs are repointed at stderr); diagnostics/logs live on
stderr. Exit codes: 0 COMPLETED, 1 FAILED, 2 INTERRUPTED/timeout, 3 usage/config.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .api.schemas import Task
from .config import Settings
from .core.llm.client import LLMClient, MockLLMClient
from .core.llm.openai_compat import create_llm_client
from .core.tools.base import BaseTool, ToolResult
from .core.tools.registry import build_tools
from .services.event_bus import EventBus
from .services.persistence import Persistence
from .services.task_manager import TaskManager

_TERMINAL = ("COMPLETED", "FAILED", "INTERRUPTED")

EXIT_COMPLETED = 0
EXIT_FAILED = 1
EXIT_INTERRUPTED = 2
EXIT_USAGE = 3

DEFAULT_TIMEOUT = 600.0

# Keys the JSON result always carries (the eval harness / judge depends on them).
_RESULT_KEYS: Tuple[str, ...] = (
    "task_id",
    "status",
    "exit_code",
    "workdir",
    "model",
    "final_answer",
    "artifacts",
    "trace_path",
    "trace_exists",
    "steps",
    "timed_out",
    "error",
)


def _redirect_agent_logs_to_stderr() -> None:
    """Keep stdout clean for the machine-readable result.

    :mod:`backend.utils.logging` binds every ``agent.*`` logger to a stdout
    ``StreamHandler`` at import time. Under ``--output json`` the harness parses
    stdout as JSON, so repoint those stream handlers at stderr (result on
    stdout, diagnostics on stderr — the usual CLI contract). File handlers and
    ``print()`` are left alone.

    Targets ``sys.__stderr__`` (the stable process stderr) rather than
    ``sys.stderr`` so the handlers never capture a caller's temporarily-swapped
    stderr object (e.g. pytest's capture wrapper, whose teardown would otherwise
    trip over a stream it does not own).
    """
    target = sys.__stderr__ or sys.stderr
    for name in list(logging.Logger.manager.loggerDict):
        if not name.startswith("agent."):
            continue
        for handler in logging.getLogger(name).handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(
                handler, logging.FileHandler
            ):
                handler.setStream(target)


def _exit_code_for(status: Optional[str], timed_out: bool) -> int:
    """Map a terminal status (+ timeout flag) onto the process exit code."""
    if timed_out or status == "INTERRUPTED":
        return EXIT_INTERRUPTED
    if status == "COMPLETED":
        return EXIT_COMPLETED
    return EXIT_FAILED  # FAILED, or a missing/unreadable task record


def _build_settings(
    workdir: Path,
    run_root: Path,
    model: Optional[str],
    base_url: Optional[str],
) -> Settings:
    """Build the one-shot Settings for a headless run.

    ``artifacts_dir`` is the caller's workdir (where the agent's file tools
    write and the harness collects products). ``data_dir`` / ``trace_dir`` live
    under a throw-away run root so the workdir stays clean (only agent output).
    Checkpoints are OFF (one-shot; no resume, and this also sidesteps the
    durability kwarg) and risk scan is OFF (no human present to answer a
    ``risk_policy=pause`` block). Init kwargs win over env in pydantic-settings,
    so unspecified fields (LLM_*, MCP_*, GIT_*) still come from env/.env.
    """
    overrides: Dict[str, Any] = {
        "artifacts_dir": str(workdir),
        "data_dir": str(run_root),
        "trace_dir": str(run_root / "traces"),
        "checkpoint_enabled": False,
        "risk_scan_enabled": False,
    }
    if model:
        overrides["llm_model"] = model
        overrides["use_mock_llm"] = False
    if base_url:
        overrides["llm_base_url"] = base_url
    return Settings(**overrides)


def _wait_terminal(tm: TaskManager, task_id: str, timeout: float) -> Optional[Task]:
    """Poll until the task reaches a terminal status or the timeout elapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = tm.get_task(task_id)
        if task is not None and task.status.value in _TERMINAL:
            return task
        time.sleep(0.1)
    return tm.get_task(task_id)


def _collect(
    settings: Settings,
    task_id: str,
    task: Optional[Task],
    workdir: Path,
    timed_out: bool,
) -> Dict[str, Any]:
    """Assemble the machine-readable result object for a finished run."""
    status = task.status.value if task is not None else None
    artifacts: List[Dict[str, str]] = []
    if task is not None:
        for art in task.artifacts:
            artifacts.append(
                {"filename": art.filename, "path": str(workdir / art.filename)}
            )
    trace_path = settings.trace_path / f"{task_id}.jsonl"
    return {
        "task_id": task_id,
        "status": status,
        "exit_code": _exit_code_for(status, timed_out),
        "workdir": str(workdir),
        "model": settings.llm_model,
        "final_answer": (task.final_answer if task is not None else "") or "",
        "artifacts": artifacts,
        "trace_path": str(trace_path),
        "trace_exists": trace_path.exists(),
        "steps": len(task.steps) if task is not None else 0,
        "timed_out": timed_out,
        "error": task.error if task is not None else None,
    }


def _run_once(
    settings: Settings,
    prompt: str,
    workdir: Path,
    *,
    auto_approve: bool,
    timeout: float,
    llm_client: Optional[LLMClient] = None,
    tools: Optional[List[BaseTool]] = None,
) -> Dict[str, Any]:
    """Drive one task to a terminal state and return the result object.

    ``llm_client`` / ``tools`` are injectable so tests (and ``--check``) can run
    a scripted mock offline; production passes neither and both are built from
    ``settings``. ``__main__`` repoints agent logs to stderr before this runs,
    so stdout stays clean for the result.
    """
    settings.artifacts_path.mkdir(parents=True, exist_ok=True)
    event_bus = EventBus()
    persistence = Persistence(settings)
    llm = llm_client or create_llm_client(settings)
    tool_set = tools if tools is not None else build_tools(settings)
    tm = TaskManager(
        settings,
        event_bus,
        persistence,
        llm_client=llm,
        tools=tool_set,
        auto_approve=auto_approve,
    )
    timed_out = False
    try:
        task_id = tm.create_task(title=prompt[:40], user_input=prompt)
        task = _wait_terminal(tm, task_id, timeout)
        if task is None or task.status.value not in _TERMINAL:
            # Never reached a terminal state in budget: stop it (bounded unwind)
            # and report INTERRUPTED rather than hanging the batch.
            tm.stop(task_id)
            timed_out = True
            task = _wait_terminal(tm, task_id, min(15.0, timeout))
        return _collect(settings, task_id, task, workdir, timed_out)
    finally:
        tm.shutdown()


def _resolve_prompt(args: argparse.Namespace) -> str:
    if args.prompt:
        return str(args.prompt).strip()
    if args.prompt_file:
        return Path(args.prompt_file).read_text(encoding="utf-8").strip()
    return ""


def _emit(result: Dict[str, Any], fmt: str) -> None:
    if fmt == "json":
        # The ONLY thing on stdout in json mode (logs already went to stderr).
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print("=== HEADLESS RESULT ===")
    print("status     :", result["status"])
    print("task_id    :", result["task_id"])
    print("model      :", result["model"])
    print("workdir    :", result["workdir"])
    print("artifacts  :", [a["filename"] for a in result["artifacts"]])
    trace_note = "(exists)" if result["trace_exists"] else "(missing)"
    print("trace      :", result["trace_path"], trace_note)
    print("steps      :", result["steps"])
    print("timed_out  :", result["timed_out"])
    if result["error"]:
        print("error      :", result["error"])
    final_answer = str(result["final_answer"])
    if final_answer:
        print("final      :", final_answer[:500])
    print("exit_code  :", result["exit_code"])


def run_task(args: argparse.Namespace) -> int:
    """Real headless run: one task, then report + exit."""
    prompt = _resolve_prompt(args)
    if not prompt:
        print(
            "error: a prompt is required (-p/--prompt or --prompt-file)",
            file=sys.stderr,
        )
        return EXIT_USAGE

    run_root = Path(tempfile.mkdtemp(prefix="headless_run_"))
    workdir = Path(args.dir).resolve() if args.dir else (run_root / "workdir")
    workdir.mkdir(parents=True, exist_ok=True)

    settings = _build_settings(workdir, run_root, args.model, args.base_url)
    if not settings.use_mock_llm and not settings.llm_api_key:
        print(
            "error: no LLM api key (set LLM_API_KEY env or .env llm_api_key); "
            "use --check for the offline smoke.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    result = _run_once(
        settings,
        prompt,
        workdir,
        auto_approve=bool(args.auto_approve),
        timeout=float(args.timeout),
    )
    _emit(result, args.output)
    return int(result["exit_code"])


def run_check() -> int:
    """Offline smoke of the headless wiring: no LLM calls, no network, no key.

    Forces the mock LLM and redirects every writable path into a temp dir, then
    proves the dependency chain runs a task to COMPLETED, drops the artifact in
    the workdir, emits a valid result object with a non-empty trace, and that
    ``--auto-approve`` really bypasses the confirmation gate for a
    ``requires_confirm`` tool (which would otherwise park the run).
    """
    tmp = Path(tempfile.mkdtemp(prefix="headless_check_"))
    workdir = tmp / "workdir"
    workdir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        use_mock_llm=True,
        llm_base_url="",
        data_dir=str(tmp),
        artifacts_dir=str(workdir),
        trace_dir=str(tmp / "traces"),
        kb_dir=str(tmp / "kb"),
        checkpoint_enabled=False,
        risk_scan_enabled=False,
        mcp_enabled=False,
        git_enabled=False,
        max_steps=50,
        sandbox_timeout=5,
    )

    print(f"=== HEADLESS OFFLINE SMOKE (--check) | temp root: {tmp} ===")
    results: List[Tuple[str, bool, str]] = []

    def step(name: str, fn: Any) -> None:
        try:
            fn()
            results.append((name, True, ""))
        except Exception as exc:  # report-and-continue: surface every failure
            results.append((name, False, repr(exc)))

    write_mock = MockLLMClient(
        plan=["Write the requested file"],
        tool_calls=[
            {
                "id": "c1",
                "name": "file_io",
                "arguments": {
                    "action": "write",
                    "path": "hello.txt",
                    "content": "headless smoke",
                },
            }
        ],
        final_answer="wrote hello.txt",
    )
    holder: Dict[str, Any] = {}

    def _run() -> None:
        holder["result"] = _run_once(
            settings,
            "write hello.txt",
            workdir,
            auto_approve=True,
            timeout=60.0,
            llm_client=write_mock,
        )

    def _assert_result() -> None:
        result = holder.get("result")
        assert result is not None, "no result produced"
        assert result["status"] == "COMPLETED", f"status={result['status']}"
        assert result["exit_code"] == EXIT_COMPLETED
        assert (workdir / "hello.txt").exists(), "artifact not written into --dir"
        assert result["artifacts"], "no artifacts reported"
        assert result["artifacts"][0]["filename"] == "hello.txt"
        for key in _RESULT_KEYS:
            assert key in result, f"result missing key {key}"
        json.loads(json.dumps(result))  # contract must round-trip
        trace_path = Path(str(result["trace_path"]))
        assert trace_path.exists(), "trace file missing"
        assert trace_path.read_text(encoding="utf-8").strip(), "trace file empty"

    step("headless run reaches COMPLETED (mock, auto_approve)", _run)
    step("result JSON contract + artifact in --dir + non-empty trace", _assert_result)

    # auto_approve bypass: a requires_confirm tool runs to COMPLETED instead of
    # parking on the (absent) human gate.
    class _Probe(BaseTool):
        name = "probe_danger"
        description = "confirm-requiring probe (headless smoke only)"
        args_schema: Dict[str, Any] = {"type": "object", "properties": {}}
        requires_confirm = True

        def run(self, **kwargs: Any) -> ToolResult:
            return ToolResult(success=True, data="probe-ok")

    probe_mock = MockLLMClient(
        plan=["Call the gated probe"],
        tool_calls=[{"id": "p1", "name": "probe_danger", "arguments": {}}],
        final_answer="probe done",
    )
    probe_holder: Dict[str, Any] = {}

    def _run_probe() -> None:
        probe_holder["result"] = _run_once(
            settings,
            "call the gated probe",
            workdir,
            auto_approve=True,
            timeout=60.0,
            llm_client=probe_mock,
            tools=[_Probe(settings)],
        )

    def _assert_probe() -> None:
        result = probe_holder.get("result")
        assert result is not None, "no probe result"
        assert result["status"] == "COMPLETED", (
            f"auto_approve did not bypass the gate: status={result['status']}"
        )

    step("--auto-approve bypasses the confirm gate (requires_confirm tool)", _run_probe)
    step("bypassed run completes", _assert_probe)

    ok = True
    for name, passed, err in results:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" — {err}" if err else ""))
        ok = ok and passed
    print("=== RESULT:", "PASS" if ok else "FAIL", "===")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.headless",
        description="Headless single-task execution entry (P0-C).",
    )
    parser.add_argument("-p", "--prompt", help="task prompt text")
    parser.add_argument("--prompt-file", help="read the task prompt from a file")
    parser.add_argument(
        "-w",
        "--dir",
        help="working dir where artifacts land (default: a temp dir)",
    )
    parser.add_argument(
        "--auto-approve",
        "--yolo",
        action="store_true",
        help="bypass the confirmation gate (evaluation mode; NOT the production default). "
        "--yolo is the cross-agent harness alias (roadmap-pawbench §四 unified form)",
    )
    parser.add_argument("--model", help="override LLM model (else env/.env)")
    parser.add_argument("--base-url", help="override LLM base_url (else env/.env)")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="wall-clock seconds to wait for a terminal state (default: 600)",
    )
    parser.add_argument(
        "--output",
        choices=("json", "text"),
        default="json",
        help="result format on stdout (default: json)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="offline smoke of the wiring only: no LLM / network / key needed",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.check:
        return run_check()
    return run_task(args)


if __name__ == "__main__":
    # Process-level, done once at the real CLI entry (not inside main(), so
    # importing and calling main()/run_check() under a test runner never mutates
    # global logging handlers). Keeps stdout clean for --output json.
    _redirect_agent_logs_to_stderr()
    raise SystemExit(main())
