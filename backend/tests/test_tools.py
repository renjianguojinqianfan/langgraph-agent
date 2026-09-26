"""Tests for the tool layer.

* the discrete sandbox file tools (``write`` / ``read`` / ``ls``) — sandbox
  whitelist: allow operations inside the root, reject escapes.
* ``code_exec`` — restricted subprocess (runs code, honours timeout, requires_confirm).
* ``http_api`` — confirm logic (GET free, write methods gated); network mocked.
* ``web_search`` — mocked provider, validates returned structure.

Everything is offline: network calls are replaced with mocks.
"""

from __future__ import annotations

from unittest.mock import patch

from backend.config import Settings
from backend.core.tools.code_exec import CodeExecTool
from backend.core.tools.file_io import LsTool, ReadTool, WriteTool
from backend.core.tools.http_api import WRITE_METHODS, HttpTool
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


def test_code_exec_child_env_has_no_secrets(settings, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "super-secret-do-not-leak")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "super-secret-do-not-leak")
    tool = CodeExecTool(settings)
    res = tool.run(
        language="python",
        code="import os; print('LLM_API_KEY' in os.environ, 'DASHSCOPE_API_KEY' in os.environ)",
    )
    assert res.success is True
    assert res.data["stdout"].strip() == "False False"
    assert "super-secret-do-not-leak" not in res.data["stdout"]


def test_code_exec_child_env_keeps_runtime_basics(settings):
    tool = CodeExecTool(settings)
    res = tool.run(
        language="python",
        code=(
            "import os; "
            "print(bool(os.environ.get('PATH')), bool(os.environ.get('PYTHONUNBUFFERED')))"
        ),
    )
    assert res.success is True
    assert res.data["stdout"].strip() == "True True"


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
