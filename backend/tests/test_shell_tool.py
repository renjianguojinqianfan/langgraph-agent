"""shell tool tests (S4 / ADR 0013 decision 3).

The contract under test:

* one command tool with an explicit shell choice (``pwsh`` | ``bash``);
* the command runs with the workspace root as cwd;
* the child sees only the ``ENV_ALLOWLIST`` environment (no secrets);
* the wall-clock timeout is configurable and kills the whole process tree;
* a task stop kills the whole process tree too;
* long output is truncated to its tail;
* Git Bash discovery follows config -> Git install dir -> PATH and never
  picks the ``System32\\bash.exe`` WSL stub;
* PowerShell 7 discovery follows config -> PATH -> install roots and never
  silently falls back to Windows PowerShell 5.1;
* the reported exit code is the real one for native commands (0 / 1 / 7), a
  cmdlet error is a failure, and the result names the shell that really ran.

Everything is offline; commands are plain shell builtins / ``sleep`` /
``Start-Sleep``.
"""

from __future__ import annotations

import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from backend.config import Settings
from backend.core.llm.client import MockLLMClient
from backend.core.tools.shell import (
    DEFAULT_SHELL,
    SHELL_BASH,
    SHELL_PWSH,
    ShellTool,
    find_git_bash,
    find_pwsh,
)
from backend.tests.conftest import make_isolated_settings, make_manager, make_settings

#: Every POSIX-syntax command below names its shell explicitly, so the same
#: expectations hold on Windows (where the default is pwsh) and on POSIX.
BASH = {"shell": SHELL_BASH}

WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="PowerShell 7 is Windows-only")


def _shell_settings(tmp_path: Path, **overrides: Any) -> Settings:
    return make_isolated_settings(
        data_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        trace_dir=str(tmp_path / "traces"),
        llm_base_url="",
        use_mock_llm=True,
        mcp_enabled=False,
        sandbox_timeout=5,
        **overrides,
    )


# ── shared execution guarantees (bash; 1:1 from the pre-S4 bash tool) ──────
def test_runs_in_the_workspace_root(settings):
    tool = ShellTool(settings)
    res = tool.run(command="echo hello > probe.txt", **BASH)
    assert res.success is True, res.error
    assert res.data["exit_code"] == 0
    assert res.data["stdout"] == ""  # the echo was redirected to the file
    assert res.data["shell"] == "bash"
    assert (settings.artifacts_path / "probe.txt").read_text(encoding="utf-8").strip() == "hello"


def test_child_env_is_the_allowlist(settings, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "super-secret-do-not-leak")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "super-secret-do-not-leak")
    tool = ShellTool(settings)
    res = tool.run(
        command=(
            'if [ -n "$LLM_API_KEY" ] || [ -n "$DASHSCOPE_API_KEY" ]; '
            "then echo leaked; else echo clean; fi"
        ),
        **BASH,
    )
    assert res.success is True, res.error
    assert res.data["stdout"].strip() == "clean"
    assert "super-secret-do-not-leak" not in res.data["stdout"]


def test_child_env_keeps_path(settings):
    tool = ShellTool(settings)
    res = tool.run(
        command='if [ -n "$PATH" ]; then echo has-path; else echo no-path; fi', **BASH
    )
    assert res.success is True, res.error
    assert res.data["stdout"].strip() == "has-path"


def test_captures_stdout_stderr_and_exit_code(settings):
    tool = ShellTool(settings)
    ok = tool.run(command="echo out; echo err >&2", **BASH)
    assert ok.success is True
    assert ok.data["stdout"].strip() == "out"
    assert ok.data["stderr"].strip() == "err"

    bad = tool.run(command="echo nope >&2; exit 3", **BASH)
    assert bad.success is False
    assert bad.data["exit_code"] == 3
    assert "nope" in bad.error


