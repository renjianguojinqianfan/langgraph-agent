"""C3: the shipped defaults keep the service local (ADR 0009 decisions 4 / 5).

* ``host`` listens on loopback and CORS only admits the app's own origins;
* ``TrustedHostMiddleware`` refuses a foreign ``Host`` header (DNS rebinding)
  while accepting the two local names the app is opened with.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import app, create_app
from backend.tests.conftest import make_settings


def test_defaults_are_loopback_and_own_origins(monkeypatch):
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    settings = Settings(_env_file=None)

    assert settings.host == "127.0.0.1"
    origins = settings.cors_origin_list
    assert "*" not in origins
    assert "http://localhost:5173" in origins  # Vite dev server
    assert "http://127.0.0.1:5173" in origins
    assert "http://localhost:8000" in origins  # the app's own origin
    assert "http://127.0.0.1:8000" in origins


def test_cors_own_origin_follows_the_configured_port():
    settings = Settings(
        _env_file=None, cors_origins="http://localhost:5173", port=9000
    )
    origins = settings.cors_origin_list

    assert "http://localhost:5173" in origins  # Vite dev server
    assert "http://localhost:9000" in origins  # own origin at --port 9000
    assert "http://127.0.0.1:9000" in origins


def test_empty_cors_means_own_origins_only():
    """Audit F6: clearing the list must not silently open CORS to everyone."""
    settings = Settings(_env_file=None, cors_origins="", port=8123)

    origins = settings.cors_origin_list
    assert "*" not in origins
    assert set(origins) == {"http://127.0.0.1:8123", "http://localhost:8123"}


def test_explicit_wildcard_stays_a_wildcard():
    assert Settings(_env_file=None, cors_origins="*").cors_origin_list == ["*"]


def test_empty_cors_sends_no_header_to_a_foreign_origin(tmp_path):
    """The Settings-level assertion above is backed by the real app (D-16).

    ``create_app`` keeps the production middleware order, so this exercises the
    actual CORS stack instead of a hand-built ``FastAPI()`` probe.
    """
    settings = make_settings(tmp_path, cors_origins="", port=8123)
    real_app = create_app(settings)

    with TestClient(real_app, base_url="http://localhost") as client:
        foreign = client.get("/health", headers={"Origin": "http://evil.example"})
        assert "access-control-allow-origin" not in foreign.headers

        own = client.get("/health", headers={"Origin": "http://localhost:8123"})
        assert own.headers["access-control-allow-origin"] == "http://localhost:8123"


def test_trusted_host_accepts_local_names_and_refuses_a_foreign_host():
    for base_url in ("http://localhost", "http://127.0.0.1"):
        with TestClient(app, base_url=base_url) as client:
            assert client.get("/health").status_code == 200

    with TestClient(app, base_url="http://example.com") as client:
        assert client.get("/health").status_code == 400
