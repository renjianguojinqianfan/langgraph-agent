"""Model-call capture for the existing task trace (#333 S7).

One immutable :class:`LLMCallScope` is seated by the caller (main agent,
subagent, compaction) around ``LLMClient.complete``. The base client turns
that scope into a logical call id; the provider adapters capture the *actual*
HTTP request/response/error per SDK attempt, so retries are visible as
``attempt`` 1..N.

Records are log-only: they are appended directly to the task's existing
``data/traces/<task_id>.jsonl`` through :class:`TraceRecorder.log_event`, not
published on the bus. The bus only carries a lightweight summary event, so the
web UI can show a pointer without receiving prompts or responses.

Interruption: every HTTP attempt first appends an ``aborted`` placeholder
(``pending=true``) before the request leaves the process. A killed process
therefore leaves evidence of the in-flight call; a normal end appends the
final record with the same ``(call_id, attempt)`` and ``pending=false``.
Readers take the last line for an attempt. No response is invented.

Redaction only touches the trace copy: auth headers and sensitive URL query
values are dropped/masked, known secrets are replaced in every string, and the
request actually sent is never modified.
"""

from __future__ import annotations

import contextvars
import json
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ...utils.logging import get_logger

logger = get_logger("llm.trace")

#: Trace copy bound for one request body (characters of serialized JSON).
#: Responses and errors stay complete; only the request preview is bounded.
REQUEST_TRACE_LIMIT = 128 * 1024

_SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "api-key",
        "x-api-key",
        "x-goog-api-key",
        "cookie",
        "set-cookie",
    }
)
_SENSITIVE_QUERY = frozenset(
    {"api_key", "apikey", "api-key", "key", "token", "access_token", "auth"}
)

TraceSink = Callable[[str, Dict[str, Any]], bool]
SummaryPublisher = Callable[[Dict[str, Any]], Any]
Redactor = Callable[[str], str]


@dataclass(frozen=True)
class LLMCallScope:
    """Who owns one logical call and where its trace copy goes."""

    owner_task_id: str
    source: str  # main | subagent | compaction
    round_no: Optional[int] = None
    step: Optional[int] = None
    sink: Optional[TraceSink] = None
    publish_summary: Optional[SummaryPublisher] = None
    redact: Optional[Redactor] = None


_scope: contextvars.ContextVar[Optional[LLMCallScope]] = contextvars.ContextVar(
    "qianfan_llm_scope", default=None
)
_active_call: contextvars.ContextVar[Optional["LLMCallRecorder"]] = contextvars.ContextVar(
    "qianfan_llm_active_call", default=None
)


@contextmanager
def llm_call_scope(
    *,
    owner_task_id: str,
    source: str,
    round_no: Optional[int] = None,
    step: Optional[int] = None,
    sink: Optional[TraceSink] = None,
    publish_summary: Optional[SummaryPublisher] = None,
    redact: Optional[Redactor] = None,
) -> Iterator[None]:
    """Seat the current call site's immutable scope for one ``complete``."""
    token = _scope.set(
        LLMCallScope(
            owner_task_id=owner_task_id,
            source=source,
            round_no=round_no,
            step=step,
            sink=sink,
            publish_summary=publish_summary,
            redact=redact,
        )
    )
    try:
        yield
    finally:
        _scope.reset(token)


def current_scope() -> Optional[LLMCallScope]:
    return _scope.get()


def current_active_call() -> Optional["LLMCallRecorder"]:
    """The call whose HTTP hooks are running in this thread (adapter seam)."""
    return _active_call.get()


def set_active_call(recorder: Optional["LLMCallRecorder"]) -> contextvars.Token:
    return _active_call.set(recorder)


def reset_active_call(token: contextvars.Token) -> None:
    _active_call.reset(token)


def capture_http_request(request: Any) -> None:
    """httpx ``request`` event hook: one real attempt leaves the process."""
    call = _active_call.get()
    if call is not None:
        call.begin_attempt(request)


