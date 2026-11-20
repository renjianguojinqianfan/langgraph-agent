"""#345: read-only skill catalogue (``GET /api/skills``).

The endpoint exposes the same two-layer discovery the injection block and
``load_skill`` use. It must never leak filesystem paths and stays behind the
regular auth dependency.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.llm.client import MockLLMClient
from backend.main import app
from backend.services.auth import TokenIssuer
from backend.tests.conftest import make_manager, make_settings


def _write_skill(root: Path, name: str, description: str) -> Path:
    skill_dir = root / ".agents" / "skills" / name
    skill_dir.mkdir(parents=True)
    path = skill_dir / "SKILL.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody for {name}\n",
        encoding="utf-8",
    )
    return path


def _client(tmp_path, *, packages=None, auth_enabled=False):
    settings = make_settings(
        tmp_path,
        **({"auth_enabled": True, "auth_token": "secret"} if auth_enabled else {}),
    )
    tm = make_manager(
        settings,
        MockLLMClient(final_answer="ok"),
        packages=packages,
    )
    client = TestClient(app, base_url="http://localhost")
    client.__enter__()
    client.app.state.settings = settings
    client.app.state.task_manager = tm
    if auth_enabled:
        client.app.state.auth = TokenIssuer(settings.auth_token, settings.auth_token_ttl_sec)
    return client, tm


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: root))
    return root


def test_lists_both_layers_for_an_existing_task(home, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_skill(home, "home-skill", "from home")
    _write_skill(workspace, "workspace-skill", "from workspace")
    client, tm = _client(tmp_path)
    task_id = tm.create_task("skills", "list skills", workspace=str(workspace))
    try:
        response = client.get(f"/api/skills?task_id={task_id}")
    finally:
        client.__exit__(None, None, None)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["enabled"] is True
    assert data["skills"] == [
        {"name": "home-skill", "description": "from home", "layer": "home"},
        {
            "name": "workspace-skill",
            "description": "from workspace",
            "layer": "workspace",
        },
    ]


def test_lists_a_new_task_workspace(home, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_skill(workspace, "probe", "workspace only")
    client, _tm = _client(tmp_path)
    try:
        response = client.get(f"/api/skills?workspace={workspace}")
    finally:
        client.__exit__(None, None, None)

    assert response.status_code == 200
    assert response.json()["data"]["skills"] == [
        {"name": "probe", "description": "workspace only", "layer": "workspace"}
    ]


def test_keeps_same_name_skills_from_both_layers(home, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_skill(home, "shared", "home copy")
    _write_skill(workspace, "shared", "workspace copy")
    client, _tm = _client(tmp_path)
    try:
        response = client.get(f"/api/skills?workspace={workspace}")
    finally:
        client.__exit__(None, None, None)

    assert response.status_code == 200
    assert response.json()["data"]["skills"] == [
        {"name": "shared", "description": "home copy", "layer": "home"},
        {"name": "shared", "description": "workspace copy", "layer": "workspace"},
    ]


def test_defaults_to_the_backend_workspace(home, tmp_path):
    client, tm = _client(tmp_path)
    default_root = Path(tm.default_workspace)
    default_root.mkdir(parents=True, exist_ok=True)
    _write_skill(default_root, "launch-dir", "default workspace")
    try:
        response = client.get("/api/skills")
    finally:
        client.__exit__(None, None, None)

    assert response.status_code == 200
    assert response.json()["data"]["skills"] == [
        {
            "name": "launch-dir",
            "description": "default workspace",
            "layer": "workspace",
        }
    ]


def test_empty_skills_directory_is_enabled_and_empty(home, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    client, _tm = _client(tmp_path)
    try:
        response = client.get(f"/api/skills?workspace={workspace}")
    finally:
        client.__exit__(None, None, None)

    assert response.status_code == 200
    assert response.json()["data"] == {"enabled": True, "skills": []}


def test_disabled_skills_package_returns_an_empty_catalogue(home, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_skill(workspace, "hidden", "not available")
    client, _tm = _client(tmp_path, packages=frozenset({"files"}))
    try:
        response = client.get(f"/api/skills?workspace={workspace}")
    finally:
        client.__exit__(None, None, None)

    assert response.status_code == 200
    assert response.json()["data"] == {"enabled": False, "skills": []}


def test_unknown_task_is_404(home, tmp_path):
    client, _tm = _client(tmp_path)
    try:
        response = client.get("/api/skills?task_id=missing")
    finally:
        client.__exit__(None, None, None)

    assert response.status_code == 404


def test_invalid_or_missing_workspace_is_400(home, tmp_path):
    client, _tm = _client(tmp_path)
    missing = tmp_path / "missing"
    try:
        absent = client.get("/api/skills", params={"workspace": str(missing)})
        unc = client.get("/api/skills", params={"workspace": r"\\server\share"})
    finally:
        client.__exit__(None, None, None)

    assert absent.status_code == 400
    assert unc.status_code == 400


def test_requires_auth(home, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    client, _tm = _client(tmp_path, auth_enabled=True)
    try:
        anonymous = client.get(f"/api/skills?workspace={workspace}")
        login = client.post("/api/auth/token", json={"token": "secret"})
        issued = login.json()["data"]["token"]
        logged_in = client.get(
            f"/api/skills?workspace={workspace}",
            headers={"Authorization": f"Bearer {issued}"},
        )
    finally:
        client.__exit__(None, None, None)

    assert anonymous.status_code == 401
    assert logged_in.status_code == 200


def test_response_never_leaks_absolute_paths(home, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_skill(home, "home-skill", "from home")
    _write_skill(workspace, "workspace-skill", "from workspace")
    client, _tm = _client(tmp_path)
    try:
        response = client.get(f"/api/skills?workspace={workspace}")
    finally:
        client.__exit__(None, None, None)

    assert response.status_code == 200
    body = json.dumps(response.json())
    assert str(home) not in body
    assert str(workspace) not in body
    assert ".agents" not in body
    assert "SKILL.md" not in body
