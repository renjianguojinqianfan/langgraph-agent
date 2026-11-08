"""``qianfan``: one command that serves the backend and the built frontend.

The launcher is deliberately thin: it checks the build output and the port,
exports the chosen port so the app's CORS own-origin follows it, then hands
over to uvicorn. Development keeps using ``start.py`` (Vite hot reload).
``qianfan repl`` routes to the terminal session instead (D-48, backend/repl.py).
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from . import server_lock
from .config import get_settings, reset_settings

HOST = "127.0.0.1"  # ADR 0010 decision 6: local only


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((HOST, port))
        except OSError:
            return False
    return True


def _stale_build(dist: Path) -> bool:
    """True when frontend/src is newer than the built index (R13)."""
    index = dist / "index.html"
    src = dist.parent / "src"
    if not src.is_dir():
        return False
    try:
        newest = max(p.stat().st_mtime for p in src.rglob("*") if p.is_file())
        return newest > index.stat().st_mtime
    except OSError:
        return False


def _open_browser(url: str) -> None:
    webbrowser.open(url)


def _open_browser_now(url: str) -> None:
    """Handoff path: the process exits right after ``main()`` returns."""
    _open_browser(url)


def _open_when_ready(
    url: str,
    port: int,
    *,
    browser: Callable[[str], None],
    probe: Callable[[int, float], Optional[dict[str, Any]]],
    wait: float = 15.0,
    interval: float = 0.2,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Open ``url`` only after ``/health`` answers; timeout prints instead (#245).

    A fixed timer lost the race with checkpoint mounting on a cold start (the
    browser hit the port before uvicorn was ready and never retried). Polling
    the same health endpoint the handoff path uses keeps the two paths honest.
    """
    deadline = clock() + wait
    while True:
        if probe(port, 0.5) is not None:
            try:
                browser(url)
            except Exception as exc:  # pragma: no cover - OS/browser-facing
                print(f"[qianfan] 打开浏览器失败（{exc}）；请手动打开 {url}")
                return False
            return True
        if clock() >= deadline:
            print(f"[qianfan] 服务未在 {wait:g}s 内就绪，未自动打开浏览器；请手动打开 {url}")
            return False
        sleep(interval)


def _start_ready_watcher(
    url: str,
    port: int,
    browser: Callable[[str], None],
    probe: Callable[[int, float], Optional[dict[str, Any]]],
) -> None:
    """Watch /health in a daemon thread; ``serve`` blocks the main thread."""
    thread = threading.Thread(
        target=_open_when_ready,
        args=(url, port),
        kwargs={"browser": browser, "probe": probe},
        name="qianfan-browser-ready",
        daemon=True,
    )
    thread.start()


def _serve(host: str, port: int) -> None:
    import uvicorn

    uvicorn.run("backend.main:app", host=host, port=port)


def _repl_entry(argv: list[str]) -> int:
    """D-48 (#20): lazily import the terminal session (keeps web startup light)."""
    from .repl import repl_main

    return repl_main(argv)


def _open_at(port: int, no_browser: bool, browser: Callable[[str], None]) -> None:
    url = f"http://{HOST}:{port}/?dir={quote(os.getcwd())}"
    if not no_browser:
        browser(url)
    print(f"[qianfan] 已有后台在 http://{HOST}:{port}/，本目录：{os.getcwd()}")


def _handoff(running: server_lock.ServerLock, no_browser: bool, browser: Callable[[str], None]) -> int:
    if running.mode == "repl":
        print(
            "[qianfan] 终端会话（qianfan repl）在跑；请先在该终端输入 exit"
            " —— 关终端会停任务。"
        )
        return 2
    if running.mode != "serve":
        print(
            "[qianfan] 开发后台（start.py）在跑，qianfan 不接开发后台；"
            "请用 http://127.0.0.1:5173，或先停掉 start.py。"
        )
        return 2
    _open_at(running.port, no_browser, browser)
    return 0


