"""Single-instance registry for the local backend (W2 / ADR 0010 decision 3).

``data/server.json`` records the serving process (pid, port, start time, a
one-time instance id). A second launcher probes ``/health`` for that id and
hands the browser to the running backend instead of starting a second writer
of ``tasks.json``. A dead pid or a mismatched id (Windows reuses pids) is a
stale record and gets taken over.

Standard library only: ``start.py`` imports this before the runtime deps are
installed.
"""

from __future__ import annotations

import ctypes
import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib import request as urlrequest

LOCK_NAME = "server.json"
_STILL_ACTIVE = 259


@dataclass(frozen=True)
class ServerLock:
    pid: int
    port: int
    started_at: str
    instance: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pid": self.pid,
            "port": self.port,
            "started_at": self.started_at,
            "instance": self.instance,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ServerLock":
        return cls(
            pid=int(data["pid"]),
            port=int(data["port"]),
            started_at=str(data["started_at"]),
            instance=str(data["instance"]),
        )


def new_instance() -> str:
    return uuid.uuid4().hex


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def lock_path(settings: Any) -> Path:
    return Path(settings.data_path) / LOCK_NAME


def read_lock(path: Path) -> Optional[ServerLock]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return ServerLock.from_dict(data)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def claim_lock(path: Path, lock: ServerLock) -> bool:
    """Create the record atomically; False when another launcher owns it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(lock.to_dict(), handle)
    return True


def release_lock(path: Path, instance: str) -> None:
    """Remove the record only when it still belongs to ``instance``."""
    current = read_lock(path)
    if current is None or current.instance != instance:
        return
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_pid_alive(pid: int) -> bool:
    # os.kill(pid, 0) would call TerminateProcess on Windows — never use it.
    loader = getattr(ctypes, "WinDLL")
    kernel32 = loader("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    open_process.restype = ctypes.c_void_p
    get_exit_code = kernel32.GetExitCodeProcess
    get_exit_code.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    get_exit_code.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int

    handle = open_process(0x1000, 0, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        last_error: Any = getattr(ctypes, "get_last_error", lambda: 0)()
        return int(last_error) == 5  # access denied -> still alive
    try:
        code = ctypes.c_uint32()
        if not get_exit_code(handle, ctypes.byref(code)):
            return True
        return int(code.value) == _STILL_ACTIVE
    finally:
        close_handle(handle)


def probe_health(port: int, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
    try:
        with urlrequest.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def find_running(
    lock_file: Path,
    *,
    probe: Callable[[int, float], Optional[Dict[str, Any]]] = probe_health,
    wait: float = 5.0,
    interval: float = 0.2,
) -> Optional[ServerLock]:
    """Return the live backend, or clear a stale record and return None.

    A live pid whose ``/health`` does not carry the recorded instance id is
    stale (pid reuse / a different program on the port); a dead pid is stale
    immediately.
    """
    lock = read_lock(lock_file)
    if lock is None:
        return None
    if not pid_alive(lock.pid):
        release_lock(lock_file, lock.instance)
        return None
    deadline = time.monotonic() + wait
    while True:
        health = probe(lock.port, 1.0)
        if health is not None:
            if str(health.get("instance") or "") == lock.instance:
                return lock
            break  # another instance owns the port: the record is stale
        if time.monotonic() >= deadline:
            break
        time.sleep(interval)
    release_lock(lock_file, lock.instance)
    return None
