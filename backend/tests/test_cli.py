"""C4: the ``qianfan`` launcher (ADR 0010).

The launcher is tested at its entry seam: ``serve`` and ``open_browser`` are
substituted, so no port is actually bound and neither uvicorn nor a browser is
started.
"""

from __future__ import annotations

import os
import socket
import time
from pathlib import Path
from urllib.parse import quote

from backend import cli
from backend.config import Settings


def _settings(monkeypatch, dist, port=8000):
    settings = Settings(
        _env_file=None,
        frontend_dist=str(dist),
        port=port,
        data_dir=str(Path(dist).parent / "server-data"),
    )
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
        when_ready=lambda url, port: opened.append(url),
    )

    assert code == 0
    assert served == [("127.0.0.1", 9123)]
    assert opened == [f"http://127.0.0.1:9123/?dir={quote(os.getcwd())}"]


def test_no_browser_skips_the_browser(tmp_path, monkeypatch):
    _settings(monkeypatch, _write_dist(tmp_path))
    opened = []
    code = cli.main(
        ["--no-browser"],
        serve=lambda host, port: None,
        when_ready=lambda url, port: opened.append(url),
    )

    assert code == 0
    assert opened == []


def test_default_port_comes_from_settings(tmp_path, monkeypatch):
    _settings(monkeypatch, _write_dist(tmp_path), port=8765)
    served = []
    code = cli.main(
        [],
        serve=lambda host, port: served.append((host, port)),
        when_ready=lambda url, port: None,
    )

    assert code == 0
    assert served == [("127.0.0.1", 8765)]


def test_invalid_ports_are_rejected_before_serving(tmp_path, monkeypatch, capsys):
    _settings(monkeypatch, _write_dist(tmp_path))
    served = []
    for port in ("0", "70000"):
        code = cli.main(["--port", port], serve=lambda host, port: served.append((host, port)))
        assert code == 1
        assert port in capsys.readouterr().out
    assert served == []


def test_stale_build_warns_but_still_serves(tmp_path, monkeypatch, capsys):
    dist = _write_dist(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.tsx").write_text("export {}", encoding="utf-8")
    old = time.time() - 100
    os.utime(dist / "index.html", (old, old))

    _settings(monkeypatch, dist)
    served = []
    code = cli.main(
        [],
        serve=lambda host, port: served.append((host, port)),
        when_ready=lambda url, port: None,
    )

    assert code == 0
    assert "npm run build" in capsys.readouterr().out
    assert served == [("127.0.0.1", 8000)]


def test_port_env_is_exported_before_the_server_imports_settings(tmp_path, monkeypatch):
    """``backend.main`` reads PORT at import time, so the launcher must set it first."""
    _settings(monkeypatch, _write_dist(tmp_path))
    seen = []

    def fake_serve(host, port):
        from backend.config import get_settings

        seen.append(get_settings().port)

    monkeypatch.delenv("PORT", raising=False)
    code = cli.main(
        ["--port", "9234"],
        serve=fake_serve,
        when_ready=lambda url, port: None,
    )

    assert code == 0
    assert seen == [9234]


def test_main_delegates_the_browser_open_to_the_ready_watcher(tmp_path, monkeypatch):
    _settings(monkeypatch, _write_dist(tmp_path))
    recorded = []
    code = cli.main(
        ["--port", "9124"],
        serve=lambda host, port: None,
        when_ready=lambda url, port: recorded.append((url, port)),
    )

    assert code == 0
    assert recorded == [(f"http://127.0.0.1:9124/?dir={quote(os.getcwd())}", 9124)]


def test_open_when_ready_waits_for_health_before_opening():
    probes = []
    responses = [None, None, {"status": "ok"}]

    def probe(port, timeout):
        probes.append((port, timeout))
        return responses.pop(0)

    opened = []
    slept = []
    url = "http://127.0.0.1:9123/?dir=example"
    ok = cli._open_when_ready(
        url, 9123, browser=opened.append, probe=probe, sleep=slept.append
    )

    assert ok is True
    assert opened == [url]
    assert [port for port, _ in probes] == [9123, 9123, 9123]
    assert slept == [0.2, 0.2]


def test_open_when_ready_timeout_prints_url_without_opening(capsys):
    ticks = iter([0.0, 0.0, 1.0])
    opened = []
    url = "http://127.0.0.1:9123/?dir=example"

    ok = cli._open_when_ready(
        url,
        9123,
        browser=opened.append,
        probe=lambda port, timeout: None,
        wait=0.5,
        clock=lambda: next(ticks),
        sleep=lambda seconds: None,
    )

    assert ok is False
    assert opened == []
    out = capsys.readouterr().out
    assert url in out
    assert "未自动打开浏览器" in out
