"""Knowledge-base retrieval tools (P1 item 3).

``memory_search`` and ``kb_query`` (alias, same implementation) let the agent
retrieve relevant chunks from the local knowledge base during a task. The KB
instance is injected by :class:`TaskManager` (``tool.kb``); when it is missing
(unit tests / disabled) the tools return an empty hit list instead of raising
— zero regression.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ...config import Settings, get_settings
from ...utils.logging import get_logger
from ..kb.knowledge_base import KnowledgeBase, get_kb_instance
from .base import BaseTool, ToolResult
from .registry import register

logger = get_logger("tool.kb")

_ARGS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Search query against the local knowledge base.",
        },
        "top_k": {
            "type": "integer",
            "description": "Optional maximum number of hits (default from settings).",
        },
    },
    "required": ["query"],
}


@register
class MemorySearchTool(BaseTool):
    name = "memory_search"
    description = (
        "Search the local knowledge base (indexed artifacts and user documents) "
        "and return the most relevant text chunks. Use it to reuse knowledge "
        "from previous sessions."
    )
    args_schema = _ARGS_SCHEMA
    requires_confirm = False
    # Local lookup: deterministic, no retry / breaker.
    retryable = False
    max_retries = 0
    circuit_breaker = False

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self.kb: Optional[KnowledgeBase] = None  # injected by TaskManager

    def _resolve_kb(self) -> Optional[KnowledgeBase]:
        return self.kb or get_kb_instance()

    def run(self, **kwargs: Any) -> ToolResult:
        kb = self._resolve_kb()
        if kb is None:
            return ToolResult(success=True, data={"hits": []})
        query = str(kwargs.get("query", "")).strip()
        if not query:
            return ToolResult(success=True, data={"hits": []})
        top_k = int(kwargs.get("top_k") or 0)
        if top_k <= 0:
            s = self.settings or get_settings()
            top_k = int(s.kb_top_k)
        try:
            hits = kb.retrieve(query, top_k)
            return ToolResult(
                success=True,
                data={"hits": [h.to_dict() for h in hits]},
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("memory_search failed: %s", exc)
            return ToolResult(success=True, data={"hits": []}, error=str(exc))


@register
class KbQueryTool(MemorySearchTool):
    """Alias of :class:`MemorySearchTool` under the ``kb_query`` name."""

    name = "kb_query"
    description = (
        "Query the local knowledge base and return matching text chunks. "
        "Alias of memory_search."
    )