def main(
    argv: list[str] | None = None,
    *,
    serve: Callable[[str, int], None] | None = None,
    open_browser: Callable[[str], None] | None = None,
    probe: Optional[Callable[[int, float], Optional[dict[str, Any]]]] = None,
    when_ready: Optional[Callable[[str, int], None]] = None,
    repl: Optional[Callable[[list[str]], int]] = None,
) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv and raw_argv[0] == "repl":
        # D-48 (#20): qianfan repl is the terminal session; the default
        # command still serves the web UI.
        return (repl or _repl_entry)(raw_argv[1:])

    settings = get_settings()
    # D-45 (#271): the launcher uses the same startup guard as the app
    # entrypoint (REPL, P3-3, reuses the same function). A bad auth posture
    # must fail before anything is claimed/served.
    from .services.auth import ensure_startup_auth_configured

    try:
        ensure_startup_auth_configured(settings)
    except RuntimeError as exc:
        print(f"[qianfan] {exc}")
        return 1
    # D-55 (#67): unknown capability preset fails at startup, before anything
    # claims the single-backend lock or touches data/.
    from .core.capability_packages import DEFAULT_PRESET, resolve_packages

    try:
        resolve_packages(getattr(settings, "plugins_preset", DEFAULT_PRESET))
    except ValueError as exc:
        print(f"[qianfan] {exc}")
        return 1
    dist: Path = settings.frontend_dist_path
    browser: Callable[[str], None] = open_browser or _open_browser
    handoff_browser = open_browser or _open_browser_now
    probe_health = probe or server_lock.probe_health
    lock_file = server_lock.lock_path(settings)

    parser = argparse.ArgumentParser(
        prog="qianfan",
        description="Run qianfan and open it in the browser",
        epilog=(
            "qianfan repl — start the terminal session instead "
            "(no browser, no uvicorn)."
        ),
    )
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    args = parser.parse_args(argv)

    if not 1 <= args.port <= 65535:
        print(f"[qianfan] 端口必须在 1–65535 之间：{args.port}")
        return 1

    # D-48 (#20): a terminal REPL holds the lock with mode="repl" but serves no
    # HTTP health endpoint, so find_running would wait out its 15s boot window.
    # Check the record directly: fast, deterministic refusal.
    existing = server_lock.read_lock(lock_file)
    if (
        existing is not None
        and existing.mode == "repl"
        and server_lock.pid_alive(existing.pid)
    ):
        print(
            "[qianfan] 终端会话（qianfan repl）在跑；请先在该终端输入 exit"
            " —— 关终端会停任务。"
        )
        return 2

    # ADR 0010 decision 3: one backend per machine. Hand the browser to the
    # live instance instead of starting a second writer of tasks.json.
    running = server_lock.find_running(lock_file, probe=probe_health)
    if running is not None:
        return _handoff(running, args.no_browser, handoff_browser)

    if not (dist / "index.html").is_file():
        print(f"[qianfan] 找不到前端构建产物：{dist}")
        print("[qianfan] 先在 frontend/ 下执行 `npm install && npm run build`；改前端时用 `python start.py`。")
        return 1

    if _stale_build(dist):
        print("[qianfan] 前端产物比 frontend/src 旧，界面可能不是最新的；重新执行 `npm run build`。")

    if not _port_available(args.port):
        print(f"[qianfan] 端口 {args.port} 已被其他程序占用（不是可交接的 qianfan 后台）。")
        print(f"[qianfan] 换一个端口：qianfan --port {args.port + 1}")
        return 1

    instance = server_lock.new_instance()
    lock = server_lock.ServerLock(
        pid=os.getpid(), port=args.port, started_at=server_lock.now_iso(), instance=instance
    )
    if not server_lock.claim_lock(lock_file, lock):
        # Lost a race with another launcher: hand off to the winner.
        running = server_lock.find_running(lock_file, probe=probe_health)
        if running is not None:
            return _handoff(running, args.no_browser, handoff_browser)
        print(f"[qianfan] 后台可能正在启动或登记记录损坏（{lock_file}），请稍后再试。")
        return 1

    try:
        os.environ["PORT"] = str(args.port)
        os.environ["QIANFAN_INSTANCE"] = instance
        reset_settings()

        # D-42 (#259): the launcher entry checks the same invariant as the
        # app's lifespan. We just claimed the record, so this recognises our own
        # instance without probing our own health; a foreign live record was
        # already handled by the handoff above.
        try:
            server_lock.ensure_single_backend(
                get_settings(), probe=probe_health, wait=0.0
            )
        except RuntimeError as exc:
            print(f"[qianfan] {exc}")
            return 2

        url = f"http://{HOST}:{args.port}/?dir={quote(os.getcwd())}"
        if not args.no_browser:
            opener = when_ready or (
                lambda target, port: _start_ready_watcher(
                    target, port, browser, probe_health
                )
            )
            opener(url, args.port)
        (serve or _serve)(HOST, args.port)
    finally:
        server_lock.release_lock(lock_file, instance)
    return 0
