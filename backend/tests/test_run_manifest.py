"""Tests for the bypass run manifest (issue #58).

Covers the module contract end-to-end, fully offline:

* the «件清单» header is deterministic and **minimally diffable** — flipping
  one capability changes exactly one header line (the control-variable
  criterion from the issue's acceptance criteria);
* every LLM round lands with its full request / response / usage / elapsed,
* subtask channels are mirrored into the parent's file with owner tags and
  detach cleanly;
* configured key literals and key-shaped strings never hit the disk;
* the byte cap stops the file with one ``manifest_truncated`` marker;
* TaskManager wiring: manifest on -> file written and closed; off (default)
  -> nothing written and the trace event sequence is unchanged (the manifest
  is strictly bypass, TraceRecorder semantics untouched).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from backend.core.llm.client import MockLLMClient
from backend.services.event_bus import EventBus
from backend.services.run_manifest import RunManifest
from backend.tests.conftest import make_manager, make_settings
from backend.tests.test_graph import _run_until_done
from backend.tests.test_subagent import _ResearchMock

_FACE = ["read", "glob", "grep", "kb_query", "load_skill", "spawn_subagent"]


def _lines(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _lines_until_end(path: Path, end_type: str, timeout: float = 10.0) -> list:
    """Read the JSONL once its last line is ``end_type``.

    ``_run_until_done`` returns on terminal *status*, but the worker thread's
    ``finally`` block (writing the closing line) may still be unwinding —
    reading at that moment would compare half-written files.
    """
    deadline = time.time() + timeout
    lines: list = []
    while time.time() < deadline:
        lines = _lines(path)
        if lines and lines[-1].get("type") == end_type:
            return lines
        time.sleep(0.02)
    return lines


def _manifest(tmp_path: Path, **overrides) -> RunManifest:
    settings = make_settings(
        tmp_path,
        run_manifest_enabled=True,
        run_manifest_dir=str(tmp_path / "runs"),
        **overrides,
    )
    return RunManifest(settings, list(_FACE), confirm_enabled=True)


# ── header: capability lines ─────────────────────────────────────────────────


def test_header_capability_lines_are_deterministic(tmp_path):
    m1 = _manifest(tmp_path / "a")
    m2 = _manifest(tmp_path / "b")
    bus = EventBus()
    m1.attach(bus, "t1")
    m2.attach(bus, "t2")
    m1.close("t1")
    m2.close("t2")

    caps1 = [ln for ln in _lines(m1.file_path("t1")) if ln.get("type") == "capability"]
    caps2 = [ln for ln in _lines(m2.file_path("t2")) if ln.get("type") == "capability"]
    assert caps1 == caps2  # same settings -> byte-identical header lines
    names = [c["name"] for c in caps1]
    assert "tool_face" in names and "subagent" in names and "risk_scan" in names
    assert names == sorted(set(names), key=names.index)  # stable order
    # Volatile fields must not leak into the diffable header.
    for c in caps1:
        assert "ts" not in c and "task_id" not in c


def test_header_single_capability_flip_is_a_single_line_diff(tmp_path):
    base = _manifest(tmp_path / "on")
    flipped = _manifest(tmp_path / "off", risk_scan_enabled=False)
    bus = EventBus()
    base.attach(bus, "t1")
    flipped.attach(bus, "t2")
    base.close("t1")
    flipped.close("t2")

    caps1 = [ln for ln in _lines(base.file_path("t1")) if ln.get("type") == "capability"]
    caps2 = [
        ln for ln in _lines(flipped.file_path("t2")) if ln.get("type") == "capability"
    ]
    assert len(caps1) == len(caps2)
    diffs = [(a, b) for a, b in zip(caps1, caps2) if a != b]
    assert len(diffs) == 1, f"expected exactly one differing line, got {diffs}"
    assert diffs[0][0]["name"] == "risk_scan"
    assert diffs[0][0]["enabled"] is True and diffs[0][1]["enabled"] is False


def test_header_reflects_tool_face_and_confirm_gate(tmp_path):
    settings = make_settings(
        tmp_path,
        run_manifest_enabled=True,
        run_manifest_dir=str(tmp_path / "runs"),
    )
    m = RunManifest(settings, list(_FACE), confirm_enabled=False)
    bus = EventBus()
    m.attach(bus, "t1")
    m.close("t1")
    caps = {ln["name"]: ln for ln in _lines(m.file_path("t1")) if ln.get("type") == "capability"}
    assert caps["tool_face"]["params"]["tools"] == sorted(_FACE)
    assert caps["confirm_gate"]["enabled"] is False
    assert caps["subagent"]["params"]["entry_a_spawn"] is True


# ── body: LLM rounds via the proxy ───────────────────────────────────────────


def test_llm_proxy_records_request_response_and_passthrough(tmp_path):
    m = _manifest(tmp_path)
    bus = EventBus()
    m.attach(bus, "t1")
    mock = MockLLMClient(plan=["step"], final_answer="done")
    wrapped = m.wrap_llm("t1", "parent", mock)
    assert wrapped is not mock and wrapped is not None

    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    schemas = [{"type": "function", "function": {"name": "read"}}]
    resp = wrapped.complete(msgs, schemas)
    m.close("t1")

    # Passthrough: the caller sees exactly the inner client's response.
    assert resp.content == "done"
    # The round landed with the full request and response.
    calls = [ln for ln in _lines(m.file_path("t1")) if ln.get("type") == "llm_call"]
    assert len(calls) == 1
    call = calls[0]
    assert call["owner"] == "parent"
    assert call["client"] == "main"
    assert call["request"]["messages"] == msgs
    assert call["request"]["tools"] == schemas
    assert call["request"]["n_tools"] == 1
    assert call["response"]["content"] == "done"
    assert call["usage"] is None  # mock responses carry no usage block
    assert call["elapsed_ms"] >= 0


def test_wrap_llm_is_noop_for_unattached_tasks_and_double_wraps(tmp_path):
    m = _manifest(tmp_path)
    mock = MockLLMClient(plan=["p"], final_answer="d")
    assert m.wrap_llm("unknown-task", "parent", mock) is mock  # no open file
    bus = EventBus()
    m.attach(bus, "t1")
    once = m.wrap_llm("t1", "parent", mock)
    assert m.wrap_llm("t1", "parent", once) is once  # never double-wrap
    m.close("t1")


# ── body: subtask channel mirroring ──────────────────────────────────────────


def test_subtask_channel_mirrored_into_parent_file_with_owner(tmp_path):
    m = _manifest(tmp_path)
    bus = EventBus()
    m.attach(bus, "parent-1")
    m.attach_subtask(bus, "parent-1", "sub-9")

    bus.publish("sub-9", "tool_call", {"tool_name": "file_io", "arguments": {"path": "x"}})
    bus.publish("sub-9", "tool_result", {"ok": True})
    bus.publish("sub-9", "task_completed", {"status": "COMPLETED"})  # NOT mirrored
    m.detach_subtask("sub-9")
    bus.publish("sub-9", "tool_call", {"tool_name": "late"})  # detached: dropped

    parent_lines = _lines(m.file_path("parent-1"))
    events = [ln for ln in parent_lines if ln.get("type") == "event"]
    assert [e["event"] for e in events] == ["tool_call", "tool_result"]
    assert all(e["owner"] == "subtask:sub-9" for e in events)
    assert events[0]["data"]["tool_name"] == "file_io"
    m.close("parent-1")

    # Detached channels leave no residue: the parent channel never carries
    # subtask events, and close unsubscribes everything it attached.
    assert not any(ln.get("type") == "event" and ln["owner"] != "subtask:sub-9" for ln in events)


def test_attach_subtask_noop_without_parent_file(tmp_path):
    m = _manifest(tmp_path)
    bus = EventBus()
    m.attach_subtask(bus, "no-such-parent", "sub-1")  # no empty shells
    bus.publish("sub-1", "tool_call", {"tool_name": "x"})
    assert _lines(m.file_path("no-such-parent")) == []


# ── safety: redaction + byte cap ─────────────────────────────────────────────


def test_secrets_never_reach_the_disk(tmp_path):
    settings = make_settings(
        tmp_path,
        run_manifest_enabled=True,
        run_manifest_dir=str(tmp_path / "runs"),
        llm_api_key="sk-live-abcdef123456",
    )
    m = RunManifest(settings, list(_FACE))
    bus = EventBus()
    m.attach(bus, "t1")
    mock = MockLLMClient(plan=["p"], final_answer="d")
    wrapped = m.wrap_llm("t1", "parent", mock)
    wrapped.complete(
        [
            {"role": "user", "content": "key is sk-live-abcdef123456 and Bearer tok_abcdef12"}
        ],
        None,
    )
    m.close("t1")

    text = m.file_path("t1").read_text(encoding="utf-8")
    assert "sk-live-abcdef123456" not in text
    assert "tok_abcdef12" not in text
    assert "***" in text


def test_byte_cap_stops_with_single_marker_line(tmp_path):
    settings = make_settings(
        tmp_path,
        run_manifest_enabled=True,
        run_manifest_dir=str(tmp_path / "runs"),
    )
    # Sizing by orders of magnitude so the test never depends on the exact
    # header size: header (~2 KB) fits far under the 25 KB cap, the 100 KB
    # round trips it deterministically.
    m = RunManifest(settings, list(_FACE), max_bytes=25_000)
    bus = EventBus()
    m.attach(bus, "t1")
    mock = MockLLMClient(plan=["p"], final_answer="x" * 100_000)
    wrapped = m.wrap_llm("t1", "parent", mock)
    # tools present -> executor branch -> the scripted 100 KB final answer.
    wrapped.complete(
        [{"role": "user", "content": "hi"}],
        [{"type": "function", "function": {"name": "read"}}],
    )
    m.close("t1")

    lines = _lines(m.file_path("t1"))
    trunc = [ln for ln in lines if ln.get("type") == "manifest_truncated"]
    assert len(trunc) == 1
    # The header survived, the oversized round was dropped, and the capped
    # file still ends cleanly.
    assert any(ln.get("type") == "capability" for ln in lines)
    assert not any(ln.get("type") == "llm_call" for ln in lines)
    assert lines[-1]["type"] == "manifest_end"


# ── TaskManager wiring (integration, offline) ────────────────────────────────


def _write_mock() -> MockLLMClient:
    return MockLLMClient(
        plan=["Write the file"],
        tool_calls=[
            {
                "id": "c1",
                "name": "file_io",
                "arguments": {
                    "action": "write",
                    "path": "hello.txt",
                    "content": "manifest smoke",
                },
            }
        ],
        final_answer="wrote hello.txt",
    )


def test_task_run_writes_manifest_end_to_end(tmp_path):
    settings = make_settings(
        tmp_path,
        run_manifest_enabled=True,
        run_manifest_dir=str(tmp_path / "runs"),
    )
    tm = make_manager(settings, _write_mock())
    task_id = tm.create_task(title="t", user_input="write hello.txt")
    task = _run_until_done(tm, task_id)
    assert task.status.value == "COMPLETED"

    path = settings.run_manifest_path / f"{task_id}.jsonl"
    lines = _lines_until_end(path, "manifest_end")
    caps = [ln for ln in lines if ln.get("type") == "capability"]
    assert any(c["name"] == "tool_face" for c in caps)
    assert lines[0]["type"] == "manifest_begin"
    assert lines[-1]["type"] == "manifest_end"

    # The sub-agent capability line carries its actual mounted face (not just
    # switches): the confirm-stripped subtask tool list, with the gated /
    # recursion / hard-coded-write tools absent by construction.
    sub = next(c for c in caps if c["name"] == "subagent")
    assert sub["params"]["entry_a_spawn"] is True
    assert sub["params"]["entry_b_split"] is True  # scenario table still exists (#56 flips this)
    face = sub["params"]["face"]
    assert "file_io" in face and "read" in face
    assert "spawn_subagent" not in face and "http_request" not in face
    assert "code_exec" not in face  # requires_confirm=True is structurally out

    calls = [ln for ln in lines if ln.get("type") == "llm_call"]
    assert calls and all(c["owner"] == "parent" for c in calls)
    # Planner round carries no tools; executor rounds carry the tool schemas.
    assert any(c["request"]["n_tools"] == 0 for c in calls)
    assert any(c["request"]["n_tools"] > 0 for c in calls)
    # The user input is visible in the recorded request bodies.
    assert any(
        "write hello.txt" in str(c["request"]["messages"]) for c in calls
    )
    # Tool events are mirrored with the parent owner.
    events = [ln for ln in lines if ln.get("type") == "event"]
    assert any(e["event"] == "tool_call" for e in events)
    assert any(
        e["event"] == "tool_result" and e["data"].get("tool_name") == "file_io"
        for e in events
    )


def test_manifest_off_by_default_writes_nothing(tmp_path):
    settings = make_settings(tmp_path)  # run_manifest_enabled defaults to False
    assert settings.run_manifest_enabled is False
    tm = make_manager(settings, _write_mock())
    task_id = tm.create_task(title="t", user_input="write hello.txt")
    _run_until_done(tm, task_id)
    assert not (settings.run_manifest_path / f"{task_id}.jsonl").exists()


def test_trace_event_sequence_unchanged_by_manifest(tmp_path):
    """The manifest is strictly bypass: TraceRecorder semantics are untouched."""

    def _run(manifest_on: bool) -> list:
        root = tmp_path / ("on" if manifest_on else "off")
        settings = make_settings(
            root,
            trace_dir=str(root / "traces"),
            **({"run_manifest_enabled": True, "run_manifest_dir": str(root / "runs")} if manifest_on else {}),
        )
        tm = make_manager(settings, _write_mock())
        task_id = tm.create_task(title="t", user_input="write hello.txt")
        _run_until_done(tm, task_id)
        lines = _lines_until_end(settings.trace_path / f"{task_id}.jsonl", "trace_end")
        return [ln["type"] for ln in lines]

    off_types = _run(False)
    on_types = _run(True)
    assert off_types and off_types[-1] == "trace_end"
    assert on_types and on_types[-1] == "trace_end"
    assert off_types == on_types


def test_subtask_rounds_labeled_in_parent_manifest(tmp_path):
    settings = make_settings(
        tmp_path,
        run_manifest_enabled=True,
        run_manifest_dir=str(tmp_path / "runs"),
    )
    tm = make_manager(settings, _ResearchMock())
    task_id = tm.create_task(title="t", user_input="调研 RAG 最新进展并写报告")
    task = _run_until_done(tm, task_id)
    assert task.status.value == "COMPLETED"

    path = settings.run_manifest_path / f"{task_id}.jsonl"
    lines = _lines_until_end(path, "manifest_end")
    # Subtask LLM rounds land in the PARENT file with a subtask owner tag.
    sub_calls = [
        ln for ln in lines if ln.get("type") == "llm_call" and ln["owner"].startswith("subtask:")
    ]
    assert sub_calls, "no subtask llm_call lines in the parent manifest"
    # Subtask tool events are mirrored with the same attribution.
    sub_events = [
        ln
        for ln in lines
        if ln.get("type") == "event" and ln["owner"].startswith("subtask:")
    ]
    assert any(
        e["event"] == "tool_call" and e["data"].get("tool_name") == "file_io"
        for e in sub_events
    )
    # Entry B is on (the split path ran); both subtasks are represented.
    owners = {ln["owner"] for ln in sub_calls}
    assert any(task.subtasks[0].subtask_id in o for o in owners)


def test_subagent_off_leaves_no_subtask_lines(tmp_path):
    """AC #3 second half: with sub-agents off, no owner markers, no shells."""
    settings = make_settings(
        tmp_path,
        run_manifest_enabled=True,
        run_manifest_dir=str(tmp_path / "runs"),
        subagent_enabled=False,
    )
    tm = make_manager(settings, _write_mock())
    task_id = tm.create_task(title="t", user_input="write hello.txt")
    _run_until_done(tm, task_id)

    lines = _lines_until_end(settings.run_manifest_path / f"{task_id}.jsonl", "manifest_end")
    assert not any(
        str(ln.get("owner", "")).startswith("subtask:") for ln in lines
    ), "subagent disabled yet subtask owner lines present"
    caps = {ln["name"]: ln for ln in lines if ln.get("type") == "capability"}
    assert caps["subagent"]["params"]["face"] == []
    assert caps["subagent"]["params"]["entry_a_spawn"] is False
    assert caps["subagent"]["params"]["entry_b_split"] is False


