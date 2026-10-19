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
    assert "hello" in res.data["stdout"] or res.data["stdout"] == ""
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
