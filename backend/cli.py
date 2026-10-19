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

from .config import get_settings, reset_settings

HOST = "127.0.0.1"  # ADR 0010 decision 6: local only


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((HOST, port))
        except OSError:
            return False
    return True


def _schedule_browser(url: str) -> None:
    timer = threading.Timer(1.0, webbrowser.open, args=(url,))
    timer.daemon = True
    timer.start()


def _serve(host: str, port: int) -> None:
    import uvicorn

    uvicorn.run("backend.main:app", host=host, port=port)


def main(
    argv: list[str] | None = None,
    *,
    serve: Callable[[str, int], None] | None = None,
    open_browser: Callable[[str], None] | None = None,
) -> int:
    settings = get_settings()
    dist: Path = settings.frontend_dist_path

    parser = argparse.ArgumentParser(prog="qianfan", description="Run qianfan and open it in the browser")
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    args = parser.parse_args(argv)

    if not (dist / "index.html").is_file():
        print(f"[qianfan] 找不到前端构建产物：{dist}")
        print("[qianfan] 先在 frontend/ 下执行 `npm install && npm run build`；改前端时用 `python start.py`。")
        return 1

    if not _port_available(args.port):
        print(f"[qianfan] 端口 {args.port} 已被占用，可能已有一个 qianfan 在运行。")
        print(f"[qianfan] 换一个端口：qianfan --port {args.port + 1}")
        return 1

    os.environ["PORT"] = str(args.port)
    reset_settings()

    url = f"http://{HOST}:{args.port}/"
    if not args.no_browser:
        (open_browser or _schedule_browser)(url)
    (serve or _serve)(HOST, args.port)
    return 0
