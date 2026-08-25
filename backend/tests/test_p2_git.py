"""P2 item 2 — Git tool integration tests (offline).

Creates a throwaway git repository inside ``tmp_path`` and exercises the seven
Git tools plus the security model:

* all 7 tools work in a real repository (status / diff / commit / log /
  branch / checkout / init);
* non-git directories return a clear error (no crash);
* the three state-changing tools require confirmation;
* blacklisted verbs / arguments are rejected outright (no execution);
* path escapes (``../``) are rejected;
* option-injection attempts (branch ``--force``, ``-D``) are rejected;
* special characters in a commit message do not inject (parametrised, no shell);
* timeout handling surfaces a clear error.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.config import Settings
from backend.core.tools.git_tools import (
    GitBranchTool,
    GitCheckoutTool,
    GitCommandError,
    GitCommitTool,
    GitDiffTool,
    GitInitTool,
    GitLogTool,
    GitStatusTool,
    GitToolRunner,
    build_git_tools,
)
from backend.core.tools.registry import build_tools


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        shell=False,
        timeout=30,
    )


@pytest.fixture
def repo(tmp_path) -> Path:
    """A real git repository at tmp_path/repo with one commit."""
    r = tmp_path / "repo"
    r.mkdir()
    _git("init", cwd=r)
    (r / "a.txt").write_text("hello\n", encoding="utf-8")
    _git("add", ".", cwd=r)
    _git("commit", "-m", "initial", cwd=r)
    (r / "a.txt").write_text("hello world\n", encoding="utf-8")
    return r


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        git_enabled=True,
        git_repo_dir=str(tmp_path / "repo"),
        git_timeout_sec=10,
    )


# ─────────────────── tools work in a real repo ───────────────────
def test_git_status_shows_changes(repo, settings):
    tool = GitStatusTool(GitToolRunner(settings.git_repo_path, settings.git_timeout_sec), settings)
    res = tool.run()
    assert res.success is True
    assert res.data["branch"] == "master" or res.data["branch"] == "main"
    assert any("a.txt" in c for c in res.data["changes"])


def test_git_diff_shows_unstaged(repo, settings):
    tool = GitDiffTool(GitToolRunner(settings.git_repo_path, settings.git_timeout_sec), settings)
    res = tool.run()
    assert res.success is True
    assert "+hello world" in res.data["diff"]
    assert "-hello" in res.data["diff"]


def test_git_diff_staged_only(repo, settings):
    runner = GitToolRunner(settings.git_repo_path, settings.git_timeout_sec)
    _git("add", "a.txt", cwd=repo)
    tool = GitDiffTool(runner, settings)
    unstaged = tool.run(staged=False)
    assert unstaged.data["diff"] == ""
    staged = tool.run(staged=True)
    assert "+hello world" in staged.data["diff"]


def test_git_log_returns_commits(repo, settings):
    tool = GitLogTool(GitToolRunner(settings.git_repo_path, settings.git_timeout_sec), settings)
    res = tool.run(limit=10)
    assert res.success is True
    assert len(res.data["commits"]) >= 1
    assert res.data["commits"][0]["message"] == "initial"
    assert res.data["commits"][0]["hash"]


def test_git_branch_lists_and_marks_current(repo, settings):
    tool = GitBranchTool(GitToolRunner(settings.git_repo_path, settings.git_timeout_sec), settings)
    res = tool.run()
    assert res.success is True
    assert res.data["current"] in res.data["branches"]


def test_git_commit_returns_hash_with_special_chars(repo, settings):
    tool = GitCommitTool(GitToolRunner(settings.git_repo_path, settings.git_timeout_sec), settings)
    _git("add", "a.txt", cwd=repo)
    message = "feat: add mcp client; rm -rf /tmp && echo hacked $(whoami)"
    res = tool.run(message=message)
    assert res.success is True
    assert len(res.data["commit"]) == 40
    # The message is stored literally — no injection side effects.
    log = _git("log", "--format=%s", "-1", cwd=repo)
    assert log.stdout.strip() == message


def test_git_commit_empty_message_rejected(repo, settings):
    tool = GitCommitTool(GitToolRunner(settings.git_repo_path, settings.git_timeout_sec), settings)
    res = tool.run(message="   ")
    assert res.success is False
    assert "message" in res.error.lower()


def test_git_commit_nothing_to_commit(repo, settings):
    tool = GitCommitTool(GitToolRunner(settings.git_repo_path, settings.git_timeout_sec), settings)
    res = tool.run(message="noop")
    assert res.success is False
    assert "no changes added to commit" in res.error.lower() or "nothing to commit" in res.error.lower()


def test_git_checkout_switches_branch(repo, settings):
    runner = GitToolRunner(settings.git_repo_path, settings.git_timeout_sec)
    _git("checkout", "-b", "feature", cwd=repo)  # now on feature
    _git("checkout", "-", cwd=repo)  # back to the original branch
    tool = GitCheckoutTool(runner, settings)
    res = tool.run(branch="feature")
    assert res.success is True
    assert res.data["branch"] == "feature"
    head = _git("branch", "--show-current", cwd=repo)
    assert head.stdout.strip() == "feature"


def test_git_checkout_missing_branch_errors(repo, settings):
    tool = GitCheckoutTool(GitToolRunner(settings.git_repo_path, settings.git_timeout_sec), settings)
    res = tool.run(branch="does-not-exist")
    assert res.success is False
    assert "error" in res.error.lower() or "pathspec" in res.error.lower()


def test_git_init_initializes_directory(tmp_path, settings):
    """git_init with a repo-relative subpath creates a nested repository."""
    target = tmp_path / "repo" / "nested"
    tool = GitInitTool(GitToolRunner(settings.git_repo_path, settings.git_timeout_sec), settings)
    res = tool.run(path="nested")
    assert res.success is True
    assert (target / ".git").exists()


def test_git_init_rejects_outside_path(tmp_path, settings):
    """Initialising a directory outside git_repo_path is rejected."""
    target = tmp_path / "outside"
    target.mkdir()
    tool = GitInitTool(GitToolRunner(settings.git_repo_path, settings.git_timeout_sec), settings)
    res = tool.run(path=str(target))
    assert res.success is False
    assert "outside" in res.error


def test_git_init_in_non_git_dir(tmp_path, settings):
    """git_init works outside an existing repo (uses the repo root default)."""
    tool = GitInitTool(GitToolRunner(settings.git_repo_path, settings.git_timeout_sec), settings)
    res = tool.run()
    assert res.success is True
    assert (settings.git_repo_path / ".git").exists()


def test_git_log_empty_repo_is_not_error(tmp_path, settings):
    empty = tmp_path / "empty"
    empty.mkdir()
    _git("init", cwd=empty)
    s2 = Settings(
        data_dir=str(tmp_path), artifacts_dir=str(tmp_path / "artifacts"),
        git_enabled=True, git_repo_dir=str(empty), git_timeout_sec=10,
    )
    tool = GitLogTool(GitToolRunner(s2.git_repo_path, s2.git_timeout_sec), s2)
    res = tool.run(limit=10)
    assert res.success is True
    assert res.data["commits"] == []


# ─────────────────── non-git directory ───────────────────
def test_non_git_directory_clear_error(tmp_path, settings):
    plain = tmp_path / "plain"
    plain.mkdir()
    runner = GitToolRunner(plain, settings.git_timeout_sec)
    tool = GitStatusTool(runner, settings)
    res = tool.run()
    assert res.success is False
    assert "not a git repository" in res.error.lower()


# ─────────────────── confirmation flags ───────────────────
def test_git_requires_confirm_flags(repo, settings):
    runner = GitToolRunner(settings.git_repo_path, settings.git_timeout_sec)
    assert GitCommitTool(runner, settings).requires_confirm is True
    assert GitCheckoutTool(runner, settings).requires_confirm is True
    assert GitInitTool(runner, settings).requires_confirm is True
    assert GitStatusTool(runner, settings).requires_confirm is False
    assert GitDiffTool(runner, settings).requires_confirm is False
    assert GitLogTool(runner, settings).requires_confirm is False
    assert GitBranchTool(runner, settings).requires_confirm is False


# ─────────────────── blacklist / injection protection ───────────────────
def test_blacklisted_verb_rejected(repo, settings):
    runner = GitToolRunner(settings.git_repo_path, settings.git_timeout_sec)
    for verb in ("push", "reset", "clean", "rebase", "merge", "clone", "remote"):
        with pytest.raises(GitCommandError):
            runner.run(verb, [])


def test_blocked_args_rejected(repo, settings):
    runner = GitToolRunner(settings.git_repo_path, settings.git_timeout_sec)
    with pytest.raises(GitCommandError):
        runner.run("status", ["--force"])
    with pytest.raises(GitCommandError):
        runner.run("diff", ["--hard"])
    with pytest.raises(GitCommandError):
        runner.run("checkout", ["-D", "x"])
    with pytest.raises(GitCommandError):
        runner.run("branch", ["-D", "x"])


def test_checkout_option_injection_rejected(repo, settings):
    tool = GitCheckoutTool(GitToolRunner(settings.git_repo_path, settings.git_timeout_sec), settings)
    res = tool.run(branch="--force")
    assert res.success is False
    assert "must not start with '-'" in res.error


def test_path_escape_rejected(repo, settings):
    runner = GitToolRunner(settings.git_repo_path, settings.git_timeout_sec)
    with pytest.raises(GitCommandError):
        runner.run("status", [], path="../outside")
    tool = GitStatusTool(runner, settings)
    res = tool.run(path="../escape")
    assert res.success is False
    assert "outside" in res.error


def test_commit_message_no_shell_injection(repo, settings):
    """A semicolon-laden message must stay a literal argv, not shell code."""
    tool = GitCommitTool(GitToolRunner(settings.git_repo_path, settings.git_timeout_sec), settings)
    _git("add", "a.txt", cwd=repo)
    evil = "x; touch injected.txt && echo pwned"
    res = tool.run(message=evil)
    assert res.success is True
    assert not (repo / "injected.txt").exists()
    log = _git("log", "--format=%s", "-1", cwd=repo)
    assert log.stdout.strip() == evil


def test_timeout_surfaces_clear_error(repo, settings, monkeypatch):
    """When subprocess.run raises TimeoutExpired the runner reports it."""
    runner = GitToolRunner(settings.git_repo_path, timeout=0.001)

    def _hang(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=0.001)

    monkeypatch.setattr("backend.core.tools.git_tools.subprocess.run", _hang)
    with pytest.raises(GitCommandError, match="timed out"):
        runner.run("status", [])


# ─────────────────── build + registration ───────────────────
def test_build_git_tools_returns_seven(tmp_path):
    s = Settings(
        data_dir=str(tmp_path), artifacts_dir=str(tmp_path / "artifacts"),
        git_enabled=True, git_repo_dir=str(tmp_path / "repos"), git_timeout_sec=10,
    )
    tools = build_git_tools(s)
    assert len(tools) == 7
    assert {t.name for t in tools} == {
        "git_status", "git_diff", "git_commit", "git_log",
        "git_branch", "git_checkout", "git_init",
    }


def test_git_tools_not_in_registry(tmp_path):
    """Git tools are NOT @register'd — they must not appear in registry build."""
    s = Settings(
        data_dir=str(tmp_path), artifacts_dir=str(tmp_path / "artifacts"),
        git_enabled=True, git_repo_dir=str(tmp_path / "repos"), git_timeout_sec=10,
    )
    names = {t.name for t in build_tools(s)}
    assert not names & {"git_status", "git_diff", "git_commit", "git_log",
                        "git_branch", "git_checkout", "git_init"}