def capture_http_response(response: Any) -> None:
    """httpx ``response`` event hook: keep the body for the attempt record."""
    call = _active_call.get()
    if call is not None:
        call.capture_response(response)


def _json_default(value: Any) -> str:
    return repr(value)


def _redact_strings(value: Any, redact: Optional[Redactor]) -> Any:
    if isinstance(value, str):
        return redact(value) if redact is not None else value
    if isinstance(value, list):
        return [_redact_strings(item, redact) for item in value]
    if isinstance(value, tuple):
        return [_redact_strings(item, redact) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _redact_strings(item, redact) for key, item in value.items()
        }
    return value


def _redact_url(url: str, redact: Optional[Redactor]) -> str:
    try:
        parts = urlsplit(url)
        pairs = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            pairs.append((key, "[redacted]" if key.lower() in _SENSITIVE_QUERY else value))
        cleaned = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(pairs), parts.fragment)
        )
    except ValueError:
        cleaned = url
    return redact(cleaned) if redact is not None else cleaned


def _headers_copy(headers: Any) -> Dict[str, str]:
    copied: Dict[str, str] = {}
    for key, value in (headers or {}).items():
        lowered = str(key).lower()
        if lowered in _SENSITIVE_HEADERS:
            continue
        copied[str(key)] = str(value)
    return copied


