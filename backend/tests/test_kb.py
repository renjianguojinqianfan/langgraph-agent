"""P1 item 3 — knowledge base / RAG tests (fully offline).

Covers indexing + retrieval, JSON persistence (cross-session), offline keyword
mode, disabled/empty-instance behaviour, artifact auto-indexing, path dedupe,
and the KB management REST endpoints.
"""

from __future__ import annotations

import time
from pathlib import Path

from backend.config import Settings
from backend.core.kb.knowledge_base import (
    KbHit,
    KnowledgeBase,
    get_kb_instance,
    set_kb_instance,
)
from backend.core.llm.client import MockLLMClient
from backend.core.tools.kb_tools import MemorySearchTool, KbQueryTool
from backend.tests.conftest import make_manager
from backend.tests.test_graph import _run_until_done


def _kb_settings(tmp_path, **overrides):
    base = dict(
        data_dir=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        trace_dir=str(tmp_path / "traces"),
        kb_dir=str(tmp_path / "kb"),
        max_steps=50,
        sandbox_timeout=5,
        use_mock_llm=True,
    )
    base.update(overrides)
    return Settings(**base)


def test_index_and_retrieve(tmp_path):
    settings = _kb_settings(tmp_path)
    kb = KnowledgeBase(settings)
    doc = tmp_path / "kb" / "rag.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(
        "Retrieval augmented generation (RAG) combines retrieval with LLMs. "
        "GraphRAG builds knowledge graphs from documents.",
        encoding="utf-8",
    )
    added = kb.add_document(doc)
    assert added is not None
    assert added.chunks >= 1

    hits = kb.retrieve("RAG 知识图谱", top_k=3)
    assert len(hits) >= 1
    assert isinstance(hits[0], KbHit)
    assert "RAG" in hits[0].content or "GraphRAG" in hits[0].content

    # Miss returns an empty list, never an exception.
    assert kb.retrieve("zzzz-no-such-token-xyz", top_k=3) == []


def test_persistence_across_instances(tmp_path):
    settings = _kb_settings(tmp_path)
    kb1 = KnowledgeBase(settings)
    doc = tmp_path / "kb" / "notes.txt"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("跨会话记忆测试：历史任务的产物可以被新任务检索。", encoding="utf-8")
    kb1.add_document(doc)

    # New instance on the same directory reloads the persisted index.
    kb2 = KnowledgeBase(settings)
    hits = kb2.retrieve("跨会话记忆", top_k=3)
    assert len(hits) == 1
    assert hits[0].path.endswith("notes.txt")


def test_offline_no_embedding(tmp_path):
    settings = _kb_settings(tmp_path, kb_embedding_enabled=False)
    kb = KnowledgeBase(settings)
    doc = tmp_path / "kb" / "offline.txt"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("离线关键词检索可用，无需任何 API Key。", encoding="utf-8")
    kb.add_document(doc)
    assert len(kb.retrieve("离线", top_k=5)) == 1


def test_disabled_is_empty_instance(tmp_path):
    settings = _kb_settings(tmp_path, kb_enabled=False)
    kb = KnowledgeBase(settings)
    assert kb.list_docs() == []
    assert kb.retrieve("anything", top_k=5) == []
    # Adding a document is a silent no-op.
    assert kb.add_document(Path("/definitely/not/exists.md")) is None


def test_dedupe_by_path(tmp_path):
    settings = _kb_settings(tmp_path)
    kb = KnowledgeBase(settings)
    doc = tmp_path / "kb" / "same.txt"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("same content", encoding="utf-8")
    kb.add_document(doc)
    kb.add_document(doc)
    assert len(kb.list_docs()) == 1


def test_rebuild_and_remove(tmp_path):
    settings = _kb_settings(tmp_path)
    kb = KnowledgeBase(settings)
    a = tmp_path / "kb" / "a.txt"
    b = tmp_path / "kb" / "b.txt"
    a.parent.mkdir(parents=True, exist_ok=True)
    a.write_text("alpha content here", encoding="utf-8")
    b.write_text("beta content here", encoding="utf-8")
    kb.add_document(a)
    kb.add_document(b)
    assert len(kb.list_docs()) == 2

    # Remove one doc -> other still retrievable.
    doc_id = kb.list_docs()[0].doc_id
    assert kb.remove(doc_id) is True
    assert len(kb.list_docs()) == 1
    assert kb.remove(doc_id) is False  # already gone

    # Rebuild rescans the directory (the removed file still exists on disk).
    count = kb.rebuild()
    assert count >= 1
    assert len(kb.list_docs()) == 2


def test_artifact_auto_index(tmp_path, event_bus):
    settings = _kb_settings(tmp_path)
    mock = MockLLMClient(
        plan=["write a file"],
        tool_calls=[
            {
                "id": "kb1",
                "name": "file_io",
                "arguments": {
                    "action": "write",
                    "path": "kb_artifact.txt",
                    "content": "知识库自动入库产物内容",
                },
            }
        ],
        final_answer="done.",
    )
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="kb", user_input="write kb artifact")
    task = _run_until_done(tm, task_id)
    assert task.status.value == "COMPLETED"
    assert tm._kb is not None
    hits = tm._kb.retrieve("自动入库", top_k=5)
    assert len(hits) >= 1


def test_kb_tools_return_hits(tmp_path):
    settings = _kb_settings(tmp_path)
    kb = KnowledgeBase(settings)
    doc = tmp_path / "kb" / "tool.txt"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("memory_search 与 kb_query 检索知识库", encoding="utf-8")
    kb.add_document(doc)

    tool = MemorySearchTool(settings)
    tool.kb = kb
    res = tool.run(query="memory_search", top_k=3)
    assert res.success is True
    assert len(res.data["hits"]) >= 1

    alias = KbQueryTool(settings)
    alias.kb = kb
    res2 = alias.run(query="kb_query", top_k=3)
    assert res2.success is True
    assert len(res2.data["hits"]) >= 1


def test_kb_tools_without_instance_return_empty():
    set_kb_instance(None)
    settings = Settings(kb_dir="data/kb")
    tool = MemorySearchTool(settings)
    res = tool.run(query="anything")
    assert res.success is True
    assert res.data == {"hits": []}


def test_kb_rest_endpoints(tmp_path):
    from fastapi.testclient import TestClient

    from backend.services.event_bus import EventBus
    from backend.services.persistence import Persistence
    from backend.main import app

    settings = _kb_settings(tmp_path)
    eb = EventBus()
    persistence = Persistence(settings)
    tm = make_manager(settings, MockLLMClient(plan=["p"], final_answer="done"), event_bus=eb, persistence=persistence)
    with TestClient(app) as client:
        client.app.state.settings = settings
        client.app.state.event_bus = eb
        client.app.state.persistence = persistence
        client.app.state.task_manager = tm

        r = client.get("/api/kb")
        assert r.status_code == 200
        assert r.json()["code"] == 0
        assert isinstance(r.json()["data"]["docs"], list)

        # Seed a doc then rebuild.
        doc = tmp_path / "kb" / "rest.txt"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text("rest rebuild content", encoding="utf-8")
        r = client.post("/api/kb/rebuild")
        assert r.status_code == 200
        assert r.json()["data"]["indexed"] >= 1

        r = client.get("/api/kb")
        docs = r.json()["data"]["docs"]
        assert len(docs) >= 1
        doc_id = docs[0]["doc_id"]

        r = client.delete(f"/api/kb/{doc_id}")
        assert r.status_code == 200
        assert r.json()["data"]["ok"] is True

        r = client.delete("/api/kb/nonexistent")
        assert r.json()["data"]["ok"] is False
