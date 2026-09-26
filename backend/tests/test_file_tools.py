"""Tests for the six-piece discrete file tools: read / write / edit / glob / grep / ls.

These are the precision file tools added under ``docs/roadmap-pawbench.md`` P0-A
(``ls`` closing the set in issue #17). They live in
``backend/core/tools/file_io.py``; the legacy multi-action ``file_io`` tool they
replaced is retired (#17).

Design contract under test:
* every tool is confined to ``Settings.artifacts_path`` (the sandbox root);
  reads / writes / edits / listings / searches that escape the root are rejected;
* ``read`` paginates by line (``offset`` / ``limit``) and prefixes 1-based line
  numbers when ``line_numbers`` is on (default) — the P1 "don't blow the context
  on a full read" fix;
* ``write`` creates or overwrites a whole file, capturing a before-image first;
* ``edit`` is an exact ``old_string`` -> ``new_string`` replacement with a
  uniqueness guard (ambiguous unless ``replace_all``);
* ``glob`` owns recursion (``**`` patterns), returns root-relative sorted paths;
* ``grep`` is a regex content search with a filename ``glob`` filter, scope
  ``path``, ``ignore_case`` and a ``max_results`` cap;
* ``ls`` is the shallow listing of the directory it was asked for (never the
  root by default alone), and its result carries no top-level ``path`` key so a
  directory can never be mistaken for a produced artifact.

Everything is offline; each test gets a fresh ``tmp_path`` sandbox.
"""

from __future__ import annotations

from pathlib import Path

import backend.core.tools.file_io as file_io
from backend.config import Settings
from backend.core.tools.file_io import (
    EditTool,
    GlobTool,
    GrepTool,
    LsTool,
    ReadTool,
    WriteTool,
)
from backend.core.tools.registry import build_tools


def _write(settings: Settings, rel: str, content: str) -> Path:
    """Create a file under the sandbox root (mkdir parents as needed)."""
    p = settings.artifacts_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ───────────────────── read (pagination / line numbers) ─────────────────────
def test_read_returns_full_content_by_default(settings):
    _write(settings, "a.txt", "line1\nline2\nline3")
    res = ReadTool(settings).run(path="a.txt")
    assert res.success is True
    assert res.data["total_lines"] == 3
    assert res.data["returned_lines"] == 3
    assert res.data["truncated"] is False
    # line_numbers defaults True -> each line carries its 1-based number.
    assert "line1" in res.data["content"]


def test_read_line_numbers_off_returns_raw(settings):
    _write(settings, "a.txt", "alpha\nbeta")
    res = ReadTool(settings).run(path="a.txt", line_numbers=False)
    assert res.success is True
    assert res.data["content"] == "alpha\nbeta"


def test_read_line_numbers_format(settings):
    _write(settings, "a.txt", "x\ny")
    res = ReadTool(settings).run(path="a.txt", line_numbers=True)
    lines = res.data["content"].split("\n")
    assert lines[0].endswith("\tx")
    assert lines[0].strip().split("\t")[0] == "1"
    assert lines[1].endswith("\ty")


def test_read_pagination_offset_limit(settings):
    _write(settings, "a.txt", "l1\nl2\nl3\nl4\nl5")
    res = ReadTool(settings).run(path="a.txt", offset=2, limit=2, line_numbers=False)
    assert res.success is True
    assert res.data["content"] == "l2\nl3"
    assert res.data["start_line"] == 2
    assert res.data["end_line"] == 3
    assert res.data["returned_lines"] == 2
    assert res.data["truncated"] is True  # l4, l5 remain unread


def test_read_pagination_to_end_not_truncated(settings):
    _write(settings, "a.txt", "l1\nl2\nl3")
    res = ReadTool(settings).run(path="a.txt", offset=2, limit=10, line_numbers=False)
    assert res.success is True
    assert res.data["content"] == "l2\nl3"
    assert res.data["truncated"] is False
    assert res.data["end_line"] == 3


def test_read_line_numbers_respect_offset(settings):
    _write(settings, "a.txt", "l1\nl2\nl3")
    res = ReadTool(settings).run(path="a.txt", offset=3, limit=1, line_numbers=True)
    assert res.data["content"].strip().startswith("3")
    assert res.data["content"].endswith("l3")


