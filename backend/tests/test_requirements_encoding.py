"""#259: requirements files stay ASCII and start.py installs like CI.

Windows pip decodes requirement files with the platform locale; on a Chinese
Windows that is cp936, so a single non-ASCII comment crashes before parsing
(UnicodeDecodeError; reproduced with locale.getpreferredencoding() == "cp936").
The three requirements files are kept ASCII-only, and start.py --install runs
the same two steps as CI (filter the mcp line, install, then mcp --no-deps).
"""

from __future__ import annotations

import codecs
import sys
from pathlib import Path

import pytest

import start

ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS_FILES = ("requirements.txt", "requirements-dev.txt", "overrides.txt")


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


def test_ci_filtered_requirements_drop_only_the_mcp_line():
    text = start._filtered_requirements_text()
    lines = text.splitlines()
    assert lines
    assert text.endswith("\n")
    # Only the bare requirement line is filtered; comments mentioning mcp stay.
    assert not any(line.startswith("mcp") for line in lines)
    assert "fastapi>=0.162.0,<0.163.0" in text
    assert "uvicorn[standard]>=0.30.0,<0.31.0" in text


def test_install_commands_match_the_ci_steps():
    commands = start._pip_install_commands("requirements-ci.txt")
    assert commands[0][0] == sys.executable
    assert commands[0][-2:] == ["-r", "requirements-ci.txt"]
    assert commands[1][0] == sys.executable
    assert commands[1][-2:] == ["--no-deps", "mcp>=1.2,<2.0"]
