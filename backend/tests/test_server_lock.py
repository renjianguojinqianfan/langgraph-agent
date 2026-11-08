"""W2: one backend per machine (ADR 0010 decision 3).

The launcher seams (serve / browser / health probe) are substituted; the lock
file lives in a temporary data directory.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from backend import cli, server_lock
from backend.config import Settings
from backend.main import app, create_app, warn_if_no_usable_model_key
from backend.server_lock import ServerLock
from backend.services.model_store import ModelStore
from backend.tests.conftest import make_settings


def _settings(monkeypatch, tmp_path, port=8000):
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        frontend_dist=str(dist),
        data_dir=str(tmp_path / "data"),
        artifacts_dir=str(tmp_path / "artifacts"),
        port=port,
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return settings


def _lock(port=9123, pid=None, instance="tok", mode="serve"):
    return ServerLock(
        pid=os.getpid() if pid is None else pid,
        port=port,
        started_at=server_lock.now_iso(),
        instance=instance,
        mode=mode,
    )


def test_second_launch_hands_off_and_opens_the_current_directory(tmp_path, monkeypatch):
    settings = _settings(monkeypatch, tmp_path)
    lock_file = server_lock.lock_path(settings)
    assert server_lock.claim_lock(lock_file, _lock())

    served: list = []
    opened: list[str] = []
    code = cli.main(
        [],
        serve=lambda host, port: served.append((host, port)),
        open_browser=opened.append,
        probe=lambda port, timeout: {"instance": "tok"},
    )

    assert code == 0
    assert served == []
    assert opened == [f"http://127.0.0.1:9123/?dir={quote(os.getcwd())}"]
    assert server_lock.read_lock(lock_file) is not None  # the running one keeps it


def test_handoff_with_no_browser_does_not_open(tmp_path, monkeypatch):
    settings = _settings(monkeypatch, tmp_path)
    server_lock.claim_lock(server_lock.lock_path(settings), _lock())
    opened: list[str] = []

    code = cli.main(
        ["--no-browser"],
        serve=lambda host, port: None,
        open_browser=opened.append,
        probe=lambda port, timeout: {"instance": "tok"},
    )

    assert code == 0
    assert opened == []


def test_stale_pid_is_taken_over(tmp_path, monkeypatch):
    settings = _settings(monkeypatch, tmp_path)
    lock_file = server_lock.lock_path(settings)
    server_lock.claim_lock(lock_file, _lock(pid=999_999, instance="old"))

    served: list = []

    def fake_serve(host, port):
        seen = server_lock.read_lock(lock_file)
        served.append((host, port, seen.instance if seen else None))

    code = cli.main(
        ["--port", "9130"],
        serve=fake_serve,
        open_browser=lambda url: None,
        probe=lambda port, timeout: None,
    )

    assert code == 0
    assert served and served[0][0] == "127.0.0.1" and served[0][1] == 9130
    assert served[0][2] not in (None, "old")  # a fresh instance id
    assert server_lock.read_lock(lock_file) is None  # released after serve returns


def test_pid_reuse_with_a_mismatched_identity_is_taken_over(tmp_path, monkeypatch):
    settings = _settings(monkeypatch, tmp_path)
    lock_file = server_lock.lock_path(settings)
    server_lock.claim_lock(lock_file, _lock(pid=os.getpid(), instance="old"))

    served: list = []
    code = cli.main(
        [],
        serve=lambda host, port: served.append(port),
        open_browser=lambda url: None,
        probe=lambda port, timeout: {"instance": "someone-else"},
    )

    assert code == 0
    assert served == [8000]


def test_port_occupied_by_a_non_qianfan_program_is_an_error(tmp_path, monkeypatch):
    _settings(monkeypatch, tmp_path)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        probe.listen(1)
        port = probe.getsockname()[1]
        served: list = []
        code = cli.main(
            ["--port", str(port)],
            serve=lambda host, port: served.append(port),
            open_browser=lambda url: None,
            probe=lambda port, timeout: None,
        )

    assert code == 1
    assert served == []


def test_claim_lock_is_exclusive(tmp_path):
    path = tmp_path / "data" / "server.json"
    assert server_lock.claim_lock(path, _lock()) is True
    assert server_lock.claim_lock(path, _lock(instance="other")) is False


def test_concurrent_claims_only_one_wins(tmp_path):
    import threading

    path = tmp_path / "data" / "server.json"
    barrier = threading.Barrier(8)
    results: list[bool] = []
    results_lock = threading.Lock()

    def _claim(n: int) -> None:
        barrier.wait()
        won = server_lock.claim_lock(path, _lock(instance=f"tok-{n}"))
        with results_lock:
            results.append(won)

    threads = [threading.Thread(target=_claim, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert results.count(True) == 1
    assert server_lock.read_lock(path) is not None


def test_a_second_launcher_cannot_drop_a_half_written_record(tmp_path, monkeypatch):
    """The record appears only after it is complete; a scan in between is harmless."""
    path = tmp_path / "data" / "server.json"
    real_dump = server_lock.json.dump
    scans: list = []

    def racing_dump(value, handle, **kwargs):
        scans.append(server_lock.find_running(path, probe=lambda port, timeout: None, wait=0))
        return real_dump(value, handle, **kwargs)

    monkeypatch.setattr(server_lock.json, "dump", racing_dump)
    assert server_lock.claim_lock(path, _lock(instance="first")) is True
    assert scans == [None]
    record = server_lock.read_lock(path)
    assert record is not None and record.instance == "first"


def test_a_losing_claim_leaves_no_temp_files(tmp_path):
    path = tmp_path / "data" / "server.json"
    assert server_lock.claim_lock(path, _lock()) is True
    assert server_lock.claim_lock(path, _lock(instance="other")) is False
    assert sorted(p.name for p in path.parent.iterdir()) == ["server.json"]


def test_pid_alive_self_and_a_missing_pid():
    assert server_lock.pid_alive(os.getpid()) is True
    assert server_lock.pid_alive(999_999) is False


def test_health_reports_the_instance_token(monkeypatch):
    monkeypatch.setenv("QIANFAN_INSTANCE", "instance-123")
    with TestClient(app, base_url="http://localhost") as client:
        body = client.get("/health").json()
    assert body["instance"] == "instance-123"


def test_start_refuses_when_a_backend_is_registered(monkeypatch):
    import start

    running = _lock(port=9000)
    monkeypatch.setattr(start, "_running_server", lambda settings: running)
    assert start._refuse_if_running(Settings(_env_file=None)) is True

    monkeypatch.setattr(start, "_running_server", lambda settings: None)
    assert start._refuse_if_running(Settings(_env_file=None)) is False


def test_start_refuses_a_running_repl_without_probing(tmp_path, monkeypatch, capsys):
    """D-48 (#20): a live mode=repl record refuses start.py before find_running.

    The REPL serves no /health, so the 15s boot wait would otherwise block
    start.py; the record check must answer immediately and never call probe.
    """
    import start

    settings = make_settings(tmp_path, single_backend_guard_enabled=True)
    lock_file = server_lock.lock_path(settings)
    assert server_lock.claim_lock(
        lock_file,
        _lock(port=settings.port, instance="repl-live", mode="repl"),
    )
    probed: list = []

    def fake_running_server(_settings):
        probed.append(True)
        return None

    monkeypatch.setattr(start, "_running_server", fake_running_server)

    assert start._refuse_if_running(settings) is True
    assert probed == []
    assert "关终端会停任务" in capsys.readouterr().out


def test_find_running_clears_a_dead_record(tmp_path):
    path = tmp_path / "data" / "server.json"
    server_lock.claim_lock(path, _lock(pid=999_999))
    assert server_lock.find_running(path, probe=lambda port, timeout: None, wait=0) is None
    assert server_lock.read_lock(path) is None


def test_find_running_drops_a_corrupt_record(tmp_path):
    path = tmp_path / "data" / "server.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    assert server_lock.find_running(path, probe=lambda port, timeout: None, wait=0) is None
    assert not path.exists()


def test_find_running_keeps_a_booting_backend_record(tmp_path):
    """A live pid whose health is not up yet must not lose its registration."""
    path = tmp_path / "data" / "server.json"
    server_lock.claim_lock(path, _lock(pid=os.getpid()))
    assert (
        server_lock.find_running(
            path, probe=lambda port, timeout: None, wait=0, interval=0
        )
        is None
    )
    assert server_lock.read_lock(path) is not None


def test_release_lock_honours_the_instance_guard(tmp_path):
    path = tmp_path / "data" / "server.json"
    server_lock.claim_lock(path, _lock(instance="mine"))
    server_lock.release_lock(path, "someone-else")
    assert server_lock.read_lock(path) is not None
    server_lock.release_lock(path, "mine")
    assert server_lock.read_lock(path) is None


def test_qianfan_refuses_to_hand_off_to_the_dev_backend(tmp_path, monkeypatch):
    settings = _settings(monkeypatch, tmp_path)
    server_lock.claim_lock(server_lock.lock_path(settings), _lock(instance="dev", mode="dev"))

    served: list = []
    opened: list = []
    code = cli.main(
        [],
        serve=lambda host, port: served.append(port),
        open_browser=opened.append,
        probe=lambda port, timeout: {"instance": "dev"},
    )

    assert code == 2
    assert served == []
    assert opened == []


def test_handoff_opens_the_browser_before_the_process_exits(tmp_path):
    """The default browser seam must fire inside the handoff process.

    A daemon ``threading.Timer`` is killed the moment ``main()`` returns, so a
    child process is the only honest witness: it runs the real default seam and
    leaves a marker before exiting.
    """
    marker = tmp_path / "opened.txt"
    root = Path(__file__).resolve().parents[2]
    child = """
import sys
from pathlib import Path

import backend.cli as cli
from backend.server_lock import ServerLock

marker = Path(sys.argv[1])


def fake_open(url):
    marker.write_text(url, encoding="utf-8")
    return True


cli.webbrowser.open = fake_open
cli.server_lock.find_running = lambda lock_file, probe=None: ServerLock(
    pid=1,
    port=9123,
    started_at="2026-01-01T00:00:00+00:00",
    instance="tok",
    mode="serve",
)
raise SystemExit(cli.main([]))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    # CI 的 Windows runner 是英文 locale（cp1252）：子进程 stdout 是管道时，
    # cli.main([]) 的中文交接提示（cli.py:117）会 UnicodeEncodeError。这里只钉
    # 真子进程的 stdio 编码；产品级控制台编码风险未归属：已记录 #259 评论，
    # 留待 P3-1/P3-3 评审裁定（spec D-22/D-51 均不覆盖 cli.py:117）。
    env["PYTHONIOENCODING"] = "utf-8"
    subprocess.run(
        [sys.executable, "-c", child, str(marker)],
        cwd=root,
        env=env,
        check=True,
        timeout=60,
    )

    assert marker.read_text(encoding="utf-8").startswith("http://127.0.0.1:9123/?dir=")


def _no_spawn(*args, **kwargs):
    raise AssertionError("start must not spawn a backend")


def test_start_main_refuses_when_a_backend_is_registered(monkeypatch):
    import start

    monkeypatch.setattr(start, "_running_server", lambda settings: _lock(port=9000))
    monkeypatch.setattr(sys, "argv", ["start.py", "--backend-only"])
    monkeypatch.setattr(start, "_run", _no_spawn)

    assert start.main() == 2


def test_start_registers_and_releases_the_dev_backend(tmp_path, monkeypatch):
    import start

    lock_file = tmp_path / "server.json"
    monkeypatch.setattr(server_lock, "lock_path", lambda settings: lock_file)
    monkeypatch.setattr(start, "_running_server", lambda settings: None)
    monkeypatch.setattr(sys, "argv", ["start.py", "--backend-only"])

    seen: list = []

    class FakeProc:
        pid = os.getpid()

        def wait(self, timeout=None):
            if timeout is None:
                seen.append(server_lock.read_lock(lock_file))
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

    proc = FakeProc()
    monkeypatch.setattr(start, "_run", lambda *args, **kwargs: proc)

    assert start.main() == 0
    assert seen and seen[0] is not None
    assert seen[0].mode == "dev"
    assert seen[0].pid == proc.pid
    assert server_lock.read_lock(lock_file) is None


def test_start_never_spawns_when_it_loses_the_lock_race(tmp_path, monkeypatch):
    """#259 D-42 changed the order: the lock is claimed BEFORE the spawn.

    Contract change (old -> new -> why): losing the race used to start uvicorn
    and terminate it afterwards; now the launcher backs out before spawning, so
    two writers can never overlap even for the spawn window.
    """
    import start

    lock_file = tmp_path / "server.json"
    monkeypatch.setattr(server_lock, "lock_path", lambda settings: lock_file)
    monkeypatch.setattr(start, "_running_server", lambda settings: None)
    monkeypatch.setattr(server_lock, "claim_lock", lambda *args, **kwargs: False)
    monkeypatch.setattr(sys, "argv", ["start.py", "--backend-only"])

    spawned: list = []
    monkeypatch.setattr(start, "_run", lambda *args, **kwargs: spawned.append(args))

    assert start.main() == 2
    assert spawned == []
    assert server_lock.read_lock(lock_file) is None


# ── #259 D-42: startup single-backend guard ─────────────────────────────────
def _guard_settings(tmp_path, **overrides):
    # Unannotated on purpose: pydantic-settings' `_env_file` is runtime-only
    # (D-18 factory seam), same rule as conftest.make_isolated_settings.
    kwargs: dict = dict(
        data_dir=str(tmp_path / "data"),
        artifacts_dir=str(tmp_path / "artifacts"),
        frontend_dist=str(tmp_path / "dist"),
        checkpoint_enabled=False,
        # The suite's env turns the production guard off (conftest); these
        # tests exercise the guard, so turn it back on explicitly.
        single_backend_guard_enabled=True,
    )
    kwargs.update(overrides)
    return Settings(_env_file=None, **kwargs)


def test_ensure_single_backend_recognises_our_own_instance(tmp_path, monkeypatch):
    settings = _guard_settings(tmp_path)
    path = server_lock.lock_path(settings)
    assert server_lock.claim_lock(path, _lock(port=9123, instance="mine"))
    monkeypatch.setenv("QIANFAN_INSTANCE", "mine")

    found = server_lock.ensure_single_backend(
        settings, probe=lambda port, timeout: None, wait=0
    )

    assert found is not None
    assert found.instance == "mine"


def test_ensure_single_backend_refuses_a_foreign_backend(tmp_path, monkeypatch):
    settings = _guard_settings(tmp_path)
    monkeypatch.setenv("QIANFAN_INSTANCE", "mine")
    path = server_lock.lock_path(settings)
    assert server_lock.claim_lock(path, _lock(port=9123, instance="other"))

    with pytest.raises(RuntimeError, match="已有后台"):
        server_lock.ensure_single_backend(
            settings, probe=lambda port, timeout: {"instance": "other"}, wait=0
        )


def test_ensure_single_backend_refuses_a_manual_start(tmp_path, monkeypatch):
    """H2: no QIANFAN_INSTANCE -> refuse outright, launchers only."""
    settings = _guard_settings(tmp_path)
    monkeypatch.delenv("QIANFAN_INSTANCE", raising=False)

    with pytest.raises(RuntimeError, match="手动 uvicorn"):
        server_lock.ensure_single_backend(
            settings, probe=lambda port, timeout: None, wait=0
        )


def test_ensure_single_backend_refuses_a_backend_that_is_still_booting(
    tmp_path, monkeypatch
):
    """H1: a live pid whose /health is not up yet is occupied, not absent."""
    settings = _guard_settings(tmp_path)
    monkeypatch.setenv("QIANFAN_INSTANCE", "mine")
    assert server_lock.claim_lock(
        server_lock.lock_path(settings), _lock(port=9124, instance="other")
    )

    with pytest.raises(RuntimeError, match="启动中"):
        server_lock.ensure_single_backend(
            settings, probe=lambda port, timeout: None, wait=0
        )


def test_ensure_single_backend_allows_a_launcher_with_no_record(tmp_path, monkeypatch):
    settings = _guard_settings(tmp_path)
    monkeypatch.setenv("QIANFAN_INSTANCE", "mine")

    assert (
        server_lock.ensure_single_backend(
            settings, probe=lambda port, timeout: None, wait=0
        )
        is None
    )


def test_ensure_single_backend_probes_the_configured_host(tmp_path, monkeypatch):
    """M2: the readiness probe must follow settings.host (::1 included)."""
    settings = _guard_settings(tmp_path, host="::1")
    monkeypatch.setenv("QIANFAN_INSTANCE", "mine")
    captured: dict = {}

    def fake_find_running(path, **kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(server_lock, "find_running", fake_find_running)
    assert server_lock.ensure_single_backend(settings, wait=0) is None
    assert captured["host"] == "::1"


def test_lifespan_refuses_when_another_backend_is_running(tmp_path, monkeypatch):
    settings = _guard_settings(tmp_path)
    monkeypatch.setenv("QIANFAN_INSTANCE", "mine")
    monkeypatch.setattr(
        server_lock,
        "find_running",
        lambda path, **kwargs: _lock(port=9911, instance="other"),
    )
    guard_app = create_app(settings)
    with pytest.raises(RuntimeError, match="已有后台"):
        with TestClient(guard_app):
            pass


def test_lifespan_refuses_a_manual_uvicorn(tmp_path, monkeypatch):
    """H2: a hand-started app has no instance id and must not serve."""
    settings = _guard_settings(tmp_path)
    monkeypatch.delenv("QIANFAN_INSTANCE", raising=False)
    guard_app = create_app(settings)
    with pytest.raises(RuntimeError, match="qianfan"):
        with TestClient(guard_app):
            pass


def test_lifespan_accepts_its_own_registered_instance(tmp_path, monkeypatch):
    settings = _guard_settings(tmp_path)
    server_lock.claim_lock(
        server_lock.lock_path(settings), _lock(port=9912, instance="mine")
    )
    monkeypatch.setenv("QIANFAN_INSTANCE", "mine")
    guard_app = create_app(settings)
    with TestClient(guard_app, base_url="http://localhost") as client:
        assert client.get("/health").json()["instance"] == "mine"


def test_lifespan_registers_the_worker_pid_without_reload(tmp_path, monkeypatch):
    settings = _guard_settings(tmp_path)
    lock_file = server_lock.lock_path(settings)
    server_lock.claim_lock(lock_file, _lock(port=9913, instance="mine"))
    monkeypatch.setenv("QIANFAN_INSTANCE", "mine")
    monkeypatch.delenv("QIANFAN_LOCK_KEEP_PID", raising=False)

    guard_app = create_app(settings)
    with TestClient(guard_app, base_url="http://localhost"):
        record = server_lock.read_lock(lock_file)
        assert record is not None and record.pid == os.getpid()


def test_lifespan_keeps_the_supervisor_pid_under_reload(tmp_path, monkeypatch):
    """M1: --reload workers must not overwrite the supervisor's lock pid."""
    settings = _guard_settings(tmp_path)
    lock_file = server_lock.lock_path(settings)
    server_lock.claim_lock(lock_file, _lock(port=9914, pid=424242, instance="mine"))
    monkeypatch.setenv("QIANFAN_INSTANCE", "mine")
    monkeypatch.setenv("QIANFAN_LOCK_KEEP_PID", "1")

    guard_app = create_app(settings)
    with TestClient(guard_app, base_url="http://localhost"):
        record = server_lock.read_lock(lock_file)
        assert record is not None and record.pid == 424242


def test_update_lock_pid_swaps_the_launcher_for_the_backend(tmp_path):
    path = tmp_path / "data" / "server.json"
    assert server_lock.claim_lock(path, _lock(port=9123, instance="mine"))

    assert server_lock.update_lock_pid(path, "mine", 424242) is True
    updated = server_lock.read_lock(path)
    assert updated is not None and updated.pid == 424242
    assert updated.instance == "mine" and updated.mode == "serve"

    assert server_lock.update_lock_pid(path, "someone-else", 1) is False
    remaining = server_lock.read_lock(path)
    assert remaining is not None and remaining.pid == 424242


def test_start_claims_the_lock_before_spawning_the_backend(tmp_path, monkeypatch):
    import start

    lock_file = tmp_path / "server.json"
    monkeypatch.setattr(server_lock, "lock_path", lambda settings: lock_file)
    monkeypatch.setattr(start, "_running_server", lambda settings: None)
    monkeypatch.setattr(sys, "argv", ["start.py", "--backend-only"])
    real_update = server_lock.update_lock_pid
    transferred: list = []

    def spy_update(path, instance, pid):
        transferred.append((instance, pid))
        return real_update(path, instance, pid)

    monkeypatch.setattr(server_lock, "update_lock_pid", spy_update)
    at_spawn: list = []

    class FakeProc:
        pid = 424242

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

    def fake_run(*args, **kwargs):
        at_spawn.append(server_lock.read_lock(lock_file))
        return FakeProc()

    monkeypatch.setattr(start, "_run", fake_run)

    assert start.main() == 0
    assert at_spawn and at_spawn[0] is not None
    assert at_spawn[0].mode == "dev"
    # The launcher claimed the record before the child existed ...
    assert at_spawn[0].pid == os.getpid()
    # ... and the pid was swapped for the real backend right after the spawn.
    assert transferred and transferred[0][1] == FakeProc.pid
    assert server_lock.read_lock(lock_file) is None


def test_launcher_env_pins_the_port_for_both_children(monkeypatch):
    import start

    monkeypatch.setenv("KEEP_ME", "yes")
    monkeypatch.delenv("USE_MOCK_LLM", raising=False)
    env = start._launcher_env(9123)
    assert env["PORT"] == "9123"
    assert env["KEEP_ME"] == "yes"
    assert "USE_MOCK_LLM" not in env
    assert start._launcher_env(9000, mock=True)["USE_MOCK_LLM"] == "true"

    base = {"PORT": "1", "KEEP": "x"}
    overridden = start._launcher_env(8000, base=base)
    assert overridden["PORT"] == "8000" and overridden["KEEP"] == "x"
    assert base == {"PORT": "1", "KEEP": "x"}  # pure: the base dict is copied


def test_start_passes_the_port_to_the_vite_child(monkeypatch):
    import start

    monkeypatch.setattr(
        sys, "argv", ["start.py", "--frontend-only", "--port", "9123"]
    )
    calls: list = []

    class FakeProc:
        pid = 424242

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

    def fake_run(cmd, cwd=None, env=None):
        calls.append((cmd, env))
        return FakeProc()

    monkeypatch.setattr(start, "_run", fake_run)

    assert start.main() == 0
    assert calls and calls[0][0][:2] == ["npm", "run"]
    assert calls[0][1] is not None and calls[0][1]["PORT"] == "9123"


# ── #259 D-43: loopback host semantics ─────────────────────────────────────
@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "LOCALHOST"])
def test_create_app_accepts_loopback_hosts(tmp_path, monkeypatch, host):
    monkeypatch.delenv("QIANFAN_INSTANCE", raising=False)
    settings = _guard_settings(
        tmp_path, host=host, single_backend_guard_enabled=False
    )
    assert create_app(settings).title == "LangGraph Autonomous Task Agent"


