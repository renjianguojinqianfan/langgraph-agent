"""Local REST/SSE client for the shared backend (#332 S6).

The terminal is a thin client (ADR 0013 decision 2): it never touches
``TaskManager`` / ``Persistence`` / ``EventBus``, only the same HTTP surface the
web page uses. This module is the single place that knows the wire shape —
envelopes, status codes, the SSE frame format — so ``repl`` can stay about the
conversation.

Rules carried here:

* the bearer token lives in the ``Authorization`` header and in memory only —
  never in a URL, a log line or printed output;
* transport failures (refused / dropped / timed out) are
  :class:`BackendUnreachable`; the caller must **not** replay a request that may
  already have been accepted;
* ``401`` is :class:`AuthExpired`, ``409`` is :class:`AdmissionRejected` (the
  backend's admission / mutex verdict, e.g. "task is RUNNING").

``transport`` exists for offline tests (``httpx.MockTransport``); production
callers pass nothing.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List, Optional

from . import server_lock

#: Connect/write are short; the read window covers the SSE heartbeat (15 s) with
#: room to spare, so an idle stream is not mistaken for a dropped one.
DEFAULT_TIMEOUT = (5.0, 60.0)


class ApiError(RuntimeError):
    """A non-2xx answer that is neither 401 nor 409."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"后台返回 HTTP {status}：{detail}")
        self.status = status
        self.detail = detail


class AuthExpired(ApiError):
    """401: the token is missing, rejected or expired."""


class AdmissionRejected(ApiError):
    """409: the backend refused the change (unknown/foreign instance, RUNNING)."""


class BackendUnreachable(RuntimeError):
    """The backend did not answer at all (refused, dropped or timed out)."""


def _authority(host: str, port: int) -> str:
    target = f"[{host}]" if ":" in host else host
    return f"http://{target}:{int(port)}"


def resolve_base_url(settings: Any) -> str:
    """``http://<host>:<port>`` for the live backend, else the configured port.

    The single-instance record is the authority while a live process owns it: a
    backend may have been started on ``--port`` and later reused, and the
    terminal must talk to the port that actually serves.
    """
    host = str(getattr(settings, "host", "") or "127.0.0.1")
    port = int(getattr(settings, "port", 8000))
    record = server_lock.read_lock(server_lock.lock_path(settings))
    if record is not None and server_lock.pid_alive(record.pid):
        port = record.port
    return _authority(host, port)


