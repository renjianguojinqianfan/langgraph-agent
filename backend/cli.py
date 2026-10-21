"""``qianfan``: one command that serves the backend and the built frontend.

The launcher is deliberately thin: it checks the build output and the port,
exports the chosen port so the app's CORS own-origin follows it, then hands
over to uvicorn. Development keeps using ``start.py`` (Vite hot reload).
"""

from __future__ import annotations

import argparse
import os
import socket
import threading
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


def _open_browser_now(url: str) -> None:
    """Handoff path: the process exits right after ``main()`` returns."""
    webbrowser.open(url)


def _schedule_browser(url: str) -> None:
    timer = threading.Timer(1.0, webbrowser.open, args=(url,))
    timer.daemon = True
    timer.start()


def _serve(host: str, port: int) -> None:
    import uvicorn

    uvicorn.run("backend.main:app", host=host, port=port)


def _open_at(port: int, no_browser: bool, browser: Callable[[str], None]) -> None:
    url = f"http://{HOST}:{port}/?dir={quote(os.getcwd())}"
    if not no_browser:
        browser(url)
    print(f"[qianfan] 已有后台在 http://{HOST}:{port}/，本目录：{os.getcwd()}")


def _handoff(running: server_lock.ServerLock, no_browser: bool, browser: Callable[[str], None]) -> int:
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
) -> int:
    settings = get_settings()
    dist: Path = settings.frontend_dist_path
    browser = open_browser or _schedule_browser
    handoff_browser = open_browser or _open_browser_now
    probe_health = probe or server_lock.probe_health
    lock_file = server_lock.lock_path(settings)

    parser = argparse.ArgumentParser(prog="qianfan", description="Run qianfan and open it in the browser")
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    args = parser.parse_args(argv)

    if not 1 <= args.port <= 65535:
        print(f"[qianfan] 端口必须在 1–65535 之间：{args.port}")
        return 1

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

        url = f"http://{HOST}:{args.port}/?dir={quote(os.getcwd())}"
        if not args.no_browser:
            browser(url)
        (serve or _serve)(HOST, args.port)
    finally:
        server_lock.release_lock(lock_file, instance)
    return 0