def test_read_default_limit_truncates_large_file(settings):
    total = file_io.DEFAULT_READ_LIMIT + 499
    big = "\n".join(f"line{i}" for i in range(1, total + 1))
    _write(settings, "big.txt", big)
    res = ReadTool(settings).run(path="big.txt", line_numbers=False)
    assert res.success is True
    assert res.data["returned_lines"] == file_io.DEFAULT_READ_LIMIT
    assert res.data["truncated"] is True
    assert res.data["total_lines"] == total


def test_read_offset_clamped_to_one(settings):
    _write(settings, "a.txt", "l1\nl2")
    res = ReadTool(settings).run(path="a.txt", offset=0, line_numbers=False)
    assert res.success is True
    assert res.data["start_line"] == 1
    assert res.data["content"] == "l1\nl2"


def test_read_missing_file_errors(settings):
    res = ReadTool(settings).run(path="nope.txt")
    assert res.success is False
    assert "not found" in res.error.lower()


def test_read_rejects_escape(settings):
    res = ReadTool(settings).run(path="../secret.txt")
    assert res.success is False
    assert "sandbox" in res.error.lower() or "rejected" in res.error.lower()


def test_read_empty_path_errors(settings):
    res = ReadTool(settings).run(path="")
    assert res.success is False
    assert "path" in res.error.lower()


def test_read_directory_errors(settings):
    (settings.artifacts_path / "adir").mkdir(parents=True, exist_ok=True)
    res = ReadTool(settings).run(path="adir")
    assert res.success is False


# ───────────────────── write (create / overwrite) ─────────────────────


def test_write_creates_file_and_reports_path_and_size(settings):
    res = WriteTool(settings).run(path="notes.md", content="# RAG\n")
    assert res.success is True
    target = Path(res.data["path"])
    assert target.read_text(encoding="utf-8") == "# RAG\n"
    assert res.data["size"] == target.stat().st_size


def test_write_creates_missing_parent_directories(settings):
    res = WriteTool(settings).run(path="sub/dir/report.md", content="x")
    assert res.success is True
    assert (settings.artifacts_path / "sub" / "dir" / "report.md").exists()


def test_write_overwrites_an_existing_file(settings):
    _write(settings, "a.txt", "old content")
    res = WriteTool(settings).run(path="a.txt", content="new")
    assert res.success is True
    assert (settings.artifacts_path / "a.txt").read_text(encoding="utf-8") == "new"
    assert res.data["size"] == 3


def test_write_empty_content_creates_an_empty_file(settings):
    res = WriteTool(settings).run(path="empty.txt", content="")
    assert res.success is True
    assert res.data["size"] == 0


def test_write_rejects_escape(settings):
    res = WriteTool(settings).run(path="../outside.txt", content="x")
    assert res.success is False
    assert "outside the sandbox" in res.error
    assert not (settings.artifacts_path.parent / "outside.txt").exists()


def test_write_requires_path(settings):
    assert WriteTool(settings).run(path="", content="x").success is False


def test_write_rejects_non_string_content(settings):
    res = WriteTool(settings).run(path="a.txt", content={"not": "text"})
    assert res.success is False


def test_write_refuses_to_clobber_a_directory(settings):
    (settings.artifacts_path / "d").mkdir(parents=True)
    res = WriteTool(settings).run(path="d", content="x")
    assert res.success is False


# ───────────────────────── edit (exact str_replace) ─────────────────────────
def test_edit_single_replacement(settings):
    _write(settings, "a.txt", "hello world")
    res = EditTool(settings).run(path="a.txt", old_string="hello", new_string="goodbye")
    assert res.success is True
    assert res.data["replacements"] == 1
    assert (settings.artifacts_path / "a.txt").read_text(encoding="utf-8") == "goodbye world"


def test_edit_replace_all(settings):
    _write(settings, "a.txt", "x-x-x")
    res = EditTool(settings).run(
        path="a.txt", old_string="x", new_string="y", replace_all=True
    )
    assert res.success is True
    assert res.data["replacements"] == 3
    assert (settings.artifacts_path / "a.txt").read_text(encoding="utf-8") == "y-y-y"


def test_edit_ambiguous_without_replace_all_errors(settings):
    _write(settings, "a.txt", "dup dup")
    res = EditTool(settings).run(path="a.txt", old_string="dup", new_string="one")
    assert res.success is False
    assert "unique" in res.error.lower() or "2" in res.error
    # file is untouched on a rejected edit
    assert (settings.artifacts_path / "a.txt").read_text(encoding="utf-8") == "dup dup"


