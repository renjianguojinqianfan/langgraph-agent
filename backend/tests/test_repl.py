"""#20 / spec D-48…D-52: thin terminal REPL (offline, fake runtime seams)."""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from backend import cli, repl, server_lock
from backend.api.schemas import TaskStatus
from backend.repl import (
    PROMPT,
    RENDERED_EVENT_TYPES,
    ReplSession,
    configure_stdio_utf8,
    render_event,
    repl_main,
)
from backend.runtime import build_runtime
from backend.services.event_bus import EventBus
from backend.services.persistence import PersistenceError
from backend.tests.conftest import make_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
SENTINEL = "repl-secret-sentinel"


# ── fakes (runtime / task manager / event bus) ──
class _FakeTask:
    def __init__(self, status: TaskStatus) -> None:
        self.status = status


class _FakeStore:
    def list_public(self):
        return {
            "default": "fake-model",
            "models": [
                {"id": "fake-model", "key_set": True, "key_source": "pasted"}
            ],
        }


class _FakeTaskManager:
    def __init__(self, *, status_sequence: Optional[List[TaskStatus]] = None) -> None:
        self.models = _FakeStore()
        self.created: List[tuple] = []
        self.sent: List[tuple] = []
        self.stopped: List[str] = []
        self.shutdown_calls = 0
        self.status = TaskStatus.INTERRUPTED
        self.status_sequence = list(status_sequence or [])
        self.send_error: Optional[Exception] = None
        self.stop_error: Optional[Exception] = None
        #: When true, a created/sent round stays RUNNING until stop()/sequence.
        self.stay_running = False

    def create_task(self, title, text, **kwargs):
        task_id = f"task{len(self.created) + 1:08d}deadbeef"
        self.created.append((task_id, text))
        self.status = (
            TaskStatus.RUNNING if self.stay_running else TaskStatus.INTERRUPTED
        )
        return task_id

    def get_task(self, task_id):
        if self.status_sequence:
            self.status = self.status_sequence.pop(0)
        return _FakeTask(self.status)

    def send_message(self, task_id, text):
        if self.send_error is not None:
            raise self.send_error
        self.sent.append((task_id, text))
        self.status = (
            TaskStatus.RUNNING if self.stay_running else TaskStatus.INTERRUPTED
        )
        return {"ok": True}

    def stop(self, task_id):
        if self.stop_error is not None:
            raise self.stop_error
        self.stopped.append(task_id)
        self.status = TaskStatus.INTERRUPTED
        return {"ok": True}

    def shutdown(self):
        self.shutdown_calls += 1

    def redact(self, text: str) -> str:
        return text.replace(SENTINEL, "[redacted]")


class _FakeBus:
    def __init__(self, history: Optional[List[Dict[str, Any]]] = None) -> None:
        self.history = list(history or [])
        self.subscribed: List[tuple] = []
        self.unsubscribed: List[tuple] = []

    def subscribe(self, task_id, callback):
        self.subscribed.append((task_id, callback))
        return list(self.history)

    def unsubscribe(self, task_id, callback):
        self.unsubscribed.append((task_id, callback))


def _fake_runtime(*, history=None, status_sequence=None):
    tm = _FakeTaskManager(status_sequence=status_sequence)
    bus = _FakeBus(history)
    runtime = SimpleNamespace(
        settings=None,
        event_bus=bus,
        persistence=None,
        task_manager=tm,
    )
    return runtime


def _clock_pair():
    state = {"now": 0.0}

    def clock() -> float:
        return state["now"]

    def sleep(seconds: float) -> None:
        state["now"] += seconds

    return clock, sleep


def _run(monkeypatch, settings, runtime, lines, **kwargs):
    out: List[str] = []
    iterator = iter(lines)

    def input_fn(prompt: str) -> str:
        return next(iterator)

    monkeypatch.setattr(repl, "get_settings", lambda: settings)
    code = repl_main(
        [],
        runtime_factory=lambda _settings: runtime,
        input_fn=input_fn,
        output=out.append,
        sleep=kwargs.pop("sleep", lambda seconds: None),
        clock=kwargs.pop("clock", lambda: 0.0),
        configure_stdio=False,
        **kwargs,
    )
    return code, out


