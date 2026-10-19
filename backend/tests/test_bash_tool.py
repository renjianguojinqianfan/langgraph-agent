"""bash tool tests (C3 / ADR 0009 decision 2).

The contract under test:

* the command runs with the workspace root as cwd;
* the child sees only the ``ENV_ALLOWLIST`` environment (no secrets);
* the wall-clock timeout is configurable and kills the whole process tree;
* a task stop kills the whole process tree too;
* long output is truncated to its tail;
* Windows bash discovery follows config -> Git install dir -> PATH and never
  picks the ``System32\\bash.exe`` WSL stub.

Everything is offline; the commands are plain shell builtins / ``sleep``.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

from backend.config import Settings
from backend.core.llm.client import MockLLMClient
from backend.core.tools.bash import BashTool, find_bash
from backend.tests.conftest import make_manager, make_settings


def _bash_settings(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(
        data_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        trace_dir=str(tmp_path / "traces"),
        llm_base_url="",
        use_mock_llm=True,
        mcp_enabled=False,
        sandbox_timeout=5,
        **overrides,
    )


def test_runs_in_the_workspace_root(settings):
    tool = BashTool(settings)
    res = tool.run(command="echo hello > probe.txt")
    assert res.success is True, res.error
    assert res.data["exit_code"] == 0
    assert res.data["stdout"] == ""  # the echo was redirected to the file
    assert (settings.artifacts_path / "probe.txt").read_text(encoding="utf-8").strip() == "hello"


def test_child_env_is_the_allowlist(settings, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "super-secret-do-not-leak")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "super-secret-do-not-leak")
    tool = BashTool(settings)
    res = tool.run(
        command=(
            'if [ -n "$LLM_API_KEY" ] || [ -n "$DASHSCOPE_API_KEY" ]; '
            "then echo leaked; else echo clean; fi"
        )
    )
    assert res.success is True, res.error
    assert res.data["stdout"].strip() == "clean"
    assert "super-secret-do-not-leak" not in res.data["stdout"]


def test_child_env_keeps_path(settings):
    tool = BashTool(settings)
    res = tool.run(command='if [ -n "$PATH" ]; then echo has-path; else echo no-path; fi')
    assert res.success is True, res.error
    assert res.data["stdout"].strip() == "has-path"


def test_captures_stdout_stderr_and_exit_code(settings):
    tool = BashTool(settings)
    ok = tool.run(command="echo out; echo err >&2")
    assert ok.success is True
    assert ok.data["stdout"].strip() == "out"
    assert ok.data["stderr"].strip() == "err"

    bad = tool.run(command="echo nope >&2; exit 3")
    assert bad.success is False
    assert bad.data["exit_code"] == 3
    assert "nope" in bad.error


def test_honours_timeout_and_kills_the_process(settings):
    tool = BashTool(settings)
    start = time.monotonic()
    res = tool.run(command="sleep 30", timeout=1)
    elapsed = time.monotonic() - start
    assert res.success is False
    assert res.data["timed_out"] is True
    assert "timed out" in res.error.lower()
    assert elapsed < 10


def test_stop_kills_the_whole_process_tree(settings):
    """A background child must not survive the stop (taskkill /T / killpg)."""
    started = settings.artifacts_path / "started.txt"
    survived = settings.artifacts_path / "child_survived.txt"
    flag = threading.Event()
    tool = BashTool(settings)
    tool.stop_checker = flag.is_set

    def _stop_after_start() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if started.exists():
                flag.set()
                return
            time.sleep(0.02)

    watcher = threading.Thread(target=_stop_after_start, daemon=True)
    watcher.start()
    res = tool.run(
        command=(
            "echo started > started.txt; "
            "(sleep 2; echo leaked > child_survived.txt) & wait"
        ),
        timeout=30,
    )
    watcher.join(timeout=5)

    assert res.success is False
    assert res.data["stopped"] is True
    assert "stop" in res.error.lower()
    # Give the orphaned child time to write its marker if the tree was not killed.
    time.sleep(2.5)
    assert not survived.exists()


def test_background_command_returns_without_waiting_for_inherited_pipes(settings):
    """`cmd &` must not hold the call open through the child's inherited pipes."""
    tool = BashTool(settings)
    flag = threading.Event()
    tool.stop_checker = flag.is_set
    start = time.monotonic()
    res = tool.run(command="sleep 5 & echo started", timeout=30)
    elapsed = time.monotonic() - start

    assert res.success is True, res.error
    assert res.data["stdout"].strip() == "started"
    assert elapsed < 2.0, f"background command took {elapsed:.2f}s"
    flag.set()  # reap the background sleep promptly