def test_edit_not_found_errors(settings):
    _write(settings, "a.txt", "content")
    res = EditTool(settings).run(path="a.txt", old_string="absent", new_string="x")
    assert res.success is False
    assert "not found" in res.error.lower()


def test_edit_identical_old_new_errors(settings):
    _write(settings, "a.txt", "same")
    res = EditTool(settings).run(path="a.txt", old_string="same", new_string="same")
    assert res.success is False


def test_edit_empty_old_string_errors(settings):
    _write(settings, "a.txt", "content")
    res = EditTool(settings).run(path="a.txt", old_string="", new_string="x")
    assert res.success is False


def test_edit_missing_file_errors(settings):
    res = EditTool(settings).run(path="nope.txt", old_string="a", new_string="b")
    assert res.success is False
    assert "not found" in res.error.lower()


def test_edit_rejects_escape(settings):
    res = EditTool(settings).run(path="../e.txt", old_string="a", new_string="b")
    assert res.success is False
    assert "sandbox" in res.error.lower() or "rejected" in res.error.lower()


def test_edit_multiline_block(settings):
    _write(settings, "a.txt", "def f():\n    return 1\n")
    res = EditTool(settings).run(
        path="a.txt", old_string="    return 1", new_string="    return 2"
    )
    assert res.success is True
    assert "return 2" in (settings.artifacts_path / "a.txt").read_text(encoding="utf-8")


def test_edit_refuses_non_utf8_file(settings):
    # edit writes the buffer back, so a lossy decode (errors="replace") would
    # silently corrupt the file. It must refuse instead and leave bytes intact.
    p = settings.artifacts_path / "bin.dat"
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = b"\xff\xfe\x00bad-bytes"
    p.write_bytes(raw)
    res = EditTool(settings).run(path="bin.dat", old_string="bad", new_string="good")
    assert res.success is False
    assert "utf-8" in res.error.lower()
    assert p.read_bytes() == raw  # unchanged, not corrupted


# ───────────────────────────── glob (recursive) ─────────────────────────────
def test_glob_recursive(settings):
    _write(settings, "a.txt", "1")
    _write(settings, "sub/b.txt", "2")
    _write(settings, "sub/deep/c.txt", "3")
    res = GlobTool(settings).run(pattern="**/*.txt")
    assert res.success is True
    assert set(res.data["matches"]) == {"a.txt", "sub/b.txt", "sub/deep/c.txt"}


def test_glob_non_recursive(settings):
    _write(settings, "a.txt", "1")
    _write(settings, "sub/b.txt", "2")
    res = GlobTool(settings).run(pattern="*.txt")
    assert res.success is True
    assert res.data["matches"] == ["a.txt"]


def test_glob_matches_sorted_relative(settings):
    _write(settings, "b.txt", "1")
    _write(settings, "a.txt", "2")
    res = GlobTool(settings).run(pattern="*.txt")
    assert res.data["matches"] == ["a.txt", "b.txt"]


def test_glob_subdir_path(settings):
    _write(settings, "sub/x.py", "1")
    _write(settings, "top.py", "2")
    res = GlobTool(settings).run(pattern="*.py", path="sub")
    assert res.success is True
    assert res.data["matches"] == ["sub/x.py"]


def test_glob_no_match_empty(settings):
    _write(settings, "a.txt", "1")
    res = GlobTool(settings).run(pattern="*.md")
    assert res.success is True
    assert res.data["matches"] == []
    assert res.data["count"] == 0
    assert res.data["truncated"] is False


def test_glob_only_files_not_dirs(settings):
    (settings.artifacts_path / "adir").mkdir(parents=True, exist_ok=True)
    _write(settings, "a.txt", "1")
    res = GlobTool(settings).run(pattern="**/*")
    assert "a.txt" in res.data["matches"]
    assert "adir" not in res.data["matches"]


def test_glob_empty_pattern_errors(settings):
    res = GlobTool(settings).run(pattern="")
    assert res.success is False
    assert "pattern" in res.error.lower()


def test_glob_rejects_path_escape(settings):
    res = GlobTool(settings).run(pattern="*.txt", path="../")
    assert res.success is False
    assert "sandbox" in res.error.lower() or "rejected" in res.error.lower()


