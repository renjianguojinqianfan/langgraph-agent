"""Tool resilience: circuit breaker + exponential backoff retry (P0 item 2).

A thin *wrapper layer* that never changes the :class:`BaseTool.run` contract:

* :class:`CircuitBreaker` — per-tool failure state machine
  ``closed -> open -> half_open -> closed``;
* :func:`with_retry` — generic exponential-backoff retry around any callable
  returning a :class:`ToolResult`;
* :class:`ToolExecutor` — combines both and emits a ``tool_circuit_open`` event
  when a dispatch is short-circuited while the circuit is open.

The kernel's ``tool_node`` calls ``ToolExecutor.dispatch(tool, **input)``
instead of ``tool.run(**input)``. Tools whose ``circuit_breaker=False`` simply
bypass the breaker with zero added latency; tools whose ``retryable=False``
never retry.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional, Tuple

from ...config import Settings
from ...utils.logging import get_logger
from .base import BaseTool, ToolResult

logger = get_logger("tool.resilience")

# Circuit breaker states (runtime only; never persisted).
CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


class CircuitBreaker:
    """Failure circuit breaker for one tool.

    * ``closed`` — normal operation; every failure increments ``failure_count``;
      when it reaches ``failure_threshold`` the circuit opens.
    * ``open`` — calls are short-circuited until ``cooldown_sec`` elapses, then
      the next call transitions to ``half_open`` and is allowed through as a
      single probe.
    * ``half_open`` — the probe either resets the breaker (``record_success``)
      or re-opens it (``record_failure``).
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_sec: float = 30.0,
    ) -> None:
        self.failure_threshold: int = max(1, int(failure_threshold))
        self.cooldown_sec: float = float(cooldown_sec)
        self.state: str = CLOSED
        self.failure_count: int = 0
        self.opened_at: Optional[float] = None

    def allow(self) -> Tuple[bool, str]:
        """Return ``(allowed, state)`` for the next dispatch."""
        now = time.time()
        if self.state == OPEN:
            if self.opened_at is not None and (now - self.opened_at) >= self.cooldown_sec:
                # Cooldown elapsed: promote to half-open and allow one probe.
                self.state = HALF_OPEN
                return True, HALF_OPEN
            return False, OPEN
        if self.state == HALF_OPEN:
            # A probe is already in flight / allowed; do not block it.
            return True, HALF_OPEN
        return True, CLOSED

    def record_success(self) -> None:
        """A dispatch succeeded: reset the breaker."""
        self.failure_count = 0
        if self.state in (OPEN, HALF_OPEN):
            self.state = CLOSED
        self.opened_at = None

    def record_failure(self) -> None:
        """A dispatch failed (counted once per dispatch, not per retry)."""
        self.failure_count += 1
        if self.state == HALF_OPEN:
            # Probe failed: reopen with a fresh cooldown.
            self.state = OPEN
            self.opened_at = time.time()
            logger.warning("circuit probe failed; reopened (failures=%d)", self.failure_count)
            return
        if self.failure_count >= self.failure_threshold:
            self.state = OPEN
            self.opened_at = time.time()
            logger.warning(
                "circuit OPEN for tool (failures=%d threshold=%d)",
                self.failure_count,
                self.failure_threshold,
            )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<CircuitBreaker state={self.state} failures={self.failure_count} "
            f"threshold={self.failure_threshold} cooldown={self.cooldown_sec}s>"
        )


def with_retry(
    fn: Callable[..., ToolResult],
    *,
    max_retries: int,
    retryable: bool,
    backoff_base: float = 1.0,
    backoff_factor: float = 2.0,
    **kwargs: Any,
) -> ToolResult:
    """Call ``fn(**kwargs)`` with exponential backoff retries.

    * A call "fails" when it returns ``success=False`` or raises — exceptions
      are converted to a failed :class:`ToolResult` (the kernel never crashes).
    * Retries happen only when ``retryable`` is True, up to ``max_retries``
      extra attempts (total attempts = ``1 + max_retries``).
    * ``delay = backoff_base * (backoff_factor ** attempt)``.
    * The returned :class:`ToolResult` carries the actual number of retries in
      ``retries``.
    """
    total_attempts = 1 + (max_retries if retryable else 0)
    last: Optional[ToolResult] = None
    for attempt in range(total_attempts):
        try:
            last = fn(**kwargs)
        except Exception as exc:
            last = ToolResult(success=False, error=str(exc))
        if last.success:
            last.retries = attempt
            return last
        if attempt < total_attempts - 1:
            delay = float(backoff_base) * (float(backoff_factor) ** attempt)
            logger.info(
                "call failed (attempt %d/%d); retrying in %.2fs",
                attempt + 1,
                total_attempts,
                delay,
            )
            time.sleep(delay)
    if last is None:  # pragma: no cover - defensive, total_attempts >= 1
        last = ToolResult(success=False, error="no attempt made")
    last.retries = total_attempts - 1
    return last


class ToolExecutor:
    """Wrapper integrating circuit breaker + retry around ``BaseTool.run``.

    ``dispatch`` never raises for tool failures; it always returns a
    :class:`ToolResult` (possibly with ``circuit_open=True`` / ``retries>0``).
    """

    def __init__(
        self,
        settings: Settings,
        publish_fn: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        self._settings = settings
        self._publish = publish_fn or (lambda _event_type, _data: None)
        self._breakers: Dict[str, CircuitBreaker] = {}

    def _breaker_for(self, tool: BaseTool) -> CircuitBreaker:
        br = self._breakers.get(tool.name)
        if br is None:
            br = CircuitBreaker(
                failure_threshold=self._settings.tool_failure_threshold,
                cooldown_sec=self._settings.tool_cooldown_sec,
            )
            self._breakers[tool.name] = br
        return br

    def _effective_max_retries(self, tool: BaseTool) -> int:
        if tool.max_retries is not None:
            return int(tool.max_retries)
        return int(self._settings.tool_max_retries)

    def dispatch(self, tool: BaseTool, **kwargs: Any) -> ToolResult:
        """Execute ``tool`` with per-tool circuit breaker + retry policy."""
        max_retries = self._effective_max_retries(tool)

        if not tool.circuit_breaker:
            # No breaker: plain retry policy only (retryable=False => 1 attempt).
            return with_retry(
                tool.run,
                max_retries=max_retries,
                retryable=tool.retryable,
                backoff_base=self._settings.tool_backoff_base,
                backoff_factor=self._settings.tool_backoff_factor,
                **kwargs,
            )

        breaker = self._breaker_for(tool)
        allowed, state = breaker.allow()
        if not allowed:
            # Short-circuit: the tool is NOT executed.
            self._publish(
                "tool_circuit_open",
                {"tool_name": tool.name, "cooldown_sec": int(breaker.cooldown_sec)},
            )
            return ToolResult(
                success=False,
                error=f"circuit open: {tool.name}",
                circuit_open=True,
            )

        result = with_retry(
            tool.run,
            max_retries=max_retries,
            retryable=tool.retryable,
            backoff_base=self._settings.tool_backoff_base,
            backoff_factor=self._settings.tool_backoff_factor,
            **kwargs,
        )
        # Counted once per dispatch (not once per retry).
        if result.success:
            breaker.record_success()
        else:
            breaker.record_failure()
        return result
