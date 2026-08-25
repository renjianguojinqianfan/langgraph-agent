"""Sandboxed code execution helper.

Runs Python / shell code inside a throwaway working directory with a wall-clock
timeout. This is a *basic* sandbox (no syscall filtering / cgroup limits); for
stronger isolation run the whole backend inside Docker (see ``docker-compose.yml``).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from subprocess import PIPE, TimeoutExpired, run
from typing import Any, Dict

from .logging import get_logger

logger = get_logger("sandbox")


def run_code(
    language: str,
    code: str,
    timeout: int = 30,
    workdir: Path | None = None,
) -> Dict[str, Any]:
    """Execute ``code`` and return ``{stdout, stderr, exit_code, error}``.

    ``language`` is one of ``python`` / ``shell`` (bash) / ``cmd``.
    """
    if workdir is None:
        workdir = Path(tempfile.mkdtemp(prefix="agent_sandbox_"))
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"

    try:
        if language == "python":
            script = workdir / "solution.py"
            script.write_text(code, encoding="utf-8")
            cmd = [sys.executable, str(script)]
        elif language in ("shell", "bash"):
            cmd = ["bash", "-c", code]
        elif language in ("cmd", "batch"):
            cmd = ["cmd", "/c", code]
        else:
            return {
                "stdout": "",
                "stderr": "",
                "exit_code": -1,
                "error": f"Unsupported language: {language}",
            }

        proc = run(
            cmd,
            cwd=str(workdir),
            env=env,
            stdout=PIPE,
            stderr=PIPE,
            text=True,
            timeout=timeout,
            shell=False,
        )
        return {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
            "error": "" if proc.returncode == 0 else proc.stderr,
        }
    except TimeoutExpired:
        logger.warning("Code execution timed out after %ss", timeout)
        return {
            "stdout": "",
            "stderr": "",
            "exit_code": -1,
            "error": f"Execution timed out after {timeout}s",
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Sandbox execution error")
        return {"stdout": "", "stderr": "", "exit_code": -1, "error": str(exc)}
