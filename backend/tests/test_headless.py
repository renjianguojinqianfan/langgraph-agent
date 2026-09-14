"""Offline tests for the P0-C headless entry (backend/headless.py).

Everything runs on a scripted mock LLM over ``make_settings(tmp_path)`` — no
network, no key, no real ``data/``. Covers: a run reaching COMPLETED with the
artifact dropped in ``--dir``; the JSON result contract; the ``--auto-approve``
confirmation-gate bypass (and its contrast: without it the run parks and is
stopped -> INTERRUPTED); exit-code mapping; FAILED propagation; settings
building; the arg parser; and the ``--check`` offline smoke.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from backend.config import Settings
from backend.core.llm.client import LLMClient, LLMResponse, MockLLMClient
from backend.core.tools.base import BaseTool, ToolResult
from backend.headless import (
    DEFAULT_TIMEOUT,
    EXIT_COMPLETED,
    EXIT_FAILED,
    EXIT_INTERRUPTED,
    EXIT_USAGE,
    _build_settings,
    _exit_code_for,
    _run_once,
    build_parser,
    main,
    run_check,
)
from backend.tests.conftest import make_settings


# ── helpers ──────────────────────────────────────────────────────────────────
class _Probe(BaseTool):
    """A ``requires_confirm`` tool: parks the run unless the gate is bypassed."""

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
                "name": "file_io",
                "arguments": {
                    "action": "write",
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


# ── confirmation-gate bypass ─────────────────────────────────────────────────
def test_auto_approve_bypasses_confirm_gate(tmp_path: Path) -> None:
    work = tmp_path / "work"
    settings = _settings(tmp_path, work)
    result = _run_once(
        settings, "call probe", work,
        auto_approve=True, timeout=60.0, llm_client=_probe_mock(),
        tools=[_Probe(settings)],
    )
    assert result["status"] == "COMPLETED"
    assert result["exit_code"] == EXIT_COMPLETED


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
