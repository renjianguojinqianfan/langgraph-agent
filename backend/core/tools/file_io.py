"""File I/O tool with sandbox path restrictions.

All paths are confined to ``Settings.artifacts_path`` (the sandbox root). Reads
outside the root, writes that escape the root, and non-existent files are
rejected. Successful writes return the absolute path so the kernel can register
an :class:`~backend.services.persistence.Artifact`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from ...config import Settings
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
