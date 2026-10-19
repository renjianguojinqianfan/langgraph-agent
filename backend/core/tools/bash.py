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

Background processes (S1 / #148 F3): the shell's descendants are tracked as a
POSIX process group or a Windows Job Object, so a stop can reap them even after
the shell itself exited. A command that returns leaves its background children
running; if the task is stopped later, the tool's reaper kills that tree. A
child that leaves the group/session itself (``setsid``, a self-daemonizing
service) is outside this control — it is a starting point, not a sandbox.
"""

from __future__ import annotations

import contextvars
import ctypes
import os
import select
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
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
#: How long to keep draining inherited pipes after the shell exits (F3).
DRAIN_SEC = 0.4
#: How often the background reaper checks its tracked process trees.
REAP_INTERVAL_SEC = 0.1


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

    The installation that owns the ``git`` on PATH is not always rooted at
    ``git.exe``'s grandparent: Git for Windows ships ``cmd/git.exe``,
    ``bin/git.exe`` and ``mingw64/bin/git.exe`` depending on the flavor, so walk
    a few ancestors and probe both bash homes at each level. Then the
    conventional Windows install roots.
    """
    candidates: list[str] = []
    git = which("git")
    if git:
        current = Path(git).resolve().parent
        for _ in range(4):
            candidates.append(str(current / "bin" / "bash.exe"))
            candidates.append(str(current / "usr" / "bin" / "bash.exe"))
            if current.parent == current:
                break
            current = current.parent
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


def _path_bash_candidates(
    environ: Mapping[str, str], first: Optional[str]
) -> list[str]:
    """The first ``which`` hit, then every PATH entry with the right filename.

    Scanning the entries lets discovery skip the System32 stub and keep looking
    instead of stopping at ``shutil.which``'s first (possibly useless) hit.
    """
    candidates: list[str] = []
    if first:
        candidates.append(first)
    filename = "bash.exe" if os.name == "nt" else "bash"
    for entry in (environ.get("PATH") or "").split(os.pathsep):
        if not entry:
            continue
        candidate = str(Path(entry) / filename)
        if candidate not in candidates:
            candidates.append(candidate)
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
    configured = (settings.bash_path or "").strip()
    if configured:
        candidates.append(configured)
    candidates.extend(_git_bash_candidates(env, lookup))
    candidates.extend(_path_bash_candidates(env, lookup("bash")))

    for candidate in candidates:
        if _is_system32_stub(candidate):
            logger.warning("Ignoring the System32 bash.exe WSL stub: %s", candidate)
            continue
        if Path(candidate).is_file():
            return candidate
    return None


def _peek_named_pipe() -> Any:
    """``PeekNamedPipe`` with the right prototypes (Windows only)."""
    loader: Any = getattr(ctypes, "WinDLL")
    kernel32 = loader("kernel32", use_last_error=True)
    peek = kernel32.PeekNamedPipe
    peek.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_void_p,
    ]
    peek.restype = ctypes.c_int
    return peek


