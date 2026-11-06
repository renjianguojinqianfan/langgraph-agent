"""Guard #252: the offline suite must not point runtime paths at the checkout.

``backend.main`` exposes a module-level app whose production lifespan builds a
``TaskManager`` over ``get_settings()``. Every test that enters that lifespan
(``TestClient(app)``) must land in the process-wide throwaway root, never in
``PROJECT_ROOT/data``. This file pins both halves: the env root itself and the
settings the module-level app actually uses.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from backend.config import PROJECT_ROOT, get_settings


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def test_test_runtime_root_is_a_created_directory_outside_the_checkout():
    raw = os.environ.get("QIANFAN_TEST_DATA_ROOT")
    assert raw, "conftest must export QIANFAN_TEST_DATA_ROOT before backend imports"
    root = Path(raw)
    assert root.is_dir()
    assert not _is_within(root, PROJECT_ROOT)


def test_default_runtime_paths_are_outside_the_checkout():
    settings = get_settings()
    for path in (
        settings.data_path,
        settings.artifacts_path,
        settings.trace_path,
        settings.checkpoint_path,
    ):
        assert not _is_within(path, PROJECT_ROOT), f"{path} points into the checkout"


def test_checkpoint_path_follows_the_test_root():
    root = Path(os.environ["QIANFAN_TEST_DATA_ROOT"])
    assert _is_within(get_settings().checkpoint_path, root)
    # c2: config.py falls back to data/checkpoints when CHECKPOINT_DIR is empty
    # or relative, so the settings assertion above is also green when conftest's
    # direct assignment is deleted. Pin the environment variable itself.
    assert _is_within(Path(os.environ["CHECKPOINT_DIR"]), root)


def test_pytest_tmp_stays_outside_the_checkout(tmp_path):
    """pytest's own basetemp must not land in the checkout when OS temp is denied.

    c6: an explicit ``PYTEST_DEBUG_TEMPROOT`` / ``--basetemp`` deliberately
    overrides the fallback (pytest's own precedence rules); skipping is honest,
    reporting that as a suite failure is not.
    """
    override = os.environ.get("PYTEST_DEBUG_TEMPROOT") or any(
        arg.startswith("--basetemp") for arg in sys.argv
    )
    if override:
        pytest.skip("explicit pytest temp-root override (#252 c6): out of scope")
    root = Path(os.environ["QIANFAN_TEST_DATA_ROOT"])
    assert not _is_within(tmp_path, PROJECT_ROOT)
    # c3: the tempfile.gettempdir() assertion was tautological with conftest's
    # ``tempfile.tempdir`` assignment; tmp_path is the real anchor here.
    assert _is_within(tmp_path, root)


def test_module_level_app_lifespan_uses_isolated_paths():
    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app, base_url="http://localhost") as client:
        settings = client.app.state.settings
        persistence = client.app.state.persistence
        assert not _is_within(settings.data_path, PROJECT_ROOT)
        assert not _is_within(persistence.data_path, PROJECT_ROOT)
        assert _is_within(
            settings.data_path, Path(os.environ["QIANFAN_TEST_DATA_ROOT"])
        )


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    if not root.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_mtime_ns,
            path.stat().st_size,
        )
        for path in root.rglob("*")
        if path.is_file()
    }


def test_module_level_app_lifespan_does_not_touch_repo_data():
    """The original TEST-01 failure: entering the app must not write checkout data."""
    from fastapi.testclient import TestClient

    from backend.main import app

    repo_data = PROJECT_ROOT / "data"
    before = _snapshot(repo_data)
    with TestClient(app, base_url="http://localhost"):
        pass
    assert _snapshot(repo_data) == before
