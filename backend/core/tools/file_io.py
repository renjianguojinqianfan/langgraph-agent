"""File I/O tools with sandbox path restrictions.

All paths are confined to ``Settings.artifacts_path`` (the sandbox root). Reads
outside the root, writes that escape the root, and non-existent files are
rejected. Successful writes return the absolute path so the kernel can register
an :class:`~backend.services.persistence.Artifact`.

Two families live here:

* :class:`FileIOTool` (``file_io``) -- the original multi-action tool
  (``read`` / ``write`` / ``list``). Retained for backward compatibility; a
  tracked follow-up covers migrating callers off it and retiring it.
* The P0-A discrete "six-piece" tools (``docs/roadmap-pawbench.md``):
  :class:`ReadTool` (``read``, line pagination + line numbers),
  :class:`WriteTool` (``write``, whole-file create/overwrite),
  :class:`EditTool` (``edit``, exact ``str_replace``), :class:`GlobTool`
  (``glob``, recursive filename match) and :class:`GrepTool` (``grep``, regex
  content search). Each is its own ``BaseTool`` so the function schema and the
  per-tool resilience/confirm policy stay focused. ``write`` exists so a
  sub-agent can be given sandbox writes without the un-splittable ``file_io``
  read/write/list bundle (issue #56).

Both families share the same sandbox confinement; the discrete tools factor it
into :class:`_SandboxedTool`.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from ...config import Settings
from ...services.snapshots import capture_before_image
from ...utils.logging import get_logger
from .base import BaseTool, ToolResult
from .registry import register

logger = get_logger("tool.file_io")


@register
class FileIOTool(BaseTool):
    name = "file_io"
    description = (
        "Read, write or list files inside the sandbox artifacts directory. "
        "Paths are relative to the sandbox root and may not escape it."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["read", "write", "list"],
                "description": "Operation to perform.",
            },
            "path": {
                "type": "string",
                "description": "Relative path inside the sandbox (for read/write).",
            },
            "content": {
                "type": "string",
                "description": "Content to write (for action=write).",
            },
        },
        "required": ["action", "path"],
    }
    requires_confirm = False
    # P0 resilience: local FS operations — deterministic, no retry, no breaker.
    retryable = False
    max_retries = 0
    circuit_breaker = False

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self._root = settings.artifacts_path if settings else Path("data/artifacts")

    def _safe_path(self, path: str) -> Path | None:
        """Resolve ``path`` against the sandbox root; return None if it escapes."""
        try:
            root = self._root.resolve()
            root.mkdir(parents=True, exist_ok=True)
            candidate = (root / path).resolve()
            if candidate != root and root not in candidate.parents:
                return None
            return candidate
        except Exception:
            return None

    def run(self, **kwargs: Any) -> ToolResult:
        action = str(kwargs.get("action", "")).lower()
        path = str(kwargs.get("path", ""))
        if not path:
            return ToolResult(success=False, error="`path` is required.")
        target = self._safe_path(path)
        if target is None:
            return ToolResult(
                success=False,
                error=f"Path '{path}' is outside the sandbox root and was rejected.",
            )

        try:
            if action == "read":
                if not target.exists():
                    return ToolResult(success=False, error=f"File not found: {path}")
                text = target.read_text(encoding="utf-8", errors="replace")
                return ToolResult(success=True, data={"path": str(target), "content": text, "size": target.stat().st_size})

            if action == "write":
                content = str(kwargs.get("content", ""))
                target.parent.mkdir(parents=True, exist_ok=True)
                # P1-B: photograph the previous version before it is overwritten
                # (fail-open — a broken snapshot store never blocks the write).
                capture_before_image(target, settings=self.settings)
                target.write_text(content, encoding="utf-8")
                size = target.stat().st_size
                logger.info("file_io wrote %s (%d bytes)", target, size)
                return ToolResult(
                    success=True,
                    data={"path": str(target), "size": size, "content": content},
                )

            if action == "list":
                root = self._root.resolve()
                root.mkdir(parents=True, exist_ok=True)
                entries = [
                    {
                        "name": p.name,
                        "is_dir": p.is_dir(),
                        "size": p.stat().st_size if p.is_file() else 0,
                    }
                    for p in sorted(root.iterdir())
                ]
                return ToolResult(success=True, data={"path": str(root), "entries": entries})

            return ToolResult(success=False, error=f"Unknown action: {action}")
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("file_io failed")
            return ToolResult(success=False, error=str(exc))


# ───────────────────── P0-A discrete file tools ─────────────────────
# Six-piece-style precision tools (read/edit/glob/grep) added under
# docs/roadmap-pawbench.md P0-A. They complement -- and will eventually replace
# -- the multi-action FileIOTool above (retirement tracked in a follow-up).
#
# The defaults below are module constants (not Settings) to avoid config
# sprawl; they can be promoted to Settings later if a deployment needs to tune
# them. Tests may monkeypatch them to exercise the caps without huge fixtures.
DEFAULT_READ_LIMIT = 2000  # max lines a single `read` returns before truncating
GLOB_MAX_MATCHES = 500  # cap on glob results (guards a runaway `**` pattern)
GREP_MAX_RESULTS = 100  # cap on grep matches returned
GREP_MAX_FILE_BYTES = 1_000_000  # skip files larger than this when grepping


def _coerce_int(value: Any, default: int) -> int:
    """Best-effort int coercion for tool args; falls back to ``default``."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class _SandboxedTool(BaseTool):
    """Base for the discrete file tools.

    Confines every path to ``Settings.artifacts_path`` and carries the same
    resilience/confirm policy as :class:`FileIOTool`: local FS operations are
    deterministic, so no retry, no circuit breaker and no human confirmation
    (sandbox-confined file writes are not on the AGENTS.md danger list).
    """

    requires_confirm = False
    retryable = False
    max_retries = 0
    circuit_breaker = False

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self._root = settings.artifacts_path if settings else Path("data/artifacts")

    def _root_resolved(self) -> Path:
        root = self._root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _safe_path(self, path: str) -> Path | None:
        """Resolve ``path`` against the sandbox root; return None if it escapes."""
        try:
            root = self._root_resolved()
            candidate = (root / path).resolve()
            if candidate != root and root not in candidate.parents:
                return None
            return candidate
        except Exception:
            return None

    def _base_dir(self, sub: str) -> Path | None:
        """Resolve an optional sub-directory scope (empty -> sandbox root)."""
        return self._safe_path(sub) if sub else self._safe_path(".")


