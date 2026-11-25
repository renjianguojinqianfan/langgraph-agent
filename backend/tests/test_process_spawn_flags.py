"""#356: every production child spawn must hide its Windows console window.

The scanner is AST-based, so comments, docstrings and synthetic sources in this
file cannot satisfy or trip it. Rules:

* production sources (``backend/`` outside ``backend/tests/``, ``scripts/`` and
  ``start.py``) must pass a ``creationflags`` value built from the shared
  ``hidden_creationflags`` helper (or the shell tool's pure wrapper);
* ``backend/tests/`` calls are covered at runtime by the autouse fixture that
  wraps ``subprocess.Popen`` for the whole session — its wiring is pinned in
  ``test_hidden_process.py`` and re-asserted here;
* a call that genuinely cannot follow the rule goes into ``_ALLOWLIST`` with a
  non-empty reason, keyed by its exact call site (never a whole file).

Known blind spots (kept explicit, not claimed as covered): ``from subprocess
import Popen`` aliasing is detected, but rebinding ``subprocess.Popen`` to a
variable (``spawn = subprocess.Popen``) and libraries that call
``CreateProcess`` themselves (for example MCP / anyio) bypass both this guard
and the test-time ``subprocess.Popen`` patch.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]

_SPAWN_NAMES = frozenset({"Popen", "run", "call", "check_output", "check_call"})

#: Function/constant names that count as carrying the hidden-window flag.
#: A new wrapper has to be added here deliberately; the guard fails loudly
#: instead of accepting an arbitrary ``creationflags`` expression.
_HIDDEN_FLAG_MARKERS = frozenset(
    {"hidden_creationflags", "_shell_creation_flags", "CREATE_NO_WINDOW"}
)

#: Calls that intentionally skip the flag, keyed by (path, enclosing scope, callee).
#: Each entry needs a non-empty reason (pinned by a test below).
_ALLOWLIST: dict[tuple[str, str, str], str] = {
    ("scripts/check_doc_links.py", "tracked_markdown", "run"): (
        "developer CLI: git runs inside the caller's existing console, no new window"
    ),
    ("scripts/check_no_machine_paths.py", "scoped_files", "run"): (
        "developer CLI: git runs inside the caller's existing console, no new window"
    ),
}


@dataclass(frozen=True)
class SpawnCall:
    """One direct ``subprocess.<spawn>`` call found in a source file."""

    path: str
    scope: str
    callee: str
    lineno: int
    has_hidden_flags: bool


def _imports(tree: ast.Module) -> tuple[set[str], dict[str, str]]:
    """(``subprocess`` module aliases, ``from subprocess import X`` aliases)."""
    modules: set[str] = set()
    direct: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                if alias.name in _SPAWN_NAMES:
                    direct[alias.asname or alias.name] = alias.name
    return modules, direct


def _spawn_callee(func: ast.expr, modules: set[str], direct: dict[str, str]) -> Optional[str]:
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.value.id in modules and func.attr in _SPAWN_NAMES:
            return func.attr
    if isinstance(func, ast.Name) and func.id in direct:
        return direct[func.id]
    return None


def _carries_hidden_flags(call: ast.Call) -> bool:
    value = next((kw.value for kw in call.keywords if kw.arg == "creationflags"), None)
    if value is None:
        return False
    for node in ast.walk(value):
        if isinstance(node, ast.Name) and node.id in _HIDDEN_FLAG_MARKERS:
            return True
        if isinstance(node, ast.Attribute) and node.attr in _HIDDEN_FLAG_MARKERS:
            return True
    return False


def spawn_calls(path: str, source: str) -> list[SpawnCall]:
    """All direct subprocess spawn calls in ``source`` (pure, synthetic-friendly)."""
    tree = ast.parse(source)
    modules, direct = _imports(tree)
    found: list[SpawnCall] = []

    def walk(node: ast.AST, scope: tuple[str, ...]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = (*scope, node.name)
        if isinstance(node, ast.Call):
            callee = _spawn_callee(node.func, modules, direct)
            if callee is not None:
                found.append(
                    SpawnCall(
                        path=path,
                        scope=".".join(scope) or "<module>",
                        callee=callee,
                        lineno=node.lineno,
                        has_hidden_flags=_carries_hidden_flags(node),
                    )
                )
        for child in ast.iter_child_nodes(node):
            walk(child, scope)

    walk(tree, ())
    return found


def violations(
    texts: dict[str, str],
    allowlist: Optional[dict[tuple[str, str, str], str]] = None,
) -> dict[str, list[str]]:
    """Repo-relative path -> readable problems (empty means the rule holds)."""
    exempt = _ALLOWLIST if allowlist is None else allowlist
    problems: dict[str, list[str]] = {}
    for rel, source in texts.items():
        if rel.startswith("backend/tests/"):
            # The session autouse fixture wraps subprocess.Popen for all of these.
            continue
        for call in spawn_calls(rel, source):
            if call.has_hidden_flags:
                continue
            if (rel, call.scope, call.callee) in exempt:
                continue
            problems.setdefault(rel, []).append(
                f"line {call.lineno}: subprocess.{call.callee} in {call.scope}"
            )
    return problems


def _scanned_files() -> list[str]:
    paths = list((REPO_ROOT / "backend").rglob("*.py"))
    paths += list((REPO_ROOT / "scripts").glob("*.py"))
    paths.append(REPO_ROOT / "start.py")
    return sorted(path.relative_to(REPO_ROOT).as_posix() for path in paths)


def _sources() -> dict[str, str]:
    return {
        rel: (REPO_ROOT / rel).read_text(encoding="utf-8") for rel in _scanned_files()
    }


# ── self-tests: the scanner must fail on a bare call and pass a flagged one ──

_BARE = """\
import subprocess
import sys


