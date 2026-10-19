"""MCP-backed BaseTool wrapper (P2 item 1).

Every tool exposed by a connected MCP server becomes a :class:`McpTool`
instance registered into the kernel tool set:

* ``name = mcp__{server}__{tool}`` (Claude Code style — cross-server conflicts
  are naturally avoided; ``/``, ``-`` and spaces in server/tool names are
  sanitised to ``_``);
* ``args_schema`` adopts the server's ``inputSchema`` directly (it already is
  JSON Schema — compatible with :meth:`BaseTool.to_openai_schema`);
* ``run()`` forwards to ``McpClientManager.call_tool`` and maps the result to a
  :class:`ToolResult` with ``data={server, tool, content, text, structured}``;
* danger is whatever the **server self-reports** in the MCP tool annotations
  (``destructiveHint`` / ``readOnlyHint``); a tool that says nothing reads as
  ``unknown`` rather than safe (issue #57). #138 (slice S4) retired the
  confirmation gate, so this classification now survives only as the inert
  :attr:`McpTool.danger_signal` attribute — nothing consumes it for a verdict.

Issue #57 replaced a verb table here (name/description matched against
``write``/``delete``/``send``/...). That was a guess sitting next to a field the
protocol already fills in, and it could only guess at *absence* — a tool named
``db_run_sql`` looked read-like to the table. The self-report is now the only
signal, and "no self-report" reads as ``unknown`` rather than safe. Per the
settled boundary the annotation was never a security boundary (that is the
sub-agent narrowing and isolation side).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ...config import Settings
from ...utils.logging import get_logger
from .base import BaseTool, ToolResult

logger = get_logger("tool.mcp")

#: The server says this tool changes state.
DANGER_DANGEROUS = "dangerous"
#: The server positively said this tool is read-only / non-destructive.
DANGER_SAFE = "safe"
#: The server said nothing usable — NOT read as safe.
DANGER_UNKNOWN = "unknown"

_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]")


def sanitize_name(value: str) -> str:
    """Replace characters outside ``[A-Za-z0-9_]`` with ``_``."""
    return _SANITIZE_RE.sub("_", str(value or ""))


def classify_annotations(annotations: Any) -> str:
    """Read the server's self-reported hints into a three-state danger signal.

    * ``destructiveHint is True``  -> :data:`DANGER_DANGEROUS`;
    * ``destructiveHint is False`` or ``readOnlyHint is True`` -> :data:`DANGER_SAFE`
      — a *positive* safety claim, not the absence of a danger claim;
    * anything else (neither hint given, only ``readOnlyHint=False`` which says
      "not read-only" without saying "destructive", a non-bool value, a
      non-dict payload) -> :data:`DANGER_UNKNOWN`.

    ``is True`` / ``is False`` on purpose: a dirty ``0`` / ``"yes"`` must not be
    read as a settled bool.

    #138 (slice S4): this used to answer the confirmation gate's "bother a
    human?" question. The gate is gone; the signal is kept as inert
    classification metadata.
    """
    if not isinstance(annotations, dict):
        return DANGER_UNKNOWN
    destructive = annotations.get("destructiveHint")
    read_only = annotations.get("readOnlyHint")
    if destructive is True:
        return DANGER_DANGEROUS
    if destructive is False or read_only is True:
        return DANGER_SAFE
    return DANGER_UNKNOWN


class McpTool(BaseTool):
    """A BaseTool that forwards execution to one MCP server tool."""

    def __init__(
        self,
        *,
        server_name: str,
        tool_name: str,
        description: str,
        input_schema: Dict[str, Any],
        manager: Any,
        settings: Optional[Settings] = None,
        annotations: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(settings)
        self._server_name = server_name
        self._tool_name = tool_name
        self.name = f"mcp__{sanitize_name(server_name)}__{sanitize_name(tool_name)}"
        self.description = description or f"MCP tool {server_name}.{tool_name}"
        self.args_schema = self._normalise_schema(input_schema)
        self._manager = manager
        #: The server's own verdict (#57). ``unknown`` — i.e. the server said
        #: nothing — is NOT read as safe. Inert since #138 removed the gate.
        self.danger_signal: str = classify_annotations(annotations)

    # ── schema helpers ──
    @staticmethod
    def _normalise_schema(input_schema: Any) -> Dict[str, Any]:
        if isinstance(input_schema, dict) and input_schema.get("type") == "object":
            schema = dict(input_schema)
            schema.setdefault("properties", {})
            schema.setdefault("required", [])
            return schema
        return {"type": "object", "properties": {}, "required": []}

    # ── run ──
    def run(self, **kwargs: Any) -> ToolResult:
        try:
            raw = self._manager.call_tool(self._server_name, self._tool_name, kwargs)
        except TimeoutError as exc:
            logger.warning("MCP tool %s timed out: %s", self.name, exc)
            return ToolResult(success=False, error=str(exc))
        except Exception as exc:
            logger.warning("MCP tool %s failed: %s", self.name, exc)
            return ToolResult(success=False, error=str(exc))

        content = raw.get("content") or []
        text_parts: List[str] = []
        structured: List[Any] = []
        for c in content:
            ctype = c.get("type") if isinstance(c, dict) else getattr(c, "type", None)
            if ctype == "text":
                text_parts.append(c.get("text", "") if isinstance(c, dict) else str(c))
            elif ctype == "structured":
                structured.append(c.get("structured") if isinstance(c, dict) else c)
        is_error = bool(raw.get("isError", False))
        data: Dict[str, Any] = {
            "server": self._server_name,
            "tool": self._tool_name,
            "content": content,
            "text": "\n".join(text_parts),
            "structured": structured,
        }
        return ToolResult(
            success=not is_error,
            data=data,
            error="" if not is_error else "mcp error (isError=true)",
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<McpTool name={self.name!r} server={self._server_name!r} "
            f"tool={self._tool_name!r} danger={self.danger_signal}>"
        )
