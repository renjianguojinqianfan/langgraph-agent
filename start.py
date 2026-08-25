#!/usr/bin/env python3
"""One-command launcher for the LangGraph autonomous-task agent.

Starts the FastAPI backend (uvicorn) and the Vite frontend (npm run dev).

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


def _run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> subprocess.Popen:
    print(f"[start] {' '.join(cmd)}", flush=True)
    return subprocess.Popen(cmd, cwd=str(cwd or ROOT), env=env, shell=False)


def main() -> int:
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
    if args.mock:
        env["USE_MOCK_LLM"] = "true"

    procs: list[subprocess.Popen] = []
    try:
        if not args.frontend_only:
            backend_cmd = [
                sys.executable, "-m", "uvicorn", "backend.main:app",
                "--host", "0.0.0.0", "--port", str(args.port), "--reload",
            ]
            procs.append(_run(backend_cmd, cwd=ROOT, env=env))
        if not args.backend_only:
            if not (ROOT / "frontend" / "node_modules").exists():
                print("[start] frontend/node_modules missing — run with --install first.")
            procs.append(_run(["npm", "run", "dev"], cwd=str(ROOT / "frontend"), env=env))
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
