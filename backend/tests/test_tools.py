"""Tests for the tool layer.

* ``file_io``  — sandbox whitelist (allow writes inside root, reject escapes).
* ``code_exec`` — restricted subprocess (runs code, honours timeout, requires_confirm).
* ``http_api`` — confirm logic (GET free, write methods gated); network mocked.
* ``web_search`` — mocked provider, validates returned structure.

Everything is offline: network calls are replaced with mocks.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.config import Settings
from backend.core.tools.code_exec import CodeExecTool
from backend.core.tools.file_io import FileIOTool
from backend.core.tools.http_api import HttpTool, WRITE_METHODS
from backend.core.tools.web_search import WebSearchTool


# ───────────────────────────── file_io ─────────────────────────────
def test_file_io_write_then_read_within_sandbox(settings):
    tool = FileIOTool(settings)
    res = tool.run(action="write", path="hello.txt", content="hi there")
    assert res.success is True
    assert "path" in res.data

    written = settings.artifacts_path / "hello.txt"
    assert written.exists()
    assert "hi there" in written.read_text(encoding="utf-8")

    read = tool.run(action="read", path="hello.txt")
    assert read.success is True
    assert read.data["content"] == "hi there"


def test_file_io_write_to_subdirectory_allowed(settings):
    tool = FileIOTool(settings)
    res = tool.run(action="write", path="sub/dir/note.txt", content="nested")
    assert res.success is True
    assert (settings.artifacts_path / "sub" / "dir" / "note.txt").exists()


def test_file_io_rejects_parent_path_escape(settings):
    tool = FileIOTool(settings)
    res = tool.run(action="write", path="../escape.txt", content="x")
    assert res.success is False
    assert "outside the sandbox" in res.error or "rejected" in res.error.lower()


def test_file_io_rejects_absolute_path_escape(settings):
    tool = FileIOTool(settings)
    res = tool.run(action="read", path="/etc/passwd")
    assert res.success is False
    assert "outside the sandbox" in res.error or "rejected" in res.error.lower()


def test_file_io_rejects_empty_path(settings):
    tool = FileIOTool(settings)
    res = tool.run(action="write", path="", content="x")
    assert res.success is False
    assert "path" in res.error.lower()


def test_file_io_read_missing_file_reports_error(settings):
    tool = FileIOTool(settings)
    res = tool.run(action="read", path="does_not_exist.txt")
    assert res.success is False
    assert "not found" in res.error.lower()


def test_file_io_list_returns_entries(settings):
    tool = FileIOTool(settings)
    tool.run(action="write", path="a.txt", content="1")
    tool.run(action="write", path="b.txt", content="2")
    res = tool.run(action="list", path=".")
    assert res.success is True
    names = {e["name"] for e in res.data["entries"]}
    assert {"a.txt", "b.txt"}.issubset(names)


def test_file_io_unknown_action_errors(settings):
    tool = FileIOTool(settings)
    res = tool.run(action="frobnicate", path="x")
    assert res.success is False


# ──────────────────────────── code_exec ────────────────────────────
def test_code_exec_runs_python_and_captures_output(settings):
    tool = CodeExecTool(settings)
    res = tool.run(language="python", code="print(1 + 1)")
    assert res.success is True
    assert "2" in res.data["stdout"]
    assert res.data["exit_code"] == 0


def test_code_exec_requires_confirm_flag():
    tool = CodeExecTool(None)
    assert tool.requires_confirm is True


def test_code_exec_failing_code_returns_failure_not_crash(settings):
    tool = CodeExecTool(settings)
    res = tool.run(language="python", code="raise ValueError('boom')")
    assert res.success is False
    assert res.data["exit_code"] != 0
    assert "boom" in res.error or "Traceback" in res.error


def test_code_exec_honours_timeout(settings):
    # Override sandbox timeout to 1s so the test stays fast.
    fast_settings = Settings(
        data_dir=settings.data_dir,
        artifacts_dir=str(settings.artifacts_path),
        sandbox_timeout=1,
    )
    tool = CodeExecTool(fast_settings)
    res = tool.run(language="python", code="import time\ntime.sleep(8)")
    assert res.success is False
    assert res.data["exit_code"] == -1
    assert "timed out" in res.error.lower()


# ──────────────────────────── http_api ─────────────────────────────
def test_http_tool_get_does_not_require_confirm():
    tool = HttpTool(None)
    assert tool._needs_confirm("GET") is False
    assert tool._needs_confirm("HEAD") is False
    assert tool._needs_confirm("OPTIONS") is False


def test_http_tool_write_methods_require_confirm():
    tool = HttpTool(None)
    for method in WRITE_METHODS:
        assert tool._needs_confirm(method) is True


class _FakeResp:
    status_code = 200
    headers = {"content-type": "application/json"}

    def json(self):
        return {"ok": True, "items": [1, 2]}

    @property
    def text(self):
        return '{"ok": true}'


class _FakeClient:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def request(self, **kwargs):
        self.last = kwargs
        return _FakeResp()


def test_http_tool_run_get_returns_parsed_structure():
    fake = _FakeClient()
    with patch("backend.core.tools.http_api.httpx.Client", lambda *a, **k: fake):
        tool = HttpTool(None)
        res = tool.run(method="GET", url="https://httpbin.test/get")
    assert res.success is True
    assert res.data["status_code"] == 200
    assert res.data["body"] == {"ok": True, "items": [1, 2]}
    assert fake.last["method"] == "GET"


def test_http_tool_run_reports_failure_on_error_status():
    class _ErrResp(_FakeResp):
        status_code = 500
        headers = {"content-type": "text/plain"}

        def json(self):
            raise ValueError("not json")

    class _ErrClient(_FakeClient):
        def request(self, **kwargs):
            self.last = kwargs
            return _ErrResp()

    with patch("backend.core.tools.http_api.httpx.Client", lambda *a, **k: _ErrClient()):
        tool = HttpTool(None)
        res = tool.run(method="GET", url="https://httpbin.test/boom")
    assert res.success is False
    assert res.data["status_code"] == 500


def test_http_tool_missing_url_errors():
    tool = HttpTool(None)
    res = tool.run(method="GET", url="")
    assert res.success is False
    assert "url" in res.error.lower()


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
