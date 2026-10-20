"""Fail when a tracked Markdown file links to a missing relative target.

Usage: python scripts/check_doc_links.py
External URLs, bare anchors and absolute paths are out of scope.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")


def tracked_markdown() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return [ROOT / line for line in out.stdout.splitlines() if line]


def main() -> int:
    broken: list[str] = []
    for path in tracked_markdown():
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for match in LINK.finditer(text):
            target = match.group(1).strip("<>")
            if re.match(r"^(?:https?:|mailto:|#|/)", target):
                continue
            clean = unquote(target.split("#", 1)[0].split("?", 1)[0])
            if not clean:
                continue
            resolved = (path.parent / clean).resolve()
            if not resolved.exists():
                broken.append(f"{path.relative_to(ROOT)} -> {target}")
    if broken:
        print("Broken relative links:")
        for item in broken:
            print(f"  {item}")
        return 1
    print("All tracked Markdown relative links resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
