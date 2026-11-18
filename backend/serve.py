"""Programmatic uvicorn entry for the shared backend (#332 S5).

``qianfan serve`` (foreground) and the detached child spawned by plain
``qianfan`` run the same code path: build one :class:`uvicorn.Server` around
the assembled FastAPI app, install the stop endpoint's exit hook, serve, and
release this instance's single-instance record when uvicorn returns.

``python -m backend.serve`` is the internal detached entry; a hand start
without an instance id is refused for the same reason a manual
``uvicorn backend.main:app`` is (it would bypass the single-backend, loopback
host and key-posture guards, #259).
"""

from __future__ import annotations

import argparse
import os
from typing import Any, Callable, Optional

from . import server_lock

HOST = "127.0.0.1"  # ADR 0010 decision 6: local only

ServerFactory = Callable[[Any, str, int], Any]


def build_server(app: Any, host: str, port: int) -> Any:
    """One uvicorn server around ``app`` (default factory for :func:`run`)."""
    import uvicorn

    return uvicorn.Server(uvicorn.Config(app, host=host, port=port))


def run(
    host: str,
    port: int,
    instance: str,
    *,
    app: Any = None,
    settings: Any = None,
    server_factory: Optional[ServerFactory] = None,
) -> int:
    """Serve ``app`` until uvicorn exits; always release our lock record.

    The stop endpoint reaches uvicorn through ``app.state.request_server_exit``
    (installed here, before startup). ``server.run`` may raise (bind failure);
    that propagates to the caller after the lock release.
    """
    if app is None:
        from .main import app as assembled_app

        app = assembled_app
    factory = server_factory or build_server
    server = factory(app, host, port)

    def request_exit() -> None:
        server.should_exit = True

    app.state.request_server_exit = request_exit
    if settings is None:
        from .config import get_settings

        settings = get_settings()
    lock_file = server_lock.lock_path(settings)
    try:
        server.run()
    finally:
        server_lock.release_lock(lock_file, instance)
    return 0


def main(
    argv: Optional[list[str]] = None,
    *,
    settings: Any = None,
) -> int:
    """``python -m backend.serve`` — the detached child entry (internal)."""
    parser = argparse.ArgumentParser(
        prog="python -m backend.serve",
        description="Serve the assembled qianfan app in the foreground (internal).",
    )
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--instance", default="")
    args = parser.parse_args(argv)

    instance = args.instance.strip() or os.environ.get("QIANFAN_INSTANCE", "").strip()
    if not instance:
        print(
            "[serve] 拒绝启动：没有 QIANFAN_INSTANCE 实例标识，"
            "手动启动会绕过单后台 / 回环 host / Key 启动守卫。"
            "请用 `qianfan`（日常）或 `qianfan serve`（前台）。"
        )
        return 2
    # The app reads PORT at import time (CORS own-origin follows it), so the
    # environment is complete before the first settings/app import.
    os.environ["PORT"] = str(args.port)
    os.environ["QIANFAN_INSTANCE"] = instance
    if settings is None:
        from .config import ensure_loopback_host, get_settings, reset_settings

        reset_settings()
        settings = get_settings()
        try:
            ensure_loopback_host(settings)
        except RuntimeError as exc:
            print(f"[serve] {exc}")
            return 2
    return run(settings.host, args.port, instance, settings=settings)


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())