@register
class ReadTool(_SandboxedTool):
    name = "read"
    description = (
        "Read a text file inside the sandbox with line-based pagination. Returns "
        "at most `limit` lines starting at 1-based `offset`, each prefixed with "
        "its line number when `line_numbers` is true (default). Prefer this over "
        "reading a whole large file so the context is not flooded."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path inside the sandbox.",
            },
            "offset": {
                "type": "integer",
                "description": "1-based first line to return (default 1).",
            },
            "limit": {
                "type": "integer",
                "description": "Max lines to return (default 2000).",
            },
            "line_numbers": {
                "type": "boolean",
                "description": "Prefix each line with its 1-based number (default true).",
            },
        },
        "required": ["path"],
    }

    def run(self, **kwargs: Any) -> ToolResult:
        path = str(kwargs.get("path", ""))
        if not path:
            return ToolResult(success=False, error="`path` is required.")
        target = self._safe_path(path)
        if target is None:
            return ToolResult(
                success=False,
                error=f"Path '{path}' is outside the sandbox root and was rejected.",
            )
        if not target.exists():
            return ToolResult(success=False, error=f"File not found: {path}")
        if target.is_dir():
            return ToolResult(
                success=False, error=f"Path is a directory, not a file: {path}"
            )

        offset = _coerce_int(kwargs.get("offset", 1), 1)
        offset = max(1, offset)
        limit = _coerce_int(kwargs.get("limit", DEFAULT_READ_LIMIT), DEFAULT_READ_LIMIT)
        if limit <= 0:
            limit = DEFAULT_READ_LIMIT
        line_numbers = bool(kwargs.get("line_numbers", True))

        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("read failed")
            return ToolResult(success=False, error=str(exc))

        all_lines = text.splitlines()
        total = len(all_lines)
        start_idx = offset - 1
        window = all_lines[start_idx : start_idx + limit]
        returned = len(window)
        end_line = start_idx + returned
        truncated = end_line < total

        if line_numbers:
            content = "\n".join(
                f"{offset + i:>6}\t{line}" for i, line in enumerate(window)
            )
        else:
            content = "\n".join(window)

        return ToolResult(
            success=True,
            data={
                "path": str(target),
                "content": content,
                "total_lines": total,
                "start_line": offset,
                "end_line": end_line,
                "returned_lines": returned,
                "truncated": truncated,
                "size": target.stat().st_size,
            },
        )


