"""C3: the shipped defaults keep the service local (ADR 0009 decisions 4 / 5).

* ``host`` listens on loopback and CORS only admits the app's own origins;
* ``TrustedHostMiddleware`` refuses a foreign ``Host`` header (DNS rebinding)
  while accepting the two local names the app is opened with.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import app


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


def test_trusted_host_accepts_local_names_and_refuses_a_foreign_host():
    for base_url in ("http://localhost", "http://127.0.0.1"):
        with TestClient(app, base_url=base_url) as client:
            assert client.get("/health").status_code == 200

    with TestClient(app, base_url="http://example.com") as client:
        assert client.get("/health").status_code == 400