def test_pump_threads_exit_after_return(settings):
    tool = BashTool(settings)
    flag = threading.Event()
    tool.stop_checker = flag.is_set
    res = tool.run(command="sleep 5 & echo started", timeout=30)
    assert res.success is True, res.error

    pumps = [t for t in threading.enumerate() if t.name.startswith("bash-pump")]
    assert pumps == []
    flag.set()


def test_stop_after_return_kills_the_background_process(settings):
    """A stop requested after the call returned still reaps its background tree."""
    marker = settings.artifacts_path / "late_survivor.txt"
    flag = threading.Event()
    tool = BashTool(settings)
    tool.stop_checker = flag.is_set

    res = tool.run(command=f"(sleep 3; echo leaked > '{marker.as_posix()}') & echo started", timeout=30)
    assert res.success is True, res.error
    assert not marker.exists()

    flag.set()
    time.sleep(3.5)
    assert not marker.exists(), "background process survived the task stop"


def test_background_process_runs_when_not_stopped(settings):
    """Control for the test above: without a stop the marker does appear."""
    marker = settings.artifacts_path / "ran_without_stop.txt"
    tool = BashTool(settings)
    tool.stop_checker = threading.Event().is_set

    res = tool.run(command=f"(sleep 2; echo ran > '{marker.as_posix()}') & echo started", timeout=30)
    assert res.success is True, res.error
    time.sleep(2.8)
    assert marker.exists()


def test_long_output_keeps_the_tail(settings):
    tool = BashTool(settings)
    res = tool.run(command="for i in $(seq 1 5000); do echo line-$i; done")
    assert res.success is True, res.error
    assert res.data["truncated"] is True
    assert "line-5000" in res.data["stdout"]
    assert "line-1\n" not in res.data["stdout"]
    assert len(res.data["stdout"]) <= 30_000


def test_empty_command_is_rejected(settings):
    res = BashTool(settings).run(command="   ")
    assert res.success is False
    assert "command" in res.error.lower()


def test_missing_bash_returns_a_readable_error(settings):
    with patch("backend.core.tools.bash.find_bash", return_value=None):
        res = BashTool(settings).run(command="echo hi")
    assert res.success is False
    assert "bash" in res.error.lower()
    assert "not found" in res.error.lower()


