"""#67 P4-2: GET/PUT /api/plugins (envelope, validation, restart flag)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.services.plugins_store import (
    PLUGIN_PRESET_OVERRIDE_ENV,
    plugins_path,
)
from backend.tests.conftest import make_settings

ALL_PACKAGE_IDS = ["files", "bash", "skills", "web-search", "subagent", "mcp"]


@pytest.fixture
def client(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app, base_url="http://localhost") as test_client:
        yield test_client, settings


def _data(response) -> dict:
    body = response.json()
    assert body["code"] == 0
    return body["data"]


def _write_config(settings, payload) -> Path:
    path = plugins_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_get_plugins_returns_catalogue_saved_and_active(client):
    test_client, settings = client
    response = test_client.get("/api/plugins")
    assert response.status_code == 200
    data = _data(response)

    assert [item["id"] for item in data["presets"]] == [
        "minimal",
        "research",
        "full",
    ]
    assert [item["id"] for item in data["packages"]] == ALL_PACKAGE_IDS
    files = next(item for item in data["packages"] if item["id"] == "files")
    assert files["tools"] == ["read", "write", "edit", "ls", "glob", "grep"]
    assert files["connects_mcp"] is False
    assert files["affects_subagent_face"] is True

    assert data["saved"] == {"preset": "full", "overrides": {}}
    assert data["active"]["preset"] == "full"
    assert data["active"]["overrides"] == {}
    assert set(data["active"]["packages"]) == set(ALL_PACKAGE_IDS)
    assert "read" in data["active"]["tools"]
    assert "spawn_subagent" in data["active"]["tools"]
    assert data["restart_required"] is False


def test_put_plugins_writes_the_file_and_reports_restart(client):
    test_client, settings = client
    response = test_client.put(
        "/api/plugins",
        json={"preset": "minimal", "overrides": {"mcp": True}},
    )
    assert response.status_code == 200
    data = _data(response)
    assert data["saved"] == {"preset": "minimal", "overrides": {"mcp": True}}
    # The running process still serves the old face until restart.
    assert data["active"]["preset"] == "full"
    assert data["restart_required"] is True

    path = plugins_path(settings)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "preset": "minimal",
        "overrides": {"mcp": True},
    }

    refreshed = _data(test_client.get("/api/plugins"))
    assert refreshed["saved"] == data["saved"]
    assert refreshed["restart_required"] is True


def test_put_same_as_active_reports_no_restart(client):
    test_client, settings = client
    response = test_client.put(
        "/api/plugins", json={"preset": "full", "overrides": {}}
    )
    assert response.status_code == 200
    data = _data(response)
    assert data["saved"] == {"preset": "full", "overrides": {}}
    assert data["restart_required"] is False
    assert plugins_path(settings).is_file()


def test_put_unknown_preset_is_400_and_does_not_write(client):
    test_client, settings = client
    response = test_client.put(
        "/api/plugins", json={"preset": "ultra", "overrides": {}}
    )
    assert response.status_code == 400
    assert "ultra" in response.json()["detail"]
    assert not plugins_path(settings).exists()


def test_put_unknown_package_is_400_and_does_not_write(client):
    test_client, settings = client
    response = test_client.put(
        "/api/plugins",
        json={"preset": "minimal", "overrides": {"nope": True}},
    )
    assert response.status_code == 400
    assert "nope" in response.json()["detail"]
    assert not plugins_path(settings).exists()


def test_put_non_boolean_override_is_422_and_does_not_write(client):
    test_client, settings = client
    response = test_client.put(
        "/api/plugins",
        json={"preset": "minimal", "overrides": {"mcp": "true"}},
    )
    assert response.status_code == 422
    assert not plugins_path(settings).exists()


def test_get_restart_required_tracks_an_external_file_edit(client):
    test_client, settings = client
    _write_config(settings, {"preset": "research", "overrides": {"mcp": False}})
    data = _data(test_client.get("/api/plugins"))
    assert data["saved"] == {"preset": "research", "overrides": {"mcp": False}}
    assert data["active"]["preset"] == "full"
    assert data["restart_required"] is True


def test_get_with_a_corrupt_file_is_a_readable_500(client):
    test_client, settings = client
    path = plugins_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")
    response = test_client.get("/api/plugins")
    assert response.status_code == 500
    assert str(path) in response.json()["detail"]


def test_cli_override_only_marks_restart_until_the_file_matches(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(PLUGIN_PRESET_OVERRIDE_ENV, "minimal")
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app, base_url="http://localhost") as test_client:
        data = _data(test_client.get("/api/plugins"))
        assert data["active"]["preset"] == "minimal"
        assert data["saved"]["preset"] == "full"
        assert data["restart_required"] is True

        response = test_client.put(
            "/api/plugins", json={"preset": "minimal", "overrides": {}}
        )
        assert response.status_code == 200
        assert _data(response)["restart_required"] is False
