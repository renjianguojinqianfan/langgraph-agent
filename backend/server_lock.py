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
    #: ``serve`` = qianfan (same-origin build); ``dev`` = start.py (Vite on
    #: :5173, the API port does not necessarily serve the UI).
    mode: str = "serve"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pid": self.pid,
            "port": self.port,
            "started_at": self.started_at,
            "instance": self.instance,
            "mode": self.mode,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ServerLock":
        return cls(
            pid=int(data["pid"]),
            port=int(data["port"]),
            started_at=str(data["started_at"]),
            instance=str(data["instance"]),
            mode=str(data.get("mode") or "serve"),
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
    """Publish a complete record atomically; False when another launcher owns it.

    The record is written and fsynced under a sibling temp name, then hard
    linked into place. Readers therefore never see an empty/partial lock, and
    only one of two concurrent claimers can win the link.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(lock.to_dict(), handle)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp, path)
        except FileExistsError:
            return False
        return True
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def release_lock(path: Path, instance: str) -> None:
    """Remove the record only when it still belongs to ``instance``."""
    current = read_lock(path)
    if current is None or current.instance != instance:
        return
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass


def _drop_corrupt(path: Path) -> None:
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


def health_url(host: str, port: int) -> str:
    """The health URL for a configured loopback host (M2 / #259).

    IPv6 literals need brackets in a URL authority; callers pass the plain
    configured host (`::1`), never a pre-bracketed form.
    """
    target = f"[{host}]" if ":" in host else host
    return f"http://{target}:{port}/health"


def probe_health(
    port: int, timeout: float = 1.0, host: str = "127.0.0.1"
) -> Optional[Dict[str, Any]]:
    try:
        with urlrequest.urlopen(health_url(host, port), timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def find_running(
    lock_file: Path,
    *,
    probe: Optional[Callable[[int, float], Optional[Dict[str, Any]]]] = None,
    wait: float = 15.0,
    interval: float = 0.2,
    host: str = "127.0.0.1",
) -> Optional[ServerLock]:
    """Return the live backend, or clear a stale record and return None.

    Stale = a dead pid, an unreadable record, or a live pid whose ``/health``
    answers with a *different* instance id (pid reuse / another program on the
    port). A live pid whose health never answers inside ``wait`` is **kept**:
    the backend is probably still starting, and deleting its record would let a
    later launch start a second writer of ``tasks.json``.
    """
    lock = read_lock(lock_file)
    if lock is None:
        if Path(lock_file).exists():
            _drop_corrupt(lock_file)
        return None
    if not pid_alive(lock.pid):
        release_lock(lock_file, lock.instance)
        return None
    effective_probe = probe
    if effective_probe is None:

        def effective_probe(target_port: int, target_timeout: float) -> Optional[Dict[str, Any]]:
            return probe_health(target_port, target_timeout, host=host)
    deadline = time.monotonic() + wait
    while True:
        health = effective_probe(lock.port, 1.0)
        if health is not None:
            if str(health.get("instance") or "") == lock.instance:
                return lock
            release_lock(lock_file, lock.instance)  # another instance owns the port
            return None
        if time.monotonic() >= deadline:
            return None  # still booting: keep the record
        time.sleep(interval)


def ensure_single_backend(
    settings: Any,
    *,
    probe: Optional[Callable[[int, float], Optional[Dict[str, Any]]]] = None,
    wait: float = 1.0,
    instance: Optional[str] = None,
) -> Optional[ServerLock]:
    """D-42 (#259): startup guard — only launcher-managed backends may serve.

    The caller's own identity comes from `QIANFAN_INSTANCE` (set by `cli` /
    `start.py`). When the recorded instance is ours we return immediately
    instead of probing our own health: `find_running` would otherwise wait for
    a server that is this very process, i.e. "waiting on ourselves".

    A manual `uvicorn backend.main:app` has no instance id and is refused
    outright (H2): allowing it would bypass the single-backend, loopback-host
    and key-posture guards that only the launchers wire up. A different live
    instance raises (the app fails startup; `cli` treats the same condition as
    a handoff). A record whose pid is alive but whose `/health` never answers is
    a *boot in progress*, not "nothing running" (H1): refusing it closes the
    window in which a second writer could start.
    """
    lock_file = lock_path(settings)
    own = instance if instance is not None else os.environ.get("QIANFAN_INSTANCE", "")
    if not own:
        raise RuntimeError(
            "手动 uvicorn 启动被拒绝：没有 QIANFAN_INSTANCE 实例标识，"
            "会绕过单后台 / 回环 host / Key 启动守卫。请用 `qianfan`（日常）"
            "或 `python start.py`（开发）启动。"
        )
    record = read_lock(lock_file)
    if record is not None and record.instance == own:
        return record
    host = str(getattr(settings, "host", "") or "127.0.0.1").strip() or "127.0.0.1"
    running = find_running(lock_file, probe=probe, wait=wait, host=host)
    if running is not None:
        if running.instance == own:
            return running
        raise RuntimeError(
            f"已有后台在 http://{host}:{running.port}/ 运行；"
            "同一台机器只允许一个后台写 data/。请直接打开现有页面，"
            "或先停掉它（关掉启动它的终端）；手动 uvicorn 请改用 qianfan。"
        )
    # find_running returns None both for "no record" and for "live pid, health
    # not up (still booting, record kept)". Only the latter is occupied.
    pending = read_lock(lock_file)
    if pending is not None and pid_alive(pending.pid):
        raise RuntimeError(
            f"后台正在启动中（pid={pending.pid}, port={pending.port}）："
            "请稍候再试，或直接使用 qianfan；不要同时启动第二个后台。"
        )
    return None


def update_lock_pid(path: Path, instance: str, pid: int) -> bool:
    """D-42 (#259): swap the launcher pid for the real backend pid.

    `start.py` claims the record *before* spawning uvicorn (so no second
    launcher can slip a writer in during the boot gap), then rewrites the pid
    once the child exists. False means the record was lost or taken over; the
    launcher must back out instead of running unregistered.

    `uvicorn --reload` complicates this (M1): the recorded pid must stay the
    reload *supervisor* (the launcher-spawned process that survives reloads),
    not a worker — a worker pid dies on every reload and a stale-detector would
    then free a lock the supervisor still holds. `start.py` sets
    `QIANFAN_LOCK_KEEP_PID` and the app lifespan skips this transfer.
    """
    lock = read_lock(path)
    if lock is None or lock.instance != instance:
        return False
    updated = ServerLock(
        pid=pid,
        port=lock.port,
        started_at=lock.started_at,
        instance=lock.instance,
        mode=lock.mode,
    )
    path = Path(path)
    temp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(updated.to_dict(), handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        return True
    except OSError:
        return False
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