def test_glob_truncates_at_cap(settings, monkeypatch):
    for i in range(10):
        _write(settings, f"f{i}.txt", "x")
    monkeypatch.setattr(file_io, "GLOB_MAX_MATCHES", 4)
    res = GlobTool(settings).run(pattern="*.txt")
    assert res.success is True
    assert len(res.data["matches"]) == 4
    assert res.data["truncated"] is True


# ─────────────────────────── grep (content search) ───────────────────────────
def test_grep_literal_match(settings):
    _write(settings, "a.txt", "foo\nbar\nfoo baz")
    res = GrepTool(settings).run(pattern="foo")
    assert res.success is True
    assert res.data["count"] == 2
    assert {m["line"] for m in res.data["matches"]} == {1, 3}
    for m in res.data["matches"]:
        assert m["file"] == "a.txt"
        assert "text" in m


def test_grep_regex_match(settings):
    _write(settings, "a.txt", "abc\n123\na1b2")
    res = GrepTool(settings).run(pattern=r"\d+")
    assert res.success is True
    texts = {m["text"] for m in res.data["matches"]}
    assert "123" in texts
    assert "a1b2" in texts


def test_grep_ignore_case(settings):
    _write(settings, "a.txt", "Hello\nworld")
    res = GrepTool(settings).run(pattern="hello", ignore_case=True)
    assert res.success is True
    assert res.data["count"] == 1
    assert GrepTool(settings).run(pattern="hello").data["count"] == 0


def test_grep_glob_filter(settings):
    _write(settings, "a.py", "needle")
    _write(settings, "b.txt", "needle")
    res = GrepTool(settings).run(pattern="needle", glob="*.py")
    assert res.success is True
    assert {m["file"] for m in res.data["matches"]} == {"a.py"}


def test_grep_recursive_subdir(settings):
    _write(settings, "sub/deep/c.txt", "target")
    res = GrepTool(settings).run(pattern="target")
    assert res.success is True
    assert res.data["matches"][0]["file"] == "sub/deep/c.txt"


def test_grep_path_scope(settings):
    _write(settings, "sub/x.txt", "hit")
    _write(settings, "top.txt", "hit")
    res = GrepTool(settings).run(pattern="hit", path="sub")
    assert {m["file"] for m in res.data["matches"]} == {"sub/x.txt"}


def test_grep_max_results_truncates(settings):
    _write(settings, "a.txt", "\n".join("match" for _ in range(10)))
    res = GrepTool(settings).run(pattern="match", max_results=3)
    assert res.success is True
    assert res.data["count"] == 3
    assert res.data["truncated"] is True


def test_grep_no_match(settings):
    _write(settings, "a.txt", "nothing here")
    res = GrepTool(settings).run(pattern="zebra")
    assert res.success is True
    assert res.data["count"] == 0
    assert res.data["matches"] == []
    assert res.data["truncated"] is False


def test_grep_invalid_regex_errors(settings):
    _write(settings, "a.txt", "x")
    res = GrepTool(settings).run(pattern="[unclosed")
    assert res.success is False
    assert "pattern" in res.error.lower() or "regex" in res.error.lower()


def test_grep_empty_pattern_errors(settings):
    res = GrepTool(settings).run(pattern="")
    assert res.success is False
    assert "pattern" in res.error.lower()


def test_grep_skips_oversized_file(settings, monkeypatch):
    _write(settings, "big.txt", "needle\n" + "x" * 200)  # > threshold -> skipped
    _write(settings, "small.txt", "needle")  # < threshold -> scanned
    monkeypatch.setattr(file_io, "GREP_MAX_FILE_BYTES", 50)
    res = GrepTool(settings).run(pattern="needle")
    files = {m["file"] for m in res.data["matches"]}
    assert "small.txt" in files
    assert "big.txt" not in files


def test_grep_files_matched_count(settings):
    _write(settings, "a.txt", "x\nx")
    _write(settings, "b.txt", "x")
    res = GrepTool(settings).run(pattern="x")
    assert res.data["files_matched"] == 2
    assert res.data["count"] == 3


def test_grep_rejects_path_escape(settings):
    res = GrepTool(settings).run(pattern="x", path="../")
    assert res.success is False
    assert "sandbox" in res.error.lower() or "rejected" in res.error.lower()


