"""W3: glob/grep skip .git and gitignored files (ADR 0010 decision 5).

The fixture builds a real temporary git repository; the tests are skipped only
when git is not installed (this chapter requires Git Bash).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.config import Settings
from backend.core.tools.file_io import GlobTool, GrepTool, LsTool, ReadTool
from backend.core.workspace_files import workspace_files

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")


def _settings(tmp_path: Path, workspace: Path) -> Settings:
    return Settings(
        data_dir=str(tmp_path / "data"),
        artifacts_dir=str(workspace),
        trace_dir=str(tmp_path / "traces"),
        llm_base_url="",
        use_mock_llm=True,
        mcp_enabled=False,
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Settings, Path]:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _git(workspace, "init", "-q")
    (workspace / ".gitignore").write_text("ignored.txt\nnode_modules/\n", encoding="utf-8")
    (workspace / "tracked.py").write_text("needle tracked\n", encoding="utf-8")
    (workspace / "untracked.py").write_text("needle untracked\n", encoding="utf-8")
    (workspace / "ignored.txt").write_text("needle ignored\n", encoding="utf-8")
    modules = workspace / "node_modules"
    modules.mkdir()
    (modules / "x.js").write_text("needle module\n", encoding="utf-8")
    plans = workspace / ".qianfan"
    plans.mkdir()
    (plans / ".gitignore").write_text("*\n", encoding="utf-8")
    (plans / "plan.md").write_text("needle plan\n", encoding="utf-8")
    _git(workspace, "add", "tracked.py", ".gitignore")
    return _settings(tmp_path, workspace), workspace


def test_glob_skips_git_and_ignored_files(repo):
    settings, _ = repo
    res = GlobTool(settings).run(pattern="**/*")
    matches = set(res.data["matches"])

    assert {"tracked.py", "untracked.py", ".gitignore"} <= matches
    assert "ignored.txt" not in matches
    assert "node_modules/x.js" not in matches
    assert ".qianfan/plan.md" not in matches
    assert not any(m.startswith(".git/") for m in matches)


def test_grep_skips_git_and_ignored_files(repo):
    settings, _ = repo
    res = GrepTool(settings).run(pattern="needle")
    files = {match["file"] for match in res.data["matches"]}

    assert {"tracked.py", "untracked.py"} <= files
    assert "ignored.txt" not in files
    assert "node_modules/x.js" not in files
    assert ".qianfan/plan.md" not in files


def test_ls_and_named_read_still_see_ignored_files(repo):
    settings, _ = repo
    entries = {entry["name"] for entry in LsTool(settings).run().data["entries"]}
    assert {"ignored.txt", "node_modules", ".qianfan"} <= entries

    read = ReadTool(settings).run(path="ignored.txt")
    assert read.success is True
    assert "needle ignored" in read.data["content"]


def test_non_git_directory_only_skips_dot_git(tmp_path):
    workspace = tmp_path / "plain"
    workspace.mkdir()
    (workspace / "a.txt").write_text("needle plain\n", encoding="utf-8")
    hidden = workspace / ".git"
    hidden.mkdir()
    (hidden / "secret.txt").write_text("needle secret\n", encoding="utf-8")
    settings = _settings(tmp_path, workspace)

    glob = GlobTool(settings).run(pattern="**/*")
    assert glob.data["matches"] == ["a.txt"]
    grep = GrepTool(settings).run(pattern="needle")
    assert {match["file"] for match in grep.data["matches"]} == {"a.txt"}


def test_workspace_files_is_the_shared_list(repo):
    _, workspace = repo
    files = workspace_files(workspace)
    names = {path.relative_to(workspace).as_posix() for path in files}

    assert {"tracked.py", "untracked.py", ".gitignore"} <= names
    assert "ignored.txt" not in names
    assert "node_modules/x.js" not in names
    assert ".qianfan/plan.md" not in names


def test_glob_matches_pathlib_semantics(tmp_path):
    """`**` spans zero or more segments; wildcard prefixes are not literals."""
    workspace = tmp_path / "plain"
    for rel in (
        "top.py",
        "src/a.py",
        "src/deep/b.py",
        "src2/c.py",
        "srcx/deep/d.py",
        "other/src/e.py",
    ):
        path = workspace / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    settings = _settings(tmp_path, workspace)

    patterns = ("*.py", "**/*.py", "src/**/*.py", "src*/**/*.py", "src/**/b.py")
    for pattern in patterns:
        expected = {
            path.relative_to(workspace).as_posix()
            for path in workspace.glob(pattern)
            if path.is_file()
        }
        result = GlobTool(settings).run(pattern=pattern)
        assert set(result.data["matches"]) == expected, pattern

    wildcard = set(GlobTool(settings).run(pattern="src*/**/*.py").data["matches"])
    assert wildcard == {
        "src/a.py",
        "src/deep/b.py",
        "src2/c.py",
        "srcx/deep/d.py",
    }


def test_glob_mid_pattern_double_star_matches_zero_directories(repo):
    settings, workspace = repo
    (workspace / "sub").mkdir()
    (workspace / "sub" / "x.py").write_text("x", encoding="utf-8")
    (workspace / "sub" / "deep").mkdir()
    (workspace / "sub" / "deep" / "y.py").write_text("y", encoding="utf-8")
    (workspace / "other").mkdir()
    (workspace / "other" / "sub").mkdir()
    (workspace / "other" / "sub" / "z.py").write_text("z", encoding="utf-8")

    matches = set(GlobTool(settings).run(pattern="sub/**/*.py").data["matches"])
    assert matches == {"sub/x.py", "sub/deep/y.py"}

    all_files = set(GlobTool(settings).run(pattern="**").data["matches"])
    assert {"tracked.py", "sub/x.py", "sub/deep/y.py"} <= all_files


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_non_git_symlink_escape_is_skipped(tmp_path):
    workspace = tmp_path / "plain"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("needle outside\n", encoding="utf-8")
    (workspace / "link.txt").symlink_to(outside)
    (workspace / "inside.txt").write_text("needle inside\n", encoding="utf-8")
    settings = _settings(tmp_path, workspace)

    glob = GlobTool(settings).run(pattern="**/*")
    assert glob.success is True
    assert glob.data["matches"] == ["inside.txt"]
    grep = GrepTool(settings).run(pattern="needle")
    assert {match["file"] for match in grep.data["matches"]} == {"inside.txt"}


def test_nested_workspace_filters_to_its_own_subtree(tmp_path):
    repo_root = tmp_path / "big"
    repo_root.mkdir()
    _git(repo_root, "init", "-q")
    sub = repo_root / "project"
    sub.mkdir()
    (sub / "a.py").write_text("x", encoding="utf-8")
    (repo_root / "other.py").write_text("x", encoding="utf-8")
    settings = _settings(tmp_path, sub)

    matches = GlobTool(settings).run(pattern="**/*.py").data["matches"]
    assert matches == ["a.py"]