class ApiClient:
    """One HTTP client bound to one backend (``with``-friendly)."""

    def __init__(
        self,
        base_url: str,
        *,
        token: Optional[str] = None,
        transport: Any = None,
    ) -> None:
        import httpx

        self.base_url = base_url.rstrip("/")
        self._token = token
        self._httpx = httpx
        timeout = httpx.Timeout(
            connect=DEFAULT_TIMEOUT[0], read=DEFAULT_TIMEOUT[1],
            write=DEFAULT_TIMEOUT[0], pool=DEFAULT_TIMEOUT[0],
        )
        self._client = httpx.Client(
            base_url=self.base_url, timeout=timeout, follow_redirects=False,
            transport=transport,
        )

    # ── lifecycle ──
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ApiClient":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    # ── auth ──
    def login(self, secret: str) -> None:
        """Exchange the local secret for a bearer token (kept in memory)."""
        data = self._request("POST", "/api/auth/token", payload={"token": secret}, auth=False)
        token = str((data or {}).get("token") or "")
        if not token:
            raise ApiError(200, "登录响应里没有 token")
        self._token = token

    # ── tasks ──
    def create_task(
        self,
        text: str,
        *,
        workspace: str,
        mode: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"input": text, "workspace": workspace}
        if mode:
            payload["mode"] = mode
        if model:
            payload["model"] = model
        return self._request("POST", "/api/tasks", payload=payload) or {}

    def get_task(self, task_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/api/tasks/{task_id}") or {}

    def list_tasks(self, *, limit: int = 20, directory: str = "current") -> Dict[str, Any]:
        return self._request(
            "GET", "/api/tasks", params={"limit": limit, "dir": directory}
        ) or {}

    def resolve_task(self, ref: str) -> Dict[str, Any]:
        """Full id, or a unique prefix of one (the terminal shows 8 chars)."""
        try:
            return self.get_task(ref)
        except ApiError as exc:
            if exc.status != 404:
                raise
        tasks = self.list_tasks(limit=100, directory="all").get("tasks") or []
        matches = [
            task for task in tasks if str(task.get("id") or "").startswith(ref)
        ]
        if not matches:
            raise ApiError(404, f"找不到任务 {ref}")
        if len(matches) > 1:
            raise ApiError(400, f"前缀 {ref} 匹配多个任务，请给更长的 id")
        return self.get_task(str(matches[0]["id"]))

    def send_message(self, task_id: str, text: str) -> Dict[str, Any]:
        return self._request(
            "POST", f"/api/tasks/{task_id}/messages", payload={"input": text}
        ) or {}

    def stop_task(self, task_id: str) -> Dict[str, Any]:
        return self._request("POST", f"/api/tasks/{task_id}/stop") or {}

    def resume_task(self, task_id: str) -> Dict[str, Any]:
        return self._request("POST", f"/api/tasks/{task_id}/resume") or {}

    def set_mode(self, task_id: str, mode: str) -> Dict[str, Any]:
        return self._request(
            "PUT", f"/api/tasks/{task_id}/mode", payload={"mode": mode}
        ) or {}

    def set_model(self, task_id: str, model: str) -> Dict[str, Any]:
        return self._request(
            "PUT", f"/api/tasks/{task_id}/model", payload={"model": model}
        ) or {}

    # ── events ──
    def events(self, task_id: str) -> Iterator[Dict[str, Any]]:
        """Yield ``{"type", "data"}`` for every SSE frame of one task.

        Ends when the server closes the stream; a dropped connection surfaces as
        :class:`BackendUnreachable` after whatever frames already arrived.
        """
        httpx = self._httpx
        headers = {"Accept": "text/event-stream", **self._auth_headers()}
        try:
            with self._client.stream(
                "GET", f"/api/tasks/{task_id}/events", headers=headers
            ) as response:
                self._raise_for_status(response)
                event_type = ""
                data_lines: List[str] = []
                for line in response.iter_lines():
                    if not line:
                        if event_type:
                            yield {"type": event_type, "data": _parse_data(data_lines)}
                        event_type, data_lines = "", []
                        continue
                    if line.startswith(":"):
                        continue
                    field, _, value = line.partition(":")
                    value = value[1:] if value.startswith(" ") else value
                    if field == "event":
                        event_type = value
                    elif field == "data":
                        data_lines.append(value)
        except httpx.TransportError as exc:
            raise BackendUnreachable(
                f"事件流中断：{self.base_url}（{type(exc).__name__}）"
            ) from exc

    # ── internals ──
    def _auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        auth: bool = True,
    ) -> Any:
        httpx = self._httpx
        try:
            response = self._client.request(
                method,
                path,
                json=payload,
                params=params,
                headers=self._auth_headers() if auth else None,
            )
        except httpx.TransportError as exc:
            raise BackendUnreachable(
                f"无法连接后台 {self.base_url}（{type(exc).__name__}）"
            ) from exc
        return self._decode(response)

    def _decode(self, response: Any) -> Any:
        self._raise_for_status(response)
        try:
            body = response.json()
        except ValueError:
            raise ApiError(response.status_code, "响应不是合法 JSON") from None
        if not isinstance(body, dict):
            raise ApiError(response.status_code, "响应格式不符")
        data = body.get("data")
        return {} if data is None else data

    def _raise_for_status(self, response: Any) -> None:
        status = int(response.status_code)
        if status < 400:
            return
        detail = _error_detail(response)
        if status == 401:
            raise AuthExpired(status, detail)
        if status == 409:
            raise AdmissionRejected(status, detail)
        raise ApiError(status, detail)


def _error_detail(response: Any) -> str:
    try:
        body = response.json()
    except ValueError:
        text = response.text.strip()
        return text or "（无说明）"
    if isinstance(body, dict):
        for key in ("detail", "message", "error"):
            value = body.get(key)
            if value:
                return str(value)
    return json.dumps(body, ensure_ascii=False, default=str)


def _parse_data(lines: List[str]) -> Dict[str, Any]:
    raw = "\n".join(lines).strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {"value": parsed}
