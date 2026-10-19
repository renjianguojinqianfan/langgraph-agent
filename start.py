#!/usr/bin/env python3
"""Development launcher: FastAPI backend (uvicorn) + Vite (npm run dev).

Daily use is the ``qianfan`` command (same-origin build, no hot reload); this
script is for changing the frontend.

Usage:
    python start.py                 # start both
    python start.py --backend-only  # backend only
    python start.py --frontend-only # frontend only
    python start.py --mock          # run backend with the offline MockLLM
    python start.py --install       # install backend + frontend deps first
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _check_interpreter() -> None:
    """Block the known-bad root ``.venv`` (Python 3.13 without dependencies).

    AGENTS.md mandates ``.venv311`` for this project; the root ``.venv`` has no
    dependencies installed and would fail with ImportError. Other interpreters
    (system Python, CI, containers) pass through untouched.
    """
    exe = Path(sys.executable).resolve()
    for parent in exe.parents:
        if parent.name == ".venv" and parent.parent == ROOT:
            print("[start] 错误：当前使用根目录 .venv（Python 3.13，未装依赖），无法运行本项目。", flush=True)
            print("[start] 请改用 .venv311，例如：.\\.venv311\\Scripts\\python.exe start.py", flush=True)
            raise SystemExit(1)


def _run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> subprocess.Popen:
    print(f"[start] {' '.join(cmd)}", flush=True)
    return subprocess.Popen(cmd, cwd=str(cwd or ROOT), env=env, shell=False)


def _running_server(settings):
    """The live qianfan/dev backend, if one is registered (W2)."""
    from backend import server_lock

    return server_lock.find_running(server_lock.lock_path(settings))


def _refuse_if_running(settings) -> bool:
    running = _running_server(settings)
    if running is None:
        return False
    print(
        f"[start] 已有后台在 http://127.0.0.1:{running.port}/ 运行；"
        "要换目录请再敲 qianfan，开发后台不能和它同时写 data/。",
        flush=True,
    )
    return True


def main() -> int:
    _check_interpreter()
    parser = argparse.ArgumentParser(description="Launch the autonomous agent stack")
    parser.add_argument("--backend-only", action="store_true")
    parser.add_argument("--frontend-only", action="store_true")
    parser.add_argument("--mock", action="store_true", help="use offline MockLLM")
    parser.add_argument("--install", action="store_true", help="install deps first")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.install:
        print("[start] installing backend deps...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], cwd=str(ROOT), check=True)
        print("[start] installing frontend deps...")
        subprocess.run(["npm", "install"], cwd=str(ROOT / "frontend"), check=True)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    # Keep the backend's settings in step with --port: CORS derives the app's
    # own origin from `settings.port`.
    env["PORT"] = str(args.port)
    if args.mock:
        env["USE_MOCK_LLM"] = "true"

    # Import only after --install: the launcher itself stays stdlib-only until
    # the dependencies are in place (backend.server_lock is stdlib-only).
    from backend import server_lock
    from backend.config import get_settings

    settings = get_settings()
    if not args.frontend_only and _refuse_if_running(settings):
        return 2
    lock_file = server_lock.lock_path(settings)
    instance = server_lock.new_instance()
    env["QIANFAN_INSTANCE"] = instance
    claimed = False

    procs: list[subprocess.Popen] = []
    try:
        if not args.frontend_only:
            backend_cmd = [
                sys.executable, "-m", "uvicorn", "backend.main:app",
                "--host", settings.host, "--port", str(args.port), "--reload",
            ]
            backend = _run(backend_cmd, cwd=ROOT, env=env)
            procs.append(backend)
            lock = server_lock.ServerLock(
                pid=backend.pid,
                port=args.port,
                started_at=server_lock.now_iso(),
                instance=instance,
            )
            if not server_lock.claim_lock(lock_file, lock):
                print(
                    "[start] 另一个后台刚刚启动了；要换目录请再敲 qianfan。",
                    flush=True,
                )
                backend.terminate()
                return 2
            claimed = True
        if not args.backend_only:
            if not (ROOT / "frontend" / "node_modules").exists():
                print("[start] frontend/node_modules missing — run with --install first.")
            procs.append(_run(["npm", "run", "dev"], cwd=ROOT / "frontend", env=env))
        print("[start] stack is running. Press Ctrl+C to stop.", flush=True)
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        print("\n[start] stopping...")
    finally:
        for p in procs:
            p.terminate()
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
        if claimed:
            server_lock.release_lock(lock_file, instance)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
