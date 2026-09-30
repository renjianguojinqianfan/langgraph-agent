"""Offline tests for the P0-C headless entry (backend/headless.py).

Everything runs on a scripted mock LLM over ``make_settings(tmp_path)`` — no
network, no key, no real ``data/``. Covers: a run reaching COMPLETED with the
artifact dropped in ``--dir``; the JSON result contract; the ``--auto-approve``
confirmation-gate auto-clear (and its contrast: without it the run parks and is
stopped -> INTERRUPTED); exit-code mapping; FAILED propagation; settings
building; the arg parser; and the ``--check`` offline smoke.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from backend.config import Settings
from backend.core.agent.nodes import CONFIRM_AUTO_APPROVED
from backend.core.llm.client import LLMClient, LLMResponse, MockLLMClient
from backend.core.tools.base import BaseTool, ToolResult
from backend.headless import (
    _RESULT_KEYS,
    DEFAULT_TIMEOUT,
    EXIT_COMPLETED,
    EXIT_FAILED,
    EXIT_INTERRUPTED,
    EXIT_USAGE,
    _build_settings,
    _emit,
    _exit_code_for,
    _force_utf8_stdio,
    _run_once,
    build_parser,
    main,
    run_check,
)
from backend.tests.conftest import make_settings


# ── helpers ──────────────────────────────────────────────────────────────────
class _Probe(BaseTool):
    """A ``requires_confirm`` tool: parks the run until a verdict, or auto-clears."""

    name = "probe_danger"
    description = "confirm-requiring probe (test only)"
    args_schema: Dict[str, Any] = {"type": "object", "properties": {}}
    requires_confirm = True

    def run(self, **kwargs: Any) -> ToolResult:
        return ToolResult(success=True, data="probe-ok")


class _RaisingExec(LLMClient):
    """Plans fine, then blows up on the executor call -> task ends FAILED."""

    def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        if tools:
            raise RuntimeError("boom")
        return LLMResponse(content=json.dumps(["do a step"]))

    def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Iterator[LLMResponse]:
        yield self.complete(messages, tools, **kwargs)


def _settings(tmp_path: Path, work: Path) -> Settings:
    """Isolated headless-shaped settings rooted at ``tmp_path``.

    risk_scan / checkpoint / subagent are all off so the runs are deterministic
    and fast (no risk-pause block, no sqlite store, no subtask threads).
    """
    return make_settings(
        tmp_path,
        artifacts_dir=str(work),
        trace_dir=str(tmp_path / "traces"),
        risk_scan_enabled=False,
        checkpoint_enabled=False,
        subagent_enabled=False,
    )


def _write_mock() -> MockLLMClient:
    return MockLLMClient(
        plan=["Write the file"],
        tool_calls=[
            {
                "id": "c1",
                "name": "write",
                "arguments": {
                    "path": "hello.txt",
                    "content": "hi from headless",
                },
            }
        ],
        final_answer="wrote hello.txt",
    )


def _probe_mock() -> MockLLMClient:
    return MockLLMClient(
        plan=["Call the gated probe"],
        tool_calls=[{"id": "p1", "name": "probe_danger", "arguments": {}}],
        final_answer="probe done",
    )


# ── happy path ───────────────────────────────────────────────────────────────
def test_run_once_completes_and_writes_artifact(tmp_path: Path) -> None:
    work = tmp_path / "work"
    settings = _settings(tmp_path, work)
    result = _run_once(
        settings, "write hello", work,
        auto_approve=True, timeout=60.0, llm_client=_write_mock(),
    )
    assert result["status"] == "COMPLETED"
    assert result["exit_code"] == EXIT_COMPLETED
    assert (work / "hello.txt").exists()
    assert result["artifacts"] and result["artifacts"][0]["filename"] == "hello.txt"
    assert result["artifacts"][0]["path"] == str(work / "hello.txt")
    trace_path = Path(str(result["trace_path"]))
    assert trace_path.exists() and trace_path.read_text(encoding="utf-8").strip()


def test_result_json_contract(tmp_path: Path) -> None:
    work = tmp_path / "work"
    settings = _settings(tmp_path, work)
    result = _run_once(
        settings, "write hello", work,
        auto_approve=True, timeout=60.0, llm_client=_write_mock(),
    )
    for key in (
        "task_id", "status", "exit_code", "workdir", "model", "final_answer",
        "artifacts", "trace_path", "trace_exists", "steps", "timed_out", "error",
    ):
        assert key in result, f"missing key {key}"
    json.loads(json.dumps(result))  # must round-trip as JSON
    assert result["timed_out"] is False
    assert result["trace_exists"] is True
    assert result["steps"] >= 1


# ── confirmation-gate behaviour under --auto-approve ─────────────────────────
def test_auto_approve_auto_clears_confirm_gate(tmp_path: Path) -> None:
    """#57 AC6：不 parking，但判定照算、闸门照进，终态是 ``auto_approved``。

    改名前这条只断言 COMPLETED ——「整块旁路」时代那就足够。旁路换成自动放行之后，
    「跑完了」不再是证据（判定被跳开也能跑完）；证据在 trace 里：``tool_call`` 带
    need_confirm（判定算了）、``human_confirm_resolved`` 带 auto_approved（闸门进了），
    而 ``human_confirm_required`` 一条都不许有（没人被问，也不许发假 ask）。
    """
    work = tmp_path / "work"
    settings = _settings(tmp_path, work)
    result = _run_once(
        settings, "call probe", work,
        auto_approve=True, timeout=60.0, llm_client=_probe_mock(),
        tools=[_Probe(settings)],
    )
    assert result["status"] == "COMPLETED"
    assert result["exit_code"] == EXIT_COMPLETED

    events = [
        json.loads(line)
        for line in Path(str(result["trace_path"])).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    calls = [e for e in events if e["type"] == "tool_call"]
    resolved = [e for e in events if e["type"] == "human_confirm_resolved"]
    asked = [e for e in events if e["type"] == "human_confirm_required"]
    # 判定确实算了（记录体带 need_confirm），闸门确实进了。
    assert calls and calls[-1]["data"]["need_confirm"] is True, "评测态把判定整块跳开了"
    assert len(resolved) == 1
    assert resolved[0]["data"]["outcome"] == CONFIRM_AUTO_APPROVED
    # 「向一个人发问」这件事没发生——真人不在，不许发假 ask 事件。
    assert asked == []
    # 自动放行确实放行：这一次调用跑了，且不是被跳过。
    results = [e for e in events if e["type"] == "tool_result"]
    assert results and results[-1]["data"]["status"] == "success"


def test_gate_parks_without_auto_approve(tmp_path: Path) -> None:
    """Contrast: with the gate armed the run parks, then is stopped -> INTERRUPTED."""
    work = tmp_path / "work"
    settings = _settings(tmp_path, work)
    result = _run_once(
        settings, "call probe", work,
        auto_approve=False, timeout=2.0, llm_client=_probe_mock(),
        tools=[_Probe(settings)],
    )
    assert result["status"] == "INTERRUPTED"
    assert result["timed_out"] is True
    assert result["exit_code"] == EXIT_INTERRUPTED


# ── failure + exit codes ─────────────────────────────────────────────────────
def test_failed_run_maps_to_exit_1(tmp_path: Path) -> None:
    work = tmp_path / "work"
    settings = _settings(tmp_path, work)
    result = _run_once(
        settings, "do something", work,
        auto_approve=True, timeout=60.0, llm_client=_RaisingExec(),
    )
    assert result["status"] == "FAILED"
    assert result["exit_code"] == EXIT_FAILED


def test_exit_code_mapping() -> None:
    assert _exit_code_for("COMPLETED", False) == EXIT_COMPLETED
    assert _exit_code_for("FAILED", False) == EXIT_FAILED
    assert _exit_code_for("INTERRUPTED", False) == EXIT_INTERRUPTED
    assert _exit_code_for("COMPLETED", True) == EXIT_INTERRUPTED  # timeout wins
    assert _exit_code_for(None, False) == EXIT_FAILED


# ── settings / parser ────────────────────────────────────────────────────────
def test_build_settings_maps_workdir_and_disables(tmp_path: Path) -> None:
    work = tmp_path / "w"
    run_root = tmp_path / "r"
    s = _build_settings(work, run_root, None, None)
    assert s.artifacts_path == work
    assert s.checkpoint_enabled is False
    assert s.risk_scan_enabled is False
    assert s.trace_path == run_root / "traces"


def test_build_settings_model_override(tmp_path: Path) -> None:
    work = tmp_path / "w"
    run_root = tmp_path / "r"
    s = _build_settings(work, run_root, "deepseek-flash", "https://api.deepseek.com")
    assert s.llm_model == "deepseek-flash"
    assert s.use_mock_llm is False
    assert s.llm_base_url == "https://api.deepseek.com"


def test_parser_defaults() -> None:
    args = build_parser().parse_args(["-p", "hi"])
    assert args.prompt == "hi"
    assert args.output == "json"
    assert args.timeout == DEFAULT_TIMEOUT
    assert args.auto_approve is False
    assert args.check is False


def test_yolo_alias_maps_to_auto_approve() -> None:
    # roadmap-pawbench §四: harness feeds every contestant the unified form
    # `-p "<prompt>" --dir <workdir> --yolo --output json`.
    args = build_parser().parse_args(["-p", "hi", "--yolo"])
    assert args.auto_approve is True


def test_main_missing_prompt_returns_usage() -> None:
    assert main(["--output", "json"]) == EXIT_USAGE


# ── offline smoke ────────────────────────────────────────────────────────────
def test_run_check_offline_passes() -> None:
    assert run_check() == 0


def test_main_check_dispatch() -> None:
    assert main(["--check"]) == 0


# ── Issue #21: UTF-8 stdio so Windows (GBK) consoles don't crash _emit ───────
def _sample_result(final_answer: str) -> Dict[str, Any]:
    """A minimal, contract-complete result dict for the ``_emit`` tests."""
    return {
        "task_id": "t1",
        "status": "COMPLETED",
        "exit_code": EXIT_COMPLETED,
        "workdir": "C:/wd",
        "model": "deepseek-v4.1-flash",
        "final_answer": final_answer,
        "artifacts": [{"filename": "a.txt", "path": "C:/wd/a.txt"}],
        "trace_path": "C:/tr/t1.jsonl",
        "trace_exists": True,
        "steps": 2,
        "timed_out": False,
        "error": "",
    }


def _gbk_stdout(monkeypatch: Any) -> io.BytesIO:
    """Swap stdout/stderr for GBK wrappers over BytesIO (a Windows console).

    Both streams are patched so ``_force_utf8_stdio`` only ever touches these
    fakes — never pytest's own capture objects.
    """
    buf = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(buf, encoding="gbk"))
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="gbk"))
    return buf


def test_force_utf8_stdio_lets_json_emit_survive_emoji(monkeypatch: Any) -> None:
    """Issue #21: GBK stdout + emoji in final_answer must not crash ``_emit``."""
    buf = _gbk_stdout(monkeypatch)
    _force_utf8_stdio()
    _emit(_sample_result("Task done \u2705"), "json")  # raises UnicodeEncodeError pre-fix
    sys.stdout.flush()
    parsed = json.loads(buf.getvalue().decode("utf-8"))
    assert parsed["final_answer"] == "Task done \u2705"