@register
class WriteTool(_SandboxedTool):
    name = "write"
    description = (
        "Write a text file inside the sandbox, creating parent directories and "
        "overwriting an existing file. Prefer this for new files; use `edit` "
        "for surgical changes to a file that already exists."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path inside the sandbox.",
            },
            "content": {
                "type": "string",
                "description": "Full file content to write.",
            },
        },
        "required": ["path", "content"],
    }

    def run(self, **kwargs: Any) -> ToolResult:
        path = str(kwargs.get("path", ""))
        if not path:
            return ToolResult(success=False, error="`path` is required.")
        content = kwargs.get("content", "")
        if not isinstance(content, str):
            return ToolResult(success=False, error="`content` must be a string.")
        target = self._safe_path(path)
        if target is None:
            return ToolResult(
                success=False,
                error=f"Path '{path}' is outside the sandbox root and was rejected.",
            )
        if target.is_dir():
            return ToolResult(success=False, error=f"Path is a directory: {path}")

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # P1-B: photograph the previous version before it is overwritten
            # (fail-open — a broken snapshot store never blocks the write).
            capture_before_image(target, settings=self.settings)
            target.write_text(content, encoding="utf-8")
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("write failed")
            return ToolResult(success=False, error=str(exc))

        size = target.stat().st_size
        logger.info("write wrote %s (%d bytes)", target, size)
        return ToolResult(success=True, data={"path": str(target), "size": size})


@register
class EditTool(_SandboxedTool):
    name = "edit"
    description = (
        "Replace an exact string in a sandbox file. `old_string` must match the "
        "file verbatim (including whitespace/indentation) and be unique unless "
        "`replace_all` is true; fails if it is absent or ambiguous. Use for "
        "surgical edits instead of rewriting the whole file."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path inside the sandbox (file must exist).",
            },
            "old_string": {
                "type": "string",
                "description": "Exact text to replace (unique unless replace_all).",
            },
            "new_string": {
                "type": "string",
                "description": "Replacement text.",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace every occurrence (default false).",
            },
        },
        "required": ["path", "old_string", "new_string"],
    }

    def run(self, **kwargs: Any) -> ToolResult:
        path = str(kwargs.get("path", ""))
        if not path:
            return ToolResult(success=False, error="`path` is required.")
        target = self._safe_path(path)
        if target is None:
            return ToolResult(
                success=False,
                error=f"Path '{path}' is outside the sandbox root and was rejected.",
            )
        if not target.exists() or target.is_dir():
            return ToolResult(success=False, error=f"File not found: {path}")

        old_string = kwargs.get("old_string", "")
        new_string = kwargs.get("new_string", "")
        if not isinstance(old_string, str) or not isinstance(new_string, str):
            return ToolResult(
                success=False, error="`old_string` and `new_string` must be strings."
            )
        if old_string == "":
            return ToolResult(
                success=False, error="`old_string` is required (cannot be empty)."
            )
        replace_all = bool(kwargs.get("replace_all", False))

        try:
            # Strict decode (no errors="replace"): unlike read/grep, edit writes
            # the buffer back to disk, so a lossy decode would silently corrupt
            # the file. Refuse instead.
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(
                success=False,
                error=f"{path} is not valid UTF-8; edit refused to avoid corrupting it.",
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("edit read failed")
            return ToolResult(success=False, error=str(exc))

        count = text.count(old_string)
        if count == 0:
            return ToolResult(success=False, error=f"old_string not found in {path}.")
        if old_string == new_string:
            return ToolResult(
                success=False,
                error="old_string and new_string are identical; nothing to change.",
            )
        if count > 1 and not replace_all:
            return ToolResult(
                success=False,
                error=(
                    f"old_string matches {count} times in {path}; not unique. "
                    "Add surrounding context or set replace_all=true."
                ),
            )

        if replace_all:
            new_text = text.replace(old_string, new_string)
            replacements = count
        else:
            new_text = text.replace(old_string, new_string, 1)
            replacements = 1

        # P1-B: photograph the previous version before it is overwritten
        # (fail-open — a broken snapshot store never blocks the edit).
        capture_before_image(target, settings=self.settings)
        try:
            # Mirrors FileIOTool.write newline handling (platform-native on
            # write); read uses universal newlines so a model-supplied "\n"
            # old_string still matches CRLF files.
            target.write_text(new_text, encoding="utf-8")
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("edit write failed")
            return ToolResult(success=False, error=str(exc))

        logger.info("edit replaced %d occurrence(s) in %s", replacements, target)
        return ToolResult(
            success=True,
            data={
                "path": str(target),
                "replacements": replacements,
                "size": target.stat().st_size,
            },
        )


@register
class GlobTool(_SandboxedTool):
    name = "glob"
    description = (
        "Find files inside the sandbox whose path matches a glob `pattern` "
        "('**/*.py' recursive, '*.txt' top-level). Optionally scope to a "
        "sub-directory via `path`. Returns root-relative paths, sorted. This is "
        "the recursive-listing tool."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern; '**' spans directories.",
            },
            "path": {
                "type": "string",
                "description": "Optional sub-directory to search under (default root).",
            },
        },
        "required": ["pattern"],
    }

    def run(self, **kwargs: Any) -> ToolResult:
        pattern = str(kwargs.get("pattern", ""))
        if not pattern:
            return ToolResult(success=False, error="`pattern` is required.")
        sub = str(kwargs.get("path", "") or "")
        base = self._base_dir(sub)
        if base is None:
            return ToolResult(
                success=False,
                error=f"Path '{sub}' is outside the sandbox root and was rejected.",
            )
        root = self._root_resolved()
        found: set[str] = set()
        try:
            if base.exists():
                for p in base.glob(pattern):
                    if not p.is_file():
                        continue
                    rp = p.resolve()
                    if rp != root and root not in rp.parents:
                        continue  # symlink-escape guard
                    found.add(rp.relative_to(root).as_posix())
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("glob failed")
            return ToolResult(success=False, error=str(exc))

        matches = sorted(found)
        truncated = len(matches) > GLOB_MAX_MATCHES
        matches = matches[:GLOB_MAX_MATCHES]
        return ToolResult(
            success=True,
            data={
                "pattern": pattern,
                "matches": matches,
                "count": len(matches),
                "truncated": truncated,
            },
        )


