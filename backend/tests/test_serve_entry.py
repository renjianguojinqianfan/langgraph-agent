"""#332 S5: ``backend.serve`` — the programmatic uvicorn entry.

Shared by ``qianfan serve`` (foreground) and the detached child spawned by
``qianfan``. It owns the exit hook the stop endpoint calls and releases the
single-instance record when uvicorn returns.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

from backend import serve, server_lock
from backend.tests.conftest import make_settings


def _claimed(settings, instance="tok"):
    lock_file = server_lock.lock_path(settings)
    assert server_lock.claim_lock(
        lock_file,
        server_lock.ServerLock(
            pid=os.getpid(),
            port=9123,
            started_at=server_lock.now_iso(),
            instance=instance,
            mode="serve",
        ),
    )
    return lock_file


def test_build_server_wraps_the_given_app_and_address():
    marker_app = object()

    server = serve.build_server(marker_app, "127.0.0.1", 8123)

    assert server.config.app is marker_app
    assert server.config.host == "127.0.0.1"
    assert server.config.port == 8123
    assert server.config.timeout_graceful_shutdown == 5


def test_run_installs_the_exit_hook_and_releases_the_lock(tmp_path):
    settings = make_settings(tmp_path)
    lock_file = _claimed(settings)
    app = SimpleNamespace(state=SimpleNamespace())
    seen: dict = {}

    class FakeServer:
        should_exit = False

        def run(self) -> None:
            seen["exit_hook"] = app.state.request_server_exit
            app.state.request_server_exit()
            seen["should_exit"] = self.should_exit

    code = serve.run(
        "127.0.0.1",
        9123,
        "tok",
        app=app,
        settings=settings,
        server_factory=lambda target, host, port: FakeServer(),
    )

    assert code == 0
    assert callable(seen["exit_hook"])
    assert seen["should_exit"] is True
    assert server_lock.read_lock(lock_file) is None


def test_run_releases_the_lock_when_uvicorn_raises(tmp_path):
    settings = make_settings(tmp_path)
    lock_file = _claimed(settings)

    class BoomServer:
        should_exit = False

        def run(self) -> None:
            raise RuntimeError("bind failed")

    try:
        serve.run(
            "127.0.0.1",
            9123,
            "tok",
            app=SimpleNamespace(state=SimpleNamespace()),
            settings=settings,
            server_factory=lambda target, host, port: BoomServer(),
        )
    except RuntimeError:
        pass
    else:  # pragma: no cover - the failure must propagate to uvicorn's caller
        raise AssertionError("run() must not swallow uvicorn failures")

    assert server_lock.read_lock(lock_file) is None


def test_module_cli_refuses_without_an_instance(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("QIANFAN_INSTANCE", raising=False)
    ran: list = []
    monkeypatch.setattr(serve, "run", lambda *args, **kwargs: ran.append(args))

    code = serve.main(["--port", "9123"], settings=make_settings(tmp_path))

    assert code == 2
    assert ran == []
    assert "手动" in capsys.readouterr().out


def test_module_cli_exports_the_port_and_instance_before_running(tmp_path, monkeypatch):
    seen: dict = {}

    def fake_run(host, port, instance, **kwargs):
        seen.update(host=host, port=port, instance=instance)
        seen["env_port"] = os.environ.get("PORT")
        seen["env_instance"] = os.environ.get("QIANFAN_INSTANCE")
        return 0

    monkeypatch.setattr(serve, "run", fake_run)
    monkeypatch.setenv("PORT", "1")
    monkeypatch.setenv("QIANFAN_INSTANCE", "old")
    settings = make_settings(tmp_path)

    code = serve.main(
        ["--port", "9234", "--instance", "tok-cli"], settings=settings
    )

    assert code == 0
    assert seen["port"] == 9234
    assert seen["instance"] == "tok-cli"
    assert seen["env_port"] == "9234"
    assert seen["env_instance"] == "tok-cli"
