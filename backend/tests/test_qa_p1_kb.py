"""QA independent boundary tests — P1 item 3 (RAG / knowledge base).

Edge cases / contracts beyond the engineer's suite:

* Chinese retrieval ranking (more matching tokens => higher score, sorted);
* ``.index.json`` is actually persisted on disk and reloads (cross-session);
* chunking respects ``kb_chunk_size`` (long documents split into blocks);
* ``kb_auto_index_artifacts=False`` -> artifact NOT auto-indexed;
* non-text files are skipped; missing dir / disabled KB degrade to empty;
* rebuild picks up content changes of an existing document.
"""

from __future__ import annotations

from pathlib import Path

from backend.config import Settings
from backend.core.kb.knowledge_base import INDEX_FILENAME, KnowledgeBase
from backend.core.llm.client import MockLLMClient
from backend.tests.conftest import make_manager, make_settings
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


def test_chinese_retrieval_ranks_more_hits_first(tmp_path):
    settings = _kb_settings(tmp_path)
    kb = KnowledgeBase(settings)
    a = tmp_path / "kb" / "a.txt"
    b = tmp_path / "kb" / "b.txt"
    a.parent.mkdir(parents=True, exist_ok=True)
    # a matches the query tokens once; b matches them AND has extra query
    # tokens (历史) -> b must rank first (keyword-hit priority, PRD 3.3).
    a.write_text("跨会话 记忆 的简单介绍。", encoding="utf-8")
    b.write_text(
        "跨会话 记忆 与历史知识复用，是跨会话记忆的核心。",
        encoding="utf-8",
    )
    kb.add_document(a)
    kb.add_document(b)

    hits = kb.retrieve("跨会话记忆 历史", top_k=5)
    assert len(hits) >= 2
    assert hits[0].path.endswith("b.txt"), "top hit should be the more relevant document"
    assert hits[0].score >= hits[1].score


def test_index_json_persisted_and_reloads(tmp_path):
    settings = _kb_settings(tmp_path)
    kb = KnowledgeBase(settings)
    doc = tmp_path / "kb" / "persist.txt"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("持久化索引：重启后依然可检索。", encoding="utf-8")
    kb.add_document(doc)

    index_file = Path(settings.kb_path) / INDEX_FILENAME
    assert index_file.exists(), ".index.json was not persisted"
    payload = index_file.read_text(encoding="utf-8")
    assert "docs" in payload and "chunks" in payload

    # A fresh instance on the same dir reads the file back (cross-session).
    kb2 = KnowledgeBase(settings)
    hits = kb2.retrieve("持久化", top_k=3)
    assert len(hits) == 1
    assert hits[0].path.endswith("persist.txt")


def test_long_document_is_chunked(tmp_path):
    settings = _kb_settings(tmp_path, kb_chunk_size=100)
    kb = KnowledgeBase(settings)
    doc = tmp_path / "kb" / "long.txt"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("词语。" * 80, encoding="utf-8")  # ~320 chars -> >= 4 chunks of 100
    added = kb.add_document(doc)
    assert added is not None
    assert added.chunks >= 3
    assert len(kb._chunks[added.doc_id]) == added.chunks
    # Retrieval still works across chunks.
    assert len(kb.retrieve("词语", top_k=5)) >= 1


def test_auto_index_disabled_does_not_index_artifact(tmp_path, event_bus):
    settings = _kb_settings(tmp_path, kb_auto_index_artifacts=False)
    mock = MockLLMClient(
        plan=["write a file"],
        tool_calls=[
            {
                "id": "qakb1",
                "name": "file_io",
                "arguments": {
                    "action": "write",
                    "path": "no_index.txt",
                    "content": "这段内容不应被自动入库",
                },
            }
        ],
        final_answer="done.",
    )
    tm = make_manager(settings, mock, event_bus=event_bus)
    task_id = tm.create_task(title="qa-kb-off", user_input="write file")
    task = _run_until_done(tm, task_id)
    assert task.status.value == "COMPLETED"
    assert tm._kb is not None
    assert tm._kb.retrieve("不应被自动入库", top_k=5) == []


def test_non_text_files_are_skipped(tmp_path):
    settings = _kb_settings(tmp_path)
    kb = KnowledgeBase(settings)
    png = tmp_path / "kb" / "image.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    png.write_bytes(b"\x89PNG\r\n\x1a\n fake binary")
    # The directory-scan indexer (index_documents) skips non-text files.
    assert kb.index_documents() == 0
    assert kb.list_docs() == []
    # NOTE (known issue): KnowledgeBase.add_document() itself does not filter by
    # extension and would index a binary file with errors="replace"; the
    # auto-index path (index_documents / TaskManager.add_artifact) is the PRD
    # entry point and is safe. Tracked as a P2 hardening item.


def test_rebuild_picks_up_changed_document(tmp_path):
    settings = _kb_settings(tmp_path)
    kb = KnowledgeBase(settings)
    doc = tmp_path / "kb" / "change.txt"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("传统数据库设计", encoding="utf-8")
    kb.add_document(doc)
    assert len(kb.retrieve("传统数据库", top_k=3)) == 1

    # Content changes on disk; rebuild must refresh the index. Use words that
    # share no single CJK character with the old text (single-char tokeniser).
    doc.write_text("量子计算入门指南", encoding="utf-8")
    count = kb.rebuild()
    assert count >= 1
    assert kb.retrieve("量子计算", top_k=3) != []
    assert kb.retrieve("传统数据库", top_k=3) == []


def test_missing_kb_dir_degrades_gracefully(tmp_path):
    settings = _kb_settings(tmp_path)
    kb = KnowledgeBase(settings)
    # Directory does not exist yet -> instance still usable (created lazily).
    assert kb.list_docs() == []
    assert kb.retrieve("anything", top_k=5) == []
    # Creating the dir + adding a doc works.
    doc = tmp_path / "kb" / "late.txt"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("晚加入的文档", encoding="utf-8")
    assert kb.add_document(doc) is not None
    assert len(kb.retrieve("晚加入", top_k=3)) == 1