# ── Windows discovery: config -> Git install dir -> PATH ────────────────────
def _fake_bash(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def test_find_bash_prefers_the_configured_path(tmp_path):
    configured = _fake_bash(tmp_path / "custom" / "bash.exe")
    settings = _bash_settings(tmp_path, bash_path=str(configured))
    assert find_bash(settings, environ={}, which=lambda _name: None) == str(configured)


def test_find_bash_uses_the_git_install_dir(tmp_path):
    git = _fake_bash(tmp_path / "Git" / "cmd" / "git.exe")
    bash = _fake_bash(tmp_path / "Git" / "bin" / "bash.exe")
    settings = _bash_settings(tmp_path)

    def _which(name: str):
        return str(git) if name == "git" else None

    assert find_bash(settings, environ={}, which=_which) == str(bash)


def test_find_bash_reads_a_mingw64_git_install(tmp_path):
    """Git for Windows often puts git.exe under <root>/mingw64/bin."""
    git = _fake_bash(tmp_path / "Git" / "mingw64" / "bin" / "git.exe")
    bash = _fake_bash(tmp_path / "Git" / "bin" / "bash.exe")
    settings = _bash_settings(tmp_path)

    def _which(name: str):
        return str(git) if name == "git" else None

    assert find_bash(settings, environ={}, which=_which) == str(bash)


def test_find_bash_falls_back_to_path_and_skips_system32(tmp_path):
    stub = tmp_path / "System32" / "bash.exe"
    stub.parent.mkdir(parents=True)
    stub.write_text("", encoding="utf-8")
    real = _fake_bash(tmp_path / "bin" / "bash.exe")
    settings = _bash_settings(tmp_path)

    # The System32 stub is the only PATH candidate: it must be ignored.
    def _stub_only(name: str):
        return str(stub) if name == "bash" else None

    assert find_bash(settings, environ={}, which=_stub_only) is None

    def _which(name: str):
        if name == "bash":
            return str(real)
        return None

    assert find_bash(settings, environ={}, which=_which) == str(real)


def test_find_bash_keeps_looking_past_a_system32_path_hit(tmp_path):
    """`which` stops at the first PATH hit; discovery must not."""
    stub_dir = tmp_path / "Windows" / "System32"
    stub = _fake_bash(stub_dir / "bash.exe")
    real_dir = tmp_path / "Git" / "bin"
    # The System32 stub is a Windows artifact; on POSIX the scanner looks for
    # a bare ``bash``, so create the file it will actually probe there.
    real = _fake_bash(real_dir / ("bash.exe" if os.name == "nt" else "bash"))
    settings = _bash_settings(tmp_path)
    environ = {"PATH": os.pathsep.join([str(stub_dir), str(real_dir)])}

    def _which(name: str):
        return str(stub) if name == "bash" else None

    assert find_bash(settings, environ=environ, which=_which) == str(real)


def test_find_bash_returns_none_when_nothing_is_found(tmp_path):
    settings = _bash_settings(tmp_path)
    assert find_bash(settings, environ={}, which=lambda _name: None) is None


# ── wiring: the manager gives the tool a stop watcher ──────────────────────
def test_task_manager_wires_bash_to_the_stop_flag(tmp_path):
    tm = make_manager(make_settings(tmp_path), MockLLMClient(final_answer="ok"))
    bash = next(t for t in tm._tools if t.name == "bash")
    assert bash.stop_checker is not None

    from backend.services.snapshots import set_current_task_id

    set_current_task_id("task-1")
    try:
        assert bash.stop_checker() is False
        tm._stop_flags["task-1"] = True
        assert bash.stop_checker() is True
    finally:
        set_current_task_id(None)
        tm._stop_flags.pop("task-1", None)


def test_task_stop_ends_a_running_command(tmp_path):
    """End to end: stop() -> the bash loop sees the flag -> tree killed -> park."""
    settings = make_settings(tmp_path, sandbox_timeout=30)
    mock = MockLLMClient(
        plan=["run"],
        tool_calls=[
            {
                "id": "b1",
                "name": "bash",
                "arguments": {
                    "command": "echo started > stop_probe.txt; sleep 30",
                },
            }
        ],
        final_answer="done",
    )
    tm = make_manager(settings, mock)
    task_id = tm.create_task(title="stop", user_input="run a long command")

    started = settings.artifacts_path / "stop_probe.txt"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not started.exists():
        time.sleep(0.05)
    assert started.exists(), "the command never started"

    stop_at = time.monotonic()
    tm.stop(task_id)
    # stop() persists INTERRUPTED immediately; the final state (with the bash
    # record) lands when the worker unwinds. Wait for that, not for the status.
    deadline = time.monotonic() + 10
    bash_records = []
    while time.monotonic() < deadline:
        task = tm.get_task(task_id)
        bash_records = [
            tc
            for step in (task.steps if task else [])
            for tc in step.tool_calls
            if tc.tool_name == "bash"
        ]
        if bash_records and bash_records[-1].status in ("failed", "success"):
            break
        time.sleep(0.05)
    elapsed = time.monotonic() - stop_at

    task = tm.get_task(task_id)
    assert task is not None
    assert task.status.value == "INTERRUPTED"
    assert elapsed < 5
    assert bash_records
    assert bash_records[-1].status == "failed"
    assert "stopped" in (bash_records[-1].error or "")


def test_task_stop_reaps_a_background_process_after_the_call_returned(tmp_path):
    """The stop flag is read in the call's context, not the reaper's thread."""
    settings = make_settings(tmp_path, sandbox_timeout=30)
    marker = settings.artifacts_path / "late_survivor_task.txt"
    waiting = settings.artifacts_path / "waiting.txt"
    mock = MockLLMClient(
        plan=["run"],
        tool_calls=[
            {
                "id": "b1",
                "name": "bash",
                "arguments": {
                    "command": f"(sleep 3; echo leaked > '{marker.as_posix()}') & echo started",
                },
            },
            {
                "id": "b2",
                "name": "bash",
                "arguments": {"command": f"echo waiting > '{waiting.as_posix()}'; sleep 30"},
            },
        ],
        final_answer="done",
    )
    tm = make_manager(settings, mock)
    task_id = tm.create_task(title="background", user_input="start a background job")

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not waiting.exists():
        time.sleep(0.05)
    assert waiting.exists(), "the second command never started"

    tm.stop(task_id)
    time.sleep(3.5)
    assert not marker.exists(), "background process survived the stop after the call returned"
