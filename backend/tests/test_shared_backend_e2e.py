"""#332 S5: real shared-backend lifecycle (real processes + HTTP).

Covers the acceptance criteria that unit seams cannot: two concurrent cold
starts produce one writer, the launcher exits while the backend stays alive,
``status``/``stop`` work against the real process, a foreign instance cannot
stop it, and a completed stop can be followed by a restart that resumes the
current-format task.

Isolation: every stack gets its own temporary data / dist / workspace / HOME,
so no real ``data/``, credentials or single-instance record is touched. The
port is ephemeral. A server that fails mid-test is force-killed in teardown.

Cost: each case starts a real uvicorn + LangGraph stack (~2-5s). Do not run
this file concurrently with a manually started backend (the single-instance
record is per data dir, but the suite's session guard assumes a quiet tree).
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from backend import server_lock
from backend.api_client import ApiClient

REPO = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _make_stack(tmp_path: Path, *, script: dict | None = None) -> SimpleNamespace:
    data = tmp_path / "data"
    dist = tmp_path / "dist"
    work = tmp_path / "work"
    home = tmp_path / "home"
    for path in (data, dist, work, home):
        path.mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text("<!doctype html><title>e2e</title>", encoding="utf-8")
    script_path = tmp_path / "mock.json"
    script_path.write_text(
        json.dumps(
            script
            if script is not None
            else {"plan": ["one"], "tool_calls": [], "final_answer": "e2e done"}
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(REPO),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
            "HOME": str(home),
            "USERPROFILE": str(home),
            "DATA_DIR": str(data),
            "ARTIFACTS_DIR": str(tmp_path / "artifacts"),
            "TRACE_DIR": str(tmp_path / "traces"),
            "CHECKPOINT_DIR": str(tmp_path / "checkpoints"),
            "FRONTEND_DIST": str(dist),
            "USE_MOCK_LLM": "true",
            "MOCK_LLM_SCRIPT_PATH": str(script_path),
            "LLM_BASE_URL": "",
            "LLM_API_KEY": "",
            "DASHSCOPE_API_KEY": "",
            "EXA_API_KEY": "",
            "SERPAPI_KEY": "",
            "AUTH_ENABLED": "false",
            "MCP_ENABLED": "false",
            "CHECKPOINT_ENABLED": "true",
            "CONTEXT_INJECT_ENABLED": "false",
            "CONTEXT_EVICT_ENABLED": "false",
            "PLUGINS_PRESET": "full",
            "SINGLE_BACKEND_GUARD_ENABLED": "true",
            "HOST": "127.0.0.1",
        }
    )
    for key in ("PORT", "QIANFAN_INSTANCE", "QIANFAN_LOCK_KEEP_PID", "PLUGINS_PRESET_OVERRIDE"):
        env.pop(key, None)
    return SimpleNamespace(env=env, data=data, work=work, dist=dist)


def _run_cli(stack: SimpleNamespace, *args: str, timeout: float = 120.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from backend.cli import main; raise SystemExit(main())",
            *args,
        ],
        cwd=str(REPO),
        env=stack.env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _launcher_popen(stack: SimpleNamespace, *args: str) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; from backend.cli import main; raise SystemExit(main())",
            *args,
        ],
        cwd=str(REPO),
        env=stack.env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _health(port: int, timeout: float = 2.0):
    try:
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, ValueError):
        return None


def _request_json(port: int, path: str, *, method: str = "GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = Request(
        f"http://127.0.0.1:{port}{path}", data=data, headers=headers, method=method
    )
    try:
        with urlopen(request, timeout=5) as response:
            raw = response.read().decode("utf-8")
            return int(response.status), json.loads(raw) if raw else {}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return int(exc.code), json.loads(raw) if raw else {}
        except ValueError:
            return int(exc.code), {"detail": raw}


def _task_status(port: int, task_id: str):
    status, body = _request_json(port, f"/api/tasks/{task_id}")
    if status != 200:
        return None
    return (body.get("data") or {}).get("status")


def _wait_until(predicate, timeout: float = 20.0, interval: float = 0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


def _record(stack: SimpleNamespace):
    return server_lock.read_lock(stack.data / "server.json")


def _force_kill(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            timeout=20,
        )
        return
    try:
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except ProcessLookupError:
        pass


@pytest.fixture
def stack(tmp_path):
    created = _make_stack(tmp_path)
    yield created
    record = _record(created)
    if record is not None and server_lock.pid_alive(record.pid):
        _run_cli(created, "stop", timeout=40)
        if server_lock.pid_alive(record.pid):
            _force_kill(record.pid)


def _start(stack: SimpleNamespace, port: int):
    result = _run_cli(stack, "--no-browser", "--port", str(port))
    assert result.returncode == 0, f"launcher failed:\n{result.stdout}\n{result.stderr}"
    record = _wait_until(lambda: _record(stack))
    assert record is not None, result.stdout
    health = _wait_until(lambda: _health(port))
    assert health is not None, result.stdout
    return record, health


def test_concurrent_cold_start_creates_exactly_one_writer(stack, tmp_path):
    port = _free_port()
    first = _launcher_popen(stack, "--no-browser", "--port", str(port))
    second = _launcher_popen(stack, "--no-browser", "--port", str(port))
    out_first, _ = first.communicate(timeout=120)
    out_second, _ = second.communicate(timeout=120)
    outputs = (out_first or "") + (out_second or "")

    assert _health(port) is not None, outputs
    record = _record(stack)
    assert record is not None, outputs
    assert record.port == port
    assert (out_first or "").count("后台已就绪") + (out_second or "").count("后台已就绪") == 1, outputs
    # Exactly one live server owns the port/instance.
    health = _health(port)
    assert health is not None and health["instance"] == record.instance
    assert server_lock.pid_alive(record.pid)


def test_launcher_exits_while_the_backend_keeps_running_and_status_reports_it(stack):
    port = _free_port()
    record, _health_body = _start(stack, port)
    assert record.pid != os.getpid()

    status = _run_cli(stack, "status", timeout=60)
    assert status.returncode == 0, status.stdout + status.stderr
    for field in (
        f"pid={record.pid}",
        f"port={port}",
        "mode=serve",
        f"instance={record.instance}",
        "started_at=",
        "healthy=true",
    ):
        assert field in status.stdout, status.stdout

    stopped = _run_cli(stack, "stop", timeout=60)
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr
    assert _wait_until(lambda: _health(port) is None), "server still answers /health"
    assert _wait_until(lambda: not server_lock.pid_alive(record.pid))
    assert _record(stack) is None


def test_stop_refuses_a_foreign_instance_without_killing_the_process(stack):
    port = _free_port()
    record, _health_body = _start(stack, port)

    status, body = _request_json(
        port, "/api/server/stop", method="POST", payload={"instance": "someone-else"}
    )

    assert status == 409
    assert "instance" in str(body.get("detail") or "")
    assert _health(port) is not None
    assert server_lock.pid_alive(record.pid)
    assert _record(stack) is not None

    stopped = _run_cli(stack, "stop", timeout=60)
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr


def _run_repl(
    stack: SimpleNamespace, cwd: Path, lines: list[str], *, timeout: float = 120.0
) -> subprocess.CompletedProcess:
    """A real ``qianfan repl`` process: piped stdin, own cwd, shared backend."""
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from backend.cli import main; raise SystemExit(main())",
            "repl",
        ],
        cwd=str(cwd),
        env=stack.env,
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _create_task(stack: SimpleNamespace, port: int, text: str) -> str:
    status, body = _request_json(
        port,
        "/api/tasks",
        method="POST",
        payload={"title": "e2e", "input": text, "workspace": str(stack.work)},
    )
    assert status == 200, body
    return str(body["data"]["task_id"])


def _task(port: int, task_id: str) -> dict:
    status, body = _request_json(port, f"/api/tasks/{task_id}")
    assert status == 200, body
    return body["data"]


def _assistant_turns(task: dict) -> list[str]:
    return [
        str(turn.get("content") or "")
        for turn in task.get("conversation") or []
        if turn.get("role") == "assistant"
    ]


def test_terminal_creates_a_task_in_its_own_directory_and_web_sees_it(stack):
    port = _free_port()
    _start(stack, port)

    repl = _run_repl(stack, stack.work, ["hello from B", "exit"])

    assert repl.returncode == 0, repl.stdout + repl.stderr
    assert "已连接后台" in repl.stdout
    with ApiClient(f"http://127.0.0.1:{port}") as client:
        tasks = client.list_tasks(limit=100, directory="all").get("tasks") or []
        mine = [task for task in tasks if task.get("workspace") == str(stack.work.resolve())]
    assert len(mine) == 1, tasks
    assert mine[0]["status"] == "INTERRUPTED"
    detail = _task(port, str(mine[0]["id"]))
    assert detail["workspace"] == str(stack.work.resolve())
    assert "e2e done" in _assistant_turns(detail)


def test_web_created_task_is_continued_from_the_terminal(stack):
    port = _free_port()
    _start(stack, port)
    task_id = _create_task(stack, port, "from web")
    assert _wait_until(lambda: _task_status(port, task_id) == "INTERRUPTED")

    repl = _run_repl(stack, stack.work, [f"/use {task_id[:8]}", "again", "exit"])

    assert repl.returncode == 0, repl.stdout + repl.stderr
    assert "已接入" in repl.stdout
    detail = _task(port, task_id)
    assert detail["status"] == "INTERRUPTED"
    assert detail["workspace"] == str(stack.work.resolve())
    assert _assistant_turns(detail).count("e2e done") == 2


def test_terminal_exit_does_not_stop_a_running_task_and_explicit_stop_works(tmp_path):
    command = "Start-Sleep -Seconds 30" if os.name == "nt" else "sleep 30"
    stack = _make_stack(
        tmp_path,
        script={
            "plan": ["wait"],
            "tool_calls": [
                {"id": "call_slow", "name": "shell", "arguments": {"command": command}}
            ],
            "final_answer": "slow done",
        },
    )
    port = _free_port()
    _start(stack, port)
    try:
        task_id = _create_task(stack, port, "slow")
        running = _wait_until(lambda: _task_status(port, task_id) == "RUNNING", timeout=10)
        assert running, "the slow task never reached RUNNING"

        repl = _run_repl(stack, stack.work, [f"/use {task_id[:8]}", "exit"])

        assert repl.returncode == 0, repl.stdout + repl.stderr
        time.sleep(0.5)
        assert _task_status(port, task_id) == "RUNNING", "terminal exit stopped the task"

        status, body = _request_json(port, f"/api/tasks/{task_id}/stop", method="POST")
        assert status == 200, body
        parked = _wait_until(lambda: _task_status(port, task_id) == "INTERRUPTED", timeout=20)
        assert parked, "explicit stop did not park the task"
    finally:
        _run_cli(stack, "stop", timeout=40)
        record = _record(stack)
        if record is not None and server_lock.pid_alive(record.pid):
            _force_kill(record.pid)


def test_a_crashed_backend_record_is_taken_over_by_the_next_launch(stack):
    port = _free_port()
    record, _health_body = _start(stack, port)
    _force_kill(record.pid)
    assert _wait_until(lambda: not server_lock.pid_alive(record.pid))

    new_record, health = _start(stack, port)

    assert new_record.instance != record.instance
    assert health["instance"] == new_record.instance
    assert server_lock.pid_alive(new_record.pid)


def test_stop_then_restart_resumes_the_current_format_task(stack):
    port = _free_port()
    _start(stack, port)

    status, body = _request_json(
        port,
        "/api/tasks",
        method="POST",
        payload={"title": "e2e", "input": "hello", "workspace": str(stack.work)},
    )
    assert status == 200, body
    task_id = body["data"]["task_id"]

    parked = _wait_until(lambda: _task_status(port, task_id) == "INTERRUPTED")
    assert parked, "the first round never parked"

    stopped = _run_cli(stack, "stop", timeout=60)
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr
    assert _wait_until(lambda: _record(stack) is None)

    _start(stack, port)
    status, body = _request_json(port, f"/api/tasks/{task_id}")
    assert status == 200, body
    assert body["data"]["status"] == "INTERRUPTED"

    status, body = _request_json(
        port, f"/api/tasks/{task_id}/messages", method="POST", payload={"input": "again"}
    )
    assert status == 200, body
    resumed = _wait_until(lambda: _task_status(port, task_id) == "INTERRUPTED")
    assert resumed, "the continuation round never parked"
