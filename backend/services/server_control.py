"""Explicit shutdown of the shared backend (#332 S5).

``POST /api/server/stop`` is the only way a client may stop the registered
backend. The order is the contract (ADR 0013 decision 2):

1. stop accepting new work (the app middleware answers 503);
2. park live tasks and wait for their workers;
3. settle their records and release process resources (``TaskManager.shutdown``);
4. remove this instance's single-instance record;
5. ask uvicorn to exit.

A worker that outlives the drain window is reported as incomplete — the lock
stays ours and the process keeps running, because a live worker may still be
writing ``tasks.json``. Retrying the endpoint is allowed; only one stop runs at
a time.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

from .. import server_lock
from ..api.schemas import TaskStatus
from ..utils.logging import get_logger

logger = get_logger("server_control")

#: How long live tasks may take to finish after ``stop()`` before the shutdown
#: is reported incomplete. Workers are given the same order of magnitude as
#: TaskManager.shutdown's own join (5s) plus room for a tool round to settle.
STOP_TIMEOUT_SEC = 10.0


@dataclass
class StopReport:
    """What the stop orchestration achieved, field by field."""

    stopped_tasks: list[str] = field(default_factory=list)
    workers_joined: bool = False
    shutdown_complete: bool = False
    lock_released: bool = False
    exit_requested: bool = False

    @property
    def complete(self) -> bool:
        return self.shutdown_complete and self.lock_released

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.complete,
            "stopped_tasks": list(self.stopped_tasks),
            "workers_joined": self.workers_joined,
            "shutdown_complete": self.shutdown_complete,
            "lock_released": self.lock_released,
            "exit_requested": self.exit_requested,
        }


def live_task_ids(task_manager: Any) -> list[str]:
    """Task ids a shutdown must park (RUNNING or PENDING)."""
    tasks = task_manager.persistence.list_all()
    return [
        task.id
        for task in tasks
        if task.status in (TaskStatus.RUNNING, TaskStatus.PENDING)
    ]


def stop_backend(app_state: Any, *, timeout: float | None = None) -> StopReport:
    """Run the graceful shutdown sequence against ``app_state``.

    ``app_state`` is the FastAPI state: ``settings``, ``task_manager``,
    ``accepting_runs`` (the 503 gate) and the optional ``request_server_exit``
    hook installed by ``backend.serve``.

    A backend started by ``start.py`` has no programmable exit hook; it is
    refused (409) before any task is touched, rather than reporting a stop that
    cannot exit the process.
    """
    if timeout is None:
        timeout = STOP_TIMEOUT_SEC
    request_exit = getattr(app_state, "request_server_exit", None)
    if not callable(request_exit):
        raise RuntimeError(
            "该后台由 start.py 启动，没有可编程退出钩子；请在它的终端按 Ctrl-C 停止。"
        )
    stop_lock = getattr(app_state, "stop_lock", None)
    if stop_lock is None:
        raise RuntimeError("后台状态不完整（缺少 stop_lock），拒绝停止。")
    report = StopReport()
    with stop_lock:
        if getattr(app_state, "stop_in_progress", False):
            raise RuntimeError("后台正在停止，请等待本进程退出")
        app_state.stop_in_progress = True
        try:
            app_state.accepting_runs = False
            task_manager = app_state.task_manager
            deadline = time.monotonic() + max(0.0, timeout)
            seen: set[str] = set()
            while True:
                live = live_task_ids(task_manager)
                for task_id in live:
                    task_manager.stop(task_id)
                    if task_id not in seen:
                        seen.add(task_id)
                        report.stopped_tasks.append(task_id)
                if task_manager.wait_for_idle(0.0) and not live:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                task_manager.wait_for_idle(min(0.1, remaining))
            report.workers_joined = task_manager.wait_for_idle(0.0)
            report.shutdown_complete = task_manager.shutdown() and report.workers_joined
            if not report.shutdown_complete:
                logger.warning(
                    "server stop: incomplete — stopped %d task(s), workers_joined=%s;"
                    " keeping the single-instance record and the process alive",
                    len(report.stopped_tasks),
                    report.workers_joined,
                )
                return report
            own = os.environ.get("QIANFAN_INSTANCE", "").strip()
            settings = app_state.settings
            lock_file = server_lock.lock_path(settings)
            server_lock.release_lock(lock_file, own)
            record = server_lock.read_lock(lock_file)
            report.lock_released = record is None or record.instance != own
            request_exit()
            report.exit_requested = True
            logger.info(
                "server stop: complete — %d task(s) parked, lock_released=%s,"
                " exit_requested=%s",
                len(report.stopped_tasks),
                report.lock_released,
                report.exit_requested,
            )
            return report
        finally:
            app_state.stop_in_progress = False