# ── build_runtime (D-48 seam) ──
def test_build_runtime_wires_the_same_objects(tmp_path):
    settings = make_settings(tmp_path)
    runtime = build_runtime(settings)
    try:
        assert runtime.settings is settings
        assert runtime.task_manager.event_bus is runtime.event_bus
        assert runtime.task_manager.persistence is runtime.persistence
        assert runtime.persistence is not None
        # make_settings roots data_dir at tmp_path; artifacts gets its own dir.
        assert (tmp_path / "artifacts").is_dir()
    finally:
        runtime.task_manager.shutdown()


def test_real_runtime_runs_two_mock_tasks_in_one_session(tmp_path, monkeypatch):
    """Spec Testing seam 2: real build_runtime + TaskManager + EventBus, MockLLM.

    Only input/output/sleep are injected — the runtime, task threads, event bus
    and persistence are the production objects (tmp DATA_DIR). Two tasks are
    dispatched in one session; the assertions read the real task records, not a
    scripted history.
    """
    script = tmp_path / "mock-script.json"
    script.write_text('{"final_answer": "integration-answer"}', encoding="utf-8")
    settings = make_settings(
        tmp_path, use_mock_llm=True, mock_llm_script_path=str(script)
    )
    runtime = build_runtime(settings)
    shutdown_calls: List[int] = []
    real_shutdown = runtime.task_manager.shutdown

    def counted_shutdown() -> None:
        shutdown_calls.append(1)
        real_shutdown()

    runtime.task_manager.shutdown = counted_shutdown

    out: List[str] = []
    prompts: List[str] = []
    lines = iter(["first task", "/new", "second task", "exit"])

    def input_fn(prompt: str) -> str:
        prompts.append(prompt)
        return next(lines)

    monkeypatch.setattr(repl, "get_settings", lambda: settings)
    code = repl_main(
        [],
        runtime_factory=lambda _settings: runtime,
        input_fn=input_fn,
        output=out.append,
        configure_stdio=False,
    )

    assert code == 0
    text = "\n".join(out)
    assert text.count("答: integration-answer") == 2
    assert sum(line.startswith("● 新任务") for line in out) == 2
    assert text.count("■ 停下") == 2

    tasks = runtime.task_manager.list_all_tasks()
    assert len(tasks) == 2
    # Deterministic terminal state: the round parked, not a timing-dependent
    # RUNNING leftover.
    assert all(task.status is TaskStatus.INTERRUPTED for task in tasks)

    # Back at the prompt after each terminal event: base prompt, task prompt,
    # base prompt (/new), task prompt.
    assert prompts[0] == PROMPT
    assert prompts[2] == PROMPT
    assert sum(prompt.startswith("[") for prompt in prompts) == 2
    assert shutdown_calls == [1]


# ── rendering (D-49 / D-50 + safety) ──
def test_rendered_event_types_are_the_small_face():
    assert RENDERED_EVENT_TYPES == frozenset(
        {
            "task_created",
            "task_resumed",
            "step_start",
            "tool_call",
            "tool_result",
            "final_answer",
            "task_interrupted",
            "task_failed",
        }
    )


@pytest.mark.parametrize(
    ("event", "fragment"),
    [
        ({"type": "task_created", "data": {"task_id": "abc12345"}}, "新任务"),
        ({"type": "task_resumed", "data": {"task_id": "abc12345"}}, "续跑"),
        ({"type": "step_start", "data": {"index": 2, "round": 1}}, "第 2 步"),
        (
            {"type": "tool_call", "data": {"tool_name": "write", "input": {"p": "a"}}},
            "write",
        ),
        (
            {
                "type": "tool_result",
                "data": {"tool_name": "write", "status": "success", "output": "ok"},
            },
            "✓",
        ),
        (
            {"type": "tool_result", "data": {"tool_name": "bash", "status": "skipped"}},
            "⊘",
        ),
        ({"type": "final_answer", "data": {"answer": "done"}}, "答: done"),
        ({"type": "task_interrupted", "data": {"status": "INTERRUPTED"}}, "停下"),
        ({"type": "task_failed", "data": {"error": "boom"}}, "boom"),
    ],
)
def test_render_event_known_types(event, fragment):
    line = render_event(event)
    assert line is not None and fragment in line


