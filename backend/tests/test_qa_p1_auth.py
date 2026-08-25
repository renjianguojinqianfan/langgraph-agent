"""QA independent boundary tests — P1 item 5 (basic auth).

Adds API-level checks beyond the engineer's unit tests:

* ``?token=`` query parameter works at the REST layer (SSE-style);
* an *expired* issued token is rejected by the API (401), not just by a
  direct store manipulation;
* a token signed with a different secret (forged) is rejected by the API;
* ``/health`` reports ``auth_enabled`` in both states;
* auth disabled -> every endpoint passes through with zero token.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from backend.core.llm.client import MockLLMClient
from backend.core.tools.registry import build_tools
from backend.main import app
from backend.services.auth import TokenIssuer
from backend.services.event_bus import EventBus
from backend.services.persistence import Persistence
from backend.services.task_manager import TaskManager
from backend.tests.conftest import make_settings


def _make_client(tmp_path, auth_enabled, ttl_sec=86400):
    settings = make_settings(
        tmp_path,
        auth_enabled=auth_enabled,
        auth_token="secret",
        auth_token_ttl_sec=ttl_sec,
    )
    eb = EventBus()
    persistence = Persistence(settings)
    mock = MockLLMClient(plan=["p"], final_answer="done")
    tm = TaskManager(settings, eb, persistence, llm_client=mock, tools=build_tools(settings))
    client = TestClient(app)
    client.__enter__()
    client.app.state.settings = settings
    client.app.state.event_bus = eb
    client.app.state.persistence = persistence
    client.app.state.task_manager = tm
    client.app.state.auth = TokenIssuer(settings.auth_token, settings.auth_token_ttl_sec)
    return client


@pytest.fixture
def auth_client(tmp_path):
    return _make_client(tmp_path, auth_enabled=True)


@pytest.fixture
def open_client(tmp_path):
    return _make_client(tmp_path, auth_enabled=False)


def _login(client) -> str:
    return client.post("/api/auth/token", json={"token": "secret"}).json()["data"]["token"]


def test_api_accepts_query_token(auth_client):
    """REST layer accepts ?token= (the SSE transport has no headers)."""
    token = _login(auth_client)
    r = auth_client.get("/api/tasks", params={"token": token})
    assert r.status_code == 200


def test_api_rejects_query_bad_token(auth_client):
    r = auth_client.get("/api/tasks", params={"token": "forged"})
    assert r.status_code == 401


def test_api_rejects_expired_token(tmp_path):
    """A real issued token that passes its TTL must 401 at the API."""
    client = _make_client(tmp_path, auth_enabled=True, ttl_sec=1)
    token = _login(client)
    time.sleep(1.2)  # let the short TTL expire
    r = client.get("/api/tasks", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_api_rejects_forged_token_from_other_secret(tmp_path):
    """A token signed with a different secret must be rejected."""
    client = _make_client(tmp_path, auth_enabled=True)
    forged_issuer = TokenIssuer("other-secret", ttl_sec=86400)
    forged, _ = forged_issuer.issue()
    r = client.get("/api/tasks", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401


def test_health_reports_auth_enabled_both_ways(tmp_path):
    with TestClient(app) as c:
        c.app.state.settings = make_settings(tmp_path, auth_enabled=True)
        assert c.get("/health").json()["auth_enabled"] is True
        c.app.state.settings = make_settings(tmp_path, auth_enabled=False)
        assert c.get("/health").json()["auth_enabled"] is False


def test_auth_disabled_all_endpoints_open(open_client):
    """Zero regression: every protected endpoint works without a token."""
    r = open_client.get("/api/tasks")
    assert r.status_code == 200
    r = open_client.post("/api/tasks", json={"input": "hello"})
    assert r.status_code == 200
    r = open_client.get("/api/kb")
    assert r.status_code == 200
    r = open_client.post("/api/kb/rebuild")
    assert r.status_code == 200
    r = open_client.delete("/api/kb/nonexistent")
    assert r.status_code == 200


def test_token_is_not_plaintext(auth_client):
    """PRD 5.1: the issued token must not be the plaintext password."""
    token = _login(auth_client)
    assert token != "secret"
    assert "secret" not in token