def test_create_app_refuses_a_non_loopback_host(tmp_path):
    settings = _guard_settings(tmp_path, host="0.0.0.0")
    with pytest.raises(RuntimeError, match="本机访问"):
        create_app(settings)


def test_health_accepts_ipv6_loopback_host(tmp_path, monkeypatch):
    """M2: TrustedHost must match the bracketed IPv6 Host header."""
    monkeypatch.delenv("QIANFAN_INSTANCE", raising=False)
    settings = _guard_settings(
        tmp_path, host="::1", single_backend_guard_enabled=False
    )
    ipv6_app = create_app(settings)
    with TestClient(ipv6_app, base_url="http://[::1]") as client:
        assert client.get("/health").status_code == 200


def test_health_url_brackets_ipv6_hosts():
    assert server_lock.health_url("::1", 8000) == "http://[::1]:8000/health"
    assert (
        server_lock.health_url("127.0.0.1", 8000)
        == "http://127.0.0.1:8000/health"
    )


# ── #259 D-44: key posture warning ─────────────────────────────────────────
def test_startup_warns_when_no_model_can_serve_a_call(tmp_path, caplog):
    settings = make_settings(tmp_path, use_mock_llm=False)
    store = ModelStore(settings, home_dir=tmp_path / "home")
    with caplog.at_level(logging.WARNING, logger="agent.main"):
        warned = warn_if_no_usable_model_key(settings, store)
    assert warned is True
    message = "".join(record.getMessage() for record in caplog.records)
    assert "设置页" in message and "环境变量" in message
    assert ".env" not in message


