"""C4 + #332 S5: the ``qianfan`` launcher.

``qianfan`` (no subcommand) ensures the shared backend: hand off to a healthy
instance, otherwise claim the record, spawn the detached ``backend.serve``
child, wait for ``/health`` and open the browser. ``qianfan serve`` is the
foreground entry. The backend seams (spawn / probe / browser / runner) are
substituted; the lock file lives in a temporary data directory.
"""

from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path
from urllib.parse import quote

import pytest

from backend import cli, server_lock
from backend.config import Settings, get_settings


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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class FakeChild:
    def __init__(self, *, exit_code=None):
        self.pid = 424242
        self._exit_code = exit_code
        self.terminated = False
        self.waited = False

    def poll(self):
        return self._exit_code

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.waited = True
        return 0


def _recording_spawn(seen: list):
    def spawn(cmd, env, log_path):
        seen.append({"cmd": cmd, "env": env, "log_path": log_path})
        return FakeChild()

    return spawn


# ── ensure (default command) ────────────────────────────────────────────────
def test_missing_dist_prints_build_hint_and_does_not_spawn(tmp_path, monkeypatch, capsys):
    _settings(monkeypatch, tmp_path / "missing")
    seen: list = []
    code = cli.main([], spawn=_recording_spawn(seen))

    out = capsys.readouterr().out
    assert code == 1
    assert "npm run build" in out
    assert seen == []


def test_explicit_port_occupancy_is_reported_before_spawning(tmp_path, monkeypatch, capsys):
    _settings(monkeypatch, _write_dist(tmp_path))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        probe.listen(1)
        port = probe.getsockname()[1]
        seen: list = []
        code = cli.main(["--port", str(port)], spawn=_recording_spawn(seen))

    assert code == 1
    assert str(port) in capsys.readouterr().out
    assert seen == []


def test_ensure_spawns_the_detached_backend_and_opens_the_browser(tmp_path, monkeypatch):
    settings = _settings(monkeypatch, _write_dist(tmp_path))
    port = _free_port()
    seen: list = []
    opened: list[str] = []
    code = cli.main(
        ["--port", str(port)],
        spawn=_recording_spawn(seen),
        open_browser=opened.append,
        probe=lambda port, timeout: {"instance": "child"},
    )

    assert code == 0
    assert len(seen) == 1
    call = seen[0]
    assert call["cmd"][:4] == [sys.executable, "-m", "backend.serve", "--port"]
    assert call["cmd"][4] == str(port)
    assert "--instance" in call["cmd"]
    instance = call["cmd"][call["cmd"].index("--instance") + 1]
    assert call["env"]["PORT"] == str(port)
    assert call["env"]["QIANFAN_INSTANCE"] == instance
    assert call["log_path"] == Path(settings.data_path) / "logs" / "server.log"
    record = server_lock.read_lock(server_lock.lock_path(settings))
    assert record is not None and record.instance == instance and record.port == port
    assert opened == [f"http://127.0.0.1:{port}/?dir={quote(os.getcwd())}"]


def test_ensure_spawns_before_waiting_and_leaves_the_record_for_the_child(tmp_path, monkeypatch):
    settings = _settings(monkeypatch, _write_dist(tmp_path))
    port = _free_port()
    at_spawn: list = []
    seen: list = []

    def spawn(cmd, env, log_path):
        at_spawn.append(server_lock.read_lock(server_lock.lock_path(settings)))
        seen.append(cmd)
        return FakeChild()

    code = cli.main(
        ["--port", str(port)],
        spawn=spawn,
        open_browser=lambda url: None,
        probe=lambda port, timeout: {"instance": "child"},
    )

    assert code == 0
    assert at_spawn and at_spawn[0] is not None
    record = server_lock.read_lock(server_lock.lock_path(settings))
    assert record is not None and record.instance == at_spawn[0].instance
    assert record.port == port