@pytest.mark.parametrize(
    "kind", ["heartbeat", "context_compressed", "tool_result_evicted", "nonsense"]
)
def test_render_event_drops_non_rendered_types(kind):
    assert render_event({"type": kind, "data": {}}) is None


def test_render_event_never_prints_reasoning_or_unredacted_secrets():
    answered = render_event(
        {
            "type": "final_answer",
            "data": {"answer": "visible", "reasoning": SENTINEL},
        }
    )
    assert answered is not None
    assert "visible" in answered
    assert SENTINEL not in answered

    redacted = render_event(
        {"type": "tool_result", "data": {"tool_name": "bash", "output": SENTINEL}},
        redact=lambda text: text.replace(SENTINEL, "[redacted]"),
    )
    assert redacted is not None
    assert SENTINEL not in redacted and "[redacted]" in redacted


# ── session loop (D-49) ──
def _final_events(task_id="t"):
    return [
        {"type": "task_created", "data": {"task_id": task_id}},
        {"type": "step_start", "data": {"index": 1, "round": 1}},
        {"type": "final_answer", "data": {"answer": "done"}},
        {"type": "task_interrupted", "data": {"status": "INTERRUPTED"}},
    ]


def test_two_tasks_run_in_one_session_with_new(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    runtime = _fake_runtime(history=_final_events())

    code, out = _run(
        monkeypatch,
        settings,
        runtime,
        ["first task", "/new", "second task", "exit"],
    )

    assert code == 0
    assert [text for _, text in runtime.task_manager.created] == [
        "first task",
        "second task",
    ]
    assert out.count("答: done") == 2
    assert any("下一条输入创建新任务" in line for line in out)
    # L1: the replayed task_created event is the only task-created line per
    # task (the header and the /new hint also mention 新任务, so match the
    # rendered line prefix).
    assert sum(line.startswith("● 新任务") for line in out) == 2
    assert runtime.task_manager.shutdown_calls == 1


def test_second_message_continues_the_same_task(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    runtime = _fake_runtime(history=_final_events())

    code, out = _run(monkeypatch, settings, runtime, ["first", "second", "quit"])

    assert code == 0
    assert len(runtime.task_manager.created) == 1
    task_id = runtime.task_manager.created[0][0]
    assert runtime.task_manager.sent == [(task_id, "second")]
    assert "答: done" in out


def test_send_message_conflict_prints_the_reason_and_keeps_the_prompt(
    tmp_path, monkeypatch
):
    settings = make_settings(tmp_path)
    runtime = _fake_runtime(history=_final_events())
    runtime.task_manager.send_error = RuntimeError(
        "task is RUNNING; only a parked task can continue"
    )

    code, out = _run(monkeypatch, settings, runtime, ["first", "second", "exit"])

    assert code == 0
    assert any("✗" in line and "RUNNING" in line for line in out)


def test_stop_is_a_noop_when_the_task_is_parked(tmp_path):
    runtime = _fake_runtime()
    out: List[str] = []
    session = ReplSession(runtime, output=out.append)
    session.task_id = "task-1"
    runtime.task_manager.status = TaskStatus.INTERRUPTED

    assert session.stop() is False
    assert runtime.task_manager.stopped == []
    assert any("没有在跑的任务" in line for line in out)


def test_stop_running_task_requests_stop(tmp_path):
    runtime = _fake_runtime()
    out: List[str] = []
    session = ReplSession(runtime, output=out.append)
    session.task_id = "task-1"
    runtime.task_manager.status = TaskStatus.RUNNING

    assert session.stop() is True
    assert runtime.task_manager.stopped == ["task-1"]
    assert any("已请求停止" in line for line in out)


def test_ctrl_c_while_waiting_stops_the_task_then_returns_to_prompt(
    tmp_path, monkeypatch
):
    settings = make_settings(tmp_path)
    runtime = _fake_runtime(history=[])  # no terminal event: status polling path
    runtime.task_manager.stay_running = True

    def sleep(seconds: float) -> None:
        if not runtime.task_manager.stopped:
            raise KeyboardInterrupt
        raise KeyboardInterrupt  # settle must also exit deterministically

    code, out = _run(
        monkeypatch,
        settings,
        runtime,
        ["long task", "exit"],
        sleep=sleep,
        clock=_clock_pair()[0],
    )

    assert code == 0
    assert runtime.task_manager.stopped, "Ctrl-C during a round must call stop()"
    assert any("已请求停止" in line for line in out)


def test_ctrl_c_at_the_prompt_exits_cleanly(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    runtime = _fake_runtime()
    calls = {"n": 0}

    def input_fn(prompt: str) -> str:
        calls["n"] += 1
        raise KeyboardInterrupt

    monkeypatch.setattr(repl, "get_settings", lambda: settings)
    out: List[str] = []
    code = repl_main(
        [],
        runtime_factory=lambda _settings: runtime,
        input_fn=input_fn,
        output=out.append,
        configure_stdio=False,
    )

    assert code == 0
    assert any("退出" in line for line in out)
    assert runtime.task_manager.shutdown_calls == 1


def test_eof_exits_cleanly(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    runtime = _fake_runtime()

    def input_fn(prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr(repl, "get_settings", lambda: settings)
    code = repl_main(
        [],
        runtime_factory=lambda _settings: runtime,
        input_fn=input_fn,
        output=lambda line: None,
        configure_stdio=False,
    )

    assert code == 0
    assert runtime.task_manager.shutdown_calls == 1


def test_terminal_event_finishes_the_round_even_before_status_settles(
    tmp_path, monkeypatch
):
    settings = make_settings(tmp_path)
    runtime = _fake_runtime(
        history=_final_events(),
        status_sequence=[TaskStatus.RUNNING, TaskStatus.INTERRUPTED],
    )
    clock, sleep = _clock_pair()

    code, out = _run(
        monkeypatch,
        settings,
        runtime,
        ["first", "exit"],
        sleep=sleep,
        clock=clock,
    )

    assert code == 0
    assert any("■ 停下" in line for line in out)
    assert runtime.task_manager.created


def test_status_polling_is_the_fallback_when_events_are_missing(
    tmp_path, monkeypatch
):
    settings = make_settings(tmp_path)
    runtime = _fake_runtime(
        history=[],
        status_sequence=[TaskStatus.RUNNING, TaskStatus.INTERRUPTED],
    )
    clock, sleep = _clock_pair()

    code, out = _run(
        monkeypatch,
        settings,
        runtime,
        ["first", "exit"],
        sleep=sleep,
        clock=clock,
    )

    assert code == 0
    assert runtime.task_manager.created
    assert not any("■ 停下" in line for line in out)  # no event was rendered


# ── lock + mutual exclusion (D-48 / requirement 2) ──
def test_repl_claims_mode_repl_and_releases_on_exit(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, single_backend_guard_enabled=True)
    runtime = _fake_runtime()
    seen: List[Any] = []
    lock_file = server_lock.lock_path(settings)

    def factory(_settings):
        seen.append(server_lock.read_lock(lock_file))
        return runtime

    out: List[str] = []
    iterator = iter(["exit"])

    def input_fn(prompt: str) -> str:
        return next(iterator)

    monkeypatch.setattr(repl, "get_settings", lambda: settings)
    code = repl_main(
        [],
        runtime_factory=factory,
        input_fn=input_fn,
        output=out.append,
        configure_stdio=False,
    )

    assert code == 0
    assert seen and seen[0] is not None and seen[0].mode == "repl"
    assert seen[0].pid == os.getpid()
    assert server_lock.read_lock(lock_file) is None
    assert runtime.task_manager.shutdown_calls == 1


def test_repl_refuses_when_a_web_backend_is_running(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, single_backend_guard_enabled=True)
    lock_file = server_lock.lock_path(settings)
    assert server_lock.claim_lock(
        lock_file,
        server_lock.ServerLock(
            pid=os.getpid(),
            port=settings.port,
            started_at=server_lock.now_iso(),
            instance="web",
            mode="serve",
        ),
    )
    monkeypatch.setattr(repl, "get_settings", lambda: settings)
    out: List[str] = []
    called: List[bool] = []

    code = repl_main(
        [],
        runtime_factory=lambda _settings: called.append(True) or _fake_runtime(),
        input_fn=lambda prompt: "exit",
        output=out.append,
        configure_stdio=False,
    )

    assert code == 1
    assert called == []
    assert any("关终端会停任务" in line for line in out)
    assert any("网页后台" in line for line in out)


def test_repl_refuses_when_another_repl_is_running(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, single_backend_guard_enabled=True)
    lock_file = server_lock.lock_path(settings)
    assert server_lock.claim_lock(
        lock_file,
        server_lock.ServerLock(
            pid=os.getpid(),
            port=settings.port,
            started_at=server_lock.now_iso(),
            instance="other-repl",
            mode="repl",
        ),
    )
    monkeypatch.setattr(repl, "get_settings", lambda: settings)
    out: List[str] = []

    code = repl_main(
        [],
        runtime_factory=lambda _settings: _fake_runtime(),
        input_fn=lambda prompt: "exit",
        output=out.append,
        configure_stdio=False,
    )

    assert code == 1
    assert any("终端会话" in line for line in out)


def test_web_launcher_refuses_a_running_repl_without_probing(
    tmp_path, monkeypatch, capsys
):
    settings = make_settings(tmp_path, single_backend_guard_enabled=True)
    lock_file = server_lock.lock_path(settings)
    assert server_lock.claim_lock(
        lock_file,
        server_lock.ServerLock(
            pid=os.getpid(),
            port=settings.port,
            started_at=server_lock.now_iso(),
            instance="repl-1",
            mode="repl",
        ),
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    served: List[tuple] = []

    code = cli.main([], serve=lambda host, port: served.append((host, port)))

    assert code == 2
    assert served == []
    assert "关终端会停任务" in capsys.readouterr().out
    # A live record is refused but must not be deleted by the refused launcher.
    record = server_lock.read_lock(lock_file)
    assert record is not None and record.instance == "repl-1"


# ── CLI dispatch + encoding + module entry (D-48 / D-51) ──
def test_qianfan_routes_the_repl_subcommand():
    seen: List[List[str]] = []
    code = cli.main(["repl", "--flag"], repl=lambda argv: seen.append(argv) or 7)
    assert code == 7
    assert seen == [["--flag"]]


def test_configure_stdio_utf8_reconfigures_gbk_wrapper():
    stream = io.TextIOWrapper(
        io.BytesIO(), encoding="gbk", errors="strict", write_through=True
    )
    configure_stdio_utf8([stream])
    assert stream.encoding.lower().replace("-", "") == "utf8"
    assert stream.errors == "replace"
    stream.write("中文 emoji 🙂")
    stream.flush()  # must not raise UnicodeEncodeError


def test_configure_stdio_utf8_skips_non_reconfigurable_streams():
    configure_stdio_utf8([object()])  # must not raise


def test_repl_module_entry_starts_and_exits(tmp_path):
    """Subprocess smoke (entry wiring + exit code).

    AGENTS.md classifies subprocess/process-tree tests separately ("one file per
    PR + 3 stable runs" before joining the Windows smoke list); this file is NOT
    on that list yet — the follow-up is recorded in the PR body, not silently
    assumed.
    """
    env = os.environ.copy()
    env.update(
        {
            "DATA_DIR": str(tmp_path / "data"),
            "ARTIFACTS_DIR": str(tmp_path / "artifacts"),
            "TRACE_DIR": str(tmp_path / "traces"),
            "CHECKPOINT_DIR": str(tmp_path / "checkpoints"),
            "USE_MOCK_LLM": "true",
            "MCP_ENABLED": "false",
            "SINGLE_BACKEND_GUARD_ENABLED": "false",
            "HOST": "127.0.0.1",
            "FRONTEND_DIST": "_no_frontend_dist_for_tests",
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "backend.repl"],
        input="exit\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO_ROOT,
        env=env,
        timeout=180,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "qianfan 终端会话" in output


# ── PR #304 rework: stale lock recovery (M1) ──
def test_repl_recovers_from_a_dead_lock_record(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, single_backend_guard_enabled=True)
    lock_file = server_lock.lock_path(settings)
    assert server_lock.claim_lock(
        lock_file,
        server_lock.ServerLock(
            pid=999_999,
            port=settings.port,
            started_at=server_lock.now_iso(),
            instance="dead",
            mode="repl",
        ),
    )
    runtime = _fake_runtime()
    iterator = iter(["exit"])
    monkeypatch.setattr(repl, "get_settings", lambda: settings)
    out: List[str] = []

    code = repl_main(
        [],
        runtime_factory=lambda _settings: runtime,
        input_fn=lambda prompt: next(iterator),
        output=out.append,
        configure_stdio=False,
    )

    assert code == 0
    assert server_lock.read_lock(lock_file) is None
    assert runtime.task_manager.shutdown_calls == 1


def test_repl_recovers_from_a_corrupt_lock_record(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, single_backend_guard_enabled=True)
    lock_file = server_lock.lock_path(settings)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text("{not json", encoding="utf-8")
    runtime = _fake_runtime()
    iterator = iter(["exit"])
    monkeypatch.setattr(repl, "get_settings", lambda: settings)
    out: List[str] = []

    code = repl_main(
        [],
        runtime_factory=lambda _settings: runtime,
        input_fn=lambda prompt: next(iterator),
        output=out.append,
        configure_stdio=False,
    )

    assert code == 0
    assert server_lock.read_lock(lock_file) is None
    assert runtime.task_manager.shutdown_calls == 1


def test_repl_reports_manual_cleanup_when_claim_races(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, single_backend_guard_enabled=True)
    lock_file = server_lock.lock_path(settings)
    monkeypatch.setattr(server_lock, "claim_lock", lambda *args, **kwargs: False)
    monkeypatch.setattr(repl, "get_settings", lambda: settings)
    out: List[str] = []

    code = repl_main(
        [],
        runtime_factory=lambda _settings: _fake_runtime(),
        input_fn=lambda prompt: "exit",
        output=out.append,
        configure_stdio=False,
    )

    assert code == 1
    message = " ".join(out)
    assert "删除" in message
    assert str(lock_file) in message


# ── PR #304 rework: /new while RUNNING (M2) + replay dedupe (L1/L2) ──
def test_new_is_refused_while_the_task_is_still_running(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    runtime = _fake_runtime(history=_final_events())
    runtime.task_manager.stay_running = True
    clock, sleep = _clock_pair()

    code, out = _run(
        monkeypatch,
        settings,
        runtime,
        ["first", "/new", "/stop", "/new", "second", "exit"],
        sleep=sleep,
        clock=clock,
    )

    assert code == 0
    assert any("还在跑" in line for line in out), "RUNNING + /new must be refused"
    assert any("仍在跑" in line for line in out), "settle timeout must warn"
    # Refused /new did not create a task; after /stop the next /new works.
    assert [text for _, text in runtime.task_manager.created] == ["first", "second"]
    assert runtime.task_manager.stopped


def test_real_event_bus_replay_renders_task_created_once(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    runtime = _fake_runtime()
    runtime.event_bus = EventBus()
    task_id = "task00000001deadbeef"
    runtime.event_bus.publish(task_id, "task_created", {"task_id": task_id})

    code, out = _run(monkeypatch, settings, runtime, ["first", "exit"])

    assert code == 0
    assert sum(line.startswith("● 新任务") for line in out) == 1


# ── PR #304 rework: command errors print, never a bare traceback (L4) ──
def test_stop_errors_are_printed_not_raised(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    runtime = _fake_runtime(history=_final_events())
    runtime.task_manager.stay_running = True
    runtime.task_manager.stop_error = PersistenceError("tasks.json write failed")
    clock, sleep = _clock_pair()

    code, out = _run(
        monkeypatch,
        settings,
        runtime,
        ["first", "/stop", "exit"],
        sleep=sleep,
        clock=clock,
    )

    assert code == 0
    assert any("✗" in line and "write failed" in line for line in out)
    assert runtime.task_manager.shutdown_calls == 1


def test_send_persistence_error_is_printed(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    runtime = _fake_runtime(history=_final_events())
    runtime.task_manager.send_error = PersistenceError("tasks.json write failed")

    code, out = _run(monkeypatch, settings, runtime, ["first", "second", "exit"])

    assert code == 0
    assert any("✗" in line and "write failed" in line for line in out)