def test_startup_does_not_warn_with_a_pasted_key(tmp_path, caplog):
    settings = make_settings(tmp_path, use_mock_llm=False)
    store = ModelStore(settings, home_dir=tmp_path / "home")
    store.set_provider_key("default", "sk-pasted-test-key")
    with caplog.at_level(logging.WARNING, logger="agent.main"):
        warned = warn_if_no_usable_model_key(settings, store)
    assert warned is False
    assert "没有可用的模型 Key" not in caplog.text


def test_startup_does_not_warn_when_another_model_has_a_key(tmp_path, caplog):
    settings = make_settings(tmp_path, use_mock_llm=False)
    store = ModelStore(settings, home_dir=tmp_path / "home")
    store.add_provider(id="alt", name="Alt", base_url="https://alt.test/v1")
    store.add_model("alt", model="alt-model")
    store.set_provider_key("alt", "sk-alt-test-key")
    with caplog.at_level(logging.WARNING, logger="agent.main"):
        warned = warn_if_no_usable_model_key(settings, store)
    assert warned is False


def test_mock_mode_never_warns_about_keys(tmp_path, caplog):
    settings = make_settings(tmp_path, use_mock_llm=True)
    store = ModelStore(settings, home_dir=tmp_path / "home")
    with caplog.at_level(logging.WARNING, logger="agent.main"):
        assert warn_if_no_usable_model_key(settings, store) is False


class _KeySnapshotStore:
    """Minimal store double: the startup guard only needs list_public()."""

    def __init__(self, snapshot: dict) -> None:
        self._snapshot = snapshot

    def list_public(self) -> dict:
        return self._snapshot


def test_key_warning_skips_when_default_is_gone_but_another_entry_has_a_key(
    tmp_path, caplog
):
    """L1: the old default-None shortcut warned despite a usable key."""
    settings = make_settings(tmp_path, use_mock_llm=False)
    store = _KeySnapshotStore(
        {
            "default": "gone",
            "models": [{"id": "alt", "key_set": True, "key_source": "pasted"}],
        }
    )
    with caplog.at_level(logging.WARNING, logger="agent.main"):
        warned = warn_if_no_usable_model_key(settings, store)
    assert warned is False
    assert "没有可用的模型 Key" not in caplog.text
