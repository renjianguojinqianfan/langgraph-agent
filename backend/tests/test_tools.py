"""Tests for the discrete file tools and web search.

* the discrete sandbox file tools (``write`` / ``read`` / ``ls``) — sandbox
  whitelist: allow operations inside the root, reject escapes.
* ``web_search`` — mocked provider, validates returned structure.

The bash tool has its own module (``test_bash_tool.py``). Everything is
offline: network calls are replaced with mocks.
"""

from __future__ import annotations

from unittest.mock import patch

from backend.config import Settings
from backend.core.tools.file_io import LsTool, ReadTool, WriteTool
from backend.core.tools.web_search import WebSearchTool


# ─────────────────────────── sandbox file tools ───────────────────────────
def test_write_then_read_within_sandbox(settings):
    res = WriteTool(settings).run(path="hello.txt", content="hi there")
    assert res.success is True
    assert "path" in res.data

    written = settings.artifacts_path / "hello.txt"
    assert written.exists()
    assert "hi there" in written.read_text(encoding="utf-8")

    read = ReadTool(settings).run(path="hello.txt", line_numbers=False)
    assert read.success is True
    assert read.data["content"] == "hi there"


def test_write_to_subdirectory_allowed(settings):
    res = WriteTool(settings).run(path="sub/dir/note.txt", content="nested")
    assert res.success is True
    assert (settings.artifacts_path / "sub" / "dir" / "note.txt").exists()


def test_write_rejects_parent_path_escape(settings):
    res = WriteTool(settings).run(path="../escape.txt", content="x")
    assert res.success is False
    assert "outside the sandbox" in res.error or "rejected" in res.error.lower()


def test_read_rejects_absolute_path_escape(settings):
    res = ReadTool(settings).run(path="/etc/passwd")
    assert res.success is False
    assert "outside the sandbox" in res.error or "rejected" in res.error.lower()


def test_write_rejects_empty_path(settings):
    res = WriteTool(settings).run(path="", content="x")
    assert res.success is False
    assert "path" in res.error.lower()


def test_read_missing_file_reports_error(settings):
    res = ReadTool(settings).run(path="does_not_exist.txt")
    assert res.success is False
    assert "not found" in res.error.lower()


def test_ls_returns_entries(settings):
    WriteTool(settings).run(path="a.txt", content="1")
    WriteTool(settings).run(path="b.txt", content="2")
    res = LsTool(settings).run()
    assert res.success is True
    names = {e["name"] for e in res.data["entries"]}
    assert {"a.txt", "b.txt"}.issubset(names)


# ─────────────────────────── web_search ────────────────────────────
class _FakeDDGS:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def text(self, query, max_results=5):
        return [
            {"title": f"r{i}", "body": f"snippet {i}", "href": f"https://e{i}.test"}
            for i in range(max_results)
        ]


def test_web_search_returns_structured_results():
    with patch("duckduckgo_search.DDGS", _FakeDDGS):
        tool = WebSearchTool(Settings(search_provider="duckduckgo"))
        res = tool.run(query="latest AI agents", max_results=3)
    assert res.success is True
    results = res.data["results"]
    assert len(results) == 3
    for r in results:
        assert {"title", "snippet", "url"} <= set(r.keys())


def test_web_search_requires_query():
    tool = WebSearchTool(Settings())
    res = tool.run(query="   ")
    assert res.success is False
    assert "query" in res.error.lower()
