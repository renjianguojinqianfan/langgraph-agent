"""``qianfan``: one command that ensures the shared backend and opens the UI.

The backend is an independent process (ADR 0013 decision 2): plain ``qianfan``
claims the single-instance record, spawns a detached ``backend.serve`` child,
waits for ``/health`` and opens the browser, then exits. The backend keeps
running after the launching terminal closes.

Subcommands:

* ``qianfan serve`` — foreground server entry (internal / manual debugging);
* ``qianfan status`` — pid / port / mode / instance / started_at / healthy;
* ``qianfan stop`` — asks ``POST /api/server/stop`` for the registered instance
  to shut down, then waits for the process to exit;
* ``qianfan repl`` — the terminal client: ensures the same backend (no browser)
  and drives it over REST/SSE only (#332 S6).

Development keeps using ``start.py`` (Vite hot reload); ``qianfan`` never takes
over a dev backend (``mode=dev``).
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import quote

from . import server_lock
from .config import get_settings, reset_settings

HOST = "127.0.0.1"  # ADR 0010 decision 6: local only

#: How long a cold start may take before the launcher gives up (checkpoint
#: mounting on a cold start made a fixed shorter timer lose the race, #245).
READY_TIMEOUT_SEC = 15.0
READY_POLL_INTERVAL_SEC = 0.2

#: How long ``qianfan stop`` waits for the process to actually disappear after
#: the endpoint reported a complete shutdown.
STOP_TIMEOUT_SEC = 15.0

#: Startup stdout/stderr of the detached child (the app log stays agent.log).
SERVER_LOG_NAME = "server.log"

#: One seam for the detach flags so both platform branches stay testable.
_IS_WINDOWS = os.name == "nt"


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((HOST, port))
        except OSError:
            return False
    return True


def _stale_build(dist: Path) -> bool:
    """True when frontend/src is newer than the built index (R13)."""
    index = dist / "index.html"
    src = dist.parent / "src"
    if not src.is_dir():
        return False
    try:
        newest = max(p.stat().st_mtime for p in src.rglob("*") if p.is_file())
        return newest > index.stat().st_mtime
    except OSError:
        return False


def _open_browser_now(url: str) -> None:
    """The default browser seam: ``main()`` returns right after opening."""
    webbrowser.open(url)


def _open_when_ready(
    url: str,
    port: int,
    *,
    browser: Optional[Callable[[str], None]],
    probe: Callable[[int, float], Optional[dict[str, Any]]],
    wait: float = READY_TIMEOUT_SEC,
    interval: float = READY_POLL_INTERVAL_SEC,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    alive: Optional[Callable[[], bool]] = None,
) -> bool:
    """Wait for ``/health``; open ``url`` only when it answers (#245).

    ``browser=None`` waits without opening (``--no-browser``). ``alive`` lets a
    cold start fail fast when the detached child died instead of burning the
    whole readiness window; ``False`` is returned in that case too.
    """
    deadline = clock() + wait
    while True:
        if probe(port, 0.5) is not None:
            if browser is not None:
                try:
                    browser(url)
                except Exception as exc:  # pragma: no cover - OS/browser-facing
                    print(f"[qianfan] 打开浏览器失败（{exc}）；请手动打开 {url}")
                    # The backend is up; a browser failure is not a startup
                    # failure and must not tear it down.
            return True
        if alive is not None and not alive():
            return False
        if clock() >= deadline:
            print(f"[qianfan] 服务未在 {wait:g}s 内就绪，未自动打开浏览器；请手动打开 {url}")
            return False
        sleep(interval)


def _repl_entry(argv: list[str]) -> int:
    """D-48 (#20): lazily import the terminal session (keeps web startup light)."""
    from .repl import repl_main

    return repl_main(argv)


def _argv_preset(argv: list[str]) -> Optional[str]:
    """Extract a --preset value from unparsed args (repl dispatch pre-check)."""
    for index, arg in enumerate(argv):
        if arg == "--preset" and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith("--preset="):
            return arg.split("=", 1)[1]
    return None


def _open_at(port: int, no_browser: bool, browser: Callable[[str], None]) -> None:
    url = f"http://{HOST}:{port}/?dir={quote(os.getcwd())}"
    if not no_browser:
        browser(url)
    print(f"[qianfan] 已有后台在 http://{HOST}:{port}/，本目录：{os.getcwd()}")


def _handoff(
    running: server_lock.ServerLock,
    no_browser: bool,
    browser: Optional[Callable[[str], None]],
    *,
    accept_dev: bool = False,
) -> int:
    """Point the caller at a healthy backend; a refusal changes nothing.

    ``accept_dev`` is for the terminal client: it speaks REST/SSE, so a
    ``start.py`` backend on its API port is usable. ``qianfan`` keeps refusing
    it because the API port is not necessarily the dev UI.
    """
    if running.mode != "serve" and not accept_dev:
        print(
            "[qianfan] 开发后台（start.py）在跑，qianfan 不接开发后台；"
            "请用 http://127.0.0.1:5173，或先停掉 start.py。"
        )
        return 2
    if browser is None or no_browser:
        print(f"[qianfan] 已有后台在 http://{HOST}:{running.port}/，本目录：{os.getcwd()}")
        return 0
    _open_at(running.port, no_browser, browser)
    return 0


def _server_log_path(settings: Any) -> Path:
    return Path(settings.data_path) / "logs" / SERVER_LOG_NAME


def _child_env(port: int, instance: str, preset: Optional[str]) -> dict[str, str]:
    """Environment for the detached server child (PORT before first import)."""
    env = dict(os.environ)
    root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"  # startup tracebacks reach server.log promptly
    env["PORT"] = str(port)
    env["QIANFAN_INSTANCE"] = instance
    if preset:
        from .services.plugins_store import PLUGIN_PRESET_OVERRIDE_ENV

        env[PLUGIN_PRESET_OVERRIDE_ENV] = preset
    return env


def _spawn_detached(
    cmd: list[str],
    env: dict[str, str],
    log_path: Path,
    *,
    popen: Optional[Callable[..., Any]] = None,
) -> Any:
    """Start ``cmd`` in its own session; stdout/stderr append to ``log_path``.

    Windows: ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`` (plus
    ``CREATE_NO_WINDOW`` so a console python.exe never flashes a window).
    POSIX: ``start_new_session=True``. In both cases the launcher can exit
    without signalling or reaping the server.
    """
    import subprocess

    spawn = subprocess.Popen if popen is None else popen
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log_path, "a", encoding="utf-8")
    try:
        kwargs: dict[str, Any] = dict(
            cwd=os.getcwd(),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            close_fds=True,
        )
        if _IS_WINDOWS:
            kwargs["creationflags"] = (
                subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.CREATE_NO_WINDOW
            )
        else:
            kwargs["start_new_session"] = True
        return spawn(cmd, **kwargs)
    finally:
        handle.close()


def _child_command(port: int, instance: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "backend.serve",
        "--port",
        str(port),
        "--instance",
        instance,
    ]


def _preflight(
    settings: Any, port: int, preset: Optional[str], *, require_dist: bool = True
) -> Optional[int]:
    """Shared build/port checks for ``ensure`` and ``serve``; exit code or None."""
    if not 1 <= port <= 65535:
        print(f"[qianfan] 端口必须在 1–65535 之间：{port}")
        return 1
    from .services.plugins_store import resolve_effective_plugins

    try:
        resolve_effective_plugins(settings, preset_override=preset)
    except ValueError as exc:
        print(f"[qianfan] {exc}")
        return 1

    if not require_dist:
        if not _port_available(port):
            print(f"[qianfan] 端口 {port} 已被其他程序占用（不是可交接的 qianfan 后台）。")
            print(f"[qianfan] 换一个端口：qianfan --port {port + 1}")
            return 1
        return None

    dist: Path = settings.frontend_dist_path
    if not (dist / "index.html").is_file():
        print(f"[qianfan] 找不到前端构建产物：{dist}")
        print("[qianfan] 先在 frontend/ 下执行 `npm install && npm run build`；改前端时用 `python start.py`。")
        return 1
    if _stale_build(dist):
        print("[qianfan] 前端产物比 frontend/src 旧，界面可能不是最新的；重新执行 `npm run build`。")
    if not _port_available(port):
        print(f"[qianfan] 端口 {port} 已被其他程序占用（不是可交接的 qianfan 后台）。")
        print(f"[qianfan] 换一个端口：qianfan --port {port + 1}")
        return 1
    return None


def ensure_backend(
    settings: Any,
    *,
    port: int,
    preset: Optional[str] = None,
    probe: Optional[Callable[[int, float], Optional[dict[str, Any]]]] = None,
    spawn: Optional[Callable[[list[str], dict[str, str], Path], Any]] = None,
    browser: Optional[Callable[[str], None]] = None,
    require_dist: bool = True,
    accept_dev: bool = False,
) -> tuple[int, int]:
    """Ensure one shared backend is reachable; returns ``(exit_code, port)``.

    A healthy instance is reused; otherwise this process claims the
    single-instance record, spawns a detached ``backend.serve`` child and waits
    for ``/health``. ``exit_code != 0`` means nothing is running for this
    caller and the reason was already printed.

    Caller-specific knobs: ``browser=None`` connects without opening anything
    (the terminal client); ``require_dist=False`` skips the built-frontend check
    for a client that never serves the UI; ``accept_dev=True`` reuses a
    ``start.py`` backend on its API port.
    """
    probe_health = probe or server_lock.probe_health
    lock_file = server_lock.lock_path(settings)

    running = server_lock.find_running(lock_file, probe=probe_health)
    if running is not None:
        if preset:
            print("[qianfan] --preset 只在本次启动生效；已有后台在跑，忽略该参数。")
        code = _handoff(running, browser is None, browser, accept_dev=accept_dev)
        return code, running.port if code == 0 else 0

    preflight = _preflight(settings, port, preset, require_dist=require_dist)
    if preflight is not None:
        return preflight, 0

    instance = server_lock.new_instance()
    lock = server_lock.ServerLock(
        pid=os.getpid(), port=port, started_at=server_lock.now_iso(), instance=instance
    )
    if not server_lock.claim_lock(lock_file, lock):
        # Lost a race with another launcher: hand off to the winner.
        running = server_lock.find_running(lock_file, probe=probe_health)
        if running is not None:
            if preset:
                print("[qianfan] --preset 只在本次启动生效；已有后台在跑，忽略该参数。")
            code = _handoff(running, browser is None, browser, accept_dev=accept_dev)
            return code, running.port if code == 0 else 0
        print(f"[qianfan] 后台可能正在启动或登记记录损坏（{lock_file}），请稍后再试。")
        print("[qianfan] 若确认没有进程在跑，可手动删除该文件；qianfan 不会按 pid 杀进程。")
        return 1, 0

    log_path = _server_log_path(settings)
    env = _child_env(port, instance, preset)
    try:
        child = (spawn or _spawn_detached)(_child_command(port, instance), env, log_path)
    except OSError as exc:
        server_lock.release_lock(lock_file, instance)
        print(f"[qianfan] 后台启动失败（{exc}）；日志：{log_path}")
        return 1, 0

    url = f"http://{HOST}:{port}/?dir={quote(os.getcwd())}"
    ready = _open_when_ready(
        url,
        port,
        browser=browser,
        probe=probe_health,
        wait=READY_TIMEOUT_SEC,
        alive=lambda: child.poll() is None,
    )
    if not ready:
        code = child.poll()
        if code is None:
            # Still alive but not answering: end the attempt instead of
            # leaving an unregistered half-started process behind.
            child.terminate()
            try:
                child.wait(timeout=5)
            except Exception:  # pragma: no cover - defensive
                child.kill()
            print(f"[qianfan] 后台未就绪，已终止本次启动；日志：{log_path}")
        else:
            print(f"[qianfan] 后台启动失败（退出码 {code}）；日志：{log_path}")
        server_lock.release_lock(lock_file, instance)
        return 1, 0
    print(f"[qianfan] 后台已就绪（pid={child.pid}）：http://{HOST}:{port}/")
    return 0, port


def _ensure(
    argv: list[str],
    settings: Any,
    *,
    open_browser: Optional[Callable[[str], None]] = None,
    probe: Optional[Callable[[int, float], Optional[dict[str, Any]]]] = None,
    spawn: Optional[Callable[[list[str], dict[str, str], Path], Any]] = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="qianfan",
        description="Ensure the shared qianfan backend and open it in the browser",
        epilog=(
            "qianfan serve  — run the backend in the foreground (internal)\n"
            "qianfan status — show pid / port / mode / instance / healthy\n"
            "qianfan stop   — stop the registered backend gracefully\n"
            "qianfan repl   — terminal session (no browser)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    parser.add_argument(
        "--preset",
        default=None,
        help=(
            "one-shot capability preset for this run "
            "(minimal | research | full; not written to data/plugins.json)"
        ),
    )
    args = parser.parse_args(argv)

    browser: Optional[Callable[[str], None]] = None if args.no_browser else (
        open_browser or _open_browser_now
    )
    code, _port = ensure_backend(
        settings,
        port=args.port,
        preset=args.preset,
        probe=probe,
        spawn=spawn,
        browser=browser,
    )
    return code


def _serve_foreground(
    argv: list[str],
    settings: Any,
    *,
    runner: Optional[Callable[[str, int], Any]] = None,
    probe: Optional[Callable[[int, float], Optional[dict[str, Any]]]] = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="qianfan serve",
        description="Run the qianfan backend in the foreground (internal / debugging)",
    )
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument(
        "--preset",
        default=None,
        help="one-shot capability preset for this run (minimal | research | full)",
    )
    args = parser.parse_args(argv)

    probe_health = probe or server_lock.probe_health
    lock_file = server_lock.lock_path(settings)
    running = server_lock.find_running(lock_file, probe=probe_health)
    if running is not None:
        print(f"[qianfan] 已有后台在 http://{HOST}:{running.port}/ 运行；serve 不启动第二个。")
        return 2

    preflight = _preflight(settings, args.port, args.preset)
    if preflight is not None:
        return preflight

    instance = server_lock.new_instance()
    if not server_lock.claim_lock(
        lock_file,
        server_lock.ServerLock(
            pid=os.getpid(), port=args.port, started_at=server_lock.now_iso(), instance=instance
        ),
    ):
        print("[qianfan] 另一个后台刚刚启动了；serve 不启动第二个。")
        return 2

    from .services.plugins_store import PLUGIN_PRESET_OVERRIDE_ENV

    try:
        os.environ["PORT"] = str(args.port)
        os.environ["QIANFAN_INSTANCE"] = instance
        if args.preset:
            os.environ[PLUGIN_PRESET_OVERRIDE_ENV] = args.preset
        reset_settings()

        def default_runner(host: str, port: int) -> int:
            from .serve import run

            return run(host, port, instance)

        (runner or default_runner)(HOST, args.port)
    finally:
        server_lock.release_lock(lock_file, instance)
    return 0


def _status(
    argv: list[str],
    settings: Any,
    *,
    probe: Optional[Callable[[int, float], Optional[dict[str, Any]]]] = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="qianfan status", description="Show the registered backend's state"
    )
    parser.parse_args(argv)

    record = server_lock.read_lock(server_lock.lock_path(settings))
    if record is None or not server_lock.pid_alive(record.pid):
        print("[qianfan] 未运行：没有 qianfan 后台在跑")
        return 1
    probe_health = probe or (
        lambda port, timeout: server_lock.probe_health(port, timeout, host=settings.host)
    )
    health = probe_health(record.port, 1.0)
    healthy = health is not None and str(health.get("instance") or "") == record.instance
    print(
        f"[qianfan] pid={record.pid} port={record.port} mode={record.mode} "
        f"instance={record.instance} started_at={record.started_at} "
        f"healthy={'true' if healthy else 'false'}"
    )
    if not healthy:
        print("[qianfan] /health 未就绪或 instance 不匹配；后台可能正在启动或已损坏")
        return 1
    return 0


def _http_json(
    method: str,
    url: str,
    *,
    payload: Optional[dict[str, Any]] = None,
    bearer: Optional[str] = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, Any]]:
    """One JSON request; returns ``(status, body)`` including HTTP errors.

    The bearer is only ever put in the Authorization header — never in the URL,
    the printed output nor the log (#332 / ADR 0013 decision 2).
    """
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else {}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    request = urlrequest.Request(url, data=data, headers=headers, method=method)
    try:
        with urlrequest.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return int(response.status), json.loads(raw) if raw else {}
    except urlerror.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {"detail": raw}
        return int(exc.code), body


def _host_for_url(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def _bearer_for(
    settings: Any,
    port: int,
    *,
    http: Callable[..., tuple[int, dict[str, Any]]],
) -> Optional[str]:
    """Log in over loopback when auth is on; the token never leaves memory."""
    if not settings.auth_enabled:
        return None
    base = f"http://{_host_for_url(settings.host)}:{port}"
    status, body = http(
        "POST", f"{base}/api/auth/token", payload={"token": settings.auth_token}
    )
    if status != 200:
        raise RuntimeError("登录失败：认证已开启，但本机配置的 auth_token 未被接受")
    token = (body.get("data") or {}).get("token")
    if not token:
        raise RuntimeError("登录失败：响应里没有 token")
    return str(token)


def _stop(
    argv: list[str],
    settings: Any,
    *,
    http: Optional[Callable[..., tuple[int, dict[str, Any]]]] = None,
    probe: Optional[Callable[[int, float], Optional[dict[str, Any]]]] = None,
    alive: Optional[Callable[[int], bool]] = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    wait: Optional[float] = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="qianfan stop", description="Stop the registered qianfan backend"
    )
    parser.parse_args(argv)

    is_alive = server_lock.pid_alive if alive is None else alive
    timeout = STOP_TIMEOUT_SEC if wait is None else wait
    lock_file = server_lock.lock_path(settings)
    record = server_lock.read_lock(lock_file)
    if record is None or not is_alive(record.pid):
        if record is not None:
            server_lock.release_lock(lock_file, record.instance)
        print("[qianfan] 未运行：没有 qianfan 后台在跑")
        return 1

    request_json = http or _http_json
    base = f"http://{_host_for_url(settings.host)}:{record.port}"
    try:
        bearer = _bearer_for(settings, record.port, http=request_json)
    except RuntimeError as exc:
        print(f"[qianfan] {exc}")
        return 1

    try:
        status, body = request_json(
            "POST",
            f"{base}/api/server/stop",
            payload={"instance": record.instance},
            bearer=bearer,
        )
    except OSError as exc:
        print(f"[qianfan] 无法连接后台（{exc}）；未确认停止")
        return 1

    if status == 409:
        detail = str(body.get("detail") or "instance 不匹配")
        print(f"[qianfan] 拒绝停止：{detail}")
        return 1
    if status == 401:
        print("[qianfan] 拒绝停止：认证无效或已过期（token 未写入输出）")
        return 1
    data = body.get("data") or {}
    if status == 504 or not data.get("ok"):
        incomplete = body.get("message") or "后台停止未完成"
        print(f"[qianfan] {incomplete}")
        print(
            "[qianfan] 收尾状态："
            f"stopped_tasks={data.get('stopped_tasks') or []} "
            f"workers_joined={data.get('workers_joined')} "
            f"shutdown_complete={data.get('shutdown_complete')} "
            f"lock_released={data.get('lock_released')}"
        )
        return 1
    if status != 200:
        print(f"[qianfan] 后台返回了意外状态 {status}；未确认停止")
        return 1

    probe_health = probe or (
        lambda port, timeout: server_lock.probe_health(port, timeout, host=settings.host)
    )
    deadline = clock() + timeout
    while True:
        if probe_health(record.port, 0.5) is None and not is_alive(record.pid):
            print(f"[qianfan] 后台已停止（pid={record.pid}）")
            return 0
        if clock() >= deadline:
            print(
                f"[qianfan] 停止请求已完成，但后台进程在 {timeout:g}s 内仍未退出；"
                "请检查 data/logs/ 下的日志"
            )
            return 1
        sleep(0.1)


def main(
    argv: list[str] | None = None,
    *,
    serve: Callable[[str, int], None] | None = None,
    open_browser: Callable[[str], None] | None = None,
    probe: Optional[Callable[[int, float], Optional[dict[str, Any]]]] = None,
    repl: Optional[Callable[[list[str]], int]] = None,
    spawn: Optional[Callable[[list[str], dict[str, str], Path], Any]] = None,
    http: Optional[Callable[..., tuple[int, dict[str, Any]]]] = None,
) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    settings = get_settings()
    # D-45 (#271): the launcher uses the same startup guard as the app
    # entrypoint (REPL, P3-3, reuses the same function). A bad auth posture
    # must fail before anything is claimed/served.
    from .services.auth import ensure_startup_auth_configured

    try:
        ensure_startup_auth_configured(settings)
    except RuntimeError as exc:
        print(f"[qianfan] {exc}")
        return 1

    subcommand = raw_argv[0] if raw_argv else ""
    if subcommand == "repl":
        # D-48 (#20): qianfan repl is the terminal session; validate the saved
        # file + the repl --preset here too (the repl sub-parser repeats it)
        # so a bad config fails before the seam can claim a lock or spawn.
        repl_args = raw_argv[1:]
        from .services.plugins_store import resolve_effective_plugins

        try:
            resolve_effective_plugins(settings, preset_override=_argv_preset(repl_args))
        except ValueError as exc:
            print(f"[qianfan] {exc}")
            return 1
        return (repl or _repl_entry)(repl_args)
    if subcommand == "serve":
        return _serve_foreground(raw_argv[1:], settings, runner=serve, probe=probe)
    if subcommand == "status":
        return _status(raw_argv[1:], settings, probe=probe)
    if subcommand == "stop":
        return _stop(raw_argv[1:], settings, http=http, probe=probe)
    return _ensure(
        raw_argv,
        settings,
        open_browser=open_browser,
        probe=probe,
        spawn=spawn,
    )
