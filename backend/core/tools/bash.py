"""Bash tool (ADR 0009 decision 2; pi-style shell access).

The command runs through bash with the workspace root as its working
directory, an allowlisted environment (:data:`backend.utils.sandbox.ENV_ALLOWLIST`),
a wall-clock timeout and a stop watcher. There is **no sandbox**: the child has
the same account privileges as this process and the working directory is a
starting point, not a fence — see README「这不是安全边界」.

The working directory is ``Settings.artifacts_path`` (the workspace root the
file tools use). Output is captured to a bounded tail buffer rather than
``subprocess.run``'s full copy: a runaway command must not be able to exhaust
memory, and the tail is what the next model turn needs.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from ...config import Settings, get_settings
from ...utils.logging import get_logger
from ...utils.sandbox import build_sandbox_env
from .base import BaseTool, ToolResult
from .registry import register

logger = get_logger("tool.bash")

#: Per-stream byte cap kept for the model (the head is discarded).
MAX_OUTPUT_BYTES = 30_000
#: Lower/upper bounds for a per-call ``timeout`` argument.
MIN_TIMEOUT_SEC = 1
MAX_TIMEOUT_SEC = 600
#: How often the run loop checks stop / timeout / process exit.
POLL_INTERVAL_SEC = 0.1


class _TailBuffer:
    """Byte sink that keeps only the last ``limit`` bytes and counts the rest."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._buf = bytearray()
        self.total = 0

    def feed(self, chunk: bytes) -> None:
        self.total += len(chunk)
        self._buf.extend(chunk)
        overflow = len(self._buf) - self._limit
        if overflow > 0:
            del self._buf[:overflow]

    def text(self) -> str:
        return self._buf.decode("utf-8", errors="replace")

    @property
    def truncated(self) -> bool:
        return self.total > self._limit


def _is_system32_stub(path: str) -> bool:
    """True for the WSL launcher ``%SystemRoot%\\System32\\bash.exe``.

    Git Bash and the WSL stub are different shells; silently picking the stub
    would make every command behave unlike the shell the user sees.
    """
    parts = [part.lower() for part in Path(path).parts]
    return Path(path).name.lower() == "bash.exe" and "system32" in parts


def _git_bash_candidates(
    environ: Mapping[str, str], which: Callable[[str], Optional[str]]
) -> list[str]:
    """bash.exe candidates derived from a Git installation.

    First the installation that owns the ``git`` on PATH (``<root>/cmd/git.exe``
    -> ``<root>/bin/bash.exe``), then the conventional Windows install roots.
    """
    candidates: list[str] = []
    git = which("git")
    if git:
        root = Path(git).resolve().parent.parent
        candidates.extend(
            [str(root / "bin" / "bash.exe"), str(root / "usr" / "bin" / "bash.exe")]
        )
    if os.name == "nt":
        for env_name in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            base = environ.get(env_name)
            if base:
                candidates.append(str(Path(base) / "Git" / "bin" / "bash.exe"))
        local = environ.get("LOCALAPPDATA")
        if local:
            candidates.append(
                str(Path(local) / "Programs" / "Git" / "bin" / "bash.exe")
            )
    return candidates


def find_bash(
    settings: Settings,
    *,
    environ: Optional[Mapping[str, str]] = None,
    which: Optional[Callable[[str], Optional[str]]] = None,
) -> Optional[str]:
    """Resolve the bash executable: config -> Git install dir -> PATH.

    ``environ`` / ``which`` are injectable so the discovery order is testable
    without depending on the host's real installation.
    """
    env = os.environ if environ is None else environ
    lookup = shutil.which if which is None else which

    candidates: list[str] = []
    configured = (getattr(settings, "bash_path", "") or "").strip()
    if configured:
        candidates.append(configured)
    candidates.extend(_git_bash_candidates(env, lookup))
    on_path = lookup("bash")
    if on_path:
        candidates.append(on_path)

    for candidate in candidates:
        if _is_system32_stub(candidate):
            logger.warning("Ignoring the System32 bash.exe WSL stub: %s", candidate)
            continue
        if Path(candidate).is_file():
            return candidate
    return None


def _pump(pipe: Any, sink: _TailBuffer) -> None:
    """Drain ``pipe`` into ``sink`` until EOF (reader thread body)."""
    try:
        while True:
            chunk = pipe.read(8192)
            if not chunk:
                break
            sink.feed(chunk)
    except Exception:  # pragma: no cover - pipe closed under us at teardown
        pass
    finally:
        try:
            pipe.close()
        except Exception:  # pragma: no cover - defensive
            pass


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """Kill the process and all of its descendants, then reap it.

    ``Popen.kill`` only reaches the direct child; a shell command routinely
    leaves grandchildren behind. On Windows ``taskkill /T`` walks the parent
    chain; elsewhere the child was started in its own session and the whole
    process group is signalled.
    """
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except Exception:  # pragma: no cover - taskkill missing / wedged
            logger.warning("taskkill failed for pid %s", proc.pid, exc_info=True)
    else:
        # getattr: Windows typings omit the POSIX process-group API.
        killpg = getattr(os, "killpg", None)
        getpgid = getattr(os, "getpgid", None)
        sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
        try:
            if killpg is not None and getpgid is not None:
                killpg(getpgid(proc.pid), sigkill)
            else:  # pragma: no cover - non-POSIX fallback
                proc.kill()
        except ProcessLookupError:
            pass
        except Exception:  # pragma: no cover - defensive
            logger.warning("killpg failed for pid %s", proc.pid, exc_info=True)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover - last resort
        proc.kill()
        proc.wait(timeout=5)


