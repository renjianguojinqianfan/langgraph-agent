"""Tests for tool circuit breaker + exponential backoff retry (P0 item 2).

Fully offline: uses tiny backoff values so the suite stays fast. Covers the
CircuitBreaker state machine (closed -> open -> half_open -> closed), the
with_retry helper (retry then succeed / max-retries cap / non-retryable /
exception conversion), and the ToolExecutor wrapper (short-circuit without
executing, circuit_open flag, retries pass-through, tool_circuit_open event,
no-breaker passthrough).
"""

from __future__ import annotations

import time

from backend.config import Settings
from backend.core.tools.base import BaseTool, ToolResult
from backend.core.tools.resilience import CircuitBreaker, ToolExecutor, with_retry


def _settings(**overrides) -> Settings:
    """Settings with tiny backoff so retry tests stay fast."""
    base = {
        "tool_failure_threshold": 3,
        "tool_cooldown_sec": 30,
        "tool_backoff_base": 0.01,
        "tool_backoff_factor": 2,
        "tool_max_retries": 2,
    }
    base.update(overrides)
    return Settings(**base)


class _FlakyTool(BaseTool):
    """Succeeds after ``fail_times`` consecutive failures."""

    name = "flaky_tool"
    description = "flaky test tool"
    args_schema = {}
    retryable = True
    max_retries = None
    circuit_breaker = True

    def __init__(self, fail_times: int = 0, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self.fail_times = fail_times
        self.calls = 0

    def run(self, **kwargs) -> ToolResult:
        self.calls += 1
        if self.calls <= self.fail_times:
            return ToolResult(success=False, error="transient boom")
        return ToolResult(success=True, data={"calls": self.calls})


class _AlwaysFailTool(BaseTool):
    """Always fails; single attempt per dispatch (max_retries=0)."""

    name = "always_fail"
    description = "always fails"
    args_schema = {}
    retryable = True
    max_retries = 0
    circuit_breaker = True

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self.calls = 0

    def run(self, **kwargs) -> ToolResult:
        self.calls += 1
        return ToolResult(success=False, error="nope")


class _NoBreakerTool(_FlakyTool):
    """Deterministic local tool: no retry, no breaker."""

    name = "no_breaker"
    retryable = False
    max_retries = 0
    circuit_breaker = False


# ── CircuitBreaker state machine ──────────────────────────────
def test_breaker_starts_closed_and_allows():
    br = CircuitBreaker(failure_threshold=3, cooldown_sec=30)
    assert br.state == "closed"
    allowed, state = br.allow()
    assert allowed is True
    assert state == "closed"


def test_breaker_opens_after_threshold_failures():
    br = CircuitBreaker(failure_threshold=3, cooldown_sec=30)
    br.record_failure()
    br.record_failure()
    assert br.state == "closed"
    br.record_failure()
    assert br.state == "open"
    assert br.opened_at is not None


def test_breaker_short_circuits_while_open():
    br = CircuitBreaker(failure_threshold=1, cooldown_sec=30)
    br.record_failure()
    assert br.state == "open"
    allowed, state = br.allow()
    assert allowed is False
    assert state == "open"


def test_breaker_half_open_after_cooldown():
    br = CircuitBreaker(failure_threshold=1, cooldown_sec=0.05)
    br.record_failure()
    assert br.state == "open"
    time.sleep(0.06)
    allowed, state = br.allow()
    assert allowed is True
    assert state == "half_open"


def test_breaker_resets_on_success_after_half_open():
    br = CircuitBreaker(failure_threshold=1, cooldown_sec=0.05)
    br.record_failure()
    time.sleep(0.06)
    assert br.allow()[0] is True  # -> half_open
    br.record_success()
    assert br.state == "closed"
    assert br.failure_count == 0


def test_breaker_probe_failure_reopens():
    br = CircuitBreaker(failure_threshold=1, cooldown_sec=0.05)
    br.record_failure()
    time.sleep(0.06)
    assert br.allow()[0] is True  # -> half_open
    br.record_failure()  # probe failed -> reopen
    assert br.state == "open"
    assert br.opened_at is not None


def test_breaker_success_in_closed_state_resets_count():
    br = CircuitBreaker(failure_threshold=3, cooldown_sec=30)
    br.record_failure()
    br.record_failure()
    br.record_success()
    assert br.failure_count == 0
    assert br.state == "closed"


# ── with_retry ────────────────────────────────────────────────
def test_with_retry_retries_then_succeeds():
    calls = {"n": 0}

    def fn(**kwargs) -> ToolResult:
        calls["n"] += 1
        if calls["n"] < 3:
            return ToolResult(success=False, error="x")
        return ToolResult(success=True, data={"n": calls["n"]})

    res = with_retry(fn, max_retries=3, retryable=True, backoff_base=0.01)
    assert res.success is True
    assert res.retries == 2
    assert calls["n"] == 3


def test_with_retry_hits_max_retries():
    calls = {"n": 0}

    def fn(**kwargs) -> ToolResult:
        calls["n"] += 1
        return ToolResult(success=False, error="always")

    res = with_retry(fn, max_retries=2, retryable=True, backoff_base=0.01)
    assert res.success is False
    assert calls["n"] == 3  # 1 initial + 2 retries
    assert res.retries == 2


def test_with_retry_no_retry_when_not_retryable():
    calls = {"n": 0}

    def fn(**kwargs) -> ToolResult:
        calls["n"] += 1
        return ToolResult(success=False, error="x")

    res = with_retry(fn, max_retries=5, retryable=False, backoff_base=0.01)
    assert res.success is False
    assert calls["n"] == 1
    assert res.retries == 0


def test_with_retry_converts_exception():
    def fn(**kwargs) -> ToolResult:
        raise ValueError("boom")

    res = with_retry(fn, max_retries=1, retryable=True, backoff_base=0.01)
    assert res.success is False
    assert "boom" in res.error
    assert res.retries == 1


# ── ToolExecutor ──────────────────────────────────────────────
def test_executor_short_circuits_after_threshold():
    settings = _settings(tool_failure_threshold=2)
    tool = _AlwaysFailTool(settings)
    events = []
    ex = ToolExecutor(settings, publish_fn=lambda t, d: events.append((t, d)))

    r1 = ex.dispatch(tool, x=1)  # failure 1
    r2 = ex.dispatch(tool, x=2)  # failure 2 -> opens
    r3 = ex.dispatch(tool, x=3)  # short-circuited

    assert r1.success is False and r1.circuit_open is False
    assert r2.success is False and r2.circuit_open is False
    assert r3.success is False
    assert r3.circuit_open is True
    assert tool.calls == 2  # the 3rd call was NOT executed
    assert any(t == "tool_circuit_open" for t, _ in events)


def test_executor_publishes_circuit_open_event_fields():
    settings = _settings(tool_failure_threshold=1, tool_cooldown_sec=42)
    tool = _AlwaysFailTool(settings)
    events = []
    ex = ToolExecutor(settings, publish_fn=lambda t, d: events.append((t, d)))

    ex.dispatch(tool, x=1)  # fails -> opens
    ex.dispatch(tool, x=2)  # short-circuited -> event

    evs = [d for t, d in events if t == "tool_circuit_open"]
    assert evs
    assert evs[0]["tool_name"] == "always_fail"
    assert evs[0]["cooldown_sec"] == 42


def test_executor_recovers_after_cooldown_half_open():
    settings = _settings(tool_failure_threshold=1, tool_cooldown_sec=30)
    tool = _FlakyTool(fail_times=1, settings=settings)  # next call succeeds
    ex = ToolExecutor(settings)

    ex.dispatch(tool, x=1)  # fails -> opens
    # Shorten the cooldown to exercise the half-open probe quickly.
    ex._breakers["flaky_tool"].cooldown_sec = 0.05
    time.sleep(0.06)
    res = ex.dispatch(tool, x=2)  # half-open probe succeeds
    assert res.success is True
    assert res.circuit_open is False
    assert ex._breakers["flaky_tool"].state == "closed"


def test_executor_retries_flaky_tool_and_reports_retries():
    settings = _settings(tool_max_retries=2)
    tool = _FlakyTool(fail_times=2, settings=settings)
    ex = ToolExecutor(settings)
    res = ex.dispatch(tool, x=1)
    assert res.success is True
    assert res.retries == 2
    assert tool.calls == 3


def test_executor_no_breaker_passthrough():
    settings = _settings()
    tool = _NoBreakerTool(fail_times=0, settings=settings)
    ex = ToolExecutor(settings)
    res = ex.dispatch(tool, x=1)
    assert res.success is True
    assert res.circuit_open is False
    assert res.retries == 0
    assert tool.calls == 1