@register
class GrepTool(_SandboxedTool):
    name = "grep"
    description = (
        "Search file contents inside the sandbox with a regular expression. "
        "Recursively scans files (optionally filtered by a filename `glob` and "
        "scoped to a sub-directory `path`), returning matching lines as "
        "{file, line, text}. Set `ignore_case` for case-insensitive matching."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regular expression to search for.",
            },
            "path": {
                "type": "string",
                "description": "Optional sub-directory to search under (default root).",
            },
            "glob": {
                "type": "string",
                "description": "Optional filename filter, e.g. '*.py'.",
            },
            "ignore_case": {
                "type": "boolean",
                "description": "Case-insensitive match (default false).",
            },
            "max_results": {
                "type": "integer",
                "description": "Max matching lines to return (default 100).",
            },
        },
        "required": ["pattern"],
    }

    def run(self, **kwargs: Any) -> ToolResult:
        pattern = kwargs.get("pattern", "")
        if not isinstance(pattern, str) or pattern == "":
            return ToolResult(success=False, error="`pattern` is required.")
        flags = re.IGNORECASE if bool(kwargs.get("ignore_case", False)) else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error as exc:
            return ToolResult(success=False, error=f"Invalid regex pattern: {exc}")

        sub = str(kwargs.get("path", "") or "")
        base = self._base_dir(sub)
        if base is None:
            return ToolResult(
                success=False,
                error=f"Path '{sub}' is outside the sandbox root and was rejected.",
            )
        name_filter = str(kwargs.get("glob", "") or "")
        max_results = _coerce_int(kwargs.get("max_results", GREP_MAX_RESULTS), GREP_MAX_RESULTS)
        if max_results <= 0:
            max_results = GREP_MAX_RESULTS

        root = self._root_resolved()
        matches: list[dict[str, Any]] = []
        files_matched: set[str] = set()
        truncated = False
        try:
            if base.exists():
                for p in sorted(base.rglob("*")):
                    if not p.is_file():
                        continue
                    rp = p.resolve()
                    if rp != root and root not in rp.parents:
                        continue  # symlink-escape guard
                    if name_filter and not fnmatch.fnmatch(rp.name, name_filter):
                        continue
                    try:
                        if rp.stat().st_size > GREP_MAX_FILE_BYTES:
                            continue
                        text = rp.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        continue  # unreadable / binary-ish file: skip, never crash
                    rel = rp.relative_to(root).as_posix()
                    for lineno, line in enumerate(text.splitlines(), start=1):
                        if not regex.search(line):
                            continue
                        if len(matches) >= max_results:
                            truncated = True
                            break
                        matches.append({"file": rel, "line": lineno, "text": line})
                        files_matched.add(rel)
                    if truncated:
                        break
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("grep failed")
            return ToolResult(success=False, error=str(exc))

        return ToolResult(
            success=True,
            data={
                "pattern": pattern,
                "matches": matches,
                "count": len(matches),
                "files_matched": len(files_matched),
                "truncated": truncated,
            },
        )
