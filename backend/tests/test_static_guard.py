"""Static-file path contract of starlette's own ``StaticFiles``.

CVE-2026-48818 (UNC path -> SMB / NTLM leak on Windows) is fixed upstream in
starlette 1.1: ``lookup_path`` refuses a leading ``/`` or ``\\`` before calling
``os.path.realpath``. The repository used to ship its own subclass
(``backend/api/static_guard.py``); with ``starlette>=1.3.1`` pinned, these tests
pin the upstream behaviour so a future range change cannot silently drop it.

The dangerous forms are pure path decisions and never open an SMB connection.
Normal nested assets must keep working on Windows, where ``get_path`` returns
``assets\\app.js`` (the 2026-10-24 white-screen regression).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles


def _scope(path: str, root_path: str = "") -> dict:
    return {"type": "http", "method": "GET", "path": path, "root_path": root_path, "headers": []}


def _status(files: StaticFiles, path: str) -> int:
    scope = _scope(path)
    try:
        asyncio.run(files.get_response(files.get_path(scope), scope))
    except HTTPException as exc:
        return exc.status_code
    return 200


def _lookup(tmp_path: Path, path: str) -> tuple[str, os.stat_result | None]:
    return StaticFiles(directory=str(tmp_path)).lookup_path(path)


@pytest.mark.parametrize(
    "path",
    [
        r"/\\attacker.example\share\x",  # UNC behind a leading slash (real HTTP shape)
        r"/\attacker.example\share",  # drive-root-relative
        r"/C:\Windows\win.ini",  # drive-absolute
        "//attacker.example/share",  # leading double slash normalises to a relative path
        "/x\x00y",  # NUL byte
        r"/\\?\C:\tmp",  # device namespace
        "",  # empty
    ],
)
def test_unsafe_paths_get_404(path: str, tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("ok", encoding="utf-8")
    assert _status(StaticFiles(directory=str(tmp_path)), path) == 404


def test_leading_separator_is_refused_before_realpath(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(path: str) -> str:  # pragma: no cover - only reached on regression
        raise AssertionError("realpath must not run on a leading-separator path")

    # Patched for this test only; the guard must return before touching the resolver.
    monkeypatch.setattr("starlette.staticfiles.os.path.realpath", boom)
    files = StaticFiles(directory=".")
    assert files.lookup_path(r"\\attacker.example\share\x") == ("", None)
    assert files.lookup_path(r"\attacker.example\share") == ("", None)


def test_nul_byte_is_refused_during_resolution(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _lookup(tmp_path, "x\x00y")


def test_drive_absolute_path_never_escapes_the_mount(tmp_path: Path) -> None:
    try:
        resolved, stat_result = _lookup(tmp_path, r"C:\Windows\win.ini")
    except ValueError:
        # Windows refuses paths on another drive inside commonpath; pin the
        # precondition so an unrelated ValueError cannot pass this branch.
        assert os.name == "nt"
        assert tmp_path.drive.upper() != Path(r"C:\Windows\win.ini").drive.upper()
    else:
        assert (resolved, stat_result) == ("", None)


def test_leading_double_slash_stays_relative(tmp_path: Path) -> None:
    files = StaticFiles(directory=str(tmp_path))
    path = files.get_path(_scope("//attacker.example/share"))
    assert path == os.path.join("attacker.example", "share")
    assert _lookup(tmp_path, path) == ("", None)


@pytest.mark.parametrize(
    "rel",
    ("index.html", "assets/app-abc123.js", "a/b/c.png", "%5C%5Cnotdecoded"),
)
def test_normal_paths_are_served(rel: str, tmp_path: Path) -> None:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("ok", encoding="utf-8")

    _, stat_result = _lookup(tmp_path, os.path.join(*rel.split("/")))
    assert stat_result is not None


def test_nested_assets_keep_windows_separator(tmp_path: Path) -> None:
    # Regression (2026-10-24 review): rejecting *any* backslash 404s every Vite
    # asset under ``assets/`` on Windows (white screen).
    target = tmp_path / "assets"
    target.mkdir()
    (target / "app.js").write_text("ok", encoding="utf-8")

    files = StaticFiles(directory=str(tmp_path))
    path = files.get_path(_scope("/assets/app.js"))
    assert path == os.path.join("assets", "app.js")
    assert _lookup(tmp_path, path)[1] is not None


def test_mount_honours_root_path() -> None:
    files = StaticFiles(directory=".")
    # A prefixed mount (root_path) is stripped before the path is resolved.
    assert files.get_path(_scope("/app/index.html", root_path="/app")) == "index.html"
