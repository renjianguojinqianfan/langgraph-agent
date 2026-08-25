"""Offline MCP echo server used by the P2 MCP tests (``test_p2_mcp.py``).

A minimal Model Context Protocol server over stdio exposing two tools:

* ``echo(text)``       -> ``TextContent("echo:<text>")``   (read-like)
* ``write_file(...)``  -> ``TextContent("wrote:<path>")``  (write-like, used by
  the per-call confirmation tests)

The test suite launches this file with ``sys.executable`` (an absolute path, so
it works on Windows without resolving ``python`` from PATH).
"""

from __future__ import annotations

import os
from pathlib import Path

import anyio

from mcp.server.lowlevel import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import ServerCapabilities, TextContent, Tool

server = Server("echo-server")

# Optional: when ECHO_PID_FILE is set, write this process's PID so tests can
# kill the server to exercise failure handling.
_pid_file = os.environ.get("ECHO_PID_FILE")
if _pid_file:
    try:
        Path(_pid_file).write_text(str(os.getpid()), encoding="utf-8")
    except Exception:  # pragma: no cover - test helper
        pass


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="echo",
            description="Echo the given text back.",
            inputSchema={
                "type": "object",
                "properties": {"text": {"type": "string", "description": "Text to echo."}},
                "required": ["text"],
            },
        ),
        Tool(
            name="write_file",
            description="Write a file (write-like, requires confirmation).",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Target path."},
                    "content": {"type": "string", "description": "File content."},
                },
                "required": ["path", "content"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "echo":
        text = str(arguments.get("text", ""))
        return [TextContent(type="text", text=f"echo:{text}")]
    if name == "write_file":
        return [TextContent(type="text", text=f"wrote:{arguments.get('path', '')}")]
    raise ValueError(f"unknown tool {name}")


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="echo-server",
                server_version="0.1.0",
                capabilities=ServerCapabilities(tools={}),
            ),
        )


if __name__ == "__main__":
    anyio.run(main)
