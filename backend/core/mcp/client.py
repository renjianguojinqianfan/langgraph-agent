"""MCP client manager — stdio transport, one thread + event loop per server.

P2 item 1. The official ``mcp`` Python SDK exposes an *async* API
(``stdio_client`` / ``ClientSession``) while :class:`BaseTool.run` is a
*synchronous* method. To bridge the two:

* every enabled server gets its own dedicated thread running an independent
  ``asyncio`` event loop (``_McpSession``);
* the sync side submits coroutines with
  ``asyncio.run_coroutine_threadsafe(...).result(timeout)`` so a slow MCP call
  blocks only the tool worker, never the event loop.

Lifecycle: ``connect_all()`` is invoked once from ``TaskManager.__init__``
(startup). ``cleanup()`` is idempotent and terminates every child process;
``main.py`` calls ``TaskManager.shutdown()`` on lifespan exit so no MCP child
process survives the app.

Failure isolation: a single bad server (missing command, protocol error,
timeout) only marks that server ``error`` and logs a warning — startup always
continues (PRD 1.2 / 1.5 acceptance).
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from ...config import Settings
from ...utils.logging import get_logger

logger = get_logger("mcp.client")

#: Default JSON-Schema fragment used when a server omits an ``inputSchema``.
_EMPTY_SCHEMA: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}


class McpServerConfig(BaseModel):
    """One entry of the ``mcp_servers`` JSON array."""

    name: str
    command: str
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    cwd: Optional[str] = None
    transport: str = "stdio"  # stdio (implemented) | http (reserved)
    url: Optional[str] = None  # reserved for Streamable HTTP

    @field_validator("name")
    @classmethod
    def _name_not_empty(cls, v: str) -> str:
        if not v or not str(v).strip():
            raise ValueError("MCP server `name` is required")
        return str(v).strip()

    @field_validator("command")
    @classmethod
    def _command_not_empty(cls, v: str) -> str:
        if not v or not str(v).strip():
            raise ValueError("MCP server `command` is required")
        return str(v).strip()


class McpServerStatus:
    """Runtime status of one MCP server (name, transport, state, tools)."""

    def __init__(
        self,
        name: str,
        transport: str = "stdio",
        status: str = "disabled",
        tools_count: int = 0,
        error: Optional[str] = None,
    ) -> None:
        self.name = name
        self.transport = transport
        self.status = status  # connected | error | disabled | closed
        self.tools_count = tools_count
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "transport": self.transport,
            "status": self.status,
            "tools_count": self.tools_count,
            "error": self.error,
        }


class _McpSession:
    """One stdio MCP session running on its own thread + event loop.

    The session thread runs ``asyncio.new_event_loop().run_forever()`` and
    performs the async SDK calls *inside* that loop. The sync side bridges with
    :func:`asyncio.run_coroutine_threadsafe`.
    """

    def __init__(self, cfg: McpServerConfig, connect_timeout: float) -> None:
        self.cfg = cfg
        self.connect_timeout = connect_timeout
        self.loop: Optional[Any] = None
        self.thread: Optional[threading.Thread] = None
        self._session: Any = None
        self._stdio: Any = None
        self._closed = False
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._start_error: Optional[str] = None

    # ── lifecycle ──
    def start(self) -> bool:
        """Start the dedicated thread + event loop. Returns True on success."""
        if self.thread is not None and self.thread.is_alive():
            return True
        self._closed = False
        self.thread = threading.Thread(
            target=self._run_loop, name=f"mcp-{self.cfg.name}", daemon=True
        )
        self.thread.start()
        # Wait until the loop is ready or the thread died.
        if not self._ready.wait(self.connect_timeout):
            self._start_error = self._start_error or "MCP session thread did not start"
            return False
        return self._start_error is None

    def _run_loop(self) -> None:
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.loop = loop
        try:
            loop.run_until_complete(self._connect())
        except Exception as exc:  # pragma: no cover - defensive
            self._start_error = str(exc)
            logger.warning("MCP session %s failed to connect: %s", self.cfg.name, exc)
        if self._start_error is not None:
            # Best-effort cleanup of a partially established connection so a
            # failed connect never leaks a stdio child process.
            try:
                async def _abort() -> None:
                    if self._session is not None:
                        await self._session.__aexit__(None, None, None)
                        self._session = None
                    if self._stdio is not None:
                        await self._stdio.__aexit__(None, None, None)
                        self._stdio = None

                loop.run_until_complete(_abort())
            except Exception:  # pragma: no cover - defensive
                pass
            self._ready.set()
            self._shutdown_loop(loop)
            return
        # Keep the loop alive so later run_coroutine_threadsafe submissions
        # (list_tools / call_tool) can execute on it. Readiness is announced
        # only once the loop is actually running (first scheduled callback) so
        # the caller's submissions never race the loop startup.
        loop.call_soon(self._ready.set)
        try:
            loop.run_forever()
        finally:
            self._shutdown_loop(loop)

    @staticmethod
    def _shutdown_loop(loop: Any) -> None:
        """Cancel pending tasks and close a loop that is no longer running."""
        import asyncio

        try:
            pending = asyncio.all_tasks(loop)
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
        except Exception:  # pragma: no cover - defensive
            pass
        try:
            loop.close()
        except Exception:  # pragma: no cover - defensive
            pass

    async def _connect(self) -> None:
        import asyncio

        import mcp
        from mcp.client.stdio import StdioServerParameters, stdio_client

        # Merge the process environment so commands like `npx` / `node` resolve.
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in self.cfg.env.items()})

        params = StdioServerParameters(
            command=self.cfg.command,
            args=list(self.cfg.args),
            env=env,
            cwd=self.cfg.cwd,
        )
        # self.cfg.transport is validated as "stdio" only; anything else is
        # treated as reserved/unimplemented and skipped at the manager level.
        self._stdio = stdio_client(params)
        read_stream, write_stream = await self._stdio.__aenter__()

        from mcp import ClientSession

        session = ClientSession(read_stream, write_stream)
        self._session = session
        await session.__aenter__()
        init = await asyncio.wait_for(session.initialize(), timeout=self.connect_timeout)
        logger.info(
            "MCP server %s connected (protocol %s)",
            self.cfg.name,
            getattr(init, "protocolVersion", "?"),
        )

    def _ensure_loop_running(self) -> None:
        """Raise when the session loop is not ready to accept work."""
        if self.loop is None or not self.loop.is_running():
            raise RuntimeError(f"MCP session {self.cfg.name} is not running")

    def _submit(self, coro_factory, timeout: float):
        """Run a coroutine (built by ``coro_factory``) on the session loop."""
        import asyncio
        import concurrent.futures

        self._ensure_loop_running()
        fut = asyncio.run_coroutine_threadsafe(coro_factory(), self.loop)
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(f"MCP call timed out after {timeout}s")
        except Exception:
            raise

    # ── public ops (sync, called from tool workers) ──
    def initialize_list_tools(self, timeout: float) -> List[Dict[str, Any]]:
        """Initialize + list_tools; returns the raw tool list."""

        async def _list() -> List[Dict[str, Any]]:
            if self._session is None:
                raise RuntimeError("session not initialized")
            result = await self._session.list_tools()
            return [t.model_dump() for t in result.tools]

        return self._submit(_list, timeout)

    def call_tool(self, name: str, arguments: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        """Forward a tool call; returns the raw CallToolResult-like dict."""

        async def _call() -> Dict[str, Any]:
            if self._session is None:
                raise RuntimeError("session not initialized")
            result = await self._session.call_tool(name, arguments or {})
            return result.model_dump()

        return self._submit(_call, timeout)

    def close(self) -> None:
        """Close the session loop-side, terminate the child, join the thread."""
        import asyncio

        if self._closed:
            return
        self._closed = True

        # Close the SDK session / stdio transport inside the event loop.
        if self.loop is not None and self.loop.is_running():
            async def _close() -> None:
                try:
                    if self._session is not None:
                        await self._session.__aexit__(None, None, None)
                        self._session = None
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("MCP session %s close error: %s", self.cfg.name, exc)
                try:
                    if self._stdio is not None:
                        await self._stdio.__aexit__(None, None, None)
                        self._stdio = None
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("MCP stdio %s close error: %s", self.cfg.name, exc)

            try:
                fut = asyncio.run_coroutine_threadsafe(_close(), self.loop)
                fut.result(timeout=self.connect_timeout)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("MCP session %s graceful close failed: %s", self.cfg.name, exc)
            try:
                self.loop.call_soon_threadsafe(self.loop.stop)
            except Exception:  # pragma: no cover - defensive
                pass

        # The SDK stdio_client manages and terminates the stdio child process
        # itself (in its async context-manager exit above); no extra fallback
        # termination is needed here.

        if self.thread is not None and self.thread.is_alive():
            try:
                self.thread.join(timeout=self.connect_timeout)
            except Exception:  # pragma: no cover - defensive
                pass


class McpClientManager:
    """Owns the configured MCP sessions and exposes sync call helpers."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sessions: Dict[str, _McpSession] = {}
        self._status: Dict[str, McpServerStatus] = {}
        self._closed = False
        self._lock = threading.Lock()

    # ── SDK import convergence (fallback reserved; see incremental-arch §1) ──
    @staticmethod
    def _import_sdk() -> Any:
        """Import the ``mcp`` SDK lazily.

        Converging the import here means a missing SDK can be detected once and
        handled gracefully (the manager simply reports no tools) instead of
        crashing startup — the documented fallback is a stdlib JSON-RPC
        implementation, which is out of scope for P2.
        """
        try:
            import mcp  # noqa: F401

            return mcp
        except Exception as exc:  # pragma: no cover - defensive
            raise ImportError(f"MCP SDK not available: {exc}") from exc

    def _parse_configs(self) -> List[McpServerConfig]:
        raw = self._settings.mcp_servers_list
        configs: List[McpServerConfig] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                cfg = McpServerConfig(**item)
                configs.append(cfg)
            except ValidationError as exc:
                logger.warning("Invalid MCP server config %r skipped: %s", item, exc)
        return configs

    def connect_all(self) -> List[Dict[str, Any]]:
        """Connect every enabled server and return the discovered tool list.

        Each tool dict has ``{server, name, description, input_schema}`` ready
        for :class:`~backend.core.tools.mcp_tool.McpTool` construction. A
        failing server is recorded as ``error`` and skipped (startup continues).
        """
        if not self._settings.mcp_enabled:
            logger.info("mcp_enabled=false; skipping MCP connection.")
            return []
        try:
            self._import_sdk()
        except ImportError as exc:
            logger.warning("MCP tools disabled: %s", exc)
            return []

        configs = self._parse_configs()
        tools: List[Dict[str, Any]] = []
        with self._lock:
            for cfg in configs:
                if not cfg.enabled:
                    self._status[cfg.name] = McpServerStatus(
                        name=cfg.name, transport=cfg.transport, status="disabled"
                    )
                    continue
                if cfg.transport != "stdio":
                    self._status[cfg.name] = McpServerStatus(
                        name=cfg.name,
                        transport=cfg.transport,
                        status="error",
                        error=f"transport {cfg.transport!r} not implemented (stdio only)",
                    )
                    logger.warning(
                        "MCP server %s uses unimplemented transport %r; skipping.",
                        cfg.name,
                        cfg.transport,
                    )
                    continue
                session = _McpSession(cfg, self._settings.mcp_connect_timeout_sec)
                if not session.start():
                    self._status[cfg.name] = McpServerStatus(
                        name=cfg.name,
                        transport=cfg.transport,
                        status="error",
                        error=session._start_error or "failed to start session",
                    )
                    logger.warning("MCP server %s failed to start: %s", cfg.name, session._start_error)
                    continue
                try:
                    raw_tools = session.initialize_list_tools(self._settings.mcp_connect_timeout_sec)
                except Exception as exc:
                    self._status[cfg.name] = McpServerStatus(
                        name=cfg.name,
                        transport=cfg.transport,
                        status="error",
                        error=str(exc),
                    )
                    logger.warning("MCP server %s list_tools failed: %s", cfg.name, exc)
                    continue
                self._sessions[cfg.name] = session
                self._status[cfg.name] = McpServerStatus(
                    name=cfg.name,
                    transport=cfg.transport,
                    status="connected",
                    tools_count=len(raw_tools),
                )
                logger.info("MCP server %s connected with %d tool(s).", cfg.name, len(raw_tools))
                for t in raw_tools:
                    tools.append(
                        {
                            "server": cfg.name,
                            "name": str(t.get("name") or ""),
                            "description": str(t.get("description") or ""),
                            "input_schema": t.get("inputSchema") or _EMPTY_SCHEMA,
                        }
                    )
        return tools

    def call_tool(
        self,
        server: str,
        tool: str,
        arguments: Dict[str, Any],
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Forward a call to ``server.tool``; returns the raw result dict.

        Raises on missing session / SDK errors / timeout — callers (McpTool)
        convert everything into a ``ToolResult(success=False)``.
        """
        session = self._sessions.get(server)
        if session is None:
            raise RuntimeError(f"MCP server {server!r} is not connected")
        if self._closed:
            raise RuntimeError("MCP client is closed")
        effective = timeout if timeout is not None else self._settings.mcp_timeout_sec
        return session.call_tool(tool, arguments, effective)

    def status_list(self) -> List[McpServerStatus]:
        """Return current status for every configured server."""
        configs = self._parse_configs()
        seen: Dict[str, McpServerStatus] = {}
        for cfg in configs:
            st = self._status.get(cfg.name)
            if st is None:
                st = McpServerStatus(name=cfg.name, transport=cfg.transport, status="disabled")
            seen[cfg.name] = st
        return list(seen.values())

    def cleanup(self) -> None:
        """Idempotently close every session and mark the manager closed."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for s in sessions:
            s.close()
            self._status[s.cfg.name] = McpServerStatus(
                name=s.cfg.name, transport=s.cfg.transport, status="closed"
            )
        logger.info("MCP client cleaned up %d session(s).", len(sessions))
