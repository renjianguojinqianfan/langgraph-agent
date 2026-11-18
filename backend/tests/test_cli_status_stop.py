"""#332 S5: ``qianfan status`` and ``qianfan stop`` (CLI clients).

Both read ``data/server.json``; ``stop`` talks to the registered port only and
echoes the recorded instance id. A mismatch is a refusal, never a pid kill.
"""

from __future__ import annotations

import os

from backend import cli, server_lock
from backend.config import Settings


def _settings(monkeypatch, tmp_path, *, auth=False):
    settings = Settings(
        _env_file=None,
        frontend_dist=str(tmp_path / "dist"),
        data_dir=str(tmp_path / "data"),
        port=8000,
        auth_enabled=auth,
        auth_token="s3cret" if auth else "changeme",
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return settings


def _claim(settings, *, pid=None, port=9123, instance="tok", mode="serve"):
    lock_file = server_lock.lock_path(settings)
    assert server_lock.claim_lock(
        lock_file,
        server_lock.ServerLock(
            pid=os.getpid() if pid is None else pid,
            port=port,
            started_at=server_lock.now_iso(),
            instance=instance,
            mode=mode,
        ),
    )
    return lock_file


# ── status ──────────────────────────────────────────────────────────────────
def test_status_without_a_service_is_non_zero_and_does_not_probe(tmp_path, monkeypatch, capsys):
    _settings(monkeypatch, tmp_path)
    probed: list = []

    code = cli.main(["status"], probe=lambda port, timeout: probed.append(port) or None)

    assert code == 1
    assert "未运行" in capsys.readouterr().out
    assert probed == []


def test_status_treats_a_dead_pid_as_not_running(tmp_path, monkeypatch, capsys):
    settings = _settings(monkeypatch, tmp_path)
    _claim(settings, pid=999_999)

    code = cli.main(["status"], probe=lambda port, timeout: {"instance": "tok"})

    assert code == 1
    assert "未运行" in capsys.readouterr().out


def test_status_prints_the_registered_fields_and_health(tmp_path, monkeypatch, capsys):
    settings = _settings(monkeypatch, tmp_path)
    _claim(settings, port=9234, instance="inst-a")

    code = cli.main(
        ["status"], probe=lambda port, timeout: {"instance": "inst-a"}
    )

    assert code == 0
    out = capsys.readouterr().out
    assert f"pid={os.getpid()}" in out
    assert "port=9234" in out
    assert "mode=serve" in out
    assert "instance=inst-a" in out
    assert "healthy=true" in out


def test_status_reports_an_unready_backend_as_unhealthy(tmp_path, monkeypatch, capsys):
    settings = _settings(monkeypatch, tmp_path)
    _claim(settings, instance="inst-a")

    assert cli.main(["status"], probe=lambda port, timeout: None) == 1
    assert "healthy=false" in capsys.readouterr().out


def test_status_reports_an_instance_mismatch_as_unhealthy(tmp_path, monkeypatch, capsys):
    settings = _settings(monkeypatch, tmp_path)
    _claim(settings, instance="inst-a")

    assert cli.main(["status"], probe=lambda port, timeout: {"instance": "other"}) == 1
    assert "healthy=false" in capsys.readouterr().out


def _alive(monkeypatch, *answers):
    remaining = iter(answers)
    monkeypatch.setattr(server_lock, "pid_alive", lambda pid: next(remaining))


# ── stop ────────────────────────────────────────────────────────────────────
def test_stop_without_a_service_is_non_zero_and_makes_no_request(tmp_path, monkeypatch, capsys):
    _settings(monkeypatch, tmp_path)
    calls: list = []

    code = cli.main(["stop"], http=lambda *args, **kwargs: calls.append(args) or (200, {}))

    assert code == 1
    assert "未运行" in capsys.readouterr().out
    assert calls == []


def test_stop_sends_the_recorded_instance_and_waits_for_the_process(tmp_path, monkeypatch, capsys):
    settings = _settings(monkeypatch, tmp_path)
    _claim(settings, pid=424242, port=9234, instance="inst-a")
    requests: list = []

    def fake_http(method, url, **kwargs):
        requests.append((method, url, kwargs))
        return 200, {"code": 0, "data": {"ok": True, "shutdown_complete": True}}

    alive_calls = iter([True, False])
    monkeypatch.setattr(server_lock, "pid_alive", lambda pid: next(alive_calls))
    code = cli.main(
        ["stop"],
        http=fake_http,
        probe=lambda port, timeout: None,
    )

    assert code == 0
    assert requests
    method, url, kwargs = requests[0]
    assert method == "POST"
    assert url == "http://127.0.0.1:9234/api/server/stop"
    assert kwargs["payload"] == {"instance": "inst-a"}
    assert kwargs["bearer"] is None
    assert "已停止" in capsys.readouterr().out


def test_stop_refuses_a_mismatched_instance_and_does_not_wait(tmp_path, monkeypatch, capsys):
    settings = _settings(monkeypatch, tmp_path)
    _claim(settings, pid=424242, instance="inst-a")
    _alive(monkeypatch, True)

    code = cli.main(
        ["stop"],
        http=lambda *args, **kwargs: (409, {"detail": "instance 不匹配"}),
        probe=lambda port, timeout: None,
    )

    assert code == 1
    out = capsys.readouterr().out
    assert "拒绝停止" in out and "instance" in out


def test_stop_reports_an_incomplete_shutdown_as_failure(tmp_path, monkeypatch, capsys):
    settings = _settings(monkeypatch, tmp_path)
    _claim(settings, pid=424242, instance="inst-a")
    _alive(monkeypatch, True)

    code = cli.main(
        ["stop"],
        http=lambda *args, **kwargs: (
            504,
            {
                "code": 1,
                "message": "后台停止未完成",
                "data": {
                    "ok": False,
                    "stopped_tasks": ["t1"],
                    "workers_joined": False,
                    "shutdown_complete": False,
                    "lock_released": False,
                },
            },
        ),
        probe=lambda port, timeout: None,
    )

    assert code == 1
    out = capsys.readouterr().out
    assert "未完成" in out
    assert "stopped_tasks=['t1']" in out
    assert "shutdown_complete=False" in out


def test_stop_does_not_claim_success_when_the_process_outlives_the_wait(tmp_path, monkeypatch, capsys):
    settings = _settings(monkeypatch, tmp_path)
    _claim(settings, pid=424242, instance="inst-a")
    _alive(monkeypatch, True)
    monkeypatch.setattr(cli, "STOP_TIMEOUT_SEC", 0.0)

    code = cli.main(
        ["stop"],
        http=lambda *args, **kwargs: (200, {"code": 0, "data": {"ok": True}}),
        probe=lambda port, timeout: {"instance": "inst-a"},
    )

    assert code == 1
    assert "仍未退出" in capsys.readouterr().out


def test_stop_logs_in_when_auth_is_enabled_without_printing_the_token(tmp_path, monkeypatch, capsys):
    settings = _settings(monkeypatch, tmp_path, auth=True)
    _claim(settings, pid=424242, instance="inst-a")
    seen: list = []

    def fake_http(method, url, **kwargs):
        seen.append((method, url, kwargs))
        if url.endswith("/api/auth/token"):
            return 200, {"code": 0, "data": {"token": "bearer-token-value"}}
        return 200, {"code": 0, "data": {"ok": True}}

    alive_calls = iter([True, False])
    monkeypatch.setattr(server_lock, "pid_alive", lambda pid: next(alive_calls))
    code = cli.main(
        ["stop"],
        http=fake_http,
        probe=lambda port, timeout: None,
    )

    assert code == 0
    assert [entry[1] for entry in seen] == [
        "http://127.0.0.1:9123/api/auth/token",
        "http://127.0.0.1:9123/api/server/stop",
    ]
    assert seen[0][2]["payload"] == {"token": "s3cret"}
    assert seen[1][2]["bearer"] == "bearer-token-value"
    out = capsys.readouterr().out
    assert "bearer-token-value" not in out
    assert "s3cret" not in out


def test_stop_reports_a_login_failure_without_printing_the_secret(tmp_path, monkeypatch, capsys):
    settings = _settings(monkeypatch, tmp_path, auth=True)
    _claim(settings, pid=424242, instance="inst-a")
    _alive(monkeypatch, True)

    code = cli.main(
        ["stop"],
        http=lambda *args, **kwargs: (401, {"detail": "invalid token"}),
        probe=lambda port, timeout: None,
    )

    assert code == 1
    out = capsys.readouterr().out
    assert "登录失败" in out
    assert "s3cret" not in out


def test_stop_accepts_a_stale_record_as_not_running(tmp_path, monkeypatch, capsys):
    settings = _settings(monkeypatch, tmp_path)
    lock_file = _claim(settings, pid=999_999)

    code = cli.main(["stop"], http=lambda *args, **kwargs: (200, {}))

    assert code == 1
    assert "未运行" in capsys.readouterr().out
    assert server_lock.read_lock(lock_file) is None