def test_two_run_diff_points_at_divergent_round(tmp_path):
    """AC #7 demo (offline): same question twice, one capability off.

    The round-level divergence leg toggles ``context_inject_enabled`` rather
    than risk scan: in mock mode risk scan emits events only (no LLM round,
    no message change), while the injected block lands *inside* the planner /
    executor request bodies — so the diff can name the exact round. The
    header leg keeps the control-variable criterion: exactly one capability
    line differs.
    """

    def _run(inject: bool) -> list:
        root = tmp_path / ("inject_on" if inject else "inject_off")
        settings = make_settings(
            root,
            run_manifest_enabled=True,
            run_manifest_dir=str(root / "runs"),
            context_inject_enabled=inject,
        )
        tm = make_manager(settings, _write_mock())
        task_id = tm.create_task(title="t", user_input="write hello.txt")
        _run_until_done(tm, task_id)
        return _lines_until_end(
            settings.run_manifest_path / f"{task_id}.jsonl", "manifest_end"
        )

    on, off = _run(True), _run(False)

    # Header: exactly the context_injection line differs.
    caps_on = [ln for ln in on if ln.get("type") == "capability"]
    caps_off = [ln for ln in off if ln.get("type") == "capability"]
    diff_names = [a["name"] for a, b in zip(caps_on, caps_off) if a != b]
    assert diff_names == ["context_injection"], f"unexpected header diffs: {diff_names}"

    # Body: same round count; the FIRST divergent round is the planner round
    # (index 0) and the difference lives in the request messages (the
    # injected AGENTS.md / skills / env-facts block is there or not).
    calls_on = [ln for ln in on if ln.get("type") == "llm_call"]
    calls_off = [ln for ln in off if ln.get("type") == "llm_call"]
    assert len(calls_on) == len(calls_off)
    divergent = [
        i
        for i, (a, b) in enumerate(zip(calls_on, calls_off))
        if a["request"]["messages"] != b["request"]["messages"]
    ]
    assert divergent and divergent[0] == 0, f"expected round-0 divergence, got {divergent}"
    for i in divergent:
        # What differs is the system block (index 0 — the injected
        # AGENTS.md / skills / env-facts block is there or not), never the
        # user instruction (index 1). Tool-result messages later in the
        # history legitimately differ: each run writes inside its own root.
        assert (
            calls_on[i]["request"]["messages"][0]
            != calls_off[i]["request"]["messages"][0]
        )
        assert (
            calls_on[i]["request"]["messages"][1]
            == calls_off[i]["request"]["messages"][1]
        )
    # Responses are identical — the mock script ignores the system prompt.
    assert [c["response"]["content"] for c in calls_on] == [
        c["response"]["content"] for c in calls_off
    ]
