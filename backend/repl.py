"""Thin terminal REPL for the shared backend (#332 S6, spec D-48…D-52).

`qianfan repl` (or `python -m backend.repl`) is a **client**: it ensures the one
shared backend is running (reusing a healthy instance, spawning a detached one
otherwise), then talks to the same REST/SSE surface the web page uses. It never
builds a runtime, never holds the single-instance lock and never shuts the
backend down (ADR 0013 decision 2): closing the terminal only disconnects, and
running tasks continue server-side.

The rendered face stays deliberately small — :data:`RENDERED_EVENT_TYPES` plus
plain `print`, no Rich, no TUI, no confirm gate (ADR 0008 decision 6 keeps the
danger policy hook as the only gate).

Concurrency model: the SSE stream is read on a worker thread into a queue; the
main thread drains and renders it. A round ends on the terminal event
(publish-then-persist, so a short settle window follows it); a bounded status
poll is the fallback when events are missing. On a dropped stream the client
first takes the authoritative task snapshot, then re-subscribes — it never
replays a create/continue request that may already have been accepted.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional, Sequence

from . import api_client
from .api.schemas import TaskStatus
from .api_client import AdmissionRejected, ApiClient, ApiError, AuthExpired, BackendUnreachable
from .config import ensure_loopback_host, get_settings
from .services.auth import ensure_startup_auth_configured
from .services.plugins_store import resolve_effective_plugins
from .utils.logging import get_logger

logger = get_logger("repl")

PROMPT = "qianfan> "
OUTPUT_LINE_MAX = 160

#: D-49 (#20): render only task/step/tool/final plus the two terminal events.
#: Everything else (heartbeat, context compression, evicted tool results,
#: subtask strips) is intentionally dropped from the terminal face.
RENDERED_EVENT_TYPES = frozenset(
    {
        "task_created",
        "task_resumed",
        "step_start",
        "tool_call",
        "tool_result",
        "final_answer",
        "task_interrupted",
        "task_failed",
    }
)
TERMINAL_EVENT_TYPES = frozenset({"task_interrupted", "task_failed"})
TERMINAL_STATUSES = frozenset({TaskStatus.INTERRUPTED, TaskStatus.FAILED})
MODES = ("plan", "build")

Output = Callable[[str], None]
InputFn = Callable[[str], str]

#: ``(settings, *, port, preset, ...) -> (exit_code, port)``; the CLI's ensure
#: path, injected in tests so no backend is spawned.
EnsureFn = Callable[..., tuple[int, int]]


def configure_stdio_utf8(streams: Optional[Sequence[Any]] = None) -> None:
    """D-51 (#20): make stdout/stderr UTF-8 with replacement (Windows GBK guard).

    Guarded with `hasattr` so wrapped / closed streams (test captures, piped
    subprocesses) are skipped instead of crashing the entrypoint.
    """
    targets = (sys.stdout, sys.stderr) if streams is None else tuple(streams)
    for stream in targets:
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # already closed or not reconfigurable
            continue


def _print_line(text: str) -> None:
    print(text, flush=True)


def _short_task_id(task_id: Any) -> str:
    value = str(task_id or "")
    return value[:8] if value else "?"


def _preview(value: Any, limit: int = OUTPUT_LINE_MAX) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def render_event(
    event: Dict[str, Any], redact: Optional[Callable[[str], str]] = None
) -> Optional[str]:
    """One terminal line for a rendered event type, else `None` (D-49).

    `redact` is applied to the final line when supplied. The server already
    redacts what it publishes; this hook stays so a caller cannot accidentally
    print an unredacted string. Reasoning is deliberately never rendered —
    `final_answer` shows the answer only (the web face owns the thinking block).
    """
    kind = str(event.get("type") or "")
    if kind not in RENDERED_EVENT_TYPES:
        return None
    data = event.get("data")
    if not isinstance(data, dict):
        data = {}
    line = _render_known(kind, data)
    if line is None:
        return None
    if redact is not None:
        line = redact(line)
    return line


def _render_known(kind: str, data: Dict[str, Any]) -> Optional[str]:
    if kind == "task_created":
        return f"● 新任务 {_short_task_id(data.get('task_id'))}"
    if kind == "task_resumed":
        return f"● 续跑 {_short_task_id(data.get('task_id'))}"
    if kind == "step_start":
        index = data.get("index")
        round_no = data.get("round")
        suffix = f"（第 {round_no} 轮）" if round_no else ""
        return f"→ 第 {index} 步{suffix}"
    if kind == "tool_call":
        name = str(data.get("tool_name") or data.get("name") or "?")
        return f"  ⚙ {name} {_preview(data.get('input') or {})}"
    if kind == "tool_result":
        name = str(data.get("tool_name") or data.get("name") or "?")
        status = str(data.get("status") or "done")
        mark = "✓" if status == "success" else ("⊘" if status == "skipped" else "✗")
        detail = data.get("output")
        if detail is None:
            detail = data.get("error") or ""
        return f"  {mark} {name}: {_preview(detail)}"
    if kind == "final_answer":
        answer = str(data.get("answer") or "").strip()
        return f"答: {answer or '(空)'}"
    if kind == "task_interrupted":
        return f"■ 停下（{data.get('status') or 'INTERRUPTED'}）"
    if kind == "task_failed":
        return f"✗ 出错：{data.get('error') or 'unknown error'}"
    return None


class _Stream:
    """One SSE subscription: a reader thread feeding a queue.

    ``error`` is what the reader hit (transport / auth), ``closed`` fires when
    the stream ends for any reason. Each stream owns its flags, so a stale
    reader from a previous task can never be mistaken for the current one.
    """

    def __init__(self, client: ApiClient, task_id: str) -> None:
        self.queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self.stop = threading.Event()
        self.closed = threading.Event()
        self.error: Optional[BaseException] = None
        self._thread = threading.Thread(
            target=self._read, args=(client, task_id), daemon=True
        )

    def start(self) -> "_Stream":
        self._thread.start()
        return self

    def _read(self, client: ApiClient, task_id: str) -> None:
        try:
            for event in client.events(task_id):
                if self.stop.is_set():
                    return
                self.queue.put(event)
        except (BackendUnreachable, AuthExpired, ApiError) as exc:
            self.error = exc
        except Exception:  # a bug, not a transport condition: never silent
            logger.exception("event stream reader failed for %s", task_id)
        finally:
            self.closed.set()


class ReplSession:
    """One terminal conversation driven entirely over REST/SSE."""

    def __init__(
        self,
        client: ApiClient,
        *,
        output: Output,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        wait_interval: float = 0.1,
        poll_interval: float = 1.0,
        grace_wait: float = 0.2,
        round_timeout: float = 600.0,
        settle_timeout: float = 5.0,
        max_reconnects: int = 1,
    ) -> None:
        self.client = client
        self._output = output
        self._sleep = sleep
        self._clock = clock
        self.wait_interval = wait_interval
        self.poll_interval = poll_interval
        self.grace_wait = grace_wait
        self.round_timeout = round_timeout
        self.settle_timeout = settle_timeout
        self.max_reconnects = max_reconnects
        self.task_id: Optional[str] = None
        self._stream: Optional[_Stream] = None

    # ── public state machine ──
    def display_task(self) -> str:
        return _short_task_id(self.task_id)

    def is_running(self) -> bool:
        """True while the current task is actually RUNNING (M2 guard)."""
        return self._status() is TaskStatus.RUNNING

    def new_task(self, text: str) -> None:
        self._leave_task()
        created = self.client.create_task(text, workspace=os.getcwd())
        self.task_id = str(created.get("task_id") or "") or None
        if self.task_id is None:
            raise RuntimeError("后台没有返回 task_id")
        # No manual "新任务" line: the SSE replay carries task_created (L1).
        self._attach()
        self._run_until_parked()

    def send(self, text: str) -> None:
        if self.task_id is None:
            raise RuntimeError("没有进行中的任务")
        self.client.send_message(self.task_id, text)
        self._run_until_parked()

    def use(self, ref: str) -> None:
        """Attach to an existing task by full id or unique prefix."""
        task = self.client.resolve_task(ref)
        self._leave_task()
        self.task_id = str(task.get("id") or ref)
        self._attach()
        self._output(
            f"● 已接入 {self.display_task()}（{task.get('status') or '?'}"
            f" / {task.get('mode') or 'build'}）"
        )

    def resume(self) -> None:
        if self.task_id is None:
            raise RuntimeError("先创建或 /use 一个任务")
        self.client.resume_task(self.task_id)
        self._run_until_parked()

    def set_mode(self, mode: str) -> None:
        if self.task_id is None:
            raise RuntimeError("先创建或 /use 一个任务")
        self.client.set_mode(self.task_id, mode)
        self._output(f"● 模式 → {mode}")

    def set_model(self, model: str) -> None:
        if self.task_id is None:
            raise RuntimeError("先创建或 /use 一个任务")
        self.client.set_model(self.task_id, model)
        self._output(f"● 模型 → {model}")

    def leave(self) -> None:
        """Forget the current conversation (/new): next input creates a task."""
        self._leave_task()

    def stop(self) -> bool:
        """Stop the current task only when it is actually running."""
        if self.task_id is None:
            self._output("当前没有任务。")
            return False
        status = self._status()
        if status is not TaskStatus.RUNNING:
            self._output("当前没有在跑的任务（已停下，可直接续说）。")
            return False
        self.client.stop_task(self.task_id)
        self._output("… 已请求停止")
        self._settle()
        return True

    def interrupt_and_stop(self) -> None:
        """Ctrl-C while a round is being awaited: stop, then back to prompt."""
        if self.task_id is None:
            return
        self._output("\n… 已请求停止")
        try:
            self.client.stop_task(self.task_id)
        except Exception:  # pragma: no cover - stop must never trap the user
            logger.exception("stop after interrupt failed")
        self._settle()

    def close(self) -> None:
        self._leave_task()

    # ── internals ──
    def _attach(self) -> None:
        """Open the event stream for the current task (replay then live)."""
        if self.task_id is None:
            return
        self._detach_stream()
        self._stream = _Stream(self.client, self.task_id).start()

    def _detach_stream(self) -> None:
        if self._stream is not None:
            self._stream.stop.set()
            self._stream = None

    def _leave_task(self) -> None:
        self._detach_stream()
        self.task_id = None

    def _stream_closed(self) -> bool:
        return self._stream is None or self._stream.closed.is_set()

    def _status(self) -> Optional[TaskStatus]:
        if self.task_id is None:
            return None
        task = self.client.get_task(self.task_id)
        raw = str(task.get("status") or "")
        try:
            return TaskStatus(raw)
        except ValueError:
            return None

    def _drain(self, wait: float = 0.0) -> bool:
        """Render queued events; return True when a terminal event was seen.

        ``wait`` blocks for the first event at most (real seconds), which is how
        a round that finds an already-parked task still shows the replayed tail.
        """
        stream = self._stream
        if stream is None:
            return False
        first: Optional[Dict[str, Any]] = None
        if wait > 0:
            try:
                first = stream.queue.get(timeout=wait)
            except queue.Empty:
                first = None
        terminal_seen = False
        while True:
            if first is not None:
                event, first = first, None
            else:
                try:
                    event = stream.queue.get_nowait()
                except queue.Empty:
                    return terminal_seen
            if str(event.get("type")) in TERMINAL_EVENT_TYPES:
                terminal_seen = True
            line = render_event(event)
            if line:
                self._output(line)

    def _settle(self) -> None:
        """Bounded wait for the status to settle after a stop / terminal event."""
        deadline = self._clock() + self.settle_timeout
        while True:
            try:
                status = self._status()
            except (BackendUnreachable, ApiError):
                return
            if status in TERMINAL_STATUSES:
                return
            if self._clock() >= deadline:
                return
            try:
                self._sleep(self.wait_interval)
            except KeyboardInterrupt:
                return
            self._drain()

    def _run_until_parked(self) -> None:
        """Await the round: SSE events first, bounded status polling as fallback."""
        deadline = self._clock() + self.round_timeout
        settle_deadline: Optional[float] = None
        # Never poll before one tick: the SSE replay of a task that was already
        # parked must land before the status decides the round is over.
        next_poll = self._clock() + self.wait_interval
        reconnects = 0
        poll_warned = False
        events_only_warned = False
        while True:
            terminal_seen = self._drain()
            if terminal_seen and settle_deadline is None:
                # publish-then-persist order: give finalize a short window.
                settle_deadline = self._clock() + self.settle_timeout
                next_poll = 0.0
            now = self._clock()
            if now >= next_poll:
                next_poll = now + self.poll_interval
                try:
                    status = self._status()
                except BackendUnreachable as exc:
                    if not poll_warned:
                        poll_warned = True
                        self._output(f"… 状态查询失败（{exc}）；继续等待事件")
                    status = None
                if status in TERMINAL_STATUSES:
                    if settle_deadline is not None:
                        # Event and persisted status agree: the round is over.
                        self._drain()
                    else:
                        # Parked but no terminal event yet: one bounded grace
                        # for the replayed tail, then status is authoritative.
                        self._drain(wait=self.grace_wait)
                    return
            if settle_deadline is not None and self._clock() >= settle_deadline:
                self._warn_if_still_running()
                return
            if self._clock() >= deadline:
                self._warn_if_still_running()
                return
            if self._stream_closed() and not terminal_seen:
                error = self._stream.error if self._stream is not None else None
                if isinstance(error, AuthExpired):
                    raise error
                if reconnects < self.max_reconnects:
                    reconnects += 1
                    # Disconnect contract: snapshot first, then events; never a
                    # replayed create/continue request.
                    try:
                        snapshot: Optional[TaskStatus] = self._status()
                    except BackendUnreachable:
                        snapshot = None
                    if snapshot in TERMINAL_STATUSES:
                        self._drain()
                        return
                    self._attach()
                    continue
                if not events_only_warned:
                    events_only_warned = True
                    self._output("… 事件流已断开，改为轮询任务状态；不会重发刚才的消息")
            self._sleep(self.wait_interval)

    def _warn_if_still_running(self) -> None:
        """M2: tell the user when the round returned without a parked status."""
        try:
            status = self._status()
        except (BackendUnreachable, ApiError):
            return
        if status is TaskStatus.RUNNING:
            self._output("⚠ 任务仍在跑：可用 /stop 停止，或等它停下再 /new。")


def _emit_tasks(session: ReplSession, emit: Output) -> None:
    tasks = session.client.list_tasks(limit=20, directory="current").get("tasks") or []
    if not tasks:
        emit("（当前目录没有任务）")
        return
    for task in tasks:
        emit(
            f"{_short_task_id(task.get('id'))}  "
            f"{task.get('status') or '?':<11} {task.get('mode') or 'build':<5} "
            f"{_preview(task.get('title') or task.get('user_input') or '', 60)}"
        )


def _run_command(session: ReplSession, text: str, emit: Output) -> bool:
    """Handle one slash command; False when ``text`` is not a command."""
    if text == "/new":
        # M2: never create a second task while the current one is still RUNNING.
        if session.is_running():
            emit("✗ 当前任务还在跑：先 /stop 或等它停下，再 /new。")
            return True
        session.leave()
        emit("● 下一条输入创建新任务。")
        return True
    if text == "/stop":
        session.stop()
        return True
    if text == "/tasks":
        _emit_tasks(session, emit)
        return True
    if text == "/resume":
        session.resume()
        return True
    if text == "/use" or text.startswith("/use "):
        ref = text[len("/use") :].strip()
        if not ref:
            emit("用法：/use <task_id>（可只给前几位）")
            return True
        session.use(ref)
        return True
    if text == "/mode" or text.startswith("/mode "):
        mode = text[len("/mode") :].strip()
        if mode not in MODES:
            emit("用法：/mode plan|build")
            return True
        session.set_mode(mode)
        return True
    if text == "/model" or text.startswith("/model "):
        model = text[len("/model") :].strip()
        if not model:
            emit("用法：/model <model_id>")
            return True
        session.set_model(model)
        return True
    return False


def repl_main(
    argv: Optional[Sequence[str]] = None,
    *,
    input_fn: Optional[InputFn] = None,
    output: Optional[Output] = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    configure_stdio: bool = True,
    transport: Optional[Any] = None,
    ensure: Optional[EnsureFn] = None,
) -> int:
    """Connect to the shared backend and run the terminal REPL (0 on exit)."""
    parser = argparse.ArgumentParser(
        prog="qianfan repl",
        description="Terminal client for the shared qianfan backend (no browser).",
    )
    parser.add_argument(
        "--preset",
        default=None,
        help="one-shot capability preset, only used when this run starts the backend",
    )
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    emit = output or _print_line
    if configure_stdio:
        configure_stdio_utf8()
    settings = get_settings()
    try:
        ensure_startup_auth_configured(settings)
        ensure_loopback_host(settings)
    except RuntimeError as exc:
        emit(f"[repl] {exc}")
        return 1
    # D-54/D-55 (#67 P4-2): an invalid saved file / --preset fails readably
    # before anything is spawned (the backend reads the same file).
    try:
        resolve_effective_plugins(settings, preset_override=args.preset)
    except ValueError as exc:
        emit(f"[repl] {exc}")
        return 1

    from . import cli

    ensure_backend = ensure or cli.ensure_backend
    code, port = ensure_backend(
        settings,
        port=settings.port,
        preset=args.preset,
        browser=None,
        require_dist=False,
        accept_dev=True,
    )
    if code != 0 or not port:
        return code or 1

    base_url = api_client.resolve_base_url(settings)
    client = ApiClient(base_url, transport=transport)
    session: Optional[ReplSession] = None
    try:
        if settings.auth_enabled:
            client.login(settings.auth_token)
        session = ReplSession(client, output=emit, sleep=sleep, clock=clock)
        emit(f"qianfan 终端会话 — 工作目录 {os.getcwd()}")
        emit(f"已连接后台 {client.base_url}（共享后台；退出终端不会停任务）")
        emit(
            "输入任务开始；/new 新任务，/tasks 任务列表，/use <id> 接入，"
            "/resume 续跑，/mode plan|build，/model <id>，/stop 停止当前任务；"
            "exit / quit 退出，Ctrl-C 退出。"
        )
        read = input_fn or (lambda prompt: input(prompt))
        while True:
            prompt = PROMPT if session.task_id is None else f"[{session.display_task()}] "
            try:
                line = read(prompt)
            except EOFError:
                emit("")
                break
            except KeyboardInterrupt:
                emit("\n[repl] 退出。")
                break
            text = (line or "").strip()
            if not text:
                continue
            if text in ("exit", "quit"):
                break
            try:
                if text.startswith("/"):
                    if not _run_command(session, text, emit):
                        emit(f"✗ 未知命令：{text}（/tasks 看任务，exit 退出）")
                    continue
                if session.task_id is None:
                    session.new_task(text)
                else:
                    session.send(text)
            except KeyboardInterrupt:
                session.interrupt_and_stop()
                continue
            except AuthExpired as exc:
                # Nothing in this session can succeed without a fresh token.
                emit(f"✗ {exc}")
                emit("… 认证已过期或无效：请在设置页确认 auth_token 后重启终端。")
                return 1
            except (AdmissionRejected, ApiError, BackendUnreachable, RuntimeError, ValueError) as exc:
                # L4: command paths print the reason instead of a raw traceback.
                emit(f"✗ {exc}")
                continue
        return 0
    except AuthExpired as exc:
        emit(f"✗ {exc}")
        emit("… 认证已过期或无效：请在设置页确认 auth_token 后重启终端。")
        return 1
    except (ApiError, BackendUnreachable, RuntimeError, ValueError) as exc:
        emit(f"✗ {exc}")
        return 1
    finally:
        if session is not None:
            session.close()
        client.close()


if __name__ == "__main__":
    configure_stdio_utf8()
    raise SystemExit(repl_main(configure_stdio=False))
