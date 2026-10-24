"""Static-file path guard (CVE-2026-48818 mitigation).

The frontend mount rejects UNC / drive-absolute paths before starlette's
``lookup_path`` calls ``os.path.realpath`` — on Windows that call would open an
SMB connection and leak the process account's NTLMv2 hash. No filesystem
access here: all cases are pure path decisions, so the test runs on any
platform.

The guard checks the **normalised** path (what ``StaticFiles.get_path``
returns), which is also why a request like ``/C:/Windows/win.ini`` is caught.
"""

from __future__ import annotations

import pytest
from starlette.exceptions import HTTPException

from backend.api.static_guard import SafeStaticFiles, is_unsafe_static_path


def _scope(path: str, root_path: str = "") -> dict:
    return {"type": "http", "path": path, "root_path": root_path, "headers": []}


@pytest.mark.parametrize(
    "path",
    [
        r"\\attacker.example\share\x",  # UNC (decoded from %5C%5C...)
        r"\attacker.example\share",  # drive-root-relative
        r"C:\Windows\win.ini",  # drive-absolute
        "//attacker.example/share",  # leading double slash
        "x\x00y",  # NUL byte
        r"\\?\C:\tmp",  # device namespace
        "",  # empty
    ],
)
def test_unsafe_paths_are_rejected(path: str) -> None:
    assert is_unsafe_static_path(path)


@pytest.mark.parametrize(
    "path",
    ["index.html", "assets/app-abc123.js", "a/b/c.png", "%5C%5Cnotdecoded"],
)
def test_normal_paths_pass(path: str) -> None:
    assert not is_unsafe_static_path(path)


@pytest.mark.parametrize(
    "path",
    [
        r"/\\attacker.example\share\x",
        "/C:/Windows/win.ini",  # leading-slash drive path: caught after normpath
        "/x\x00y",
    ],
)
def test_mount_refuses_unsafe_paths_before_resolution(path: str) -> None:
    # `//host/share` is rejected by the helper, but on POSIX `StaticFiles.get_path`
    # normalises it into a relative path first, so the mount test cannot use it.
    files = SafeStaticFiles(directory=".")
    with pytest.raises(HTTPException) as excinfo:
        files.get_path(_scope(path))
    assert excinfo.value.status_code == 404


def test_mount_serves_normal_paths() -> None:
    files = SafeStaticFiles(directory=".")
    assert files.get_path(_scope("/index.html")) == "index.html"


def test_mount_honours_root_path() -> None:
    files = SafeStaticFiles(directory=".")
    # A prefixed mount (root_path) must be stripped before the guard decides.
    assert files.get_path(_scope("/app/index.html", root_path="/app")) == "index.html"
