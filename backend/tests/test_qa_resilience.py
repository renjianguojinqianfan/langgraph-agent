"""QA independent edge-case tests — circuit breaker + retry (P0 item 2).

Reviewer-perspective coverage beyond the engineer's suite: exact exponential
backoff delay sequence, tool-level ``max_retries`` override, breaker counting
once per dispatch (not per retry), short-circuit never executing the underlying
tool, and *real* built-in tools behaving per policy (code_exec/file_io: no
retry/no breaker; web_search/http_request: retryable + trippable breaker).
All offline (tiny backoff / monkeypatched sleep, deterministic failure paths).
"""

from __future__ import annotations

import time

from backend.config import Settings
from backend.core.tools.base import BaseTool, ToolResult
from backend.core.tools.code_exec import CodeExecTool
from backend.core.tools.file_io import FileIOTool
from backend.core.tools.http_api import HttpTool
from backend.core.tools.resilience import CircuitBreaker, ToolExecutor, with_retry
from backend.core.tools.web_search import WebSearchTool


def _settings(**overrides) -> Settings:
    base = {
        "tool_failure_threshold": 3,
        "tool_cooldown_sec": 30,
        "tool_backoff_base": 0.01,
        "tool_backoff_factor": 2,
        "tool_max_retries": 2,
    }
    base.update(overrides)
    return Settings(**base)


# ── with_retry: exact backoff delay sequence ──────────────────
def test_with_retry_backoff_delay_sequence(monkeypatch):
    delays = []
    monkeypatch.setattr("backend.core.tools.resilience.time.sleep", delays.append)

    calls = {"n": 0}

    def fn(**kwargs) -> ToolResult:
        calls["n"] += 1
        if calls["n"] < 4:
            return ToolResult(success=False, error="x")
        return ToolResult(success=True, data={"n": calls["n"]})

    res = with_retry(fn, max_retries=5, retryable=True, backoff_base=1.0, backoff_factor=2.0)
    assert res.success is True
    assert calls["n"] == 4
    # delay = base * factor ** attempt  -> 1.0, 2.0, 4.0
    assert delays == [1.0, 2.0, 4.0]


# ── ToolExecutor: tool-level override & counting semantics ────
class _OverrideTool(BaseTool):
    name = "override_tool"
    description = "d"
    args_schema = {}
    retryable = True
    max_retries = 5
    circuit_breaker = True

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self.calls = 0

    def run(self, **kwargs) -> ToolResult:
        self.calls += 1
        return ToolResult(success=False, error="nope")


def test_executor_uses_tool_level_max_retries_override(monkeypatch):
    settings = _settings(tool_max_retries=0)  # global says "no retry"
    tool = _OverrideTool(settings)  # but tool.max_retries=5 wins
    ex = ToolExecutor(settings)
    monkeypatch.setattr("backend.core.tools.resilience.time.sleep", lambda s: None)

    res = ex.dispatch(tool)
    assert res.success is False
    assert tool.calls == 6  # 1 initial + 5 tool-level retries
    assert res.retries == 5


def test_executor_counts_failure_once_per_dispatch():
    settings = _settings(tool_failure_threshold=3)
    calls = {"n": 0}

    class _FailN(BaseTool):
        name = "failn"
        description = "d"
        args_schema = {}
        retryable = True
        max_retries = 3
        circuit_breaker = True

        def run(self, **kwargs) -> ToolResult:
            calls["n"] += 1
            return ToolResult(success=False, error="x")

    tool = _FailN(settings)
    ex = ToolExecutor(settings)
    ex.dispatch(tool)  # 4 internal attempts all fail
    # Counted ONCE for the whole dispatch, not once per retry.
    assert ex._breakers["failn"].failure_count == 1
    assert ex._breakers["failn"].state == "closed"  # threshold=3
    assert calls["n"] == 4


def test_short_circuit_never_executes_underlying_tool():
    settings = _settings(tool_failure_threshold=1, tool_max_retries=0)
    calls = {"n": 0}

    class _Counting(BaseTool):
        name = "counting"
        description = "d"
        args_schema = {}
        retryable = True
        max_retries = 0
        circuit_breaker = True

        def run(self, **kwargs) -> ToolResult:
            calls["n"] += 1
            return ToolResult(success=False, error="x")

    tool = _Counting(settings)
    ex = ToolExecutor(settings)
    ex.dispatch(tool)
    assert calls["n"] == 1  # this failure opened the circuit
    r2 = ex.dispatch(tool)  # short-circuited
    assert r2.circuit_open is True
    assert calls["n"] == 1  # the tool was NOT executed
    assert ex.dispatch(tool).circuit_open is True
    assert calls["n"] == 1


