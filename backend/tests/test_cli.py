"""C4: the ``qianfan`` launcher (ADR 0010).

The launcher is tested at its entry seam: ``serve`` and ``open_browser`` are
substituted, so no port is actually bound and neither uvicorn nor a browser is
started.
"""

from __future__ import annotations

import socket

from backend import cli
from backend.config import Settings


def _settings(monkeypatch, dist, port=8000):
    settings = Settings(_env_file=None, frontend_dist=str(dist), port=port)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return settings


def _write_dist(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>qianfan</title>", encoding="utf-8")
    return dist


def test_missing_dist_prints_build_hint_and_does_not_serve(tmp_path, monkeypatch, capsys):
    _settings(monkeypatch, tmp_path / "missing")
    served = []
    code = cli.main([], serve=lambda host, port: served.append((host, port)))

    out = capsys.readouterr().out
    assert code == 1
    assert "npm run build" in out
    assert served == []


def test_explicit_port_occupancy_is_reported(tmp_path, monkeypatch, capsys):
    _settings(monkeypatch, _write_dist(tmp_path))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        probe.listen(1)
        port = probe.getsockname()[1]
        served = []
        code = cli.main(["--port", str(port)], serve=lambda host, port: served.append((host, port)))

    assert code == 1
    assert str(port) in capsys.readouterr().out
    assert served == []


def test_serves_loopback_on_the_requested_port_and_opens_the_browser(tmp_path, monkeypatch):
    _settings(monkeypatch, _write_dist(tmp_path))
    served = []
    opened = []
    code = cli.main(
        ["--port", "9123"],
        serve=lambda host, port: served.append((host, port)),
        open_browser=opened.append,
    )

    assert code == 0
    assert served == [("127.0.0.1", 9123)]
    assert opened == ["http://127.0.0.1:9123/"]


def test_no_browser_skips_the_browser(tmp_path, monkeypatch):
    _settings(monkeypatch, _write_dist(tmp_path))
    opened = []
    code = cli.main(
        ["--no-browser"],
        serve=lambda host, port: None,
        open_browser=opened.append,
    )

    assert code == 0
    assert opened == []


def test_default_port_comes_from_settings(tmp_path, monkeypatch):
    _settings(monkeypatch, _write_dist(tmp_path), port=8765)
    served = []
    code = cli.main([], serve=lambda host, port: served.append((host, port)), open_browser=lambda url: None)

    assert code == 0
    assert served == [("127.0.0.1", 8765)]


def test_port_env_is_exported_before_the_server_imports_settings(tmp_path, monkeypatch):
    """``backend.main`` reads PORT at import time, so the launcher must set it first."""
    _settings(monkeypatch, _write_dist(tmp_path))
    seen = []

    def fake_serve(host, port):
        from backend.config import get_settings

        seen.append(get_settings().port)

    monkeypatch.delenv("PORT", raising=False)
    code = cli.main(["--port", "9234"], serve=fake_serve, open_browser=lambda url: None)

    assert code == 0
    assert seen == [9234]
