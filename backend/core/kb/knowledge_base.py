"""Offline knowledge base (P1 item 3 — RAG + cross-session memory).

The :class:`KnowledgeBase` indexes text documents under ``kb_dir``
(``.txt`` / ``.md`` / ``.json`` / ``.csv`` / source files) by splitting them
into chunks (≤ ``kb_chunk_size`` chars) and building a pure standard-library
keyword / structural index. The index is persisted to ``<kb_dir>/.index.json``
so a restart / a later session can still retrieve documents indexed earlier
(cross-session memory).

Degradation contract:

* ``kb_enabled=False`` -> an **empty instance** (``list_docs() == []``,
  ``retrieve() == []``) — zero regression;
* missing directory -> created lazily, no error;
* no Embedding key -> keyword retrieval works fully offline
  (``kb_embedding_enabled=False`` is the default);
* retrieval misses return an empty list, never an exception.

``set_embedder(fn)`` is the vector-retrieval placeholder (P1 does not ship a
vector store).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ...config import Settings
from ...utils.logging import get_logger

logger = get_logger("kb")

INDEX_FILENAME = ".index.json"
#: Extensions treated as text documents.
_TEXT_EXTS = {
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".csv",
    ".log",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".html",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".rst",
}

_WORD_RE = re.compile(r"[a-z0-9\u4e00-\u9fff]+", re.IGNORECASE)


@dataclass
class KbDoc:
    """Metadata of one indexed document."""

    doc_id: str
    path: str
    size: int = 0
    chunks: int = 0
    indexed_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "path": self.path,
            "size": self.size,
            "chunks": self.chunks,
            "indexed_at": self.indexed_at,
        }


@dataclass
class KbChunk:
    """A single chunk of an indexed document."""

    doc_id: str
    path: str
    index: int = 0
    content: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"doc_id": self.doc_id, "path": self.path, "index": self.index, "content": self.content}


@dataclass
class KbHit:
    """A retrieval hit (used by the ``memory_search`` / ``kb_query`` tools)."""

    doc_id: str
    path: str
    chunk_index: int = 0
    content: str = ""
    score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "path": self.path,
            "chunk_index": self.chunk_index,
            "content": self.content,
            "score": self.score,
        }


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _chunk_text(text: str, chunk_size: int) -> List[str]:
    """Split ``text`` into chunks of at most ``chunk_size`` characters."""
    size = max(64, int(chunk_size))
    text = (text or "").strip()
    if not text:
        return []
    return [text[i : i + size] for i in range(0, len(text), size)]


def _tokenize(text: str) -> List[str]:
    """Tokenise for keyword matching.

    ASCII runs become single lower-cased tokens; CJK runs are split into
    individual characters so short Chinese queries ("跨会话") match reliably
    regardless of where the word boundary falls.
    """
    tokens: List[str] = []
    for m in _WORD_RE.finditer(text or ""):
        word = m.group(0).lower()
        cjk = re.findall(r"[\u4e00-\u9fff]", word)
        if cjk:
            tokens.extend(cjk)
            ascii_part = re.sub(r"[\u4e00-\u9fff]", "", word)
            if ascii_part:
                tokens.append(ascii_part)
        else:
            tokens.append(word)
    return tokens


def _is_useful_token(token: str) -> bool:
    """Single CJK characters are useful; ASCII tokens need >= 2 chars."""
    if not token:
        return False
    if any("\u4e00" <= ch <= "\u9fff" for ch in token):
        return True
    return len(token) >= 2


class KnowledgeBase:
    """Keyword/structural index with JSON persistence under ``kb_dir``."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._embedder: Optional[Callable[[str], List[float]]] = None
        self._docs: Dict[str, KbDoc] = {}
        self._chunks: Dict[str, List[KbChunk]] = {}
        # keyword -> [(doc_id, chunk_index)]
        self._index: Dict[str, List[tuple]] = {}
        self._dir: Optional[Path] = None
        self._index_file: Optional[Path] = None
        if not settings.kb_enabled:
            logger.info("kb disabled (kb_enabled=False); empty instance.")
            return
        self._dir = settings.kb_path
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("kb dir unavailable (%s); empty instance.", exc)
            self._dir = None
            return
        self._index_file = self._dir / INDEX_FILENAME
        self._load_index()
        try:
            self.index_documents()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("kb initial index_documents failed: %s", exc)

    # ── persistence ──
    def _load_index(self) -> None:
        if self._index_file is None or not self._index_file.exists():
            return
        try:
            raw = json.loads(self._index_file.read_text(encoding="utf-8"))
            for item in raw.get("docs", []):
                doc = KbDoc(**item)
                self._docs[doc.doc_id] = doc
            for doc_id, chunk_list in (raw.get("chunks") or {}).items():
                self._chunks[doc_id] = [KbChunk(**c) for c in chunk_list]
            self._rebuild_index()
        except Exception as exc:
            logger.warning("kb index load failed (%s); starting fresh.", exc)
            self._docs.clear()
            self._chunks.clear()
            self._index.clear()

    def _persist(self) -> None:
        if self._index_file is None:
            return
        try:
            payload = {
                "docs": [d.to_dict() for d in self._docs.values()],
                "chunks": {
                    doc_id: [c.to_dict() for c in chunks]
                    for doc_id, chunks in self._chunks.items()
                },
            }
            self._index_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("kb index persist failed: %s", exc)

    def _rebuild_index(self) -> None:
        index: Dict[str, List[tuple]] = {}
        for doc_id, chunks in self._chunks.items():
            for c in chunks:
                for token in set(_tokenize(c.content)):
                    index.setdefault(token, []).append((doc_id, c.index))
        self._index = index

    # ── indexing ──
    def _is_text_file(self, path: Path) -> bool:
        return path.suffix.lower() in _TEXT_EXTS

    def index_documents(self) -> int:
        """Scan ``kb_dir`` and index every new text document (dedupe by path).

        Returns the number of newly indexed documents.
        """
        if self._dir is None or not self._dir.exists():
            return 0
        count = 0
        for path in sorted(self._dir.rglob("*")):
            if not path.is_file() or path.name == INDEX_FILENAME:
                continue
            if not self._is_text_file(path):
                continue
            if self._find_doc_by_path(str(path.resolve())) is not None:
                continue
            if self.add_document(path) is not None:
                count += 1
        return count

    def _find_doc_by_path(self, resolved: str) -> Optional[KbDoc]:
        for doc in self._docs.values():
            try:
                if Path(doc.path).resolve() == Path(resolved):
                    return doc
            except Exception:  # pragma: no cover - defensive
                continue
        return None

    def add_document(self, path: Any) -> Optional[KbDoc]:
        """Index one file (dedupe by resolved path). Returns the doc or None."""
        if self._dir is None:
            return None
        try:
            path_obj = Path(path)
            if not path_obj.exists() or not path_obj.is_file():
                logger.warning("kb add_document: file not found %s", path_obj)
                return None
            resolved = str(path_obj.resolve())
            existing = self._find_doc_by_path(resolved)
            if existing is not None:
                return existing
            text = path_obj.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            logger.warning("kb add_document failed for %s: %s", path, exc)
            return None

        doc_id = f"doc_{abs(hash(resolved)) & 0xFFFFFFFF:08x}_{len(self._docs) + 1}"
        doc = KbDoc(
            doc_id=doc_id,
            path=resolved,
            size=path_obj.stat().st_size if path_obj.exists() else len(text),
            chunks=0,
            indexed_at=_now(),
        )
        chunks = [
            KbChunk(doc_id=doc_id, path=resolved, index=i, content=chunk)
            for i, chunk in enumerate(_chunk_text(text, self.settings.kb_chunk_size))
        ]
        doc.chunks = len(chunks)
        self._docs[doc_id] = doc
        self._chunks[doc_id] = chunks
        for c in chunks:
            for token in set(_tokenize(c.content)):
                self._index.setdefault(token, []).append((doc_id, c.index))
        self._persist()
        logger.info("kb indexed %s (%d chunks)", resolved, len(chunks))
        return doc

    def rebuild(self) -> int:
        """Clear the index, rescan the directory, and persist. Returns count."""
        self._docs.clear()
        self._chunks.clear()
        self._index.clear()
        count = self.index_documents()
        self._persist()
        return count

    def remove(self, doc_id: str) -> bool:
        """Remove a document from the index. Returns True when removed."""
        if doc_id not in self._docs:
            return False
        self._docs.pop(doc_id, None)
        self._chunks.pop(doc_id, None)
        self._rebuild_index()
        self._persist()
        return True

    # ── retrieval ──
    def retrieve(self, query: str, top_k: int = 5) -> List[KbHit]:
        """Keyword retrieval: score chunks by token/substring matches.

        Returns up to ``top_k`` hits sorted by score descending; misses return
        an empty list (never an exception).
        """
        if self._dir is None or not self._docs:
            return []
        q = (query or "").strip()
        if not q:
            return []
        tokens = [t for t in _tokenize(q) if _is_useful_token(t)]
        if not tokens:
            return []
        scores: Dict[str, float] = {}
        for token in tokens:
            for doc_id, chunk_idx in self._index.get(token, []):
                key = f"{doc_id}:{chunk_idx}"
                scores[key] = scores.get(key, 0.0) + 1.0
        hits: List[KbHit] = []
        for key, score in scores.items():
            doc_id, chunk_idx = key.split(":", 1)
            chunks = self._chunks.get(doc_id, [])
            chunk = chunks[int(chunk_idx)] if int(chunk_idx) < len(chunks) else None
            if chunk is None:
                continue
            hits.append(
                KbHit(
                    doc_id=doc_id,
                    path=chunk.path,
                    chunk_index=chunk.index,
                    content=chunk.content,
                    score=score,
                )
            )
        hits.sort(key=lambda h: (-h.score, h.path, h.chunk_index))
        k = max(1, int(top_k))
        return hits[:k]

    def list_docs(self) -> List[KbDoc]:
        return sorted(self._docs.values(), key=lambda d: d.path)

    def set_embedder(self, fn: Callable[[str], List[float]]) -> None:
        """Vector-retrieval placeholder (P1: keyword retrieval only)."""
        self._embedder = fn
        logger.info("kb embedder set (vector retrieval reserved for P2).")


# ── module-level singleton holder (used by KB tools / routes) ──
_KB_INSTANCE: Optional[KnowledgeBase] = None


def set_kb_instance(kb: Optional[KnowledgeBase]) -> None:
    """Register the process-wide KB instance (called by TaskManager)."""
    global _KB_INSTANCE
    _KB_INSTANCE = kb


def get_kb_instance() -> Optional[KnowledgeBase]:
    """Return the process-wide KB instance (may be None in tests)."""
    return _KB_INSTANCE