def test_force_utf8_stdio_lets_text_emit_survive_emoji(monkeypatch: Any) -> None:
    """Same guard for ``--output text`` (prints final_answer raw)."""
    buf = _gbk_stdout(monkeypatch)
    _force_utf8_stdio()
    _emit(_sample_result("\u5b8c\u6210 \u2705"), "text")  # raises UnicodeEncodeError pre-fix
    sys.stdout.flush()
    assert "\u5b8c\u6210 \u2705".encode("utf-8") in buf.getvalue()


def test_force_utf8_stdio_tolerates_stream_without_reconfigure(monkeypatch: Any) -> None:
    """Guard: a replaced stream lacking ``reconfigure()`` must not crash."""

    class _NoReconf:
        def write(self, s: str) -> int:
            return len(s)

        def flush(self) -> None:
            pass

    fake = _NoReconf()
    monkeypatch.setattr(sys, "stdout", fake)
    monkeypatch.setattr(sys, "stderr", fake)
    _force_utf8_stdio()  # must not raise


def test_force_utf8_stdio_swallows_reconfigure_error(monkeypatch: Any) -> None:
    """Guard: a failing ``reconfigure()`` (detached stream) degrades, never raises."""

    class _Boom:
        def reconfigure(self, **kwargs: Any) -> None:
            raise ValueError("stream detached")

        def write(self, s: str) -> int:
            return len(s)

        def flush(self) -> None:
            pass

    boom = _Boom()
    monkeypatch.setattr(sys, "stdout", boom)
    monkeypatch.setattr(sys, "stderr", boom)
    _force_utf8_stdio()  # must not raise


