"""``scripts/export_task_evidence.py`` must scrub machine paths before export.

Evidence JSON is committed under ``docs/assets/verification/`` and scanned by
``scripts/check_no_machine_paths.py``; the exporter is the tool that keeps those
two gates from fighting (P5-1 retro #7).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "export_task_evidence.py"

_spec = importlib.util.spec_from_file_location("export_task_evidence", SCRIPT)
assert _spec is not None and _spec.loader is not None
export = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(export)


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
    directory that merely extends a root's name (``.../<repo-root>-old``) is
    not that root's content, so the regex must not match it."""
    for root in (REPO_ROOT, Path.home()):
        pattern = export._path_regex(root)
        name = str(root)
        assert pattern.search(name) is not None  # the root itself matches
        assert pattern.search(name + "-old") is None  # a sibling does not
        assert pattern.search(name + "/keep.txt") is not None  # real content does


def test_scrub_does_not_whitewash_a_sibling_of_the_roots():
    """A directory that merely extends a root's name is not that root's
    content: misattributing it as ``<repo-root>-old`` / ``<user>-old`` would
    fail the machine-path gate (its placeholder list matches whole segments
    only). A checkout living under the home directory is legitimately scrubbed
    by its home prefix first, so only the misattribution markers are pinned
    here; the mechanism itself is pinned by the regex-level test above."""
    for root in (REPO_ROOT, Path.home()):
        cleaned = export.scrub(str(root) + "-old/keep.txt")
        assert "<repo-root>-old" not in cleaned
        assert "<user>-old" not in cleaned


def test_scrub_placeholders_are_registered_in_the_machine_path_gate():
    """scrub() is the tool that keeps exported evidence passing
    ``scripts/check_no_machine_paths.py`` (that gate's own red/green matrix is
    pinned in ``test_no_machine_paths.py``). The two scripts move together: a
    placeholder renamed on one side only used to surface as a CI failure on a
    committed evidence file, so the seam is pinned here."""
    spec = importlib.util.spec_from_file_location(
        "check_no_machine_paths", REPO_ROOT / "scripts" / "check_no_machine_paths.py"
    )
    assert spec is not None and spec.loader is not None
    gate = importlib.util.module_from_spec(spec)
    # Register before exec: the module's Rule dataclass resolves its own module
    # through sys.modules at class-creation time.
    sys.modules["check_no_machine_paths"] = gate
    spec.loader.exec_module(gate)

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