def _decode_body(raw: bytes, encoding: str = "utf-8") -> Any:
    text = raw.decode(encoding, errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


def _http_request(request: Any, redact: Optional[Redactor]) -> Dict[str, Any]:
    body = _decode_body(request.content or b"")
    return {
        "method": str(request.method),
        "url": _redact_url(str(request.url), redact),
        "headers": _headers_copy(request.headers),
        "body": _redact_strings(body, redact),
    }


def _http_response(response: Any, redact: Optional[Redactor]) -> Dict[str, Any]:
    try:
        response.read()
    except Exception:  # pragma: no cover - a broken stream is still an answer
        pass
    body = _decode_body(response.content or b"")
    return {
        "status": int(response.status_code),
        "headers": _headers_copy(response.headers),
        "body": _redact_strings(body, redact),
    }


def _usage_from(response: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not response:
        return None
    body = response.get("body")
    if isinstance(body, dict):
        usage = body.get("usage")
        if isinstance(usage, dict):
            return usage
    return None


def _bounded_request(request: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
    try:
        encoded = json.dumps(request, ensure_ascii=False, default=_json_default)
    except Exception:
        return {"preview": repr(request)}, True
    if len(encoded) <= REQUEST_TRACE_LIMIT:
        return request, False
    return {"truncated": True, "preview": encoded[:REQUEST_TRACE_LIMIT]}, True


@dataclass
class _Attempt:
    number: int
    started_at: float
    request: Dict[str, Any]
    response: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    recorded: bool = False


@dataclass
class LLMCallRecorder:
    """One logical call: attempts, trace records, and the lightweight summary."""

    scope: LLMCallScope
    model: str
    protocol: str
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: float = field(default_factory=time.time)
    _attempts: List[_Attempt] = field(default_factory=list)
    _finished: bool = False
    _fallback_request: Dict[str, Any] = field(default_factory=dict)
    _record_failed: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ── adapter hooks ──
    def begin_attempt(self, request: Any) -> None:
        """A request is about to leave: keep an aborted placeholder first."""
        try:
            snapshot = _http_request(request, self.scope.redact)
        except Exception:  # pragma: no cover - capture must never block I/O
            snapshot = {"error": "request capture failed"}
        with self._lock:
            if self._finished:
                return
            if self._attempts and not self._attempts[-1].recorded:
                if self._attempts[-1].response is None:
                    self._finalize_open("error", "retried before a response arrived")
                else:
                    self._finalize_open("ok", None)
            attempt = _Attempt(
                number=len(self._attempts) + 1,
                started_at=time.time(),
                request=snapshot,
            )
            self._attempts.append(attempt)
            self._write(attempt, status="aborted", pending=True)

    def capture_response(self, response: Any) -> None:
        """A response arrived; finalize it when the SDK ends or retries."""
        try:
            snapshot = _http_response(response, self.scope.redact)
        except Exception:  # pragma: no cover - capture must never block I/O
            snapshot = {"error": "response capture failed"}
        with self._lock:
            if self._finished or not self._attempts:
                return
            self._attempts[-1].response = snapshot

    def set_fallback_request(self, request: Dict[str, Any]) -> None:
        """Adapter-prepared request for paths with no HTTP hook (Mock, stubs)."""
        self._fallback_request = _redact_strings(request, self.scope.redact)

    def finish(self, status: str, error: Optional[str] = None) -> None:
        """Finalize the call; never raises into the model result."""
        try:
            self._finish(status, error)
        except Exception:  # pragma: no cover - recording is best-effort
            logger.exception("llm_call finalize failed for %s", self.call_id)

    def _finish(self, status: str, error: Optional[str]) -> None:
        with self._lock:
            if self._finished:
                return
            self._finished = True
            if not self._attempts:
                self._attempts.append(
                    _Attempt(number=1, started_at=self.started_at, request=self._fallback_request)
                )
            message = None
            if error:
                message = self.scope.redact(error) if self.scope.redact else error
            self._finalize_open(status, message)
            final_status = "error" if self._attempts[-1].error else status
            if status == "aborted":
                final_status = "aborted"
            self._emit_summary(final_status)

    def _finalize_open(self, status: str, error: Optional[str]) -> None:
        """Append the final line for the last attempt (if not already final)."""
        if not self._attempts:
            return
        attempt = self._attempts[-1]
        if attempt.recorded:
            return
        if attempt.response is not None:
            if status == "ok" and int(attempt.response.get("status", 0)) >= 400:
                status = "error"
            if status == "error" and not error:
                error = f"HTTP {attempt.response.get('status')}"
        attempt.error = error
        self._write(attempt, status=status, pending=False)
        attempt.recorded = True

    def _write(self, attempt: _Attempt, *, status: str, pending: bool) -> None:
        request, truncated = _bounded_request(attempt.request)
        data: Dict[str, Any] = {
            "call_id": self.call_id,
            "attempt": attempt.number,
            "owner_task_id": self.scope.owner_task_id,
            "source": self.scope.source,
            "round": self.scope.round_no,
            "step": self.scope.step,
            "model": self.model,
            "protocol": self.protocol,
            "started_at": attempt.started_at,
            "ended_at": None if pending else time.time(),
            "status": status,
            "request": request,
            "response": attempt.response,
            "error": attempt.error,
            "truncated": truncated,
            "usage": _usage_from(attempt.response),
            "pending": pending,
        }
        sink = self.scope.sink
        recorded = False
        if sink is not None:
            try:
                recorded = bool(sink("llm_call", data))
            except Exception:
                logger.exception("llm_call trace sink failed for %s", self.call_id)
        if sink is not None and not recorded and not self._record_failed:
            self._record_failed = True
            logger.warning(
                "llm_call not recorded: task=%s call=%s protocol=%s "
                "(trace unavailable or write failed)",
                self.scope.owner_task_id,
                self.call_id,
                self.protocol,
            )

    def _emit_summary(self, status: str) -> None:
        publisher = self.scope.publish_summary
        if publisher is None:
            return
        payload = {
            "call_id": self.call_id,
            "status": status,
            "summary": f"{self.scope.source} {self.model or self.protocol} {status}",
            "task_id": self.scope.owner_task_id,
            "attempts": len(self._attempts),
        }
        try:
            publisher(payload)
        except Exception:  # pragma: no cover - SSE is best-effort
            logger.exception("llm_call summary publish failed for %s", self.call_id)
