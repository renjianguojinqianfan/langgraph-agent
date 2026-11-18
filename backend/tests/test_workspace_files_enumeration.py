"""S10: a bounded listing is exactly the prefix of the full enumeration.

The route caps the visible list at 2000, but the cap must never become a scan
limit: stopping early is only allowed when the order is already known. These
tests pin that the bounded call returns the same paths in the same order as
the unlimited call for both enumeration branches (git / plain walk).
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from backend.core.workspace_files import workspace_files


def _make_tree(root):
    root.mkdir(parents=True)
    for rel in (
        "a.txt",
        "A.txt",
        "b.txt",
        "dir/a.txt",
        "dir/deep/z.txt",
        "other/x.txt",
        "zzz.txt",
    ):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rel, encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "hidden").write_text("hidden", encoding="utf-8")


def _assert_prefix(root):
    full = workspace_files(root)
    assert full
    for limit in (1, 2, len(full) - 1, len(full), len(full) + 5):
        assert workspace_files(root, limit=limit) == full[:limit]


def test_plain_tree_bounded_listing_is_a_prefix(tmp_path):
    root = tmp_path / "plain"
    _make_tree(root)
    _assert_prefix(root)


def test_git_tree_bounded_listing_is_a_prefix(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is required")
    root = tmp_path / "repo"
    _make_tree(root)
    (root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (root / "ignored.txt").write_text("ignored", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "a.txt", ".gitignore"], cwd=root, check=True, capture_output=True)
    _assert_prefix(root)


def test_zero_limit_returns_nothing(tmp_path):
    root = tmp_path / "plain"
    _make_tree(root)
    assert workspace_files(root, limit=0) == []


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_bounded_listing_with_a_link_stays_exact(tmp_path):
    """A late link can resolve to an early path; the prefix must still match."""
    root = tmp_path / "plain"
    _make_tree(root)
    (root / "zzz_link.txt").symlink_to(root / ".git" / "hidden")
    _assert_prefix(root)