def _pump_posix(fd: int, sink: _TailBuffer, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            ready, _, _ = select.select([fd], [], [], 0.1)
        except (OSError, ValueError):
            return
        if not ready:
            continue
        try:
            chunk = os.read(fd, 8192)
        except OSError:
            return
        if not chunk:
            return
        sink.feed(chunk)


def _pump_windows(fd: int, sink: _TailBuffer, stop: threading.Event) -> None:
    import msvcrt

    peek = _peek_named_pipe()
    handle = msvcrt.get_osfhandle(fd)
    while not stop.is_set():
        available = ctypes.c_uint32()
        if not peek(handle, None, 0, None, ctypes.byref(available), None):
            return
        if not available.value:
            stop.wait(0.05)
            continue
        try:
            chunk = os.read(fd, min(8192, int(available.value)))
        except OSError:
            return
        if not chunk:
            return
        sink.feed(chunk)


def _pump(pipe: Any, sink: _TailBuffer, stop: threading.Event) -> None:
    """Drain ``pipe`` into ``sink`` without blocking on inherited handles.

    A background child can keep the write end open after the shell exits;
    blocking ``read`` would then hang the reader thread. Both branches poll.
    """
    try:
        fd = pipe.fileno()
        if os.name == "nt":
            _pump_windows(fd, sink, stop)
        else:
            _pump_posix(fd, sink, stop)
    except Exception:  # pragma: no cover - pipe closed under us at teardown
        pass
    finally:
        try:
            pipe.close()
        except Exception:  # pragma: no cover - defensive
            pass


def _taskkill(pid: int) -> None:
    taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
    try:
        subprocess.run(
            [str(taskkill), "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except Exception:  # pragma: no cover - taskkill missing / wedged
        logger.warning("taskkill failed for pid %s", pid, exc_info=True)


class _ProcessContainer:
    """One shell and its descendants: a POSIX process group or a Windows job."""

    def __init__(self, proc: subprocess.Popen[bytes], job: Any = None) -> None:
        self.proc = proc
        self.job = job

    def alive(self) -> bool:
        if self.job is not None:
            return bool(self.job.active_processes() > 0)
        if os.name == "nt":
            return self.proc.poll() is None
        killpg: Any = getattr(os, "killpg")
        try:
            killpg(self.proc.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except Exception:  # pragma: no cover - defensive
            return False

    def terminate(self) -> None:
        """Kill the tree, including children left behind by an exited shell."""
        if self.job is not None:
            self.job.terminate()
        elif os.name == "nt":
            if self.proc.poll() is None:
                _taskkill(self.proc.pid)
        else:
            killpg: Any = getattr(os, "killpg")
            sigkill: Any = getattr(signal, "SIGKILL", signal.SIGTERM)
            try:
                killpg(self.proc.pid, sigkill)
            except ProcessLookupError:
                pass
            except Exception:  # pragma: no cover - defensive
                logger.warning("killpg failed for pid %s", self.proc.pid, exc_info=True)
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - last resort
            self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except Exception:  # pragma: no cover - defensive
                pass

    def close(self) -> None:
        if self.job is not None:
            self.job.close()
            self.job = None


@dataclass
class _BackgroundProcess:
    container: _ProcessContainer
    context: contextvars.Context
    checker: Callable[[], bool]


_CREATE_SUSPENDED = 0x00000004


def _make_job() -> Any:
    """A Windows Job Object, or None when unavailable (POSIX / OS failure)."""
    if os.name != "nt":
        return None
    try:
        from ...utils.winjob import WindowsJob

        return WindowsJob()
    except Exception:  # pragma: no cover - OS-facing
        logger.warning(
            "Windows Job Object unavailable; background tree cleanup falls back to taskkill",
            exc_info=True,
        )
        return None


def _never_stopped() -> bool:
    return False


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
        #: Wired by TaskManager so a long command can observe a task stop.
        self.stop_checker: Optional[Callable[[], bool]] = None
        self._background: list[_BackgroundProcess] = []
        self._background_lock = threading.Lock()
        self._reaper: Optional[threading.Thread] = None

    @property
    def _settings(self) -> Settings:
        return self.settings or get_settings()

    def _resolve_timeout(self, raw: Any) -> int:
        default = self._settings.sandbox_timeout
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = default
        if value <= 0:
            value = default
        if value > MAX_TIMEOUT_SEC:
            logger.warning(
                "bash timeout %ss exceeds the %ss cap; clamping",
                value,
                MAX_TIMEOUT_SEC,
            )
            value = MAX_TIMEOUT_SEC
        return max(MIN_TIMEOUT_SEC, value)

    def _stop_requested(self) -> bool:
        checker = self.stop_checker
        if checker is None:
            return False
        try:
            return bool(checker())
        except Exception:  # pragma: no cover - a broken watcher must not hang a command
            logger.warning("bash stop checker failed", exc_info=True)
            return False

    def _register_background(
        self,
        container: _ProcessContainer,
        context: contextvars.Context,
        checker: Callable[[], bool],
    ) -> None:
        with self._background_lock:
            self._background.append(_BackgroundProcess(container, context, checker))
            if self._reaper is None or not self._reaper.is_alive():
                self._reaper = threading.Thread(
                    target=self._reap_background, name="bash-reaper", daemon=True
                )
                self._reaper.start()

    def _reap_background(self) -> None:
        """Kill tracked trees when their task stops; drop them when they end."""
        while True:
            with self._background_lock:
                if not self._background:
                    self._reaper = None
                    return
                entries = list(self._background)
            finished: list[_BackgroundProcess] = []
            for entry in entries:
                try:
                    stopped = bool(entry.context.run(entry.checker))
                except Exception:  # pragma: no cover - a broken watcher must not wedge the reaper
                    stopped = False
                if stopped:
                    entry.container.terminate()
                    finished.append(entry)
                elif not entry.container.alive():
                    finished.append(entry)
            if finished:
                with self._background_lock:
                    for entry in finished:
                        if entry in self._background:
                            self._background.remove(entry)
                for entry in finished:
                    entry.container.close()
            else:
                time.sleep(REAP_INTERVAL_SEC)

    def run(self, **kwargs: Any) -> ToolResult:
        command = kwargs.get("command", "")
        if not isinstance(command, str) or not command.strip():
            return ToolResult(success=False, error="`command` is required.")

        stop_context = contextvars.copy_context()
        stop_checker: Callable[[], bool] = self.stop_checker or _never_stopped
        settings = self._settings
        bash = find_bash(settings)
        if bash is None:
            configured = (settings.bash_path or "").strip()
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
        job = _make_job()
        if job is not None:
            # Start suspended so the shell cannot spawn before it joins the job.
            popen_kwargs["creationflags"] = _CREATE_SUSPENDED
        elif os.name != "nt":
            # Own session -> the whole tree shares one process group.
            popen_kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen([bash, "-c", command], **popen_kwargs)
        except Exception as exc:
            if job is not None:
                job.close()
            logger.exception("bash failed to start")
            return ToolResult(success=False, error=f"failed to start bash: {exc}")

        if job is not None:
            assigned = True
            try:
                job.assign(proc)
            except Exception:  # pragma: no cover - job assignment is OS-facing
                assigned = False
                logger.warning("job assignment failed; background cleanup is best effort", exc_info=True)
            try:
                job.resume(proc)
            except Exception as exc:  # pragma: no cover - OS-facing
                job.close()
                proc.kill()
                return ToolResult(success=False, error=f"failed to resume bash: {exc}")
            if not assigned:
                job.close()
                job = None
        container = _ProcessContainer(proc, job)

        stdout = _TailBuffer(MAX_OUTPUT_BYTES)
        stderr = _TailBuffer(MAX_OUTPUT_BYTES)
        stop_event = threading.Event()
        pumps = [
            threading.Thread(
                target=_pump, args=(proc.stdout, stdout, stop_event), name="bash-pump-out", daemon=True
            ),
            threading.Thread(
                target=_pump, args=(proc.stderr, stderr, stop_event), name="bash-pump-err", daemon=True
            ),
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
                container.terminate()
                break
            if time.monotonic() >= deadline:
                timed_out = True
                container.terminate()
                break
            time.sleep(POLL_INTERVAL_SEC)

        # Bounded drain: a background child may hold the write end open. Give
        # the pumps a short window, then stop them without waiting for EOF.
        drain_deadline = time.monotonic() + DRAIN_SEC
        while time.monotonic() < drain_deadline:
            if not any(pump.is_alive() for pump in pumps):
                break  # EOF reached: nothing is holding the pipes
            if not stopped and not timed_out and self._stop_requested():
                stopped = True
                container.terminate()
                break
            stop_event.wait(0.05)
        stop_event.set()
        for pump in pumps:
            pump.join(timeout=0.5)

        exit_code = proc.poll()
        if exit_code is None:  # pragma: no cover - terminate reaps, belt and braces
            container.terminate()
            exit_code = proc.wait(timeout=5)

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

        if container.alive():
            self._register_background(container, stop_context, stop_checker)
        else:
            container.close()

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
