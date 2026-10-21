"""W2: one backend per machine (ADR 0010 decision 3).

The launcher seams (serve / browser / health probe) are substituted; the lock
file lives in a temporary data directory.
"""

from __future__ import annotations

import os
import socket
from urllib.parse import quote

from fastapi.testclient import TestClient

from backend import cli, server_lock
from backend.config import Settings
from backend.main import app
from backend.server_lock import ServerLock


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
