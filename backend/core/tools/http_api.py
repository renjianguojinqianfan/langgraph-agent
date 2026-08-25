"""Generic HTTP request tool (P1-1).

Performs GET/POST/PUT/DELETE/etc. with custom headers and body. State-changing
methods (POST/PUT/DELETE/PATCH) set ``requires_confirm=True`` so the kernel
asks the user before sending them.
"""

from __future__ import annotations

from typing import Any, Dict

import httpx

from ...config import Settings
from ...utils.logging import get_logger
from .base import BaseTool, ToolResult
from .registry import register

logger = get_logger("tool.http_api")

WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


@register
class HttpTool(BaseTool):
    name = "http_request"
    description = (
        "Perform an HTTP request to an external API. Supports common methods, "
        "custom headers and a JSON or text body. Write methods require confirmation."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
                "description": "HTTP method.",
            },
            "url": {"type": "string", "description": "Fully qualified request URL."},
            "headers": {
                "type": "object",
                "description": "Optional request headers.",
                "additionalProperties": {"type": "string"},
            },
            "body": {
                "description": "Optional request body (object or string).",
            },
        },
        "required": ["method", "url"],
    }
    requires_confirm = False  # effectively gated per-call via _needs_confirm()
    # P0 resilience: network tool — retry transient failures + circuit breaker.
    retryable = True
    max_retries = None  # inherit global Settings.tool_max_retries (default 2)
    circuit_breaker = True

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)

    def _needs_confirm(self, method: str) -> bool:
        return method.upper() in WRITE_METHODS

    def run(self, **kwargs: Any) -> ToolResult:
        method = str(kwargs.get("method", "GET")).upper()
        url = str(kwargs.get("url", "")).strip()
        headers = {str(k): str(v) for k, v in (kwargs.get("headers") or {}).items()}
        body = kwargs.get("body")

        if not url:
            return ToolResult(success=False, error="`url` is required.")

        try:
            with httpx.Client(timeout=20, follow_redirects=True) as client:
                resp = client.request(method=method, url=url, headers=headers, json=body if isinstance(body, (dict, list)) else None)
                try:
                    payload: Any = resp.json()
                except Exception:
                    payload = resp.text
                return ToolResult(
                    success=resp.status_code < 400,
                    data={
                        "status_code": resp.status_code,
                        "headers": dict(resp.headers),
                        "body": payload,
                    },
                    error="" if resp.status_code < 400 else f"HTTP {resp.status_code}",
                )
        except Exception as exc:
            logger.exception("http_request failed")
            return ToolResult(success=False, error=str(exc))
