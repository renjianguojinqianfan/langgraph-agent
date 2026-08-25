"""Knowledge base package (P1 item 3 — RAG + cross-session memory)."""

from __future__ import annotations

from .knowledge_base import (
    KbChunk,
    KbDoc,
    KbHit,
    KnowledgeBase,
    get_kb_instance,
    set_kb_instance,
)

__all__ = [
    "KbChunk",
    "KbDoc",
    "KbHit",
    "KnowledgeBase",
    "get_kb_instance",
    "set_kb_instance",
]
