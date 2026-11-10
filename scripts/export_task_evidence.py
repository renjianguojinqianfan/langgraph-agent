"""Export one task record as sanitized manual-verification evidence JSON.

Usage:
    python scripts/export_task_evidence.py <task_id> [--out PATH] [--base-url URL]

Default output follows the evidence convention in ``docs/manual-verification.md``:
``docs/assets/verification/<YYYY-MM-DD>/task-<task_id>.json``. The repo root and
the user home become the documented ``<repo-root>`` / ``<user>`` placeholders;
any other absolute path (a workspace opened from a different directory) becomes
``<workspace>`` — so the file passes ``scripts/check_no_machine_paths.py``
before it is committed.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
KEEP = (
    "id",
    "title",
    "status",
    "mode",
    "model",
    "workspace",
    "plan_version",
    "final_answer",
    "error",
    "steps",
    "conversation",
    "subtasks",
    "created_at",
    "updated_at",
)


def _path_regex(path: Path) -> re.Pattern[str]:
    escaped = re.escape(str(path))
    # The record may carry either separator style; match both, case-insensitively.
    # The trailing lookahead keeps a sibling that merely extends the root name
    # (``.../<repo-root>-old``) from being whitewashed along with it.
    return re.compile(
        escaped.replace("\\\\", r"[\\/]") + r"(?![A-Za-z0-9_-])", re.IGNORECASE
    )


#: Remaining absolute paths the machine-path gate would flag: a drive path, a
#: git-bash mount (``/c/...``) or a POSIX home path. The lookbehind keeps URLs
#: (``https://example.com/home/x``) from matching.
_RESIDUAL_ABS = re.compile(
    r"(?i)(?:(?<![\w])[a-z]:[\\/]|(?<![\w.:/])/(?:[a-z]|users|home)[\\/])[^\s\"'`|<>]*"
)

_REPO_RE = _path_regex(ROOT)
_HOME_RE = _path_regex(Path.home())


def scrub(value: Any) -> Any:
    """Replace machine-specific absolute paths anywhere in the record."""
    if isinstance(value, str):
        text = _HOME_RE.sub("<user>", _REPO_RE.sub("<repo-root>", value))
        return _RESIDUAL_ABS.sub("<workspace>", text)
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items()}
    return value


def pick(task: dict[str, Any]) -> dict[str, Any]:
    """Keep the fields the verification tables cite, nothing else."""
    return {key: task.get(key) for key in KEEP}


def fetch_task(task_id: str, base_url: str) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/api/tasks/{urllib.parse.quote(task_id, safe='')}"
    with urllib.request.urlopen(url, timeout=10) as response:
        payload: dict[str, Any] = json.load(response)
    data: dict[str, Any] = payload["data"]
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export sanitized task evidence JSON.")
    parser.add_argument("task_id")
    parser.add_argument("--out", type=Path, help="output path (default: docs/assets/verification/<today>/)")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)

    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.task_id):
        parser.error("task_id must be a plain id (letters, digits, '-' or '_')")

    task = fetch_task(args.task_id, args.base_url)
    out = args.out or (
        ROOT / "docs" / "assets" / "verification" / date.today().isoformat() / f"task-{args.task_id}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(scrub(pick(task)), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