# ── Issue #90 (seam): the console-stream indirection ─────────────────────────
def test_set_console_stream_covers_later_created_loggers(monkeypatch: Any, tmp_path: Path) -> None:
    """The indirection seam: a flip before creation reaches a lazy logger.

    ``set_console_stream`` is the headless-side fix for the leak class the
    cold-start test below reproduces end-to-end; this pins the mechanism itself
    in-process (cheap, and it documents that loggers created *after* the flip
    honor it — the old direct ``sys.stdout`` binding did not).
    """
    import backend.utils.logging as agent_logging

    monkeypatch.setattr(agent_logging, "get_settings", lambda: Settings(data_dir=str(tmp_path)))
    buf = io.StringIO()
    monkeypatch.setattr(agent_logging, "_console_stream", buf)
    logger = agent_logging.get_logger("issue90_late_probe")
    logger.info("late-logger-marker")
    assert "late-logger-marker" in buf.getvalue()


# ── Issue #90: cold-start subprocess contract — ONLY the result on stdout ────
# Why a real subprocess and not an in-process assertion: every in-process test
# shares the interpreter, so the ``agent.*`` loggers were created during earlier
# imports and the entry's stream-flip catches them. The production bug is an
# ordering one — :func:`backend.utils.logging.get_logger` bound each lazily
# created ``agent.*`` logger to ``sys.stdout`` at *first use*, i.e. after
# ``__main__`` had already redirected the then-existing handlers (the ticket's
# repro lines: ``agent.tool.resilience`` / ``agent.mcp.client``). Only a cold
# ``python -m backend.headless`` process reproduces that sequence, so this test
# spawns one and asserts the stdout contract itself: first char ``{``, whole
# stdout parses as the result JSON — and the lazy logs still arrive, on stderr.
def _cold_start_env() -> Dict[str, str]:
    """Child-process env: mirrors conftest's offline isolation, then adds the
    Issue #90 trigger (a lazily imported MCP client manager that logs).

    The LangSmith / LangChain self-read switches are *removed* (same policy as
    ``conftest.py``'s pop block): an inherited ``LANGSMITH_TRACING=true`` must
    not turn this cold run into a real egress. ``SEARCH_PROVIDER=serpapi`` with
    no key keeps the default mock's web_search call offline-deterministic.
    ``MCP_SERVERS`` points at a command that cannot exist: ``agent.mcp.client``
    is imported lazily by ``TaskManager._load_mcp_tools`` — after the entry's
    redirect — and logs during the run, which is exactly the leak face.
    """
    env = os.environ.copy()
    for key in (
        "LANGSMITH_TRACING",
        "LANGSMITH_TRACING_V2",
        "LANGCHAIN_TRACING",
        "LANGCHAIN_TRACING_V2",
        "LANGCHAIN_HANDLER",
        "LANGSMITH_API_KEY",
        "LANGCHAIN_API_KEY",
    ):
        env.pop(key, None)
    env.update(
        {
            "USE_MOCK_LLM": "true",
            "LLM_API_KEY": "",
            "LLM_BASE_URL": "",
            "AUX_LLM_ENABLED": "false",
            "AUTH_ENABLED": "false",
            "OPENAPI_ENABLED": "false",
            "GIT_ENABLED": "false",
            "CHECKPOINT_ENABLED": "false",
            "CONTEXT_INJECT_ENABLED": "false",
            "CONTEXT_EVICT_ENABLED": "false",
            "SNAPSHOT_ENABLED": "false",
            "SEARCH_PROVIDER": "serpapi",
            "SERPAPI_KEY": "",
            "MCP_ENABLED": "true",
            "MCP_SERVERS": json.dumps(
                [
                    {
                        "name": "issue90-ghost",
                        "command": "issue90-ghost-mcp-server-not-installed",
                        "args": [],
                        "enabled": True,
                    }
                ]
            ),
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return env


def test_cold_start_output_json_stdout_is_only_the_result(tmp_path: Path) -> None:
    """AC (#90): real CLI, ``--output json`` — stdout is pure parseable JSON."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "backend.headless",
            "-p",
            "write a small report",
            "--dir",
            str(workdir),
            "--auto-approve",
            "--output",
            "json",
            "--timeout",
            "60",
        ],
        cwd=str(root),
        env=_cold_start_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=240,
    )
    # The contract, literally: the result object is the ONLY thing on stdout.
    assert proc.stdout.startswith("{"), (
        f"stdout is not pure JSON — first line: {proc.stdout.splitlines()[:3]!r}"
    )
    result = json.loads(proc.stdout)
    for key in _RESULT_KEYS:
        assert key in result, f"result missing key {key}"
    assert result["status"] == "COMPLETED", result
    assert result["exit_code"] == EXIT_COMPLETED
    assert proc.returncode == EXIT_COMPLETED

    # Review-side note (#90): a clean stdout must not cost us the logs — the
    # lazily created MCP logger's lines have to surface on stderr instead.
    assert "agent.mcp" in proc.stderr, (
        "agent.* logs were dropped instead of repointed at stderr"
    )
