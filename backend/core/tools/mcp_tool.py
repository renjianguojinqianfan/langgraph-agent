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
* write-like tools (heuristic on name/description + ``mcp_force_confirm``
  override) set ``needs_per_call_confirm=True`` so the executor performs a
  per-call confirmation via the existing P0 ``human_confirm`` flow.

Resilience: ``McpTool`` is a standard :class:`BaseTool` subclass — the kernel's
``ToolExecutor.dispatch`` automatically applies the P0 circuit breaker + retry
(``resilience.py`` is untouched). Write-like tools disable retry at the
instance level to avoid repeating side effects after a user confirmation.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ...config import Settings
from ...utils.logging import get_logger
from .base import BaseTool, ToolResult

logger = get_logger("tool.mcp")

#: Write-like verbs (matched against the MCP tool name / description).
_WRITE_VERBS = (
    "write", "create", "delete", "update", "edit", "insert", "remove",
    "send", "push", "upload", "execute", "add", "set", "put", "post",
    "patch", "modify", "rename", "move", "copy", "append", "clear",
    "reset", "format", "drop", "truncate",
)

_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]")


def sanitize_name(value: str) -> str:
    """Replace characters outside ``[A-Za-z0-9_]`` with ``_``."""
    return _SANITIZE_RE.sub("_", str(value or ""))


class McpTool(BaseTool):
    """A BaseTool that forwards execution to one MCP server tool."""

    #: Duck-typed marker read by ``executor`` to run the per-call confirm
    #: judgement (``nodes.py`` P2 branch). Not part of BaseTool.
    needs_per_call_confirm = True

    def __init__(
        self,
        *,
        server_name: str,
        tool_name: str,
        description: str,
        input_schema: Dict[str, Any],
        manager: Any,
        settings: Optional[Settings] = None,
    ) -> None:
        super().__init__(settings)
        self._server_name = server_name
        self._tool_name = tool_name
        self.name = f"mcp__{sanitize_name(server_name)}__{sanitize_name(tool_name)}"
        self.description = description or f"MCP tool {server_name}.{tool_name}"
        self.args_schema = self._normalise_schema(input_schema)
        self._manager = manager
        #: A pre-resolved per-call confirmation decision for an empty argument
        #: set — write-like tools must be confirmed even with no args.
        self._write_like = self._heuristic_write_like()

        if self._write_like:
            # Write-like: after confirmation execute exactly once — never retry
            # (would repeat side effects). The circuit breaker is preserved so
            # consecutive failures still open the tool circuit (PRD 1.5).
            self.retryable = False
            self.max_retries = 0
            self.circuit_breaker = True
        else:
            # Read-like: keep the BaseTool defaults (retry + breaker) so
            # transient network failures are retried by the existing executor.
            self.retryable = True
            self.max_retries = None
            self.circuit_breaker = True

    # ── schema / heuristic helpers ──
    @staticmethod
    def _normalise_schema(input_schema: Any) -> Dict[str, Any]:
        if isinstance(input_schema, dict) and input_schema.get("type") == "object":
            schema = dict(input_schema)
            schema.setdefault("properties", {})
            schema.setdefault("required", [])
            return schema
        return {"type": "object", "properties": {}, "required": []}

    def _heuristic_write_like(self) -> bool:
        """True when the tool name/description suggests a state-changing op."""
        hay = f"{self._tool_name} {self.description}".lower()
        return any(v in hay for v in _WRITE_VERBS)

    def _needs_confirm(self, args: Dict[str, Any]) -> bool:
        """Per-call confirmation judgement.

        * ``mcp_force_confirm`` (settings) lists the full tool name
          (``mcp__{server}__{tool}``) -> always True;
        * otherwise the write-like heuristic decides.

        The executor calls this only when ``needs_per_call_confirm`` is set and
        wraps the call in a try/except — a judgement failure never blocks
        execution.
        """
        force = getattr(getattr(self, "settings", None), "mcp_force_confirm_list", None) or []
        if self.name in force:
            return True
        return self._write_like

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
            f"tool={self._tool_name!r} write_like={self._write_like}>"
        )
