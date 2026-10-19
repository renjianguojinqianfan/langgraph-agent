"""C4: same-origin hosting of the built frontend (ADR 0010 decision 6)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.main import mount_frontend


def _write_dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>qianfan</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    return dist


def test_mounts_index_and_assets_at_the_same_origin(tmp_path):
    app = FastAPI()

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    assert mount_frontend(app, _write_dist(tmp_path)) is True
    client = TestClient(app)

    assert client.get("/").status_code == 200
    assert "qianfan" in client.get("/").text
    assert client.get("/assets/app.js").text == "console.log('ok')"
    # API routes are registered before the "/" mount and keep precedence.
    assert client.get("/api/ping").json() == {"ok": True}


def test_missing_dist_is_not_mounted(tmp_path):
    app = FastAPI()

    assert mount_frontend(app, tmp_path / "missing") is False
    assert TestClient(app).get("/").status_code == 404
