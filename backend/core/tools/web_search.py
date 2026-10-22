"""Web search tool (pluggable provider).

Default provider is DuckDuckGo (free, no key); ``serpapi`` uses
``serpapi_key`` and ``exa`` uses ``EXA_API_KEY`` from the environment. Exa is
the practical choice when DuckDuckGo is unreachable (e.g. mainland China).
The tool is network-dependent; the kernel still runs offline for tasks that
do not need it.
"""

from __future__ import annotations

from typing import Any, Dict

from ...config import Settings
from ...utils.logging import get_logger
from .base import BaseTool, ToolResult
from .registry import register

logger = get_logger("tool.web_search")

#: Characters requested from Exa's page text, and the snippet cap we return.
_EXA_TEXT_CHARS = 1000
_EXA_SNIPPET_CHARS = 500


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
    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self._provider = (settings.search_provider if settings else "duckduckgo").lower()
        self._serpapi_key = settings.serpapi_key if settings else ""
        self._exa_key = settings.exa_api_key if settings else ""

    def _search_duckduckgo(self, query: str, max_results: int) -> list[Dict[str, Any]]:
        try:
            from duckduckgo_search import DDGS
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

    @staticmethod
    def _exa_snippet(result: Dict[str, Any]) -> str:
        highlights = result.get("highlights")
        if isinstance(highlights, list):
            text = " … ".join(str(item) for item in highlights if item)
        else:
            text = str(highlights or "")
        if not text:
            text = str(result.get("text") or "")
        return text[:_EXA_SNIPPET_CHARS]

    def _search_exa(self, query: str, max_results: int) -> list[Dict[str, Any]]:
        if not self._exa_key:
            return [
                {
                    "title": "Exa unavailable",
                    "snippet": "exa_api_key not set (export EXA_API_KEY).",
                    "url": "",
                }
            ]
        try:
            import httpx

            resp = httpx.post(
                "https://api.exa.ai/search",
                headers={"x-api-key": self._exa_key, "Content-Type": "application/json"},
                json={
                    "query": query,
                    "numResults": max_results,
                    "type": "auto",
                    "contents": {
                        "highlights": True,
                        "text": {"maxCharacters": _EXA_TEXT_CHARS},
                    },
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            return [
                {
                    "title": r.get("title") or "",
                    "snippet": self._exa_snippet(r),
                    "url": r.get("url") or "",
                }
                for r in data.get("results", [])
            ]
        except Exception as exc:  # pragma: no cover - network dependent
            # Header-validation errors can embed the header value; keep the key
            # out of the tool result and the log even then.
            message = str(exc)
            if self._exa_key:
                message = message.replace(self._exa_key, "[redacted]")
            logger.warning("Exa search failed: %s", message)
            return [{"title": "Exa error", "snippet": message, "url": ""}]

    def run(self, **kwargs: Any) -> ToolResult:
        query = str(kwargs.get("query", "")).strip()
        max_results = int(kwargs.get("max_results", 5) or 5)
        if not query:
            return ToolResult(success=False, error="`query` is required.")
        try:
            if self._provider == "serpapi":
                results = self._search_serpapi(query, max_results)
            elif self._provider == "exa":
                results = self._search_exa(query, max_results)
            else:
                results = self._search_duckduckgo(query, max_results)
            return ToolResult(success=True, data={"query": query, "results": results})
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("web_search failed")
            return ToolResult(success=False, error=str(exc))