def test_honours_timeout_and_kills_the_process(settings):
    tool = ShellTool(settings)
    start = time.monotonic()
    res = tool.run(command="sleep 30", timeout=1, **BASH)
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
    tool = ShellTool(settings)
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
        **BASH,
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
    tool = ShellTool(settings)
    flag = threading.Event()
    tool.stop_checker = flag.is_set
    start = time.monotonic()
    res = tool.run(command="sleep 5 & echo started", timeout=30, **BASH)
    elapsed = time.monotonic() - start

    assert res.success is True, res.error
    assert res.data["stdout"].strip() == "started"
    assert elapsed < 1.5, f"background command took {elapsed:.2f}s"
    flag.set()  # reap the background sleep promptly


def test_background_mechanism_is_platform_specific():
    """Windows uses a Job Object; POSIX uses the shell's process group."""
    from backend.core.tools.shell import _make_job

    if os.name == "nt":
        job = _make_job()
        assert job is not None
        job.close()
    else:
        assert _make_job() is None


def test_background_child_survives_writing_to_inherited_pipes(settings):
    """The returned call must not close pipes a background child still owns."""
    out_marker = settings.artifacts_path / "late_stdout.txt"
    err_marker = settings.artifacts_path / "late_stderr.txt"
    tool = ShellTool(settings)

    res = tool.run(
        command=(
            "(sleep 1; echo late-out; echo out-ok > '{out}'; "
            "echo late-err >&2; echo err-ok > '{err}') & echo started"
        ).format(out=out_marker.as_posix(), err=err_marker.as_posix()),
        timeout=30,
        **BASH,
    )
    assert res.success is True, res.error
    assert res.data["stdout"].strip() == "started"

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not (out_marker.exists() and err_marker.exists()):
        time.sleep(0.05)
    assert out_marker.exists(), "background child died writing to the returned call's stdout"
    assert err_marker.exists(), "background child died writing to the returned call's stderr"


def test_background_child_with_redirected_output_survives(settings):
    """Control: a child that never writes to the inherited pipes was fine before."""
    marker = settings.artifacts_path / "redirected_survived.txt"
    tool = ShellTool(settings)

    res = tool.run(
        command=(
            f"(sleep 1; echo late > bg.log; echo survived > '{marker.as_posix()}') & echo started"
        ),
        timeout=30,
        **BASH,
    )
    assert res.success is True, res.error

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not marker.exists():
        time.sleep(0.05)
    assert marker.exists(), "a redirected background child did not survive the return"


def test_background_pumps_exit_after_the_tree_is_reaped(settings):
    """While the child lives its pumps drain; a stop closes them and they exit."""
    tool = ShellTool(settings)
    flag = threading.Event()
    tool.stop_checker = flag.is_set
    try:
        res = tool.run(command="sleep 5 & echo started", timeout=30, **BASH)
        assert res.success is True, res.error

        pumps = [t for t in threading.enumerate() if t.name.startswith("shell-pump")]
        assert pumps, "the returned call dropped the background pumps (its pipes would break)"
    finally:
        flag.set()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not any(t.name.startswith("shell-pump") for t in threading.enumerate()):
            break
        time.sleep(0.05)
    assert not [t for t in threading.enumerate() if t.name.startswith("shell-pump")]


def test_stop_after_return_kills_the_background_process(settings):
    """A stop requested after the call returned still reaps its background tree."""
    marker = settings.artifacts_path / "late_survivor.txt"
    flag = threading.Event()
    tool = ShellTool(settings)
    tool.stop_checker = flag.is_set

    res = tool.run(
        command=f"(sleep 3; echo leaked > '{marker.as_posix()}') & echo started",
        timeout=30,
        **BASH,
    )
    assert res.success is True, res.error
    assert not marker.exists()

    flag.set()
    time.sleep(3.5)
    assert not marker.exists(), "background process survived the task stop"