def start() -> None:
    subprocess.Popen([sys.executable, "-c", "pass"])
"""

_FLAGGED = """\
import subprocess

from backend.utils.hidden_process import hidden_creationflags


def start() -> None:
    subprocess.run(["git"], creationflags=hidden_creationflags())
"""


def test_scanner_flags_a_bare_production_spawn() -> None:
    assert violations({"backend/example.py": _BARE}, allowlist={}) == {
        "backend/example.py": ["line 6: subprocess.Popen in start"]
    }


def test_scanner_accepts_the_shared_hidden_flags_helper() -> None:
    assert violations({"backend/example.py": _FLAGGED}, allowlist={}) == {}


def test_scanner_detects_from_import_aliases() -> None:
    source = "from subprocess import run as spawn\n\nspawn(['git'])\n"

    assert violations({"backend/example.py": source}, allowlist={}) != {}


def test_scanner_treats_test_files_as_covered_by_the_fixture() -> None:
    assert violations({"backend/tests/test_example.py": _BARE}, allowlist={}) == {}


def test_allowlist_is_keyed_by_the_exact_call_site() -> None:
    allow = {("backend/example.py", "start", "Popen"): "documented exception"}

    assert violations({"backend/example.py": _BARE}, allowlist=allow) == {}
    moved = _BARE.replace("def start", "def other")
    assert violations({"backend/example.py": moved}, allowlist=allow) != {}


def test_allowlist_entries_carry_a_reason() -> None:
    assert all(reason.strip() for reason in _ALLOWLIST.values())


# ── the real tree ────────────────────────────────────────────────────────────

_ANCHORED_CALLS = frozenset(
    {
        ("backend/core/tools/shell.py", "ShellTool.run", "Popen"),
        ("backend/core/workspace_files.py", "_git_files", "run"),
        ("start.py", "_run", "Popen"),
    }
)


def test_scanner_sees_the_known_production_spawns() -> None:
    seen = {
        (call.path, call.scope, call.callee)
        for rel, source in _sources().items()
        for call in spawn_calls(rel, source)
    }

    assert _ANCHORED_CALLS <= seen, f"missing anchors: {sorted(_ANCHORED_CALLS - seen)}"


def test_no_production_spawn_skips_the_hidden_window_flags() -> None:
    problems = violations(_sources())

    assert problems == {}, "bare subprocess spawns on Windows: " + repr(problems)


def test_conftest_protects_test_side_spawns() -> None:
    test_calls = [
        call
        for rel, source in _sources().items()
        if rel.startswith("backend/tests/")
        for call in spawn_calls(rel, source)
    ]

    assert test_calls, "expected test helpers that spawn child processes"
    # The fixture itself is pinned in test_hidden_process.py; this assertion
    # keeps the two rules linked if that file is deleted.
    conftest = (REPO_ROOT / "backend" / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "_hide_child_console_windows" in conftest
    assert "install_hidden_popen" in conftest
