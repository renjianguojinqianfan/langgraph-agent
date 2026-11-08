#!/usr/bin/env python3
"""Development launcher: FastAPI backend (uvicorn) + Vite (npm run dev).

Daily use is the ``qianfan`` command (same-origin build, no hot reload); this
script is for changing the frontend.

Usage:
    python start.py                 # start both
    python start.py --backend-only  # backend only
    python start.py --frontend-only # frontend only
    python start.py --mock          # run backend with the offline MockLLM
    python start.py --install       # CI-equivalent backend deps + frontend deps first

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


def _filtered_requirements_text() -> str:
    """requirements.txt as CI installs it directly (mcp lines filtered out).

    CI (both jobs) installs the equivalent of `grep -vE '^mcp' requirements.txt`
    and then mcp with `--no-deps`. Keeping the local installer on the same
    contract is what makes the Windows path work: mcp's metadata demands
    uvicorn>=0.31.1 while this repo pins uvicorn<0.31.
    """
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.startswith("mcp")) + "\n"


def _pip_install_commands(filtered_path: str) -> list[list[str]]:
    """The two CI-equivalent pip steps (exposed for a parser-level test)."""
    return [
        [sys.executable, "-m", "pip", "install", "-r", filtered_path],
        [sys.executable, "-m", "pip", "install", "--no-deps", "mcp>=1.2,<2.0"],
    ]


def _install_backend_deps() -> None:
    """Install runtime deps exactly like CI, in a locale-proof way.

    Plain `pip install -r requirements.txt` fails on Chinese Windows before
    parsing anything: pip decodes requirement files with the platform locale
    (cp936 -> UnicodeDecodeError, #259), and mcp's metadata conflicts with the
    pinned uvicorn. The ASCII-only requirements files plus the two CI steps
    (filter mcp, install, then mcp `--no-deps`) fix both.
    """
    import tempfile

    with tempfile.NamedTemporaryFile(
        "w",
        suffix="-requirements-ci.txt",
        delete=False,
        encoding="utf-8",
        newline="\n",
    ) as handle:
        handle.write(_filtered_requirements_text())
        filtered = handle.name
    try:
        print(
            "[start] installing backend deps (CI-equivalent: mcp filtered, then --no-deps)...",
            flush=True,
        )
        for command in _pip_install_commands(filtered):
            subprocess.run(command, cwd=str(ROOT), check=True)
    finally:
        Path(filtered).unlink(missing_ok=True)


def _launcher_env(port: int, *, mock: bool = False, base: dict | None = None) -> dict:
    """Environment shared by the backend and Vite children (D-41 / #259).

    The single `PORT` value is what keeps `--port`, the backend's own-origin
    CORS list and the Vite proxy target in step (frontend/vite.config.ts reads
    the same variable). The dict is a copy: callers may add launcher-private
    entries (`QIANFAN_INSTANCE` / `QIANFAN_LOCK_KEEP_PID`) without touching the
    parent process environment, and a supplied `base` is never mutated.
    """
    env = dict(os.environ if base is None else base)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PORT"] = str(port)
    if mock:
        env["USE_MOCK_LLM"] = "true"
    return env


def _running_server(settings):
    """The live qianfan/dev backend, if one is registered (W2)."""
    from backend import server_lock

    # M2 (#259): probe the configured loopback host, not always 127.0.0.1, so a
    # ::1-bound dev backend is still recognised as running.
    return server_lock.find_running(
        server_lock.lock_path(settings), host=settings.host
    )


def _refuse_if_running(settings) -> bool:
    from backend import server_lock

    # D-48 (#20): a terminal REPL holds the lock with mode="repl" but serves no
    # /health, so find_running would sit out its 15s boot window. Refuse from
    # the record directly instead.
    lock_file = server_lock.lock_path(settings)
    existing = server_lock.read_lock(lock_file)
    if (
        existing is not None
        and existing.mode == "repl"
        and server_lock.pid_alive(existing.pid)
    ):
        print(
            f"[start] 终端会话（qianfan repl）在运行（pid={existing.pid}）；"
            "先在该终端 exit —— 关终端会停任务。",
            flush=True,
        )
        return True
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
        _install_backend_deps()
        print("[start] installing frontend deps...")
        subprocess.run(["npm", "ci"], cwd=str(ROOT / "frontend"), check=True)

    # Both children (uvicorn and Vite) get the same PORT: the backend's CORS
    # own-origin follows settings.port, and the Vite proxy reads it too (D-41).
    env = _launcher_env(args.port, mock=args.mock)

    # backend.server_lock is stdlib-only; backend.config needs the runtime deps
    # and is imported only when this launch actually starts the backend.
    from backend import server_lock

    lock_file = None
    instance = ""
    claimed = False

    procs: list[subprocess.Popen] = []
    try:
        if not args.frontend_only:
            from backend.config import ensure_loopback_host, get_settings

            settings = get_settings()
            try:
                ensure_loopback_host(settings)
            except RuntimeError as exc:
                print(f"[start] {exc}", flush=True)
                return 2
            # D-55 (#67): resolve the capability preset before claiming the
            # single-backend lock or spawning uvicorn/Vite; an unknown/empty
            # preset fails here with a readable line and zero children.
            # D-54/D-55 (#67 P4-2): the dev launcher reads data/plugins.json
            # (no CLI flag here) before claiming the lock or spawning children;
            # an invalid file fails readably with zero spawns.
            from backend.services.plugins_store import resolve_effective_plugins

            try:
                resolve_effective_plugins(settings)
            except ValueError as exc:
                print(f"[start] {exc}", flush=True)
                return 2
            if _refuse_if_running(settings):
                return 2
            lock_file = server_lock.lock_path(settings)
            instance = server_lock.new_instance()
            # D-42 (#259): claim the record BEFORE spawning uvicorn. The old
            # order left a boot gap in which a second launcher saw a free lock
            # and started a second writer of data/. The recorded pid is swapped
            # for the real backend pid right after the spawn.
            claimed = server_lock.claim_lock(
                lock_file,
                server_lock.ServerLock(
                    pid=os.getpid(),
                    port=args.port,
                    started_at=server_lock.now_iso(),
                    instance=instance,
                    mode="dev",
                ),
            )
            if not claimed:
                print(
                    "[start] 另一个后台刚刚启动了；要换目录请再敲 qianfan。",
                    flush=True,
                )
                return 2
            env["QIANFAN_INSTANCE"] = instance
            # M1 (#259): --reload spawns a supervisor + reload workers. The
            # record must keep the supervisor pid (the process the launcher
            # spawned), so the app lifespan must not overwrite it with a worker
            # pid that dies on every reload.
            env["QIANFAN_LOCK_KEEP_PID"] = "1"
            backend_cmd = [
                sys.executable, "-m", "uvicorn", "backend.main:app",
                "--host", settings.host, "--port", str(args.port), "--reload",
            ]
            backend = _run(backend_cmd, cwd=ROOT, env=env)
            procs.append(backend)
            if not server_lock.update_lock_pid(lock_file, instance, backend.pid):
                print("[start] 单实例记录被接管，放弃本次启动。", flush=True)
                backend.terminate()
                return 2
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
        if claimed and lock_file is not None:
            server_lock.release_lock(lock_file, instance)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