def test_background_process_runs_when_not_stopped(settings):
    """Control for the test above: without a stop the marker does appear."""
    marker = settings.artifacts_path / "ran_without_stop.txt"
    tool = ShellTool(settings)
    tool.stop_checker = threading.Event().is_set

    res = tool.run(
        command=f"(sleep 2; echo ran > '{marker.as_posix()}') & echo started",
        timeout=30,
        **BASH,
    )
    assert res.success is True, res.error
    time.sleep(2.8)
    assert marker.exists()


def test_long_output_keeps_the_tail(settings):
    tool = ShellTool(settings)
    res = tool.run(command="for i in $(seq 1 5000); do echo line-$i; done", **BASH)
    assert res.success is True, res.error
    assert res.data["truncated"] is True
    assert "line-5000" in res.data["stdout"]
    assert "line-1\n" not in res.data["stdout"]
    assert len(res.data["stdout"]) <= 30_000


def test_empty_command_is_rejected(settings):
    res = ShellTool(settings).run(command="   ", **BASH)
    assert res.success is False
    assert "command" in res.error.lower()


def test_unknown_shell_is_rejected(settings):
    res = ShellTool(settings).run(command="echo hi", shell="zsh")
    assert res.success is False
    assert "zsh" in res.error
    assert "pwsh" in res.error and "bash" in res.error


def test_result_names_the_shell_that_ran_the_command(settings):
    tool = ShellTool(settings)
    bash = tool.run(command="echo hi", **BASH)
    assert bash.data["shell"] == "bash"
    assert Path(bash.data["shell_path"]).name.lower() in ("bash", "bash.exe")


# ── shell selection: platform default + explicit choice ────────────────────
def test_default_shell_is_platform_specific():
    assert DEFAULT_SHELL == ("pwsh" if os.name == "nt" else "bash")


def test_schema_advertises_the_optional_shell_choice():
    schema = ShellTool.args_schema
    assert schema["required"] == ["command"]
    assert schema["properties"]["shell"]["enum"] == ["pwsh", "bash"]
    assert "shell" not in schema["properties"]["command"].get("enum", [])


@WINDOWS_ONLY
def test_windows_defaults_to_pwsh(settings):
    res = ShellTool(settings).run(command="Write-Output 'default-shell'")
    assert res.success is True, res.error
    assert res.data["shell"] == "pwsh"
    assert res.data["stdout"].strip() == "default-shell"


@WINDOWS_ONLY
def test_explicit_bash_runs_git_bash(settings):
    res = ShellTool(settings).run(command="echo git-bash", **BASH)
    assert res.success is True, res.error
    assert res.data["shell"] == "bash"
    assert res.data["stdout"].strip() == "git-bash"


def test_missing_git_bash_returns_a_readable_error(settings):
    with patch("backend.core.tools.shell.find_git_bash", return_value=None):
        res = ShellTool(settings).run(command="echo hi", **BASH)
    assert res.success is False
    assert "bash" in res.error.lower()
    assert "not found" in res.error.lower()


@WINDOWS_ONLY
def test_missing_pwsh_returns_a_readable_error(settings):
    """No silent downgrade to Windows PowerShell 5.1 (ADR 0013)."""
    with patch("backend.core.tools.shell.find_pwsh", return_value=None):
        res = ShellTool(settings).run(command="Write-Output hi", shell="pwsh")
    assert res.success is False
    assert "pwsh" in res.error.lower()
    assert "not found" in res.error.lower()
    assert "5.1" in res.error
    assert "shell='bash'" in res.error


@pytest.mark.skipif(os.name == "nt", reason="pwsh is the Windows default; POSIX has no pwsh")
def test_pwsh_is_not_available_on_posix(settings):
    res = ShellTool(settings).run(command="echo hi", shell="pwsh")
    assert res.success is False
    assert "windows" in res.error.lower()


