"""Thin terminal REPL for the local agent (#20 / spec D-48…D-52).

`qianfan repl` (or `python -m backend.repl`) runs a persistent, multi-turn
session in the terminal without uvicorn: it reuses the same EventBus /
Persistence / TaskManager wiring as the web app (`backend.runtime.build_runtime`)
and renders a deliberately small event subset with plain `print` — no Rich, no
TUI, no confirm gate (ADR 0008 decision 6 keeps the danger policy hook as the
only gate; the #20 y/n / --auto-approve options are retired by spec).

Concurrency model: task workers run in their own threads and publish events;
the bus callback only puts events into a thread-safe queue, the main thread
drains and renders them. Rounds are awaited by a bounded poll on the task
status (the terminal event usually arrives first — the finalize path publishes
before persisting, so status is the fallback, not the primary signal).
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import time
from typing import Any, Callable, Dict, Optional, Sequence

from . import server_lock
from .api.schemas import TaskStatus
from .config import Settings, ensure_loopback_host, get_settings
from .core.capability_packages import DEFAULT_PRESET, resolve_packages
from .runtime import Runtime, build_runtime, model_key_is_missing
from .services.auth import ensure_startup_auth_configured
from .services.event_bus import Event
from .services.persistence import PersistenceError
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

Output = Callable[[str], None]
InputFn = Callable[[str], str]


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

    `redact` (TaskManager.redact in the real session) is applied to the final
    line: even if an upstream event ever carried a key-like string, the
    terminal face cannot print it. Reasoning is deliberately never rendered —
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


class ReplSession:
    """One terminal conversation: create → send_message rounds with rendering."""

    def __init__(
        self,
        runtime: Runtime,
        *,
        output: Output,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        wait_interval: float = 0.1,
        round_timeout: float = 600.0,
        settle_timeout: float = 5.0,
    ) -> None:
        self.runtime = runtime
        self.tm = runtime.task_manager
        self.bus = runtime.event_bus
        self._output = output
        self._sleep = sleep
        self._clock = clock
        self.wait_interval = wait_interval
        self.round_timeout = round_timeout
        self.settle_timeout = settle_timeout
        self.task_id: Optional[str] = None
        self._events: "queue.Queue[Event]" = queue.Queue()
        self._callback: Optional[Callable[[Event], None]] = None

    # ── public state machine ──
    def display_task(self) -> str:
        return _short_task_id(self.task_id)

    def is_running(self) -> bool:
        """True while the current task is actually RUNNING (M2 guard)."""
        return self._status() is TaskStatus.RUNNING

    def new_task(self, text: str) -> None:
        self._leave_task()
        self.task_id = self.tm.create_task(None, text)
        # No manual "新任务" line: create_task publishes task_created and the
        # post-create subscribe replays it — printing here duplicated it (L1).
        self._subscribe()
        self._run_until_parked()

    def send(self, text: str) -> None:
        if self.task_id is None:
            raise RuntimeError("没有进行中的任务")
        self.tm.send_message(self.task_id, text)
        self._run_until_parked()

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
        self.tm.stop(self.task_id)
        self._output("… 已请求停止")
        self._settle()
        return True

    def interrupt_and_stop(self) -> None:
        """Ctrl-C while a round is being awaited: stop, then back to prompt."""
        if self.task_id is None:
            return
        self._output("\n… 已请求停止")
        try:
            self.tm.stop(self.task_id)
        except Exception:  # pragma: no cover - stop must never trap the user
            logger.exception("stop after interrupt failed")
        self._settle()

    def close(self) -> None:
        self._leave_task()

    # ── internals ──
    def _subscribe(self) -> None:
        if self.task_id is None:
            return
        callback = self._events.put
        self._callback = callback
        history = self.bus.subscribe(self.task_id, callback)
        if history:
            for event in history:
                self._events.put(event)

    def _leave_task(self) -> None:
        if self.task_id is not None and self._callback is not None:
            try:
                self.bus.unsubscribe(self.task_id, self._callback)
            except Exception:  # pragma: no cover - bus is process-local
                logger.exception("unsubscribe failed for %s", self.task_id)
        self.task_id = None
        self._callback = None
        self._events = queue.Queue()

    def _status(self) -> Optional[TaskStatus]:
        if self.task_id is None:
            return None
        task = self.tm.get_task(self.task_id)
        if task is None:
            return None
        return task.status

    def _drain(self) -> bool:
        """Render queued events; return True when a terminal event was seen."""
        terminal_seen = False
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                return terminal_seen
            if str(event.get("type")) in TERMINAL_EVENT_TYPES:
                terminal_seen = True
            line = render_event(event, redact=self.tm.redact)
            if line:
                self._output(line)

    def _settle(self) -> None:
        """Bounded wait for the status to settle after a stop / terminal event."""
        deadline = self._clock() + self.settle_timeout
        while self._status() not in TERMINAL_STATUSES:
            if self._clock() >= deadline:
                return
            try:
                self._sleep(self.wait_interval)
            except KeyboardInterrupt:
                return
            self._drain()

    def _run_until_parked(self) -> None:
        """Await the round: events first, bounded status polling as fallback."""
        deadline = self._clock() + self.round_timeout
        settle_deadline: Optional[float] = None
        while True:
            if self._drain() and settle_deadline is None:
                # publish-then-persist order: give finalize a short window.
                settle_deadline = self._clock() + self.settle_timeout
            status = self._status()
            if status in TERMINAL_STATUSES:
                self._drain()
                return
            now = self._clock()
            if settle_deadline is not None and now >= settle_deadline:
                self._warn_if_still_running()
                return
            if now >= deadline:
                self._warn_if_still_running()
                return
            self._sleep(self.wait_interval)

    def _warn_if_still_running(self) -> None:
        """M2: tell the user when the round returned without a parked status."""
        if self._status() is TaskStatus.RUNNING:
            self._output(
                "⚠ 任务仍在跑：可用 /stop 停止，或等它停下再 /new。"
            )


def _busy_message(lock: server_lock.ServerLock) -> str:
    labels = {
        "serve": "网页后台（qianfan）",
        "dev": "开发后台（python start.py）",
        "repl": "终端会话（qianfan repl）",
    }
    label = labels.get(lock.mode, "qianfan 后台")
    return (
        f"[repl] 已有{label}在运行（pid={lock.pid}）。"
        "同一台机器只允许一个后台写 data/；请先停掉它——"
        "关终端会停任务；终端会话请在该终端输入 exit。"
    )


def repl_main(
    argv: Optional[Sequence[str]] = None,
    *,
    runtime_factory: Optional[Callable[[Settings], Runtime]] = None,
    input_fn: Optional[InputFn] = None,
    output: Optional[Output] = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    configure_stdio: bool = True,
) -> int:
    """Run the terminal REPL; returns a process exit code (0 normal)."""
    parser = argparse.ArgumentParser(
        prog="qianfan repl",
        description="Terminal session for the local agent (no browser, no uvicorn).",
    )
    parser.parse_args(list(sys.argv[1:] if argv is None else argv))

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
    # D-55 (#67): unknown / empty capability preset must fail before the
    # single-backend lock is claimed, with a readable line (no traceback).
    try:
        resolve_packages(getattr(settings, "plugins_preset", DEFAULT_PRESET))
    except ValueError as exc:
        emit(f"[repl] {exc}")
        return 1

    lock_file: Optional[Any] = None
    instance: Optional[str] = None
    previous_instance = os.environ.get("QIANFAN_INSTANCE")
    runtime: Optional[Runtime] = None
    session: Optional[ReplSession] = None
    try:
        if settings.single_backend_guard_enabled:
            lock_file = server_lock.lock_path(settings)
            existing = server_lock.read_lock(lock_file)
            if existing is not None and server_lock.pid_alive(existing.pid):
                emit(_busy_message(existing))
                return 1
            # M1: a dead-pid or corrupt record must not wedge the REPL. Use the
            # same stale-cleanup path cli/start use: dead pid -> release,
            # unparsable -> drop, live-but-booting -> kept (+None), and a live
            # backend that answered /health -> handed to _busy_message.
            running = server_lock.find_running(lock_file, wait=0.0)
            if running is not None:
                emit(_busy_message(running))
                return 1
            if server_lock.read_lock(lock_file) is not None:
                emit(
                    "[repl] 后台启动中（server.json 仍被一个活进程占用）："
                    f"请稍候再试；若确认没有进程在跑，可手动删除 {lock_file}。"
                )
                return 1
            instance = server_lock.new_instance()
            os.environ["QIANFAN_INSTANCE"] = instance
            claimed = server_lock.claim_lock(
                lock_file,
                server_lock.ServerLock(
                    pid=os.getpid(),
                    port=settings.port,
                    started_at=server_lock.now_iso(),
                    instance=instance,
                    mode="repl",
                ),
            )
            if not claimed:
                emit(
                    "[repl] 另一个 qianfan 入口刚刚启动；请稍后再试。"
                    f"若确认没有进程在跑，可手动删除 {lock_file}。"
                )
                return 1
            try:
                server_lock.ensure_single_backend(settings, wait=0.0)
            except RuntimeError as exc:
                emit(f"[repl] {exc}")
                return 1

        try:
            runtime = (runtime_factory or build_runtime)(settings)
        except ValueError as exc:
            # Defence in depth (the preset was validated above): a runtime
            # factory raising a config ValueError still reports readably and the
            # finally block releases the claimed lock.
            emit(f"[repl] {exc}")
            return 1
        if model_key_is_missing(settings, runtime.task_manager.models):
            emit(
                "[repl] 没有可用的模型 Key：在网页设置页给 Provider 粘贴，"
                "或设置它配置的环境变量（任务调用模型时才会失败）。"
            )
        session = ReplSession(runtime, output=emit, sleep=sleep, clock=clock)
        emit(
            f"qianfan 终端会话 — 工作目录 {os.getcwd()}"
            "（bash / 写工具会动真实文件；撤销靠 git）"
        )
        emit(
            "输入任务开始；/new 新任务，/stop 停止当前任务，"
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
                if text == "/new":
                    # M2: never create a second task while the current one is
                    # still RUNNING (timeout / slow settle leaves it live).
                    if session.is_running():
                        emit("✗ 当前任务还在跑：先 /stop 或等它停下，再 /new。")
                        continue
                    session.leave()
                    emit("● 下一条输入创建新任务。")
                    continue
                if text == "/stop":
                    session.stop()
                    continue
                if session.task_id is None:
                    session.new_task(text)
                else:
                    session.send(text)
            except KeyboardInterrupt:
                session.interrupt_and_stop()
                continue
            except (PersistenceError, RuntimeError, ValueError) as exc:
                # L4: command paths print the reason instead of a raw traceback;
                # the finally block still shuts the runtime down and frees the lock.
                emit(f"✗ {exc}")
                continue
        return 0
    finally:
        if session is not None:
            session.close()
        if runtime is not None:
            try:
                runtime.task_manager.shutdown()
            except Exception:  # pragma: no cover - shutdown must not mask exits
                logger.exception("repl task_manager.shutdown failed")
        if instance is not None and lock_file is not None:
            server_lock.release_lock(lock_file, instance)
        if previous_instance is None:
            os.environ.pop("QIANFAN_INSTANCE", None)
        else:
            os.environ["QIANFAN_INSTANCE"] = previous_instance


if __name__ == "__main__":
    configure_stdio_utf8()
    raise SystemExit(repl_main(configure_stdio=False))