# ── CircuitBreaker: half-open probe failure → reopen → recover ─
class _ProbeFailOnce(BaseTool):
    name = "probe_fail_once"
    description = "d"
    args_schema = {}
    retryable = True
    max_retries = 0
    circuit_breaker = True

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self.calls = 0

    def run(self, **kwargs) -> ToolResult:
        self.calls += 1
        if self.calls == 1:
            return ToolResult(success=False, error="first fail")  # opens
        if self.calls == 2:
            return ToolResult(success=False, error="probe fail")  # reopens
        return ToolResult(success=True, data={})  # second probe succeeds


def test_half_open_probe_failure_reopens_then_second_probe_recovers():
    settings = _settings(tool_failure_threshold=1, tool_cooldown_sec=30, tool_max_retries=0)
    tool = _ProbeFailOnce(settings)
    ex = ToolExecutor(settings)

    ex.dispatch(tool)  # fail -> open
    br = ex._breakers["probe_fail_once"]
    assert br.state == "open"

    br.cooldown_sec = 0.02
    time.sleep(0.03)
    ex.dispatch(tool)  # half-open probe -> fails -> reopen
    assert br.state == "open"

    time.sleep(0.03)  # second cooldown elapses
    res = ex.dispatch(tool)  # half-open probe -> succeeds -> closed
    assert res.success is True
    assert br.state == "closed"


# ── Real built-in tools: policy enforcement ───────────────────
def test_real_code_exec_never_retries_or_breaks():
    settings = _settings(tool_max_retries=5, tool_failure_threshold=1)
    tool = CodeExecTool(settings)
    ex = ToolExecutor(settings)

    assert tool.retryable is False
    assert tool.max_retries == 0
    assert tool.circuit_breaker is False

    r = ex.dispatch(tool, language="python", code="")
    assert r.success is False
    assert r.retries == 0
    assert r.circuit_open is False
    assert "code_exec" not in ex._breakers

    # Repeat failures must NEVER short-circuit a non-breaker tool.
    for _ in range(3):
        r2 = ex.dispatch(tool, language="python", code="")
        assert r2.circuit_open is False
    assert "code_exec" not in ex._breakers


def test_real_file_io_never_retries_or_breaks():
    settings = _settings(tool_max_retries=5, tool_failure_threshold=1)
    tool = FileIOTool(settings)
    ex = ToolExecutor(settings)

    assert tool.retryable is False
    assert tool.max_retries == 0
    assert tool.circuit_breaker is False

    r = ex.dispatch(tool, action="read", path="")
    assert r.success is False
    assert r.retries == 0
    assert r.circuit_open is False
    assert "file_io" not in ex._breakers
    for _ in range(3):
        assert ex.dispatch(tool, action="read", path="").circuit_open is False
    assert "file_io" not in ex._breakers


def test_real_web_search_retryable_and_trips_breaker(monkeypatch):
    settings = _settings(
        tool_max_retries=1, tool_failure_threshold=2, tool_backoff_base=0.001
    )
    tool = WebSearchTool(settings)
    ex = ToolExecutor(settings)
    monkeypatch.setattr("backend.core.tools.resilience.time.sleep", lambda s: None)

    assert tool.retryable is True
    assert tool.circuit_breaker is True
    assert tool.max_retries is None  # inherits global

    r1 = ex.dispatch(tool, query="")  # deterministic failure (no network)
    assert r1.success is False
    assert r1.retries == 1  # retried once (global max_retries=1)

    r2 = ex.dispatch(tool, query="")  # failure #2 -> opens
    assert r2.success is False

    r3 = ex.dispatch(tool, query="")  # short-circuited
    assert r3.circuit_open is True
    assert r3.retries == 0  # never executed -> never retried
    assert ex._breakers["web_search"].state == "open"


def test_real_http_request_retryable_and_trips_breaker(monkeypatch):
    settings = _settings(
        tool_max_retries=1, tool_failure_threshold=2, tool_backoff_base=0.001
    )
    tool = HttpTool(settings)
    ex = ToolExecutor(settings)
    monkeypatch.setattr("backend.core.tools.resilience.time.sleep", lambda s: None)

    assert tool.retryable is True
    assert tool.circuit_breaker is True

    r1 = ex.dispatch(tool, method="GET", url="")
    assert r1.success is False
    assert r1.retries == 1

    r2 = ex.dispatch(tool, method="GET", url="")
    assert r2.success is False
    r3 = ex.dispatch(tool, method="GET", url="")
    assert r3.circuit_open is True
    assert ex._breakers["http_request"].state == "open"