# ── Git Bash discovery: config -> Git install dir -> PATH ──────────────────
def _fake_bash(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def test_find_git_bash_prefers_the_configured_path(tmp_path):
    configured = _fake_bash(tmp_path / "custom" / "bash.exe")
    settings = _shell_settings(tmp_path, bash_path=str(configured))
    assert find_git_bash(settings, environ={}, which=lambda _name: None) == str(configured)


def test_find_git_bash_uses_the_git_install_dir(tmp_path):
    git = _fake_bash(tmp_path / "Git" / "cmd" / "git.exe")
    bash = _fake_bash(tmp_path / "Git" / "bin" / "bash.exe")
    settings = _shell_settings(tmp_path)

    def _which(name: str):
        return str(git) if name == "git" else None

    assert find_git_bash(settings, environ={}, which=_which) == str(bash)


def test_find_git_bash_reads_a_mingw64_git_install(tmp_path):
    """Git for Windows often puts git.exe under <root>/mingw64/bin."""
    git = _fake_bash(tmp_path / "Git" / "mingw64" / "bin" / "git.exe")
    bash = _fake_bash(tmp_path / "Git" / "bin" / "bash.exe")
    settings = _shell_settings(tmp_path)

    def _which(name: str):
        return str(git) if name == "git" else None

    assert find_git_bash(settings, environ={}, which=_which) == str(bash)


def test_find_git_bash_falls_back_to_path_and_skips_system32(tmp_path):
    stub = tmp_path / "System32" / "bash.exe"
    stub.parent.mkdir(parents=True)
    stub.write_text("", encoding="utf-8")
    real = _fake_bash(tmp_path / "bin" / "bash.exe")
    settings = _shell_settings(tmp_path)

    # The System32 stub is the only PATH candidate: it must be ignored.
    def _stub_only(name: str):
        return str(stub) if name == "bash" else None

    assert find_git_bash(settings, environ={}, which=_stub_only) is None

    def _which(name: str):
        if name == "bash":
            return str(real)
        return None

    assert find_git_bash(settings, environ={}, which=_which) == str(real)


def test_find_git_bash_keeps_looking_past_a_system32_path_hit(tmp_path):
    """`which` stops at the first PATH hit; discovery must not."""
    stub_dir = tmp_path / "Windows" / "System32"
    stub = _fake_bash(stub_dir / "bash.exe")
    real_dir = tmp_path / "Git" / "bin"
    # The System32 stub is a Windows artifact; on POSIX the scanner looks for
    # a bare ``bash``, so create the file it will actually probe there.
    real = _fake_bash(real_dir / ("bash.exe" if os.name == "nt" else "bash"))
    settings = _shell_settings(tmp_path)
    environ = {"PATH": os.pathsep.join([str(stub_dir), str(real_dir)])}

    def _which(name: str):
        return str(stub) if name == "bash" else None

    assert find_git_bash(settings, environ=environ, which=_which) == str(real)


def test_find_git_bash_returns_none_when_nothing_is_found(tmp_path):
    settings = _shell_settings(tmp_path)
    assert find_git_bash(settings, environ={}, which=lambda _name: None) is None


# ── PowerShell 7 discovery: config -> PATH -> install roots ────────────────
@WINDOWS_ONLY
def test_find_pwsh_prefers_the_configured_path(tmp_path):
    configured = _fake_bash(tmp_path / "custom" / "pwsh.exe")
    settings = _shell_settings(tmp_path, pwsh_path=str(configured))
    assert find_pwsh(settings, environ={}, which=lambda _name: None) == str(configured)


@WINDOWS_ONLY
def test_find_pwsh_reads_the_windowsapps_alias(tmp_path):
    """The winget / Store install lives under LOCALAPPDATA\\Microsoft\\WindowsApps."""
    pwsh = _fake_bash(tmp_path / "AppData" / "Local" / "Microsoft" / "WindowsApps" / "pwsh.exe")
    settings = _shell_settings(tmp_path)
    environ = {
        "ProgramFiles": str(tmp_path / "ProgramFiles"),
        "LOCALAPPDATA": str(tmp_path / "AppData" / "Local"),
    }
    assert find_pwsh(settings, environ=environ, which=lambda _name: None) == str(pwsh)


@WINDOWS_ONLY
def test_find_pwsh_uses_path_before_install_roots(tmp_path):
    on_path = _fake_bash(tmp_path / "bin" / "pwsh.exe")
    settings = _shell_settings(tmp_path)
    environ = {"PATH": str(on_path.parent), "ProgramFiles": str(tmp_path / "ProgramFiles")}
    assert find_pwsh(settings, environ=environ, which=lambda _name: str(on_path)) == str(on_path)


@WINDOWS_ONLY
def test_find_pwsh_never_returns_windows_powershell_5(tmp_path):
    five = _fake_bash(tmp_path / "WindowsPowerShell" / "v1.0" / "powershell.exe")
    settings = _shell_settings(tmp_path)
    environ = {"PATH": str(five.parent)}
    assert find_pwsh(settings, environ=environ, which=lambda _name: None) is None


def test_find_pwsh_is_none_off_windows(tmp_path):
    settings = _shell_settings(tmp_path)
    if os.name == "nt":
        pytest.skip("POSIX-only expectation")
    assert find_pwsh(settings) is None


# ── PowerShell 7 execution matrix (Windows-only) ───────────────────────────
@WINDOWS_ONLY
def test_pwsh_exit_codes_are_the_real_ones(settings):
    tool = ShellTool(settings)
    for code in (0, 1, 7):
        res = tool.run(command=f"exit {code}", shell=SHELL_PWSH)
        assert res.data["exit_code"] == code, (code, res)
        assert res.success is (code == 0)


@WINDOWS_ONLY
def test_pwsh_native_exit_code_is_propagated(settings):
    res = ShellTool(settings).run(command="cmd /c exit 7", shell=SHELL_PWSH)
    assert res.success is False
    assert res.data["exit_code"] == 7
    assert res.data["shell"] == "pwsh"


@WINDOWS_ONLY
def test_pwsh_cmdlet_error_is_a_failure(settings):
    res = ShellTool(settings).run(
        command="Get-Item -Path 'C:\\definitely-not-here-s4'", shell=SHELL_PWSH
    )
    assert res.success is False
    assert res.data["exit_code"] != 0
    assert res.error  # the cmdlet message is surfaced


@WINDOWS_ONLY
def test_pwsh_compound_statement_last_exit_code_wins(settings):
    tool = ShellTool(settings)
    ok = tool.run(command="Write-Output a; Write-Output b; exit 0", shell=SHELL_PWSH)
    assert ok.success is True, ok.error
    assert ok.data["stdout"].split() == ["a", "b"]

    bad = tool.run(command="cmd /c exit 7; exit 0", shell=SHELL_PWSH)
    assert bad.success is True  # the explicit trailing `exit 0` is the verdict

    native = tool.run(command="Write-Output first; cmd /c exit 7", shell=SHELL_PWSH)
    assert native.success is False
    assert native.data["exit_code"] == 7


@WINDOWS_ONLY
def test_pwsh_cjk_quotes_and_empty_arguments_round_trip(settings):
    res = ShellTool(settings).run(
        command="Write-Output '中文 空格 \"引号\"'; Write-Output ('[' + '' + ']')",
        shell=SHELL_PWSH,
    )
    assert res.success is True, res.error
    out = res.data["stdout"]
    assert "中文 空格" in out
    assert '"引号"' in out
    assert "[]" in out


@WINDOWS_ONLY
def test_pwsh_result_is_utf8(settings):
    res = ShellTool(settings).run(command="Write-Output '中文输出'", shell=SHELL_PWSH)
    assert res.success is True, res.error
    assert "中文输出" in res.data["stdout"]


@WINDOWS_ONLY
def test_pwsh_runs_npm_cmd_when_available(settings):
    npm = shutil.which("npm")
    if npm is None:
        pytest.skip("npm is not installed on this host")
    res = ShellTool(settings).run(command="npm --version", shell=SHELL_PWSH)
    assert res.success is True, res.error
    assert re.search(r"\d+\.\d+\.\d+", res.data["stdout"])


@WINDOWS_ONLY
def test_pwsh_child_env_is_the_allowlist(settings, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "super-secret-do-not-leak")
    res = ShellTool(settings).run(
        command=(
            "if ($env:LLM_API_KEY -or $env:DASHSCOPE_API_KEY) { 'leaked' } "
            "else { 'clean' }; if ($env:PATH) { 'has-path' }"
        ),
        shell=SHELL_PWSH,
    )
    assert res.success is True, res.error
    assert res.data["stdout"].split() == ["clean", "has-path"]
    assert "super-secret-do-not-leak" not in res.data["stdout"]


@WINDOWS_ONLY
def test_pwsh_output_keeps_the_tail(settings):
    res = ShellTool(settings).run(
        command="1..5000 | ForEach-Object { \"line-$_\" }", shell=SHELL_PWSH
    )
    assert res.success is True, res.error
    assert res.data["truncated"] is True
    assert "line-5000" in res.data["stdout"]
    assert len(res.data["stdout"]) <= 30_000


@WINDOWS_ONLY
def test_pwsh_timeout_kills_the_process(settings):
    start = time.monotonic()
    res = ShellTool(settings).run(command="Start-Sleep -Seconds 30", timeout=1, shell=SHELL_PWSH)
    assert res.success is False
    assert res.data["timed_out"] is True
    assert time.monotonic() - start < 10


@WINDOWS_ONLY
def test_pwsh_stop_kills_the_process(settings):
    started = settings.artifacts_path / "pwsh_started.txt"
    flag = threading.Event()
    tool = ShellTool(settings)
    tool.stop_checker = flag.is_set

    def _stop_after_start() -> None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if started.exists():
                flag.set()
                return
            time.sleep(0.02)

    watcher = threading.Thread(target=_stop_after_start, daemon=True)
    watcher.start()
    res = tool.run(
        command=(
            "Set-Content -Path pwsh_started.txt -Value started; "
            "Start-Sleep -Seconds 30"
        ),
        timeout=30,
        shell=SHELL_PWSH,
    )
    watcher.join(timeout=5)
    assert res.success is False
    assert res.data["stopped"] is True


@WINDOWS_ONLY
def test_pwsh_background_job_returns_without_holding_pipes(settings):
    flag = threading.Event()
    tool = ShellTool(settings)
    tool.stop_checker = flag.is_set
    start = time.monotonic()
    res = tool.run(
        command="Start-Process -NoNewWindow -FilePath $PSHOME\\pwsh.exe "
        "-ArgumentList '-NoProfile','-NonInteractive','-Command','Start-Sleep -Seconds 5' | Out-Null; "
        "Write-Output 'started'",
        timeout=30,
        shell=SHELL_PWSH,
    )
    elapsed = time.monotonic() - start
    assert res.success is True, res.error
    assert res.data["stdout"].strip() == "started"
    assert elapsed < 5, f"background job took {elapsed:.2f}s"
    flag.set()  # reap the background tree promptly


@WINDOWS_ONLY
def test_pwsh_stop_reaps_the_background_process_tree(settings):
    """A Start-Process child must die with the stopped pwsh (Job Object)."""
    started = settings.artifacts_path / "pwsh_tree_started.txt"
    survived = settings.artifacts_path / "pwsh_child_survived.txt"
    flag = threading.Event()
    tool = ShellTool(settings)
    tool.stop_checker = flag.is_set

    def _stop_after_start() -> None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if started.exists():
                flag.set()
                return
            time.sleep(0.02)

    watcher = threading.Thread(target=_stop_after_start, daemon=True)
    watcher.start()
    res = tool.run(
        command=(
            "Start-Process -NoNewWindow -FilePath $PSHOME\\pwsh.exe "
            "-ArgumentList '-NoProfile','-NonInteractive','-Command',"
            "'Start-Sleep -Seconds 2; Set-Content -Path pwsh_child_survived.txt -Value leaked' "
            "| Out-Null; "
            "Set-Content -Path pwsh_tree_started.txt -Value started; "
            "Start-Sleep -Seconds 30"
        ),
        timeout=30,
        shell=SHELL_PWSH,
    )
    watcher.join(timeout=5)
    assert res.success is False
    assert res.data["stopped"] is True
    time.sleep(2.5)
    assert not survived.exists(), "the pwsh process tree survived the stop"


# ── wiring: the manager gives the tool a stop watcher ──────────────────────
def test_task_manager_wires_shell_to_the_stop_flag(tmp_path):
    tm = make_manager(make_settings(tmp_path), MockLLMClient(final_answer="ok"))
    shell = next(t for t in tm._tools if t.name == "shell")
    assert shell.stop_checker is not None

    from backend.core.runtime_context import set_current_task_id

    set_current_task_id("task-1")
    try:
        assert shell.stop_checker() is False
        tm._stop_flags["task-1"] = True
        assert shell.stop_checker() is True
    finally:
        set_current_task_id(None)
        tm._stop_flags.pop("task-1", None)


def test_task_stop_ends_a_running_command(tmp_path):
    """End to end: stop() -> the shell loop sees the flag -> tree killed -> park."""
    settings = make_settings(tmp_path, sandbox_timeout=30)
    mock = MockLLMClient(
        plan=["run"],
        tool_calls=[
            {
                "id": "b1",
                "name": "shell",
                "arguments": {
                    "shell": "bash",
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
    # stop() persists INTERRUPTED immediately; the final state (with the shell
    # record) lands when the worker unwinds. Wait for that, not for the status.
    deadline = time.monotonic() + 10
    shell_records = []
    while time.monotonic() < deadline:
        task = tm.get_task(task_id)
        shell_records = [
            tc
            for step in (task.steps if task else [])
            for tc in step.tool_calls
            if tc.tool_name == "shell"
        ]
        if shell_records and shell_records[-1].status in ("failed", "success"):
            break
        time.sleep(0.05)
    elapsed = time.monotonic() - stop_at

    task = tm.get_task(task_id)
    assert task is not None
    assert task.status.value == "INTERRUPTED"
    assert elapsed < 5
    assert shell_records
    assert shell_records[-1].status == "failed"
    assert "stopped" in (shell_records[-1].error or "")


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
                "name": "shell",
                "arguments": {
                    "shell": "bash",
                    "command": f"(sleep 3; echo leaked > '{marker.as_posix()}') & echo started",
                },
            },
            {
                "id": "b2",
                "name": "shell",
                "arguments": {
                    "shell": "bash",
                    "command": f"echo waiting > '{waiting.as_posix()}'; sleep 30",
                },
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


def test_stop_reaps_the_background_process_of_a_parked_task(tmp_path):
    """A finished round parks; a later stop must still reap its background tree."""
    settings = make_settings(tmp_path, sandbox_timeout=30)
    marker = settings.artifacts_path / "parked_survivor.txt"
    mock = MockLLMClient(
        plan=["run"],
        tool_calls=[
            {
                "id": "b1",
                "name": "shell",
                "arguments": {
                    "shell": "bash",
                    "command": f"(sleep 3; echo leaked > '{marker.as_posix()}') & echo started",
                },
            }
        ],
        final_answer="done",
    )
    tm = make_manager(settings, mock)
    task_id = tm.create_task(title="parked", user_input="start a background job")

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and task_id in tm._threads:
        time.sleep(0.05)
    assert task_id not in tm._threads, "the round never parked"

    tm.stop(task_id)  # no live run; only the background killer can act
    time.sleep(3.5)
    assert not marker.exists(), "a parked task's background process survived the stop"
