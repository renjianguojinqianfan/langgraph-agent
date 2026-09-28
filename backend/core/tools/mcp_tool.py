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
* danger is what the **server self-reports** in the MCP tool annotations
  (``destructiveHint`` / ``readOnlyHint``), with ``mcp_force_confirm`` as the
  operator-level override; tools that set nothing are asked about rather than
  waved through (issue #57). The verdict sets ``needs_per_call_confirm=True`` so
  the executor performs a per-call confirmation via the existing P0
  ``human_confirm`` flow.

Issue #57 replaced a verb table here (name/description matched against
``write``/``delete``/``send``/...). That was a guess sitting next to a field the
protocol already fills in, and it could only guess at *absence* — a tool named
``db_run_sql`` looked read-like to the table. The self-report is now the only
signal, and "no self-report" asks instead of passing. Per the settled boundary,
the annotation decides **whether to bother a human**, nothing else: it is not a
security boundary (that is the sub-agent narrowing and isolation side).

Resilience: ``McpTool`` is a standard :class:`BaseTool` subclass — the kernel's
``ToolExecutor.dispatch`` automatically applies the P0 circuit breaker + retry
(``resilience.py`` is untouched). Anything not known to be read-only disables
retry at the instance level to avoid repeating side effects after a user
confirmation.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ...config import Settings
from ...utils.logging import get_logger
from .base import BaseTool, ToolResult

logger = get_logger("tool.mcp")

#: The server says this tool changes state (or the override says so).
DANGER_DANGEROUS = "dangerous"
#: The server positively said this tool is read-only / non-destructive.
DANGER_SAFE = "safe"
#: The server said nothing usable — treated the same as ``dangerous`` by the gate.
DANGER_UNKNOWN = "unknown"

_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]")

#: Prefix of a ``mcp_force_confirm`` entry that says "never bother a human".
MUTE_PREFIX = "!"


def sanitize_name(value: str) -> str:
    """Replace characters outside ``[A-Za-z0-9_]`` with ``_``."""
    return _SANITIZE_RE.sub("_", str(value or ""))


def _override_verdict(
    entries: List[str], full_name: str, server_prefix: str
) -> Optional[bool]:
    """What ``mcp_force_confirm`` says about this tool (``None`` = says nothing).

    Entry shapes (#57 AC3 — one config key, no per-tool registry UI):

    * ``mcp__{server}__{tool}`` — that one tool (the P2 shape, unchanged);
    * ``mcp__{server}__*``      — **every** tool of that server;
    * either shape prefixed with ``!`` — the opposite verdict (免问).

    An exact-name entry wins over a server wildcard no matter what order the
    operator wrote them in, so ``["!mcp__echo__*", "mcp__echo__purge"]`` mutes
    the server and still asks for ``purge``. Unparseable entries (blank, a
    non-string coerced to something that matches nothing) simply do not match —
    the annotation then decides, exactly as if the list were empty.
    """
    wanted = [str(e).strip() for e in entries]
    for shape in (full_name, f"{server_prefix}*"):
        if shape in wanted:
            return True
        if f"{MUTE_PREFIX}{shape}" in wanted:
            return False
    return None


def classify_annotations(annotations: Any) -> str:
    """Read the server's self-reported hints into a three-state danger signal.

    * ``destructiveHint is True``  -> :data:`DANGER_DANGEROUS`;
    * ``destructiveHint is False`` or ``readOnlyHint is True`` -> :data:`DANGER_SAFE`
      — a *positive* safety claim, not the absence of a danger claim;
    * anything else (neither hint given, only ``readOnlyHint=False`` which says
      "not read-only" without saying "destructive", a non-bool value, a
      non-dict payload) -> :data:`DANGER_UNKNOWN`, which the gate asks about.

    ``is True`` / ``is False`` on purpose: a dirty ``0`` / ``"yes"`` must not be
    read as a settled bool.

    This is the **gate's** view (whether to bother a human). Retry policy does not
    read it — see :func:`_read_only_claim`.
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


def _read_only_claim(annotations: Any) -> bool:
    """Whether the server positively says this tool is read-only.

    Retry policy keys off this rather than off :func:`classify_annotations`,
    because the two questions are different: ``destructiveHint=False`` is the
    protocol's words for an **append-only write** — the gate may skip the human
    for it, but retrying it would repeat a side effect. README's
    「写类工具 ``retryable=False``」 is the invariant being protected here.
    """
    return isinstance(annotations, dict) and annotations.get("readOnlyHint") is True


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
        #: nothing — is NOT read as safe.
        self.danger_signal: str = classify_annotations(annotations)

        if _read_only_claim(annotations):
            # Read-only by the server's own words: keep the BaseTool defaults
            # (retry + breaker) so transient network failures are retried by
            # the existing executor.
            self.retryable = True
            self.max_retries = None
            self.circuit_breaker = True
        else:
            # Dangerous, unreported, **or an append-only write**
            # (``destructiveHint=False``): after confirmation execute exactly
            # once — never retry (would repeat side effects). The circuit breaker
            # is preserved so consecutive failures still open the tool circuit
            # (PRD 1.5).
            self.retryable = False
            self.max_retries = 0
            self.circuit_breaker = True

    # ── schema / judgement helpers ──
    @staticmethod
    def _normalise_schema(input_schema: Any) -> Dict[str, Any]:
        if isinstance(input_schema, dict) and input_schema.get("type") == "object":
            schema = dict(input_schema)
            schema.setdefault("properties", {})
            schema.setdefault("required", [])
            return schema
        return {"type": "object", "properties": {}, "required": []}

    def _needs_confirm(self, args: Dict[str, Any]) -> bool:
        """Per-call confirmation judgement.

        * ``mcp_force_confirm`` (settings) first — exact tool name or the
          ``mcp__{server}__*`` server wildcard, either optionally muted with
          ``!`` (#57 AC3);
        * otherwise the server's self-reported annotation decides — only a
          positive safety claim (:data:`DANGER_SAFE`) stays quiet, and both
          ``dangerous`` and ``unknown`` ask (#57 AC2: 不再默认放行).

        The executor calls this only when ``needs_per_call_confirm`` is set and
        wraps the call in a try/except — a judgement failure fails **closed**
        (the executor requires confirmation), never leaking an ungated call
        through (#47).
        """
        settings = getattr(self, "settings", None)
        entries = getattr(settings, "mcp_force_confirm_list", None) or []
        verdict = _override_verdict(
            list(entries), self.name, f"mcp__{sanitize_name(self._server_name)}__"
        )
        if verdict is not None:
            return verdict
        return self.danger_signal != DANGER_SAFE

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