# ──────────────────────────── ls (shallow listing) ────────────────────────────
def test_ls_lists_the_sandbox_root_by_default(settings):
    _write(settings, "a.txt", "1")
    _write(settings, "b.txt", "22")
    res = LsTool(settings).run()
    assert res.success is True
    by_name = {e["name"]: e for e in res.data["entries"]}
    assert set(by_name) == {"a.txt", "b.txt"}
    assert by_name["a.txt"]["is_dir"] is False
    assert by_name["a.txt"]["size"] == 1
    assert by_name["b.txt"]["size"] == 2


def test_ls_lists_the_requested_directory_not_the_root(settings):
    """The retired ``file_io`` list validated ``path`` and then listed the root
    anyway; ``ls`` lists what it was asked for, one level deep."""
    _write(settings, "root.txt", "x")
    _write(settings, "sub/inner.txt", "y")
    _write(settings, "sub/deep/z.txt", "z")

    res = LsTool(settings).run(path="sub")

    assert res.success is True
    assert {e["name"] for e in res.data["entries"]} == {"inner.txt", "deep"}
    assert {e["name"] for e in res.data["entries"] if e["is_dir"]} == {"deep"}


def test_ls_result_carries_no_top_level_path_key(settings):
    """A top-level ``path`` sends the tool node down
    ``add_artifact(directory)``, and verification then reads the 0-byte dir as
    「产物为空」and loops the task back (PR #69 live attribution)."""
    res = LsTool(settings).run()
    assert res.success is True
    assert "path" not in res.data
    assert Path(res.data["dir"]).is_dir()


def test_ls_empty_directory_returns_no_entries(settings):
    (settings.artifacts_path / "empty").mkdir(parents=True)
    res = LsTool(settings).run(path="empty")
    assert res.success is True
    assert res.data["entries"] == []


def test_ls_missing_directory_reports_error(settings):
    res = LsTool(settings).run(path="nope")
    assert res.success is False
    assert "not found" in res.error.lower()


def test_ls_on_a_file_reports_error(settings):
    _write(settings, "a.txt", "1")
    res = LsTool(settings).run(path="a.txt")
    assert res.success is False
    assert "directory" in res.error.lower()


def test_ls_rejects_escape(settings):
    res = LsTool(settings).run(path="../")
    assert res.success is False
    assert "sandbox" in res.error.lower() or "rejected" in res.error.lower()


# ────────────── cross-cutting: policy / schema / registry / round-trip ──────────────
def test_new_tools_sandbox_policy(settings):
    for tool in (
        ReadTool(settings),
        GlobTool(settings),
        GrepTool(settings),
        EditTool(settings),
        WriteTool(settings),
        LsTool(settings),
    ):
        # sandbox-confined local FS: no confirm, no retry, no circuit breaker
        assert tool.requires_confirm is False
        assert tool.retryable is False
        assert tool.circuit_breaker is False


def test_new_tools_openai_schema_names(settings):
    assert ReadTool(settings).to_openai_schema()["function"]["name"] == "read"
    assert WriteTool(settings).to_openai_schema()["function"]["name"] == "write"
    assert EditTool(settings).to_openai_schema()["function"]["name"] == "edit"
    assert GlobTool(settings).to_openai_schema()["function"]["name"] == "glob"
    assert GrepTool(settings).to_openai_schema()["function"]["name"] == "grep"
    assert LsTool(settings).to_openai_schema()["function"]["name"] == "ls"


def test_build_tools_exposes_the_six_pieces_and_no_file_io(settings):
    names = {t.name for t in build_tools(settings)}
    assert {"read", "write", "edit", "glob", "grep", "ls"} <= names
    # retired in #17: the un-splittable multi-action bundle is off the face
    assert "file_io" not in names


def test_write_then_read_round_trip(settings):
    WriteTool(settings).run(path="rt.txt", content="line1\nline2\n")
    res = ReadTool(settings).run(path="rt.txt")
    assert res.data["total_lines"] == 2
    assert res.data["start_line"] == 1


def test_edit_then_read_round_trip(settings):
    _write(settings, "a.txt", "one two three")
    EditTool(settings).run(path="a.txt", old_string="two", new_string="2")
    res = ReadTool(settings).run(path="a.txt", line_numbers=False)
    assert res.data["content"] == "one 2 three"


def test_write_then_ls_round_trip(settings):
    """The delivery path a task actually takes: write lands, ls sees it."""
    res = WriteTool(settings).run(path="out/report.txt", content="body")
    assert res.success is True
    listed = LsTool(settings).run(path="out")
    assert {e["name"] for e in listed.data["entries"]} == {"report.txt"}
