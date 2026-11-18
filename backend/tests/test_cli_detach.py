"""#332 S5: the detached-server spawn flags (one platform each).

Windows: ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`` —
the launcher can exit without the server sharing its console or receiving its
Ctrl+C. POSIX: ``start_new_session=True``.

Both platform branches are asserted by patching ``cli._IS_WINDOWS`` so the
pure flag shape is covered everywhere; the real-OS cases are skipped on the
other platform. The end-to-end detach behavior lives in
``test_shared_backend_e2e.py``.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from backend import cli


class _FakePopen:
    calls: list = []

    def __init__(self, cmd, **kwargs):
        self.commands = cmd
        self.kwargs = kwargs
        _FakePopen.calls.append(self)


def _spawn(tmp_path, monkeypatch, *, windows: bool):
    _FakePopen.calls.clear()
    monkeypatch.setattr(cli, "_IS_WINDOWS", windows)
    log_path = tmp_path / "data" / "logs" / "server.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("old line\n", encoding="utf-8")

    child = cli._spawn_detached(
        ["python", "-m", "backend.serve"],
        {"PORT": "9123", "QIANFAN_INSTANCE": "tok"},
        log_path,
        popen=_FakePopen,
    )
    return child, log_path


@pytest.mark.skipif(os.name != "nt", reason="Windows creation flags only exist on Windows")
def test_spawn_detached_uses_windows_detached_process_flags(tmp_path, monkeypatch):
    child, log_path = _spawn(tmp_path, monkeypatch, windows=True)
    kwargs = child.kwargs

    flags = kwargs["creationflags"]
    assert flags & subprocess.DETACHED_PROCESS
    assert flags & subprocess.CREATE_NEW_PROCESS_GROUP
    assert flags & subprocess.CREATE_NO_WINDOW
    assert "start_new_session" not in kwargs
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"].name == str(log_path)
    assert kwargs["stderr"] is subprocess.STDOUT
    assert kwargs["cwd"] == os.getcwd()
    assert kwargs["env"]["PORT"] == "9123"
    # Append mode: the startup log keeps earlier attempts.
    assert log_path.read_text(encoding="utf-8") == "old line\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX session flag")
def test_spawn_detached_uses_a_new_session_on_posix(tmp_path, monkeypatch):
    child, _log_path = _spawn(tmp_path, monkeypatch, windows=False)
    kwargs = child.kwargs

    assert kwargs["start_new_session"] is True
    assert "creationflags" not in kwargs
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.STDOUT


def test_spawn_detached_creates_the_log_directory(tmp_path, monkeypatch):
    _FakePopen.calls.clear()
    monkeypatch.setattr(cli, "_IS_WINDOWS", False)
    log_path = tmp_path / "nested" / "logs" / "server.log"

    cli._spawn_detached(["python"], {}, log_path, popen=_FakePopen)

    assert log_path.parent.is_dir()
    assert log_path.is_file()
