"""P1 item 5 — basic auth tests (fully offline).

Covers :class:`TokenIssuer` signing/verification/expiry and the FastAPI
integration: 401 without a token, login issuance, Bearer acceptance, SSE
``?token=`` handling, and the ``auth_enabled=false`` passthrough (zero
regression).
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


# ── TokenIssuer unit tests ──
def test_issue_and_verify():
    issuer = TokenIssuer("secret", ttl_sec=86400)
    token, expires_at = issuer.issue()
    assert token.count(".") == 2
    assert expires_at > time.time()
    assert issuer.verify(token) is True


def test_verify_rejects_tampered_token():
    issuer = TokenIssuer("secret", ttl_sec=86400)
    token, _ = issuer.issue()
    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
    assert issuer.verify(tampered) is False


def test_verify_rejects_expired_token():
    issuer = TokenIssuer("secret", ttl_sec=86400)
    token, _ = issuer.issue()
    # Simulate the token expiring (registered expiry in the past).
    with issuer._lock:
        issuer._store[token] = time.time() - 1
    assert issuer.verify(token) is False


def test_verify_rejects_unregistered_token():
    issuer = TokenIssuer("secret", ttl_sec=86400)
    # A token signed with the same secret but never issued is rejected.
    other = TokenIssuer("secret", ttl_sec=86400)
    token, _ = other.issue()
    assert issuer.verify(token) is False


def test_verify_rejects_garbage():
    assert TokenIssuer("s").verify("not-a-token") is False
    assert TokenIssuer("s").verify("") is False


# ── API integration ──
def _make_client(tmp_path, auth_enabled):
    settings = make_settings(
        tmp_path,
        auth_enabled=auth_enabled,
        auth_token="secret",
        auth_token_ttl_sec=86400,
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


def test_auth_enabled_401_without_token(auth_client):
    r = auth_client.get("/api/tasks")
    assert r.status_code == 401


def test_auth_enabled_401_with_bad_token(auth_client):
    r = auth_client.get("/api/tasks", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_login_issues_token_and_bearer_works(auth_client):
    r = auth_client.post("/api/auth/token", json={"token": "secret"})
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    token = body["data"]["token"]
    assert token
    assert body["data"]["expires_at"]

    r = auth_client.get("/api/tasks", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_login_wrong_password_401(auth_client):
    r = auth_client.post("/api/auth/token", json={"token": "nope"})
    assert r.status_code == 401


def test_auth_disabled_passthrough(open_client):
    r = open_client.get("/api/tasks")
    assert r.status_code == 200
    # auth disabled still returns the {ok:true} placeholder shape.
    r = open_client.post("/api/auth/token", json={"token": "secret"})
    assert r.status_code == 200
    assert r.json()["data"]["ok"] is True


def test_verify_token_accepts_query_param(tmp_path):
    """SSE uses ``?token=`` (EventSource cannot set headers) — the dependency
    must read the query parameter directly."""
    from types import SimpleNamespace

    from fastapi import HTTPException

    from backend.services.auth import verify_token

    settings = make_settings(tmp_path, auth_enabled=True, auth_token="secret")
    issuer = TokenIssuer("secret", ttl_sec=86400)
    token, _ = issuer.issue()
    fake_app = SimpleNamespace(state=SimpleNamespace(settings=settings, auth=issuer))
    fake_req = SimpleNamespace(app=fake_app)

    assert verify_token(fake_req, authorization=None, token=token) == token
    with pytest.raises(HTTPException) as exc:
        verify_token(fake_req, authorization=None, token="bad-token")
    assert exc.value.status_code == 401


def test_sse_without_token_401(auth_client):
    tid = auth_client.post(
        "/api/tasks", json={"input": "t"}, headers={"Authorization": f"Bearer {_login(auth_client)}"}
    ).json()["data"]["task_id"]
    r = auth_client.get(f"/api/tasks/{tid}/events")
    assert r.status_code == 401


def _login(client) -> str:
    return client.post("/api/auth/token", json={"token": "secret"}).json()["data"]["token"]


def test_health_reports_auth_enabled(tmp_path):
    with TestClient(app) as c:
        c.app.state.settings = make_settings(tmp_path, auth_enabled=True)
        r = c.get("/health")
        assert r.status_code == 200
        assert r.json()["auth_enabled"] is True