def test_no_browser_still_waits_for_health(tmp_path, monkeypatch):
    _settings(monkeypatch, _write_dist(tmp_path))
    port = _free_port()
    probes: list = []
    opened: list = []
    code = cli.main(
        ["--port", str(port), "--no-browser"],
        spawn=_recording_spawn([]),
        open_browser=opened.append,
        probe=lambda target, timeout: probes.append(target) or {"instance": "child"},
    )

    assert code == 0
    assert probes == [port]
    assert opened == []


def test_default_port_comes_from_settings(tmp_path, monkeypatch):
    port = _free_port()
    settings = _settings(monkeypatch, _write_dist(tmp_path), port=port)
    seen: list = []
    code = cli.main(
        [],
        spawn=_recording_spawn(seen),
        open_browser=lambda url: None,
        probe=lambda target, timeout: {"instance": "child"},
    )

    assert code == 0
    assert settings.port == port
    assert seen[0]["cmd"][4] == str(port)


def test_invalid_ports_are_rejected_before_spawning(tmp_path, monkeypatch, capsys):
    _settings(monkeypatch, _write_dist(tmp_path))
    seen: list = []
    for port in ("0", "70000"):
        code = cli.main(["--port", port], spawn=_recording_spawn(seen))
        assert code == 1
        assert port in capsys.readouterr().out
    assert seen == []


