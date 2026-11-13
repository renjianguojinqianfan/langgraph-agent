"""#259: requirements files stay ASCII and start.py installs like CI.

Windows pip decodes requirement files with the platform locale; on a Chinese
Windows that is cp936, so a single non-ASCII comment crashes before parsing
(UnicodeDecodeError; reproduced with locale.getpreferredencoding() == "cp936").
The requirements files are kept ASCII-only, and start.py --install runs the same
single step as both CI jobs.
"""

from __future__ import annotations

import codecs
import sys
from pathlib import Path

import pytest

import start

ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS_FILES = ("requirements.txt", "requirements-dev.txt")


@pytest.mark.parametrize("name", REQUIREMENTS_FILES)
def test_requirements_files_are_ascii(name):
    data = (ROOT / name).read_bytes()
    assert not data.startswith(codecs.BOM_UTF8), (
        f"{name}: a UTF-8 BOM is not needed; keep the file plain ASCII"
    )
    offsets = [index for index, byte in enumerate(data) if byte > 0x7F]
    assert not offsets, (
        f"{name} has non-ASCII bytes at {offsets[:5]}; pip decodes requirement "
        "files with the platform locale (cp936 on Chinese Windows) and crashes "
        "before parsing. Keep comments ASCII (#259)."
    )


def test_requirements_declare_the_web_stack_bounds():
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "fastapi>=0.162.0,<0.163.0" in text
    assert "uvicorn[standard]>=0.54.0,<0.55.0" in text
    # mcp is an ordinary requirement now: no install path filters it out.
    assert "mcp>=1.2,<2.0" in text


def test_install_command_matches_the_ci_step():
    commands = start._pip_install_commands()
    assert len(commands) == 1
    assert commands[0][0] == sys.executable
    assert commands[0][-2:] == ["-r", "requirements.txt"]


def test_both_ci_jobs_install_requirements_directly():
    """Neither job may bring back the mcp filter or the --no-deps side install.

    The negative assertions scan the whole file, comments included: a comment
    naming those mechanisms fails too, on purpose.
    """
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "--no-deps" not in text
    assert "requirements-ci.txt" not in text
    assert "^mcp" not in text
    install_lines = [
        line.strip() for line in text.splitlines() if line.strip() == "pip install -r requirements.txt"
    ]
    assert len(install_lines) == 2


def test_no_dependency_override_file():
    """An override file silently forced uvicorn back below mcp's floor; keep it gone.

    Both entry points of that trap are guarded: the file itself, and the
    documented install command (a renamed override file slips past the first).
    """
    assert not (ROOT / "overrides.txt").exists()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "--overrides" not in readme
