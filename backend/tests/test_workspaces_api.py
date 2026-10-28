"""#242: read-only workspace validation for the page's directory switcher."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.core.llm.client import MockLLMClient
from backend.main import app
from backend.tests.conftest import make_manager, make_settings


def _client(tmp_path):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    client = TestClient(app, base_url="http://localhost")
    client.__enter__()
    getattr(client.app, "state").task_manager = tm
    return client, tm


def _validate(client, path):
    return client.get("/api/workspaces/validate", params={"path": path})


def test_existing_directory_returns_the_normalized_path(tmp_path):
    client, _ = _client(tmp_path)
    try:
        workspace = tmp_path / "project"
        workspace.mkdir()
        response = _validate(client, str(workspace))
        assert response.status_code == 200
        assert response.json()["data"]["path"] == str(workspace.resolve())
    finally:
        client.__exit__(None, None, None)


def test_relative_path_resolves_against_the_backend_cwd(tmp_path, monkeypatch):
    workspace = tmp_path / "rel"
    workspace.mkdir()
    monkeypatch.chdir(tmp_path)
    client, _ = _client(tmp_path / "runtime")
    try:
        response = _validate(client, "rel")
        assert response.status_code == 200
        assert response.json()["data"]["path"] == str(workspace.resolve())
    finally:
        client.__exit__(None, None, None)


@pytest.mark.parametrize("path", ["", "   "])
def test_blank_path_is_rejected(tmp_path, path):
    client, _ = _client(tmp_path)
    try:
        response = _validate(client, path)
        assert response.status_code == 400
        assert "required" in response.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_missing_directory_is_rejected(tmp_path):
    client, _ = _client(tmp_path)
    try:
        response = _validate(client, str(tmp_path / "missing"))
        assert response.status_code == 400
        assert "does not exist" in response.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def test_a_file_is_not_a_workspace(tmp_path):
    client, _ = _client(tmp_path)
    try:
        target = tmp_path / "file.txt"
        target.write_text("x", encoding="utf-8")
        response = _validate(client, str(target))
        assert response.status_code == 400
        assert "not a directory" in response.json()["detail"]
    finally:
        client.__exit__(None, None, None)


@pytest.mark.parametrize("path", [r"\\attacker.example\share", r"\\.\pipe\x"])
def test_non_local_paths_are_rejected(tmp_path, path):
    client, _ = _client(tmp_path)
    try:
        response = _validate(client, path)
        assert response.status_code == 400
        assert "local path" in response.json()["detail"]
    finally:
        client.__exit__(None, None, None)