def test_stale_build_warns_but_still_spawns(tmp_path, monkeypatch, capsys):
    dist = _write_dist(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.tsx").write_text("export {}", encoding="utf-8")
    old = time.time() - 100
    os.utime(dist / "index.html", (old, old))

    _settings(monkeypatch, dist)
    seen: list = []
    code = cli.main(
        ["--port", str(_free_port())],
        spawn=_recording_spawn(seen),
        open_browser=lambda url: None,
        probe=lambda port, timeout: {"instance": "child"},
    )

    assert code == 0
    assert "npm run build" in capsys.readouterr().out
    assert len(seen) == 1


def test_invalid_preset_is_rejected_before_spawning(tmp_path, monkeypatch, capsys):
    _settings(monkeypatch, _write_dist(tmp_path))
    seen: list = []
    code = cli.main(["--preset", "nope"], spawn=_recording_spawn(seen))

    assert code == 1
    assert "nope" in capsys.readouterr().out
    assert seen == []


def test_spawn_failure_releases_the_record_and_reports_the_log(tmp_path, monkeypatch, capsys):
    settings = _settings(monkeypatch, _write_dist(tmp_path))

    def boom(cmd, env, log_path):
        raise OSError("interpreter missing")

    code = cli.main(["--port", str(_free_port())], spawn=boom, open_browser=lambda url: None)

    assert code == 1
    assert server_lock.read_lock(server_lock.lock_path(settings)) is None
    out = capsys.readouterr().out
    assert "启动失败" in out and "server.log" in out


def test_child_exit_releases_the_record_and_reports_its_code(tmp_path, monkeypatch, capsys):
    settings = _settings(monkeypatch, _write_dist(tmp_path))

    def spawn(cmd, env, log_path):
        return FakeChild(exit_code=2)

    code = cli.main(
        ["--port", str(_free_port())],
        spawn=spawn,
        open_browser=lambda url: None,
        probe=lambda port, timeout: None,
    )

    assert code == 1
    assert server_lock.read_lock(server_lock.lock_path(settings)) is None
    out = capsys.readouterr().out
    assert "退出码 2" in out and "server.log" in out


def test_readiness_timeout_terminates_the_child_and_releases(tmp_path, monkeypatch, capsys):
    settings = _settings(monkeypatch, _write_dist(tmp_path))
    children: list = []

    def spawn(cmd, env, log_path):
        child = FakeChild()
        children.append(child)
        return child

    monkeypatch.setattr(cli, "READY_TIMEOUT_SEC", 0.05)
    code = cli.main(
        ["--port", str(_free_port())],
        spawn=spawn,
        open_browser=lambda url: None,
        probe=lambda port, timeout: None,
    )

    assert code == 1
    assert children[0].terminated is True
    assert server_lock.read_lock(server_lock.lock_path(settings)) is None
    assert "未就绪" in capsys.readouterr().out


def test_lost_claim_race_without_a_winner_reports_and_does_not_spawn(tmp_path, monkeypatch, capsys):
    _settings(monkeypatch, _write_dist(tmp_path))
    seen: list = []
    monkeypatch.setattr(server_lock, "claim_lock", lambda *args, **kwargs: False)

    code = cli.main(
        ["--port", str(_free_port())],
        spawn=_recording_spawn(seen),
        open_browser=lambda url: None,
        probe=lambda port, timeout: None,
    )

    assert code == 1
    assert seen == []
    assert "请稍后再试" in capsys.readouterr().out


def test_unwritable_log_directory_is_a_readable_startup_failure(tmp_path, monkeypatch, capsys):
    settings = _settings(monkeypatch, _write_dist(tmp_path))
    Path(settings.data_path).mkdir(parents=True, exist_ok=True)
    (Path(settings.data_path) / "logs").write_text("not a directory", encoding="utf-8")

    code = cli.main(["--port", str(_free_port())], open_browser=lambda url: None)

    assert code == 1
    assert server_lock.read_lock(server_lock.lock_path(settings)) is None
    out = capsys.readouterr().out
    assert "启动失败" in out and "server.log" in out


# ── serve (foreground entry) ────────────────────────────────────────────────
def test_serve_runs_foreground_and_releases_the_record(tmp_path, monkeypatch):
    settings = _settings(monkeypatch, _write_dist(tmp_path))
    port = _free_port()
    seen: list = []

    def fake_serve(host, target_port):
        record = server_lock.read_lock(server_lock.lock_path(settings))
        seen.append((host, target_port, record.instance if record else None))

    code = cli.main(["serve", "--port", str(port)], serve=fake_serve)

    assert code == 0
    assert seen and seen[0][0] == "127.0.0.1" and seen[0][1] == port
    assert seen[0][2] not in (None, "")
    assert server_lock.read_lock(server_lock.lock_path(settings)) is None


def test_serve_exports_port_before_the_server_imports_settings(tmp_path, monkeypatch):
    """``backend.main`` reads PORT at import time, so serve must set it first."""
    _settings(monkeypatch, _write_dist(tmp_path))
    seen: list = []

    def fake_serve(host, port):
        seen.append(get_settings().port)

    monkeypatch.setenv("PORT", "1")
    monkeypatch.setenv("QIANFAN_INSTANCE", "old")
    code = cli.main(["serve", "--port", "9234"], serve=fake_serve)

    assert code == 0
    assert seen == [9234]


def test_serve_releases_the_record_when_the_runner_raises(tmp_path, monkeypatch):
    settings = _settings(monkeypatch, _write_dist(tmp_path))

    def boom(host, port):
        raise RuntimeError("bind failed")

    with pytest.raises(RuntimeError):
        cli.main(["serve", "--port", str(_free_port())], serve=boom)

    assert server_lock.read_lock(server_lock.lock_path(settings)) is None


def test_serve_refuses_when_a_backend_is_registered(tmp_path, monkeypatch, capsys):
    settings = _settings(monkeypatch, _write_dist(tmp_path))
    lock_file = server_lock.lock_path(settings)
    assert server_lock.claim_lock(
        lock_file,
        server_lock.ServerLock(
            pid=os.getpid(),
            port=9123,
            started_at=server_lock.now_iso(),
            instance="tok",
            mode="serve",
        ),
    )
    called: list = []
    code = cli.main(
        ["serve", "--port", str(_free_port())],
        serve=lambda host, port: called.append((host, port)),
        probe=lambda port, timeout: {"instance": "tok"},
    )

    assert code == 2
    assert called == []
    assert "已有后台" in capsys.readouterr().out


def test_serve_missing_dist_and_invalid_port_do_not_claim(tmp_path, monkeypatch, capsys):
    settings = _settings(monkeypatch, tmp_path / "missing")
    called: list = []
    assert cli.main(["serve"], serve=lambda h, p: called.append((h, p))) == 1
    assert "npm run build" in capsys.readouterr().out
    assert cli.main(["serve", "--port", "0"], serve=lambda h, p: called.append((h, p))) == 1
    assert called == []
    assert server_lock.read_lock(server_lock.lock_path(settings)) is None


def test_serve_port_occupied_is_reported(tmp_path, monkeypatch, capsys):
    _settings(monkeypatch, _write_dist(tmp_path))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        probe.listen(1)
        port = probe.getsockname()[1]
        called: list = []
        code = cli.main(["serve", "--port", str(port)], serve=lambda h, p: called.append((h, p)))

    assert code == 1
    assert str(port) in capsys.readouterr().out
    assert called == []


# ── readiness helper ────────────────────────────────────────────────────────
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


def test_open_when_ready_stops_early_when_the_child_is_dead():
    ok = cli._open_when_ready(
        "http://127.0.0.1:9123/",
        9123,
        browser=None,
        probe=lambda port, timeout: None,
        alive=lambda: False,
        sleep=lambda seconds: None,
    )

    assert ok is False


def test_open_when_ready_does_not_report_a_browser_failure_as_a_startup_failure(capsys):
    def boom(url):
        raise RuntimeError("no browser")

    ok = cli._open_when_ready(
        "http://127.0.0.1:9123/",
        9123,
        browser=boom,
        probe=lambda port, timeout: {"instance": "child"},
    )

    assert ok is True
    assert "打开浏览器失败" in capsys.readouterr().out


# ── #332 S6: the shared ensure seam used by `qianfan repl` ────────────────
def test_ensure_backend_reuses_a_running_backend_without_spawning(tmp_path, monkeypatch, capsys):
    settings = _settings(monkeypatch, _write_dist(tmp_path))
    port = _free_port()
    server_lock.claim_lock(
        server_lock.lock_path(settings),
        server_lock.ServerLock(
            pid=os.getpid(),
            port=port,
            started_at=server_lock.now_iso(),
            instance="tok",
            mode="serve",
        ),
    )
    seen: list = []
    code, live_port = cli.ensure_backend(
        settings,
        port=_free_port(),
        probe=lambda port, timeout: {"instance": "tok"},
        spawn=_recording_spawn(seen),
        browser=None,
        require_dist=False,
        accept_dev=True,
    )

    assert (code, live_port) == (0, port)
    assert seen == []
    assert f"http://127.0.0.1:{port}/" in capsys.readouterr().out


def test_ensure_backend_for_a_terminal_needs_no_frontend_build(tmp_path, monkeypatch):
    settings = _settings(monkeypatch, tmp_path / "missing-dist")
    seen: list = []
    code, live_port = cli.ensure_backend(
        settings,
        port=_free_port(),
        probe=lambda port, timeout: {"instance": "child"},
        spawn=_recording_spawn(seen),
        browser=None,
        require_dist=False,
        accept_dev=True,
    )

    assert code == 0
    assert seen and live_port == int(seen[0]["env"]["PORT"])


def test_ensure_backend_terminal_accepts_a_dev_backend(tmp_path, monkeypatch):
    settings = _settings(monkeypatch, _write_dist(tmp_path))
    server_lock.claim_lock(
        server_lock.lock_path(settings),
        server_lock.ServerLock(
            pid=os.getpid(),
            port=9123,
            started_at=server_lock.now_iso(),
            instance="dev",
            mode="dev",
        ),
    )
    code, live_port = cli.ensure_backend(
        settings,
        port=_free_port(),
        probe=lambda port, timeout: {"instance": "dev"},
        spawn=_recording_spawn([]),
        browser=None,
        require_dist=False,
        accept_dev=True,
    )

    assert (code, live_port) == (0, 9123)


def test_help_mentions_every_subcommand(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])

    assert exc.value.code == 0
    out = capsys.readouterr().out
    for name in ("serve", "status", "stop", "repl"):
        assert name in out
