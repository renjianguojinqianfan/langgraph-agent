"""#356: Windows test children never get their own console window.

The session protection is the autouse fixture in ``conftest.py``; it delegates
to :mod:`backend.tests._hidden_process`. These tests exercise the helper with
the platform seam patched (never the real OS) and pin the fixture wiring at the
source level, so the runtime protection cannot be removed silently.

``subprocess.run`` / ``call`` / ``check_output`` / ``check_call`` all resolve
``Popen`` through the ``subprocess`` module at call time, so patching that one
attribute covers every test helper that spawns a child.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from typing import Any

from backend.tests import _hidden_process
from backend.utils import hidden_process

CONFTEST = Path(__file__).with_name("conftest.py")


def test_hidden_creationflags_adds_the_bit_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(hidden_process, "_IS_WINDOWS", True)

    bare = hidden_process.hidden_creationflags()
    assert bare == hidden_process.CREATE_NO_WINDOW

    combined = hidden_process.hidden_creationflags(0x4)
    assert combined & 0x4, "caller-supplied flags must survive"
    assert combined & hidden_process.CREATE_NO_WINDOW


def test_hidden_creationflags_leaves_non_windows_untouched(monkeypatch) -> None:
    monkeypatch.setattr(hidden_process, "_IS_WINDOWS", False)

    assert hidden_process.hidden_creationflags() == 0
    assert hidden_process.hidden_creationflags(0x4) == 0x4


def test_apply_hidden_flags_preserves_caller_kwargs(monkeypatch) -> None:
    monkeypatch.setattr(hidden_process, "_IS_WINDOWS", True)

    injected = _hidden_process.apply_hidden_flags({})
    assert injected == {"creationflags": hidden_process.CREATE_NO_WINDOW}

    merged = _hidden_process.apply_hidden_flags({"creationflags": 0x4, "shell": False})
    assert merged["creationflags"] & 0x4
    assert merged["creationflags"] & hidden_process.CREATE_NO_WINDOW
    assert merged["shell"] is False


def test_apply_hidden_flags_is_a_noop_elsewhere(monkeypatch) -> None:
    monkeypatch.setattr(hidden_process, "_IS_WINDOWS", False)

    untouched: dict[str, Any] = {}
    assert _hidden_process.apply_hidden_flags(untouched) == {}
    assert _hidden_process.apply_hidden_flags({"creationflags": 0x4}) == {"creationflags": 0x4}


def test_patch_restores_the_exact_original_popen() -> None:
    original = subprocess.Popen
    patch = _hidden_process.HiddenPopenPatch().install()
    try:
        assert subprocess.Popen is not original
    finally:
        patch.restore()

    assert subprocess.Popen is original


def test_install_is_a_noop_outside_windows(monkeypatch) -> None:
    monkeypatch.setattr(hidden_process, "_IS_WINDOWS", False)
    original = subprocess.Popen

    patch = _hidden_process.install_hidden_popen()

    assert patch.installed is False
    assert subprocess.Popen is original


def test_install_wraps_popen_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(hidden_process, "_IS_WINDOWS", True)
    original = subprocess.Popen

    patch = _hidden_process.install_hidden_popen()
    try:
        assert patch.installed is True
        assert subprocess.Popen is not original
    finally:
        patch.restore()

    assert subprocess.Popen is original


def _fixture_info() -> dict[str, Any]:
    """The ``_hide_child_console_windows`` fixture wiring, straight from source."""
    tree = ast.parse(CONFTEST.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name != "_hide_child_console_windows":
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "fixture"
            ):
                continue
            keywords = {kw.arg: kw.value for kw in decorator.keywords if kw.arg is not None}
            autouse = keywords.get("autouse")
            scope = keywords.get("scope")
            calls: set[str] = set()
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                if isinstance(child.func, ast.Name):
                    calls.add(child.func.id)
                elif isinstance(child.func, ast.Attribute):
                    calls.add(child.func.attr)
            return {
                "autouse": isinstance(autouse, ast.Constant) and autouse.value is True,
                "scope": scope.value if isinstance(scope, ast.Constant) else None,
                "calls": calls,
            }
    raise AssertionError("conftest.py 缺少 _hide_child_console_windows fixture")


def test_conftest_installs_a_session_wide_autouse_fixture() -> None:
    info = _fixture_info()

    assert info["autouse"] is True, "fixture must run without being requested"
    assert info["scope"] == "session", "one patch for the whole suite"
    assert "install_hidden_popen" in info["calls"]
    assert "restore" in info["calls"], "the exact original Popen must come back"
