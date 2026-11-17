"""``scripts/export_task_evidence.py`` must scrub machine paths before export.

Evidence JSON is committed under ``docs/assets/verification/`` and scanned by
``scripts/check_no_machine_paths.py``; the exporter is the tool that keeps those
two gates from fighting (P5-1 retro #7).
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.tests.conftest import load_script_module

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "export_task_evidence.py"

export = load_script_module("export_task_evidence", SCRIPT)


def test_scrub_replaces_repo_root_and_home():
    record = {
        "workspace": str(REPO_ROOT / "data" / "artifacts" / "t1"),
        "home_file": str(Path.home() / ".qianfan" / "credentials.json"),
        "nested": [{"path": str(REPO_ROOT)}],
    }

    cleaned = export.scrub(record)

    assert cleaned["workspace"].startswith("<repo-root>")
    assert cleaned["home_file"].startswith("<user>")
    assert cleaned["nested"][0]["path"] == "<repo-root>"
    assert str(REPO_ROOT) not in json.dumps(cleaned, ensure_ascii=False)


def test_scrub_matches_both_separator_styles():
    forward = str(REPO_ROOT).replace("\\", "/") + "/frontend"

    assert export.scrub(forward) == "<repo-root>/frontend"


def test_scrub_replaces_absolute_paths_outside_repo_and_home():
    """A task opened from another directory must not leak its launch path."""
    record = {
        "workspace": "D:\\work\\demo",
        "output": "wrote D:\\work\\demo\\a.txt",
        "mount": "/c/projects/demo/file.txt",
    }

    cleaned = export.scrub(record)

    assert cleaned["workspace"] == "<workspace>"
    assert cleaned["output"] == "wrote <workspace>"
    assert cleaned["mount"] == "<workspace>"


def test_scrub_keeps_urls():
    assert (
        export.scrub("see https://example.com/home/docs")
        == "see https://example.com/home/docs"
    )


def test_scrub_leaves_plain_values_alone():
    record = {"status": "INTERRUPTED", "steps": 3, "error": None, "ok": True}

    assert export.scrub(record) == record


def test_root_regexes_keep_a_sibling_of_the_root_name():
    """The trailing lookahead in ``_path_regex`` (#318 review rework): a
    sibling that extends a root's name with word characters or ``-``
    (``.../<repo-root>-old``) is not that root's content, so the regex must
    not match it. Scope note (#323 review): dot/space extensions (``.bak``,
    `` copy``) still slip past the lookahead — a known semantic
    misattribution, not a gate failure."""
    for root in (REPO_ROOT, Path.home()):
        pattern = export._path_regex(root)
        name = str(root)
        assert pattern.search(name) is not None  # the root itself matches
        for suffix in ("-old", "2", "_bak"):
            assert pattern.search(name + suffix) is None  # siblings do not
        assert pattern.search(name + "/keep.txt") is not None  # real content does


def test_scrub_does_not_whitewash_a_sibling_of_the_roots():
    """A sibling that extends a root's name with word characters or ``-`` is
    not that root's content: misattributing it as ``<repo-root>-old`` /
    ``<user>-old`` would glue a placeholder onto a foreign directory. That is
    a semantic misattribution, not a CI red — the machine-path gate does not
    flag placeholder-prefixed paths (#323 review correction). A checkout
    living under the home directory is legitimately scrubbed by its home
    prefix first, so only the misattribution markers are pinned here; the
    mechanism itself is pinned by the regex-level test above."""
    for root in (REPO_ROOT, Path.home()):
        for suffix in ("-old", "2", "_bak"):
            cleaned = export.scrub(str(root) + suffix + "/keep.txt")
            assert f"<repo-root>{suffix}" not in cleaned
            assert f"<user>{suffix}" not in cleaned


def test_scrub_placeholders_are_registered_in_the_machine_path_gate():
    """scrub() and ``scripts/check_no_machine_paths.py`` must carry the same
    placeholder vocabulary (the gate's own red/green matrix is pinned in
    ``test_no_machine_paths.py``). The interlock is presence on both sides,
    not a CI-red causality: the gate only scans drive / mount / POSIX-home
    prefixes, so a placeholder missing from one list fails silently on bare
    placeholder paths (#323 review)."""
    gate = load_script_module(
        "check_no_machine_paths", REPO_ROOT / "scripts" / "check_no_machine_paths.py"
    )

    assert {"<repo-root>", "<user>", "<workspace>"} <= set(gate.PLACEHOLDERS)


def test_pick_keeps_evidence_fields_only():
    task = {
        "id": "t1",
        "title": "题",
        "status": "INTERRUPTED",
        "steps": [],
        "conversation": [],
        "unknown_internal_field": "drop me",
    }

    picked = export.pick(task)

    assert picked["id"] == "t1"
    assert picked["steps"] == []
    assert "unknown_internal_field" not in picked
