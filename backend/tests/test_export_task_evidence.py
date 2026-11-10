"""``scripts/export_task_evidence.py`` must scrub machine paths before export.

Evidence JSON is committed under ``docs/assets/verification/`` and scanned by
``scripts/check_no_machine_paths.py``; the exporter is the tool that keeps those
two gates from fighting (P5-1 retro #7).
"""

from __future__ import annotations

import importlib.util
import json
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