@register
class BashTool(BaseTool):
    name = "bash"
    description = (
        "Run a bash command in the workspace root. Returns the exit code and the "
        "tail of stdout/stderr (long output is truncated). The command runs with "
        "this process's account privileges; the working directory is a starting "
        "point, not a sandbox."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Bash command line, passed to `bash -c`.",
            },
            "timeout": {
                "type": "integer",
                "description": "Optional wall-clock limit in seconds (default: server setting).",
            },
        },
        "required": ["command"],
    }
    # Danger marker (inert since #138; the policy hook is what can block a call).
    requires_confirm = True
    # Bash writes files without a single advertised ``path``, so it is not a
    # product source (same rule the removed ``code_exec`` followed).
    registers_artifact = False

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self._default_timeout = settings.sandbox_timeout if settings else 30
        #: Wired by TaskManager so a long command can observe a task stop.
        self.stop_checker: Optional[Callable[[], bool]] = None

    def _resolve_timeout(self, raw: Any) -> int:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = self._default_timeout
        if value <= 0:
            value = self._default_timeout
        return max(MIN_TIMEOUT_SEC, min(value, MAX_TIMEOUT_SEC))

    def _stop_requested(self) -> bool:
        checker = self.stop_checker
        if checker is None:
            return False
        try:
            return bool(checker())
        except Exception:  # pragma: no cover - a broken watcher must not hang a command
            logger.warning("bash stop checker failed", exc_info=True)
            return False

    def run(self, **kwargs: Any) -> ToolResult:
        command = kwargs.get("command", "")
        if not isinstance(command, str) or not command.strip():
            return ToolResult(success=False, error="`command` is required.")

        settings = self.settings or get_settings()
        bash = find_bash(settings)
        if bash is None:
            configured = (getattr(settings, "bash_path", "") or "").strip()
            hint = f" (bash_path={configured!r})" if configured else ""
            return ToolResult(
                success=False,
                error=(
                    "bash not found" + hint + ". Install Git for Windows (bash.exe "
                    "under <Git>\\bin) or put bash on PATH; System32\\bash.exe is "
                    "the WSL stub and is ignored."
                ),
            )

        timeout = self._resolve_timeout(kwargs.get("timeout"))
        workdir = settings.artifacts_path
        try:
            workdir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # pragma: no cover - defensive
            return ToolResult(success=False, error=f"cannot create workspace root: {exc}")

        env = build_sandbox_env()
        popen_kwargs: Dict[str, Any] = dict(
            cwd=str(workdir),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        if os.name != "nt":
            # Own session -> the whole tree shares one process group.
            popen_kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen([bash, "-c", command], **popen_kwargs)
        except Exception as exc:
            logger.exception("bash failed to start")
            return ToolResult(success=False, error=f"failed to start bash: {exc}")

        stdout = _TailBuffer(MAX_OUTPUT_BYTES)
        stderr = _TailBuffer(MAX_OUTPUT_BYTES)
        pumps = [
            threading.Thread(target=_pump, args=(proc.stdout, stdout), daemon=True),
            threading.Thread(target=_pump, args=(proc.stderr, stderr), daemon=True),
        ]
        for pump in pumps:
            pump.start()

        deadline = time.monotonic() + timeout
        timed_out = False
        stopped = False
        while True:
            if proc.poll() is not None:
                break
            if self._stop_requested():
                stopped = True
                _kill_tree(proc)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                _kill_tree(proc)
                break
            time.sleep(POLL_INTERVAL_SEC)

        exit_code = proc.poll()
        if exit_code is None:  # pragma: no cover - _kill_tree reaps, belt and braces
            proc.kill()
            exit_code = proc.wait(timeout=5)
        for pump in pumps:
            pump.join(timeout=5)

        out = stdout.text()
        err = stderr.text()
        if stopped:
            error = "command stopped: task stop requested"
        elif timed_out:
            error = f"command timed out after {timeout}s"
        elif exit_code != 0:
            error = err.strip() or f"command exited with code {exit_code}"
        else:
            error = ""

        return ToolResult(
            success=exit_code == 0 and not stopped and not timed_out,
            data={
                "stdout": out,
                "stderr": err,
                "exit_code": exit_code,
                "cwd": str(workdir),
                "timed_out": timed_out,
                "stopped": stopped,
                "truncated": stdout.truncated or stderr.truncated,
            },
            error=error,
        )
