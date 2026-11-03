"""#262: the machine-path gate's red/green matrix, as reproducible assertions.

``scripts/check_no_machine_paths.py`` is the mechanical gate for
``docs/security.md`` §3 (「不得出现真实用户名 / 本机绝对路径；占位符按清单」).
Its judgement used to be verified by an ad-hoc run whose result only existed in
a PR description — a reviewer had to trust a claimed "45 cases pass". This file
turns that matrix into the offline suite, so a regression fails CI instead of
 relying on someone re-running the script by hand.

Table-driven on purpose: every case is one line of data, and the groups mirror
what the #283 reviews actually probed —

* bypass shapes (a real path laundered behind an exemption) must be RED;
* the sanctioned spellings this repo ships must be GREEN (the doc/gate
  interlock: every replacement form the cleanup introduced);
* fresh real paths must be RED (the allowlist is an exact list, not a shape);
* system roots and the ellipsis shorthand must be GREEN (no false alarms).

Loading the script by path (not import) is deliberate: it lives in ``scripts/``,
outside the ``backend`` package, and this way the test exercises the exact file
the gate command runs.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_no_machine_paths.py"

_spec = importlib.util.spec_from_file_location("check_no_machine_paths", SCRIPT)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
# Register before exec: the module's Rule dataclass resolves its own module
# through sys.modules at class-creation time.
sys.modules["check_no_machine_paths"] = gate
_spec.loader.exec_module(gate)


def _violations(line: str) -> list[str]:
    """The tokens the gate flags in one line (empty == allowed)."""
    found: list[str] = []
    for rule in gate.RULES:
        for match in rule.pattern.finditer(line):
            if not rule.allows(line, match):
                found.append(match.group(0))
    return found


# ── bypass shapes: a real path hidden behind an exemption — must be RED ──
BYPASS_FORMS = [
    # real username, placeholder only in the tail (review attack form 1)
    "workspace = C:\\Users\\DELL\\<workspace>",
    # real checkout tree, placeholder only in the tail (review attack form 2)
    "cache at E:\\code\\demo\\<tmp>\\x please",
    # HTML glue-on: <code> is not a placeholder, the token must stop at it
    "<code>E:\\笔记\\02_沉淀库\\C专业知识\\C5 Agent研究\\agent模式库\\20260921_C5_x.md</code>",
    # system-root shortcut + traversal: the root before .. proves nothing
    "C:\\Windows\\..\\Users\\DELL",
    # ellipsis shorthand with a real tail
    "C:\\...\\Users\\DELL",
    # git-bash drive mount with a real username
    "/c/Users/DELL/project",
    # unregistered placeholder spelling (must use the §3 vocabulary)
    "C:\\Users\\<用户名>\\.cache",
]


# ── sanctioned spellings shipped by the #262 cleanup — must be GREEN ──
SANCTIONED_FORMS = [
    "不得出现 `C:\\Users\\<user>`、家目录 / 个人笔记路径",
    "`C:\\Users\\<user>\\.cache`",
    '"C:\\Users\\<user>\\AppData\\Roaming\\uv\\python\\cpython-3.11.15-windows-x86_64-none\\python.exe"',
    "/c/Users/<user>/.cache",
    "/home/<user>/project",
    'setx PRE_COMMIT_HOME "<repo-root>\\..\\.precommit-cache"',
    "| `<repo-root>\\..\\..\\venvtest4`（仓库外、同盘、同上级目录） | 好 |",
    '& "<repo-root>\\.venv311\\Scripts\\python.exe" -c',
    "本目录：<tmp>\\qianfan-c7-20261021\\B",
    '"workspace": "<repo-root>\\\\.qoder\\\\tmp\\\\qa2026-a"',
    "pwd -> <repo-root>/data/artifacts",
    "<code>&lt;personal-notes&gt;\\20260921_C5_上下文预算与压缩.md</code>",
    "`<git-root>\\usr\\bin\\sh.exe: *** fatal error`",
    "| `<python-install>\\python.exe`（3.13） | ok |",
    "pytest-of-<user>/",
]


# ── fresh real paths — must be RED (exact list, never a shape guess) ──
FRESH_REAL_PATHS = [
    "see E:\\code\\demo\\realproject here",
    "D:\\projects\\foo\\bar",
    "C:\\Users\\newuser\\Documents",
    "E:/code/demo/realproject",
    'setx PRE_COMMIT_HOME "E:\\code\\demo\\.precommit-cache"',
    "pwd -> /e/code/demo/langgraph-agent/data/artifacts",
    "home is /home/newuser/work",
    "file:///E:/笔记/x.md",
]


# ── system roots / shorthands / non-path prose — must be GREEN ──
SYSTEM_AND_PROSE = [
    "C:\\Windows\\System32\\bash.exe (the WSL stub) is ignored.",
    "C:\\Program Files\\x",
    "C:\\Program Files (x86)\\Common Files\\y",
    "C:\\ProgramData\\z",
    "/c/Windows/System32/cmd.exe",
    '`"C:\\\\Windows\\\\win.ini"`',
    "`C:\\...`",
    "%TEMP%\\opencode\\venvtest",
    "页签 `role=tablist/tab/tabpanel` + `aria-controls` + ←/→/Home/End。",
    "- [r/LangChain](https://reddit.com/r/LangChain)",
    '`{"tool":"bash","pattern":"rm\\\\s+-rf","reason":"x"}`',
]


@pytest.mark.parametrize("line", BYPASS_FORMS)
def test_bypass_shapes_are_violations(line: str) -> None:
    assert _violations(line), f"bypass shape slipped through: {line}"


@pytest.mark.parametrize("line", SANCTIONED_FORMS)
def test_sanctioned_spellings_are_allowed(line: str) -> None:
    assert not _violations(line), f"sanctioned spelling flagged: {line}"


@pytest.mark.parametrize("line", FRESH_REAL_PATHS)
def test_fresh_real_paths_are_violations(line: str) -> None:
    assert _violations(line), f"real path slipped through: {line}"


@pytest.mark.parametrize("line", SYSTEM_AND_PROSE)
def test_system_roots_and_prose_are_allowed(line: str) -> None:
    assert not _violations(line), f"false positive: {line}"


def test_scoped_files_covers_the_documented_surface() -> None:
    """Pin the scan surface's member structure (not the AGENTS.md wording).

    Asserts one concrete member of each surface the gate claims — root
    Markdown, docs Markdown, tracked retros, lessons/, docs evidence — and
    that code stays out. It deliberately does **not** parse the AGENTS.md
    trigger line: that is prose, and a prose parser here would be a brittle
    second source of truth. The gate script is the authority; the AGENTS.md
    wording is kept in sync by hand (see docs/security.md §3).
    """
    files = {path.relative_to(gate.ROOT).as_posix() for path in gate.scoped_files()}
    assert "AGENTS.md" in files, "root Markdown missing from the scan surface"
    assert "docs/architecture.md" in files, "docs Markdown missing from the scan surface"
    assert any(f.startswith(".agents/retros/") for f in files), "tracked retros missing"
    assert any(f.startswith("lessons/") for f in files), "lessons missing from the scan surface"
    assert any(f.startswith("docs/assets/verification/") for f in files), "evidence missing"
    assert not any(f.startswith("backend/") for f in files), "code must stay out of scope"


def test_repo_docs_surface_is_clean() -> None:
    """The gate's own scan over the committed tree — CI-enforced, not human-invoked.

    Runs exactly what ``python scripts/check_no_machine_paths.py`` runs, so the
    #262 cleanup cannot silently regress on a branch nobody remembered to check.
    """
    assert gate.main() == 0
