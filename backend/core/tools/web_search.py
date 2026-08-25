"""Web search tool (pluggable provider).

Default provider is DuckDuckGo (free, no key). A SerpAPI provider is supported
when ``search_provider=serpapi`` and ``serpapi_key`` is configured. The tool is
network-dependent; the kernel still runs offline for tasks that do not need it.
"""

from __future__ import annotations

from typing import Any, Dict

from ...config import Settings
from ...utils.logging import get_logger
from .base import BaseTool, ToolResult
from .registry import register

logger = get_logger("tool.web_search")


@register
class WebSearchTool(BaseTool):
    name = "web_search"
    description = (
        "Search the web for a query and return a list of results with title, "
        "snippet and URL. Use this to gather up-to-date information."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query."},
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return.",
                "default": 5,
            },
        },
        "required": ["query"],
    }
    requires_confirm = False
    # P0 resilience: network tool — retry transient failures + circuit breaker.
    retryable = True
    max_retries = None  # inherit global Settings.tool_max_retries (default 2)
    circuit_breaker = True

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self._provider = (settings.search_provider if settings else "duckduckgo").lower()
        self._serpapi_key = settings.serpapi_key if settings else ""

    def _search_duckduckgo(self, query: str, max_results: int) -> list[Dict[str, Any]]:
        try:
            from duckduckgo_search import DDGS  # type: ignore
        except ImportError:
            return [{
                "title": "DuckDuckGo unavailable",
                "snippet": "Install `duckduckgo-search` to enable web search.",
                "url": "",
            }]
        results: list[Dict[str, Any]] = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                    "url": r.get("href", ""),
                })
        return results

    def _search_serpapi(self, query: str, max_results: int) -> list[Dict[str, Any]]:
        if not self._serpapi_key:
            return [{"title": "SerpAPI unavailable", "snippet": "serpapi_key not set.", "url": ""}]
        try:
            import urllib.parse

            import httpx

            resp = httpx.get(
                "https://serpapi.com/search",
                params={
                    "engine": "google",
                    "q": query,
                    "num": max_results,
                    "api_key": self._serpapi_key,
                },
                timeout=20,
            )
            data = resp.json()
            return [
                {"title": o.get("title", ""), "snippet": o.get("snippet", ""), "url": o.get("link", "")}
                for o in data.get("organic_results", [])
            ]
        except Exception as exc:  # pragma: no cover - network dependent
            logger.warning("SerpAPI search failed: %s", exc)
            return [{"title": "SerpAPI error", "snippet": str(exc), "url": ""}]

    def run(self, **kwargs: Any) -> ToolResult:
        query = str(kwargs.get("query", "")).strip()
        max_results = int(kwargs.get("max_results", 5) or 5)
        if not query:
            return ToolResult(success=False, error="`query` is required.")
        try:
            if self._provider == "serpapi":
                results = self._search_serpapi(query, max_results)
            else:
                results = self._search_duckduckgo(query, max_results)
            return ToolResult(success=True, data={"query": query, "results": results})
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("web_search failed")
            return ToolResult(success=False, error=str(exc))
