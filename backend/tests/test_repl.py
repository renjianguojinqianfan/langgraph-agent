"""#332 S6: the terminal as a thin client (fake HTTP/SSE transport).

Every case runs the real ``repl_main`` / ``ReplSession`` / ``ApiClient`` against
an ``httpx.MockTransport`` that behaves like the backend: it records requests,
serves an SSE body per subscription and keeps task status. The assertions are
user-visible (printed lines, exit codes, which requests were sent), so the
transport can be replaced by a real backend end-to-end without rewriting them.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import httpx
import pytest

from backend import cli, repl
from backend.repl import (
    PROMPT,
    RENDERED_EVENT_TYPES,
    configure_stdio_utf8,
    render_event,
    repl_main,
)
from backend.tests.conftest import make_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
SENTINEL = "repl-secret-sentinel"


# ── a stateful stand-in for the backend (REST + SSE) ──
class FakeBackend:
    """Answers like the real API; queued SSE bodies are served in order."""

    def __init__(self, *, auth: bool = False, start_running: bool = False) -> None:
        self._lock = threading.Lock()
        self.auth = auth
        self.start_running = start_running
        self.auto_park = True
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.status_plans: Dict[str, List[str]] = {}
        self.streams: List[Dict[str, Any]] = []
        self.requests: List[Tuple[str, str, Dict[str, Any]]] = []
        self.stop_requests: List[str] = []
        self.sent_messages: List[Tuple[str, str]] = []
        self._counter = 0
        self._token = "tok-1"
        #: What ``/api/auth/token`` issues; keep it equal to ``_token`` unless a
        #: case needs a token that is already stale by the time it is used.
        self.issued_token = "tok-1"

    # ── test controls ──
    def queue_stream(self, *events: Any) -> None:
        """Queue one SSE body; ``queue_stream(a, b)`` == ``queue_stream([a, b])``."""
        self.streams.append({"events": _flat(events), "broken": False})

    def queue_broken_stream(self, *events: Any) -> None:
        self.streams.append({"events": _flat(events), "broken": True})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def calls(self, method: str, path: str) -> int:
        return sum(1 for m, p, _ in self.requests if m == method and p == path)

    def add_task(self, *, status: str = "INTERRUPTED", mode: str = "build", title: str = "任务") -> str:
        task_id = self._new_id()
        self.tasks[task_id] = {
            "id": task_id,
            "status": status,
            "mode": mode,
            "title": title,
            "user_input": title,
            "workspace": "E:/work",
        }
        return task_id

    def _new_id(self) -> str:
        self._counter += 1
        return f"task{self._counter:04d}deadbeef"

    # ── transport ──
    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload: Dict[str, Any] = {}
        if request.content:
            try:
                payload = json.loads(request.content)
            except ValueError:
                payload = {}
        with self._lock:
            self.requests.append((request.method, path, payload))

        if path == "/api/auth/token":
            if payload.get("token") != "local-secret":
                return httpx.Response(401, json={"detail": "invalid token"})
            return httpx.Response(200, json={"code": 0, "data": {"token": self.issued_token}})
        if self.auth and request.headers.get("Authorization") != f"Bearer {self._token}":
            return httpx.Response(401, json={"detail": "invalid token"})

        if request.method == "POST" and path == "/api/tasks":
            task_id = self._new_id()
            self.tasks[task_id] = {
                "id": task_id,
                "status": "RUNNING" if self.start_running else "INTERRUPTED",
                "mode": payload.get("mode") or "build",
                "title": payload.get("input") or "",
                "user_input": payload.get("input") or "",
                "workspace": payload.get("workspace") or "",
            }
            return _envelope({"task_id": task_id})

        if request.method == "GET" and path == "/api/tasks":
            tasks = list(self.tasks.values())
            return _envelope({"tasks": tasks})

        if path.startswith("/api/tasks/"):
            task_id = path.split("/")[3]
            task = self.tasks.get(task_id)
            if task is None:
                return httpx.Response(404, json={"detail": "task not found"})
            rest = path[len(f"/api/tasks/{task_id}") :]
            return self._task_route(request, task, rest, payload)

        return httpx.Response(404, json={"detail": f"no route {path}"})

    def _task_route(
        self,
        request: httpx.Request,
        task: Dict[str, Any],
        rest: str,
        payload: Dict[str, Any],
    ) -> httpx.Response:
        task_id = str(task["id"])
        if request.method == "GET" and rest == "":
            plan = self.status_plans.get(task_id)
            if plan:
                task["status"] = plan.pop(0) if len(plan) > 1 else plan[0]
            return _envelope(dict(task))
        if request.method == "GET" and rest == "/events":
            return self._stream_response(request, task)
        if request.method == "POST" and rest == "/messages":
            if task["status"] != "INTERRUPTED":
                return httpx.Response(
                    409,
                    json={"detail": "task is RUNNING; only a parked task can continue"},
                )
            self.sent_messages.append((task_id, str(payload.get("input") or "")))
            task["status"] = "RUNNING"
            return _envelope({"ok": True})
        if request.method == "POST" and rest == "/stop":
            self.stop_requests.append(task_id)
            task["status"] = "INTERRUPTED"
            self.status_plans.pop(task_id, None)
            return _envelope({"ok": True, "status": "INTERRUPTED"})
        if request.method == "POST" and rest == "/resume":
            if task["status"] != "INTERRUPTED":
                return httpx.Response(409, json={"detail": "task is not INTERRUPTED"})
            task["status"] = "RUNNING"
            return _envelope({"ok": True})
        if request.method == "PUT" and rest == "/mode":
            task["mode"] = payload.get("mode")
            return _envelope({"mode": task["mode"]})
        if request.method == "PUT" and rest == "/model":
            task["model"] = payload.get("model")
            return _envelope({"model": task["model"]})
        return httpx.Response(404, json={"detail": f"no route {rest}"})

    def _stream_response(self, request: httpx.Request, task: Dict[str, Any]) -> httpx.Response:
        with self._lock:
            entry: Dict[str, Any] = (
                self.streams.pop(0) if self.streams else {"events": [], "broken": False}
            )
        frames = [
            f"event: {event.get('type')}\ndata: {json.dumps(event.get('data') or {})}\n\n"
            for event in entry["events"]
        ]
        if self.auto_park:
            for event in entry["events"]:
                if event.get("type") == "task_interrupted":
                    task["status"] = "INTERRUPTED"
                elif event.get("type") == "task_failed":
                    task["status"] = "FAILED"

        def body():
            for frame in frames:
                yield frame.encode("utf-8")
            if entry["broken"]:
                raise httpx.ReadError("connection dropped", request=request)

        return httpx.Response(
            200, content=body(), headers={"Content-Type": "text/event-stream"}
        )


def _envelope(data: Dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"code": 0, "data": data, "message": "ok"})


def _flat(events: Tuple[Any, ...]) -> List[Dict[str, Any]]:
    """One event list from ``f(a, b)`` or ``f([a, b])`` in a test call."""
    if len(events) == 1 and isinstance(events[0], list):
        return [dict(event) for event in events[0]]
    return [dict(event) for event in events]


# ── harness ──
def _clock_pair():
    state = {"now": 0.0}

    def clock() -> float:
        return state["now"]

    def sleep(seconds: float) -> None:
        state["now"] += seconds
        time.sleep(0)  # let the SSE reader thread push before the next tick

    return clock, sleep


def _run(
    monkeypatch,
    settings,
    fake: FakeBackend,
    lines: List[str],
    **kwargs,
) -> Tuple[int, List[str]]:
    out: List[str] = []
    prompts: List[str] = []
    iterator = iter(lines)
    clock, sleep = _clock_pair()

    def input_fn(prompt: str) -> str:
        prompts.append(prompt)
        return next(iterator)

    monkeypatch.setattr(repl, "get_settings", lambda: settings)
    code = repl_main(
        [],
        input_fn=input_fn,
        output=out.append,
        sleep=kwargs.pop("sleep", sleep),
        clock=kwargs.pop("clock", clock),
        configure_stdio=False,
        transport=kwargs.pop("transport", fake.transport()),
        ensure=kwargs.pop("ensure", lambda _settings, **kw: (0, settings.port)),
        **kwargs,
    )
    out.extend(f"PROMPT:{prompt}" for prompt in prompts)
    return code, out


def _body(out: List[str]) -> str:
    return "\n".join(line for line in out if not line.startswith("PROMPT:"))


def _final_events(task_id: Any) -> List[Dict[str, Any]]:
    return [
        {"type": "task_created", "data": {"task_id": task_id}},
        {"type": "step_start", "data": {"index": 1, "round": 1}},
        {"type": "final_answer", "data": {"answer": "done"}},
        {"type": "task_interrupted", "data": {"status": "INTERRUPTED"}},
    ]


# ── rendering (D-49 / D-50 + safety) ──
def test_rendered_event_types_are_the_small_face():
    assert RENDERED_EVENT_TYPES == frozenset(
        {
            "task_created",
            "task_resumed",
            "step_start",
            "tool_call",
            "tool_result",
            "final_answer",
            "llm_call",
            "task_interrupted",
            "task_failed",
        }
    )


@pytest.mark.parametrize(
    ("event", "fragment"),
    [
        ({"type": "task_created", "data": {"task_id": "abc12345"}}, "新任务"),
        ({"type": "task_resumed", "data": {"task_id": "abc12345"}}, "续跑"),
        ({"type": "step_start", "data": {"index": 2, "round": 1}}, "第 2 步"),
        (
            {"type": "tool_call", "data": {"tool_name": "write", "input": {"p": "a"}}},
            "write",
        ),
        (
            {
                "type": "tool_result",
                "data": {"tool_name": "write", "status": "success", "output": "ok"},
            },
            "✓",
        ),
        (
            {"type": "tool_result", "data": {"tool_name": "shell", "status": "skipped"}},
            "⊘",
        ),
        ({"type": "final_answer", "data": {"answer": "done"}}, "答: done"),
        (
            {
                "type": "llm_call",
                "data": {
                    "summary": "main m1 ok",
                    "task_id": "t1",
                    "call_id": "c1",
                },
            },
            "/api/tasks/t1/trace?type=llm_call&call_id=c1",
        ),
        ({"type": "task_interrupted", "data": {"status": "INTERRUPTED"}}, "停下"),
        ({"type": "task_failed", "data": {"error": "boom"}}, "boom"),
    ],
)
def test_render_event_known_types(event, fragment):
    line = render_event(event)
    assert line is not None and fragment in line


@pytest.mark.parametrize(
    "kind", ["heartbeat", "context_compressed", "tool_result_evicted", "nonsense"]
)
def test_render_event_drops_non_rendered_types(kind):
    assert render_event({"type": kind, "data": {}}) is None


def test_render_event_never_prints_reasoning_or_unredacted_secrets():
    answered = render_event(
        {
            "type": "final_answer",
            "data": {"answer": "visible", "reasoning": SENTINEL},
        }
    )
    assert answered is not None
    assert "visible" in answered
    assert SENTINEL not in answered

    redacted = render_event(
        {"type": "tool_result", "data": {"tool_name": "shell", "output": SENTINEL}},
        redact=lambda text: text.replace(SENTINEL, "[redacted]"),
    )
    assert redacted is not None
    assert SENTINEL not in redacted and "[redacted]" in redacted


# ── session loop over REST/SSE ──
def test_a_task_round_renders_the_stream_and_parks(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, port=9123)
    fake = FakeBackend()
    fake.queue_stream(*_final_events("task0001deadbeef"))

    code, out = _run(monkeypatch, settings, fake, ["first task", "exit"])

    text = _body(out)
    assert code == 0
    assert "答: done" in text
    assert "■ 停下" in text
    assert text.count("● 新任务") == 1
    # The create carried this terminal's directory (ADR 0013 decision 2).
    create = [r for r in fake.requests if r[0] == "POST" and r[1] == "/api/tasks"][0]
    assert create[2]["workspace"] == os.getcwd()
    assert create[2]["input"] == "first task"


def test_two_tasks_run_in_one_session_with_new(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, port=9123)
    fake = FakeBackend()
    fake.queue_stream(*_final_events("task0001deadbeef"))
    fake.queue_stream(*_final_events("task0002deadbeef"))

    code, out = _run(
        monkeypatch, settings, fake, ["first task", "/new", "second task", "exit"]
    )

    prompts = [line[7:] for line in out if line.startswith("PROMPT:")]
    text = _body(out)
    assert code == 0
    assert text.count("答: done") == 2
    assert text.count("● 新任务") == 2
    assert any("下一条输入创建新任务" in line for line in out)
    # Phases: base prompt, task prompt, base prompt (/new), task prompt.
    assert prompts[0] == PROMPT
    assert prompts[2] == PROMPT
    assert sum(prompt.startswith("[") for prompt in prompts) == 2


def test_second_message_continues_the_same_task(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, port=9123)
    fake = FakeBackend()
    fake.queue_stream(*_final_events("task0001deadbeef"))
    fake.queue_stream(
        [
            {"type": "final_answer", "data": {"answer": "again"}},
            {"type": "task_interrupted", "data": {"status": "INTERRUPTED"}},
        ]
    )

    code, out = _run(monkeypatch, settings, fake, ["first", "second", "quit"])

    assert code == 0
    assert fake.calls("POST", "/api/tasks") == 1
    assert len(fake.sent_messages) == 1
    assert fake.sent_messages[0][1] == "second"
    assert "答: again" in _body(out)


def test_an_admission_409_prints_the_reason_and_keeps_the_prompt(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, port=9123)
    fake = FakeBackend(start_running=True)
    fake.auto_park = False
    fake.queue_stream(
        [
            {"type": "final_answer", "data": {"answer": "done"}},
            {"type": "task_interrupted", "data": {"status": "INTERRUPTED"}},
        ]
    )

    code, out = _run(monkeypatch, settings, fake, ["first", "second", "exit"])

    assert code == 0
    assert any("✗" in line and "RUNNING" in line for line in out)
    # No automatic retry of the refused continuation.
    assert fake.calls("POST", "/api/tasks/task0001deadbeef/messages") == 1


def test_stop_is_a_noop_when_the_task_is_parked(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, port=9123)
    fake = FakeBackend()
    fake.queue_stream(*_final_events("task0001deadbeef"))

    code, out = _run(monkeypatch, settings, fake, ["first", "/stop", "exit"])

    assert code == 0
    assert any("没有在跑的任务" in line for line in out)
    assert fake.stop_requests == []


def test_stop_running_task_requests_stop(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, port=9123)
    fake = FakeBackend(start_running=True)
    fake.auto_park = False
    fake.queue_stream(
        [
            {"type": "final_answer", "data": {"answer": "still going"}},
            {"type": "task_interrupted", "data": {"status": "INTERRUPTED"}},
        ]
    )

    code, out = _run(monkeypatch, settings, fake, ["first", "/stop", "exit"])

    assert code == 0
    assert fake.stop_requests == ["task0001deadbeef"]
    assert any("已请求停止" in line for line in out)


def test_ctrl_c_while_waiting_stops_the_task_then_returns_to_prompt(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, port=9123)
    fake = FakeBackend(start_running=True)
    fake.auto_park = False
    fake.queue_stream()  # no events: the round is still waiting

    def sleep(seconds: float) -> None:
        raise KeyboardInterrupt

    code, out = _run(
        monkeypatch, settings, fake, ["long task", "exit"], sleep=sleep
    )

    assert code == 0
    assert fake.stop_requests == ["task0001deadbeef"], "Ctrl-C must stop the task"
    assert any("已请求停止" in line for line in out)


def test_ctrl_c_at_the_prompt_exits_cleanly(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, port=9123)
    fake = FakeBackend()

    def input_fn(prompt: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(repl, "get_settings", lambda: settings)
    out: List[str] = []
    code = repl_main(
        [],
        input_fn=input_fn,
        output=out.append,
        configure_stdio=False,
        transport=fake.transport(),
        ensure=lambda _settings, **kw: (0, settings.port),
    )

    assert code == 0
    assert any("退出" in line for line in out)
    assert fake.requests == []


def test_eof_exits_cleanly(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, port=9123)
    fake = FakeBackend()

    def input_fn(prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr(repl, "get_settings", lambda: settings)
    code = repl_main(
        [],
        input_fn=input_fn,
        output=lambda line: None,
        configure_stdio=False,
        transport=fake.transport(),
        ensure=lambda _settings, **kw: (0, settings.port),
    )

    assert code == 0


def test_repl_normalizes_no_proxy_before_building_the_client(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, port=9123)
    fake = FakeBackend()
    monkeypatch.setenv("NO_PROXY", "[::1], localhost")
    monkeypatch.setenv("no_proxy", "[::1], localhost")
    monkeypatch.setattr(repl, "get_settings", lambda: settings)

    code = repl_main(
        [],
        input_fn=lambda prompt: "exit",
        output=lambda line: None,
        configure_stdio=False,
        transport=fake.transport(),
        ensure=lambda _settings, **kw: (0, settings.port),
    )

    assert code == 0
    assert os.environ["NO_PROXY"] == "::1,localhost"
    assert os.environ["no_proxy"] == "::1,localhost"


def test_terminal_event_finishes_the_round_even_before_status_settles(
    tmp_path, monkeypatch
):
    settings = make_settings(tmp_path, port=9123)
    fake = FakeBackend()
    fake.auto_park = False
    fake.status_plans["task0001deadbeef"] = ["RUNNING", "INTERRUPTED"]
    fake.queue_stream(*_final_events("task0001deadbeef"))

    code, out = _run(monkeypatch, settings, fake, ["first", "exit"])

    assert code == 0
    assert any("■ 停下" in line for line in out)
    assert not any("仍在跑" in line for line in out)


def test_status_polling_is_the_fallback_when_events_are_missing(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, port=9123)
    fake = FakeBackend()
    fake.auto_park = False
    fake.status_plans["task0001deadbeef"] = ["RUNNING", "RUNNING", "INTERRUPTED"]
    fake.queue_stream()  # the stream ends without a single event

    code, out = _run(monkeypatch, settings, fake, ["first", "exit"])

    text = _body(out)
    assert code == 0
    assert not any("■ 停下" in line for line in out), "no event was rendered"
    assert "改为轮询任务状态" in text
    assert not any("仍在跑" in line for line in out), "the poll saw it park"


def test_a_dropped_stream_takes_a_snapshot_and_resumes_without_resending(
    tmp_path, monkeypatch
):
    settings = make_settings(tmp_path, port=9123)
    fake = FakeBackend(start_running=True)
    fake.queue_broken_stream(
        {"type": "task_created", "data": {"task_id": "task0001deadbeef"}},
        {"type": "step_start", "data": {"index": 1}},
    )
    fake.queue_stream(
        [
            {"type": "final_answer", "data": {"answer": "after-reconnect"}},
            {"type": "task_interrupted", "data": {"status": "INTERRUPTED"}},
        ]
    )

    code, out = _run(monkeypatch, settings, fake, ["first", "exit"])

    text = _body(out)
    assert code == 0
    assert "答: after-reconnect" in text
    assert fake.calls("GET", "/api/tasks/task0001deadbeef/events") == 2
    # Contract: one create, one snapshot (the status poll) and no replay.
    assert fake.calls("POST", "/api/tasks") == 1
    assert fake.calls("POST", "/api/tasks/task0001deadbeef/messages") == 0


def test_exiting_the_terminal_stops_nothing_and_leaves_no_record(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, single_backend_guard_enabled=True, port=9123)
    fake = FakeBackend()
    fake.queue_stream(*_final_events("task0001deadbeef"))

    code, _out = _run(monkeypatch, settings, fake, ["first", "exit"])

    assert code == 0
    assert fake.stop_requests == []
    assert fake.calls("POST", "/api/server/stop") == 0
    # The client never holds the single-instance record.
    assert not (tmp_path / "server.json").exists()
    assert fake.tasks["task0001deadbeef"]["status"] == "INTERRUPTED"


# ── commands: /tasks /use /resume /mode /model ──
def test_tasks_lists_this_directory(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, port=9123)
    fake = FakeBackend()
    fake.add_task(status="INTERRUPTED", mode="plan", title="整理计划")

    code, out = _run(monkeypatch, settings, fake, ["/tasks", "exit"])

    text = _body(out)
    assert code == 0
    assert "task0001" in text and "整理计划" in text
    assert fake.calls("GET", "/api/tasks") >= 1


def test_use_attaches_to_an_existing_task_by_prefix(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, port=9123)
    fake = FakeBackend()
    task_id = fake.add_task(title="已有的任务")
    fake.queue_stream(
        [
            {"type": "final_answer", "data": {"answer": "continued"}},
            {"type": "task_interrupted", "data": {"status": "INTERRUPTED"}},
        ]
    )

    code, out = _run(monkeypatch, settings, fake, ["/use task0001", "more", "exit"])

    text = _body(out)
    assert code == 0
    assert "已接入 task0001" in text
    assert fake.sent_messages == [(task_id, "more")]
    assert "答: continued" in text


def test_resume_continues_a_parked_task(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, port=9123)
    fake = FakeBackend()
    fake.add_task(status="INTERRUPTED")
    fake.queue_stream(
        [
            {"type": "task_resumed", "data": {"task_id": "task0001deadbeef"}},
            {"type": "final_answer", "data": {"answer": "resumed"}},
            {"type": "task_interrupted", "data": {"status": "INTERRUPTED"}},
        ]
    )

    code, out = _run(monkeypatch, settings, fake, ["/use task0001", "/resume", "exit"])

    text = _body(out)
    assert code == 0
    assert "● 续跑" in text
    assert "答: resumed" in text
    assert fake.calls("POST", "/api/tasks/task0001deadbeef/resume") == 1


def test_mode_and_model_switch_the_current_task(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, port=9123)
    fake = FakeBackend()
    fake.queue_stream(*_final_events("task0001deadbeef"))

    code, out = _run(
        monkeypatch,
        settings,
        fake,
        ["first", "/mode plan", "/model m-42", "exit"],
    )

    text = _body(out)
    assert code == 0
    assert "● 模式 → plan" in text
    assert "● 模型 → m-42" in text
    assert fake.tasks["task0001deadbeef"]["mode"] == "plan"
    assert fake.tasks["task0001deadbeef"]["model"] == "m-42"


def test_an_invalid_mode_is_refused_without_a_request(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, port=9123)
    fake = FakeBackend()
    fake.queue_stream(*_final_events("task0001deadbeef"))

    code, out = _run(monkeypatch, settings, fake, ["first", "/mode turbo", "exit"])

    assert code == 0
    assert any("用法：/mode" in line for line in out)
    assert fake.calls("PUT", "/api/tasks/task0001deadbeef/mode") == 0


def test_an_unknown_command_is_refused_not_sent_to_the_model(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, port=9123)
    fake = FakeBackend()
    fake.queue_stream(*_final_events("task0001deadbeef"))

    code, out = _run(monkeypatch, settings, fake, ["first", "/mod plan", "exit"])

    assert code == 0
    assert any("未知命令" in line for line in out)
    assert fake.calls("POST", "/api/tasks/task0001deadbeef/messages") == 0


def test_new_is_refused_while_the_task_is_still_running(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, port=9123)
    fake = FakeBackend(start_running=True)
    fake.auto_park = False
    fake.queue_stream(
        [
            {"type": "final_answer", "data": {"answer": "still going"}},
            {"type": "task_interrupted", "data": {"status": "INTERRUPTED"}},
        ]
    )
    fake.queue_stream(*_final_events("task0002deadbeef"))

    code, out = _run(
        monkeypatch,
        settings,
        fake,
        ["first", "/new", "/stop", "/new", "second", "exit"],
    )

    text = _body(out)
    assert code == 0
    assert "还在跑" in text, "RUNNING + /new must be refused"
    assert "仍在跑" in text, "settle timeout must warn"
    assert fake.calls("POST", "/api/tasks") == 2
    assert fake.stop_requests == ["task0001deadbeef"]


# ── failure classification (auth / unreachable / server errors) ──
def test_a_401_reports_expiry_and_exits_nonzero(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, port=9123, auth_enabled=True, auth_token="local-secret")
    fake = FakeBackend(auth=True)
    # Login succeeds but hands out a token the backend already refuses (rotation
    # on another client): the first task call is a 401.
    fake.issued_token = "rotated-elsewhere"

    code, out = _run(monkeypatch, settings, fake, ["first", "exit"])

    text = _body(out)
    assert code == 1
    assert "认证已过期" in text
    assert "local-secret" not in text
    assert "tok-1" not in text


def test_login_failure_is_reported_without_the_secret(tmp_path, monkeypatch):
    settings = make_settings(
        tmp_path, port=9123, auth_enabled=True, auth_token="wrong-local-secret"
    )
    fake = FakeBackend(auth=True)

    code, out = _run(monkeypatch, settings, fake, ["exit"])

    text = _body(out)
    assert code == 1
    assert "401" in text
    assert "wrong-local-secret" not in text


def test_an_unreachable_backend_is_reported(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, port=9123)

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    lines = iter(["first", "exit"])
    monkeypatch.setattr(repl, "get_settings", lambda: settings)
    out: List[str] = []
    code = repl_main(
        [],
        input_fn=lambda prompt: next(lines),
        output=out.append,
        configure_stdio=False,
        transport=httpx.MockTransport(boom),
        ensure=lambda _settings, **kw: (0, settings.port),
    )

    assert code == 0  # the prompt survives: the user can fix it and retry
    assert any("✗" in line and "无法连接后台" in line for line in out)


def test_stop_errors_are_printed_not_raised(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, port=9123)
    fake = FakeBackend(start_running=True)
    fake.auto_park = False
    fake.queue_stream(
        [
            {"type": "final_answer", "data": {"answer": "still going"}},
            {"type": "task_interrupted", "data": {"status": "INTERRUPTED"}},
        ]
    )
    original = fake.handler

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/stop"):
            return httpx.Response(500, json={"detail": "tasks.json write failed"})
        return original(request)

    code, out = _run(
        monkeypatch,
        settings,
        fake,
        ["first", "/stop", "exit"],
        transport=httpx.MockTransport(flaky),
    )

    assert code == 0
    assert any("✗" in line and "write failed" in line for line in out)


def test_send_persistence_error_is_printed(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, port=9123)
    fake = FakeBackend()
    fake.queue_stream(*_final_events("task0001deadbeef"))
    original = fake.handler

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            return httpx.Response(500, json={"detail": "tasks.json write failed"})
        return original(request)

    code, out = _run(
        monkeypatch,
        settings,
        fake,
        ["first", "second", "exit"],
        transport=httpx.MockTransport(flaky),
    )

    assert code == 0
    assert any("✗" in line and "write failed" in line for line in out)


def test_ensure_failure_returns_nonzero(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, port=9123)
    fake = FakeBackend()

    code, out = _run(
        monkeypatch,
        settings,
        fake,
        ["exit"],
        ensure=lambda _settings, **kw: (1, 0),
    )

    assert code == 1
    assert fake.requests == []


def test_repl_ensures_the_shared_backend_without_a_browser_or_a_build(
    tmp_path, monkeypatch
):
    """`qianfan repl` reuses/spawns the one backend; it never opens the UI.

    ``require_dist=False`` keeps the terminal usable without a frontend build,
    ``accept_dev=True`` lets it reuse a ``start.py`` backend, and ``browser=None``
    is "connect only" — the three knobs that make this the terminal, not
    ``qianfan``.
    """
    settings = make_settings(tmp_path, port=9123)
    fake = FakeBackend()
    seen: Dict[str, Any] = {}

    def fake_ensure(settings_arg, **kwargs):
        seen.update(kwargs)
        return 0, settings_arg.port

    monkeypatch.setattr(cli, "ensure_backend", fake_ensure)
    monkeypatch.setattr(repl, "get_settings", lambda: settings)
    code = repl_main(
        ["--preset", "minimal"],
        input_fn=lambda prompt: "exit",
        output=lambda line: None,
        configure_stdio=False,
        transport=fake.transport(),
    )

    assert code == 0
    assert seen == {
        "port": 9123,
        "preset": "minimal",
        "browser": None,
        "require_dist": False,
        "accept_dev": True,
    }


# ── CLI dispatch + encoding + module entry (D-48 / D-51) ──
def test_qianfan_routes_the_repl_subcommand():
    seen: List[List[str]] = []
    code = cli.main(["repl", "--flag"], repl=lambda argv: seen.append(argv) or 7)
    assert code == 7
    assert seen == [["--flag"]]


def test_qianfan_rejects_an_unknown_preset_before_dispatch():
    seen: List[List[str]] = []
    code = cli.main(["repl", "--preset", "nope"], repl=lambda argv: seen.append(argv) or 0)
    assert code == 1
    assert seen == []


def test_configure_stdio_utf8_reconfigures_gbk_wrapper():
    stream = io.TextIOWrapper(
        io.BytesIO(), encoding="gbk", errors="strict", write_through=True
    )
    configure_stdio_utf8([stream])
    assert stream.encoding.lower().replace("-", "") == "utf8"
    assert stream.errors == "replace"
    stream.write("中文 emoji 🙂")
    stream.flush()  # must not raise UnicodeEncodeError


def test_configure_stdio_utf8_skips_non_reconfigurable_streams():
    configure_stdio_utf8([object()])  # must not raise


def test_repl_module_entry_exits_with_a_readable_preset_error(tmp_path):
    """Subprocess smoke (module entry + exit code) without spawning a backend.

    An invalid ``--preset`` is refused before the ensure step, so this checks
    the entry wiring (``python -m backend.repl``) and never starts uvicorn.
    """
    env = os.environ.copy()
    env.update(
        {
            "DATA_DIR": str(tmp_path / "data"),
            "ARTIFACTS_DIR": str(tmp_path / "artifacts"),
            "TRACE_DIR": str(tmp_path / "traces"),
            "CHECKPOINT_DIR": str(tmp_path / "checkpoints"),
            "USE_MOCK_LLM": "true",
            "MCP_ENABLED": "false",
            "SINGLE_BACKEND_GUARD_ENABLED": "false",
            "HOST": "127.0.0.1",
            "FRONTEND_DIST": "_no_frontend_dist_for_tests",
            "PLUGINS_PRESET": "full",
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "backend.repl", "--preset", "nope"],
        input="exit\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO_ROOT,
        env=env,
        timeout=180,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "能力包配置无效" in output
    assert not (tmp_path / "data" / "server.json").exists()
