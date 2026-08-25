"""QA independent boundary tests — P2 item 2 (Git tools).

These tests complement ``test_p2_git.py`` (engineer's suite) with an
independent perspective. Coverage gaps addressed here:

* read tools with a repo-relative ``path`` scope (status / diff / log);
* ``git_log`` invalid ``limit`` handling (0 / negative / non-int);
* ``git_checkout`` empty / ``-``-prefixed branch rejected;
* ``git_init`` on an already-initialised directory returns a "reinitialised"
  hint (not an error);
* runner argument validation rejects unknown ``-``-prefixed options
  (option-injection defence beyond the fixed blacklist);
* ``resolve_path`` boundary cases: absolute path inside the repo is fine,
  ``../`` escapes and ``-``-prefixed paths are rejected;
* TaskManager registration: ``git_enabled=true`` loads exactly 7 tools,
  ``git_enabled=false`` loads none, a name conflict keeps the first tool;
* ``build_git_tools`` auto-creates a missing repo root;
* end-to-end executor confirmation: ``git_commit`` needs confirm, rejection
  skips (no commit), approval commits.

All tests are offline (throwaway ``git init`` inside tmp_path).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.config import Settings
from backend.core.agent.nodes import AgentRuntime
from backend.core.llm.client import MockLLMClient
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
from backend.services.event_bus import EventBus
from backend.services.persistence import Persistence
from backend.services.task_manager import TaskManager
from backend.tests.conftest import make_settings


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


@pytest.fixture
def runner(settings) -> GitToolRunner:
    return GitToolRunner(settings.git_repo_path, settings.git_timeout_sec)


# ─────────────────── read tools with path scope ───────────────────
def test_git_status_path_scope(repo, settings):
    (repo / "sub").mkdir()
    (repo / "sub" / "inner.txt").write_text("x\n", encoding="utf-8")
    _git("add", "sub/inner.txt", cwd=repo)          # tracked…
    (repo / "sub" / "inner.txt").write_text("y\n", encoding="utf-8")  # …then modified
    (repo / "other.txt").write_text("z\n", encoding="utf-8")  # untracked elsewhere
    tool = GitStatusTool(GitToolRunner(settings.git_repo_path, settings.git_timeout_sec), settings)
    res = tool.run(path="sub")
    assert res.success is True
    assert any("inner.txt" in c for c in res.data["changes"])
    # Implementation semantics: `path` runs git with `-C <subdir>` (the runner
    # resolves the path and switches the working directory). Git status then
    # reports the whole repo with parent entries shown as "../..." — the
    # subdir file is what the caller scoped to, parent files are still listed
    # with an explicit ../ prefix (never dropped silently).
    assert any("../" in c for c in res.data["changes"])


def test_git_diff_path_scope(repo, settings):
    (repo / "sub").mkdir()
    (repo / "sub" / "inner.txt").write_text("one\n", encoding="utf-8")
    _git("add", "sub/inner.txt", cwd=repo)                       # tracked…
    (repo / "sub" / "inner.txt").write_text("one-updated\n", encoding="utf-8")  # …then modified
    (repo / "other.txt").write_text("two\n", encoding="utf-8")   # untracked -> NOT in diff
    tool = GitDiffTool(GitToolRunner(settings.git_repo_path, settings.git_timeout_sec), settings)
    res = tool.run(path="sub")
    assert res.success is True
    assert "inner.txt" in res.data["diff"]
    assert "other.txt" not in res.data["diff"]


def test_git_log_path_scope(repo, settings):
    (repo / "sub").mkdir()
    (repo / "sub" / "inner.txt").write_text("x\n", encoding="utf-8")
    _git("add", ".", cwd=repo)
    _git("commit", "-m", "add sub", cwd=repo)
    tool = GitLogTool(GitToolRunner(settings.git_repo_path, settings.git_timeout_sec), settings)
    res = tool.run(limit=10, path="sub")
    assert res.success is True
    assert res.data["commits"][0]["message"] == "add sub"


# ─────────────────── git_log limit validation ───────────────────
def test_git_log_invalid_limit(repo, settings):
    tool = GitLogTool(GitToolRunner(settings.git_repo_path, settings.git_timeout_sec), settings)
    assert tool.run(limit=0).success is False
    assert tool.run(limit=-3).success is False
    assert tool.run(limit="abc").success is False
    # Valid int string works.
    res = tool.run(limit="5")
    assert res.success is True
    assert len(res.data["commits"]) >= 1


# ─────────────────── git_checkout validation ───────────────────
def test_git_checkout_empty_branch_rejected(repo, settings):
    tool = GitCheckoutTool(GitToolRunner(settings.git_repo_path, settings.git_timeout_sec), settings)
    res = tool.run(branch="   ")
    assert res.success is False
    assert "branch" in res.error.lower()


def test_git_checkout_dash_branch_rejected(repo, settings):
    tool = GitCheckoutTool(GitToolRunner(settings.git_repo_path, settings.git_timeout_sec), settings)
    res = tool.run(branch="-x")
    assert res.success is False
    assert "must not start with '-'" in res.error


# ─────────────────── git_init on existing repo ───────────────────
def test_git_init_reinitialised_not_error(repo, settings):
    tool = GitInitTool(GitToolRunner(settings.git_repo_path, settings.git_timeout_sec), settings)
    res = tool.run()
    assert res.success is True
    assert res.data["reinitialized"] is True


# ─────────────────── runner validation defence-in-depth ───────────────────
def test_runner_unknown_dash_arg_rejected(repo, runner):
    """Unknown option flags are rejected even if not in the fixed blacklist."""
    for bad in ("--custom", "-Z", "--untracked-files=all"):
        with pytest.raises(GitCommandError):
            runner.run("status", [bad])


def test_runner_allows_known_internal_flags(repo, runner):
    res = runner.run("status", ["--short"])
    assert res["exit_code"] == 0
    res = runner.run("log", ["--oneline", "-n", "3"])
    assert res["exit_code"] == 0
    res = runner.run("diff", ["--cached"])
    assert res["exit_code"] == 0


def test_resolve_path_boundaries(repo, settings):
    r = GitToolRunner(settings.git_repo_path, settings.git_timeout_sec)
    # Default / "." / "./" -> repo root.
    assert r.resolve_path(None) == settings.git_repo_path.resolve()
    assert r.resolve_path(".") == settings.git_repo_path.resolve()
    assert r.resolve_path("./") == settings.git_repo_path.resolve()
    # Absolute path inside the repo is allowed.
    inner = settings.git_repo_path / "sub"
    inner.mkdir()
    assert r.resolve_path(str(inner)) == inner.resolve()
    # Escapes rejected.
    with pytest.raises(GitCommandError):
        r.resolve_path("../outside")
    # Dash-prefixed path rejected.
    with pytest.raises(GitCommandError):
        r.resolve_path("-flag")


# ─────────────────── TaskManager registration ───────────────────
def test_task_manager_git_enabled_loads_seven(tmp_path):
    settings = Settings(
        data_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        git_enabled=True,
        git_repo_dir=str(tmp_path / "repos"),
        git_timeout_sec=10,
    )
    eb = EventBus()
    persistence = Persistence(settings)
    mock = MockLLMClient(final_answer="ok")
    tm = TaskManager(settings, eb, persistence, llm_client=mock, tools=build_tools(settings))
    git_names = {t.name for t in tm._tools if t.name.startswith("git_")}
    assert git_names == {
        "git_status", "git_diff", "git_commit", "git_log",
        "git_branch", "git_checkout", "git_init",
    }
    tm.shutdown()


def test_task_manager_git_disabled_loads_none(tmp_path):
    settings = make_settings(tmp_path)  # git_enabled=False
    eb = EventBus()
    persistence = Persistence(settings)
    mock = MockLLMClient(final_answer="ok")
    tm = TaskManager(settings, eb, persistence, llm_client=mock, tools=build_tools(settings))
    git_names = {t.name for t in tm._tools if t.name.startswith("git_")}
    assert git_names == set()
    tm.shutdown()


def test_task_manager_git_conflict_keeps_first(tmp_path):
    """If a tool named git_status already exists, the Git tool is skipped."""
    from backend.core.tools.base import BaseTool, ToolResult

    class _FakeGitStatus(BaseTool):
        name = "git_status"
        description = "pre-existing fake"
        args_schema = {}

        def run(self, **kwargs):
            return ToolResult(success=True, data={"fake": True})

    settings = Settings(
        data_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        git_enabled=True,
        git_repo_dir=str(tmp_path / "repos"),
        git_timeout_sec=10,
    )
    eb = EventBus()
    persistence = Persistence(settings)
    mock = MockLLMClient(final_answer="ok")
    tools = build_tools(settings) + [_FakeGitStatus(settings)]
    tm = TaskManager(settings, eb, persistence, llm_client=mock, tools=tools)
    git_status = next(t for t in tm._tools if t.name == "git_status")
    assert isinstance(git_status, _FakeGitStatus)
    # The other six real Git tools are still appended.
    names = {t.name for t in tm._tools}
    assert {"git_diff", "git_commit", "git_log", "git_branch", "git_checkout", "git_init"} <= names
    tm.shutdown()


def test_build_git_tools_creates_missing_root(tmp_path):
    settings = Settings(
        data_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        git_enabled=True,
        git_repo_dir=str(tmp_path / "brand-new" / "repos"),
        git_timeout_sec=10,
    )
    tools = build_git_tools(settings)
    assert len(tools) == 7
    assert settings.git_repo_path.exists()
    assert settings.git_repo_path.is_dir()


# ─────────────────── end-to-end executor confirmation ───────────────────
def _base_state() -> dict:
    return {
        "step_index": 1,
        "steps": [{"index": 1, "thought": "", "tool_calls": [], "status": "running"}],
        "messages": [{"role": "user", "content": "hi"}],
        "plan": [], "artifacts": [], "status": "RUNNING", "stop_requested": False,
        "pending_confirm": {}, "final_answer": "", "error": "",
        "_last_action": "", "_current_tool_calls": [], "_confirmed_ids": [],
        "_rejected_ids": [], "_needs_confirm": False, "risk_report": [],
        "_risk_blocked": False, "subtasks": [], "_is_subtask": False,
    }


def _run_confirm_flow(repo, settings, tool, approved: bool) -> dict:
    """Run executor -> human_confirm -> tool_node with a fake TaskManager."""
    from backend.core.agent.nodes import AgentRuntime

    mock = MockLLMClient(
        tool_calls=[{"id": "c1", "name": tool.name, "arguments": {"message": "qa commit"}}]
    )
    bus = EventBus()

    class _FakeTM:
        # NOTE: never write `settings = settings` in a class body — the class
        # namespace shadows the enclosing function argument (resolves to the
        # module-level fixture function instead). Use __init__ parameters.
        def __init__(self, s, eb):
            self.settings = s
            self.event_bus = eb

        def request_confirm(self, task_id, tool_call_id):
            return type("EV", (), {"is_set": lambda self: True})()

        def consume_confirm(self, task_id, tool_call_id):
            return approved

        def add_artifact(self, *a, **kw):
            return None

    rt = AgentRuntime(
        "t_qa_git", _FakeTM(settings, bus), llm=mock, tools=[tool],
        tool_schemas=[tool.to_openai_schema()], confirm_enabled=True,
    )
    state = _base_state()
    rt.executor(state)
    rt.human_confirm_node(state)
    rt.tool_node(state)
    return state


def test_git_commit_requires_confirm_and_executes_after_approval(repo, settings):
    _git("add", "a.txt", cwd=repo)
    tool = GitCommitTool(GitToolRunner(settings.git_repo_path, settings.git_timeout_sec), settings)
    state = _run_confirm_flow(repo, settings, tool, approved=True)
    rec = state["_current_tool_calls"][0]
    assert rec["need_confirm"] is True
    assert rec["status"] == "success"
    assert len(rec["output"]["commit"]) == 40
    head = _git("log", "--format=%s", "-1", cwd=repo)
    assert head.stdout.strip() == "qa commit"


def test_git_commit_rejected_skips_without_side_effect(repo, settings):
    before = _git("log", "--oneline", cwd=repo).stdout.strip()
    tool = GitCommitTool(GitToolRunner(settings.git_repo_path, settings.git_timeout_sec), settings)
    state = _run_confirm_flow(repo, settings, tool, approved=False)
    rec = state["_current_tool_calls"][0]
    assert rec["status"] == "skipped"
    assert rec["error"] == "rejected by user"
    after = _git("log", "--oneline", cwd=repo).stdout.strip()
    assert before == after  # no commit happened


def test_git_read_tools_do_not_confirm(repo, settings):
    tool = GitStatusTool(GitToolRunner(settings.git_repo_path, settings.git_timeout_sec), settings)
    mock = MockLLMClient(
        tool_calls=[{"id": "c1", "name": tool.name, "arguments": {}}]
    )
    bus = EventBus()

    class _FakeTM:
        def __init__(self, s, eb):
            self.settings = s
            self.event_bus = eb

        def request_confirm(self, *a):
            raise AssertionError("read tool must not confirm")

        def consume_confirm(self, *a):
            return False

        def add_artifact(self, *a, **kw):
            return None

    from backend.core.agent.nodes import AgentRuntime

    rt = AgentRuntime(
        "t_qa_git_read", _FakeTM(settings, bus), llm=mock, tools=[tool],
        tool_schemas=[tool.to_openai_schema()], confirm_enabled=True,
    )
    state = _base_state()
    rt.executor(state)
    assert state["_current_tool_calls"][0]["need_confirm"] is False
    rt.tool_node(state)
    assert state["_current_tool_calls"][0]["status"] == "success"
