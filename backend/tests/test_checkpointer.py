"""Checkpoint persistence spot-checks (spec Issue #4, testing layer 2).

These tests pin down the fragile links of the checkpoint wiring without
going through the full graph:

1. The saver round-trip under the project's ``thread_id = task_id``
   convention, including cross-thread isolation.
2. The serialization round-trip of a fully-populated :class:`AgentState`
   (sentinel against future non-serializable state fields).
3. The pre-migration store guard (Issue #7): a checkpoint file written before
   the langgraph 0.2 -> 1.2.x migration must be detected, warned about and
   refused at mount time instead of being silently trusted.

They deliberately use the real SqliteSaver (temp file) rather than
MemorySaver so the on-disk format and connection semantics are exercised.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from backend.api.schemas import Task, TaskStatus
from backend.core.agent.state import AgentState
from backend.core.llm.client import MockLLMClient
from backend.core.tools.base import BaseTool, ToolResult
from backend.services.event_bus import EventBus
from backend.services.persistence import Persistence
from backend.services.task_manager import CHECKPOINT_FORMAT_VERSION, TaskManager
from backend.tests.conftest import make_manager, make_settings


@pytest.fixture
def saver(tmp_path):
    """A real SqliteSaver rooted at a throwaway file (sync usage).

    Note: the ``with`` form closes the connection on exit — correct for a
    test, but production must construct the saver over its own long-lived
    ``sqlite3.connect(...)`` instead of borrowing this context manager.
    """
    conn_path = tmp_path / "checkpoints.sqlite"
    with SqliteSaver.from_conn_string(str(conn_path)) as saver:
        yield saver


def _config(task_id: str) -> Dict[str, Any]:
    """The project-wide convention: thread_id == task_id."""
    return {"configurable": {"thread_id": task_id}}


def _full_state() -> AgentState:
    """An AgentState with every field populated to a meaningful value."""
    return {
        "task_id": "t-full",
        "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'},
        ],
        "plan": [{"index": 0, "description": "do a thing", "status": "done"}],
        "steps": [{"index": 0, "action": "tool_call", "tool": "web_search"}],
        "artifacts": [],
        "status": "RUNNING",
        "stop_requested": False,
        "pending_confirm": {"tool_call_id": "c1", "tool_name": "file_write"},
        "step_index": 3,
        "final_answer": "",
        "error": "",
        "compressed": False,
        "context_tokens": 512,
        "_last_action": "tool_done",
        "_current_tool_calls": [],
        "_confirmed_ids": ["c0"],
        "_rejected_ids": [],
        "_needs_confirm": True,
        "risk_report": [{"level": "high", "keyword": "rm -rf"}],
        "_risk_blocked": False,
        "subtasks": [],
        "_is_subtask": False,
    }


class TestCheckpointRoundTrip:
    def test_put_then_get_returns_identical_state_via_graph_state_channel(
        self, saver
    ):
        """A state saved under our thread_id convention comes back intact.

        We exercise the saver through a minimal StateGraph because channel
        values are what the resume path actually reads back — raw saver.put
        calls bypass langgraph's checkpoint envelope formatting.
        """
        from langgraph.graph import END, START, StateGraph

        class _S(AgentState):
            pass

        def echo(state: AgentState) -> dict:
            return {}

        g = StateGraph(_S)
        g.add_node("echo", echo)
        g.add_edge(START, "echo")
        g.add_edge("echo", END)
        app = g.compile(checkpointer=saver)

        state = _full_state()
        task_id = state["task_id"]
        app.invoke(state, _config(task_id))

        snap = app.get_state(_config(task_id))
        assert snap is not None
        values = dict(snap.values)
        for key, expected in state.items():
            assert values.get(key) == expected, f"field {key!r} lost in round-trip"

    def test_threads_are_isolated(self, saver):
        """Two threads never see each other's checkpoints."""
        from langgraph.graph import END, START, StateGraph

        class _S(AgentState):
            pass

        def mark(state: AgentState) -> dict:
            return {"final_answer": f"answer-of-{state['task_id']}"}

        g = StateGraph(_S)
        g.add_node("mark", mark)
        g.add_edge(START, "mark")
        g.add_edge("mark", END)
        app = g.compile(checkpointer=saver)

        app.invoke({"task_id": "t-a"}, _config("t-a"))
        app.invoke({"task_id": "t-b"}, _config("t-b"))

        sa = app.get_state(_config("t-a"))
        sb = app.get_state(_config("t-b"))
        assert sa.values["final_answer"] == "answer-of-t-a"
        assert sb.values["final_answer"] == "answer-of-t-b"

    def test_resume_invoke_none_continues_from_checkpoint(self, saver):
        """invoke(None, config) resumes without re-running completed work."""
        from langgraph.graph import END, START, StateGraph

        calls = {"n": 0}

        class _S(AgentState):
            pass

        def count(state: AgentState) -> dict:
            calls["n"] += 1
            return {}

        g = StateGraph(_S)
        g.add_node("count", count)
        g.add_edge(START, "count")
        g.add_edge("count", END)
        app = g.compile(checkpointer=saver)

        cfg = _config("t-resume")
        app.invoke({"task_id": "t-resume"}, cfg)
        assert calls["n"] == 1

        # Resuming a finished graph must not re-execute completed nodes.
        app.invoke(None, cfg)
        assert calls["n"] == 1


class TestEmptyStateRoundTrip:
    def test_minimal_state_survives_round_trip(self, saver):
        """A bare-bones state (only task_id) does not crash the checkpoint."""
        from langgraph.graph import END, START, StateGraph

        class _S(AgentState):
            pass

        g = StateGraph(_S)
        g.add_node("noop", lambda s: {})
        g.add_edge(START, "noop")
        g.add_edge("noop", END)
        app = g.compile(checkpointer=saver)

        cfg = _config("t-empty")
        app.invoke({"task_id": "t-empty"}, cfg)
        snap = app.get_state(cfg)
        assert snap.values.get("task_id") == "t-empty"


class TestAgentStateSerde:
    """Sentinel: the saver's serde pipeline must swallow a fully-populated AgentState.

    If someone ever adds a non-serializable field (client instance, lock,
    file handle) to AgentState, these tests are the tripwire.
    """

    def test_full_agentstate_round_trips_through_saver_serde(self, saver):
        from langgraph.graph import END, START, StateGraph

        class _S(AgentState):
            pass

        captured: Dict[str, Any] = {}

        def capture(state: AgentState) -> dict:
            captured.update(state)  # what the node actually received
            return {}  # write nothing back: keep the input state as the checkpoint

        g = StateGraph(_S)
        g.add_node("capture", capture)
        g.add_edge(START, "capture")
        g.add_edge("capture", END)
        app = g.compile(checkpointer=saver)

        state = _full_state()
        cfg = _config(state["task_id"])
        app.invoke(state, cfg)

        # The node must receive every field intact (pre-serialization).
        assert dict(captured) == {k: v for k, v in state.items()}

        snap = app.get_state(cfg)
        # Every input field must survive the serialize -> deserialize cycle
        # and reach the checkpoint snapshot unchanged.
        for key, expected in state.items():
            actual = snap.values.get(key)
            assert actual == expected, (
                f"field {key!r} changed across saver serde: {actual!r} != {expected!r}"
            )

    def test_unserializable_value_is_dropped_by_serde_documented(self, saver):
        """Documented behavior: langgraph's serde silently DROPS values it
        cannot serialize (e.g. threading.Lock) instead of raising.

        This is a real corruption risk for resumed tasks — recorded here so
        the behavior is pinned and noticed if it ever changes. The project
        guard is convention: AgentState stays JSON-safe (see spec Issue #4).
        """
        from langgraph.graph import END, START, StateGraph

        class _S(AgentState):
            pass

        lock = __import__("threading").Lock()

        def bad(state: AgentState) -> dict:
            return {"_secret_lock": lock}  # not a declared AgentState field

        g = StateGraph(_S)
        g.add_node("bad", bad)
        g.add_edge(START, "bad")
        g.add_edge("bad", END)
        app = g.compile(checkpointer=saver)

        cfg = _config("t-bad")
        app.invoke({"task_id": "t-bad"}, cfg)  # must not crash
        snap = app.get_state(cfg)
        # The unusable value did not survive: either absent or still the live
        # object it started as — never a corrupted re-hydrated copy.
        stored = snap.values.get("_secret_lock")
        assert stored is None or stored is lock


# ── Issue #7：迁移前遗留 checkpoint 库的启动检测 ─────────────────────
#
# 上游 sqlite saver 不带 schema 版本表（2.0.11 与 3.1.1 的建表 SQL 逐字符相同），
# 所以「这个库是升级前写的」只能靠本仓库自己打的标记识别：
# ``PRAGMA user_version == CHECKPOINT_FORMAT_VERSION``。判定规则与理由见
# docs/migration-langgraph-1x.md §2.4。

#: langgraph-checkpoint-sqlite 2.0.11 ``SqliteSaver.setup()`` 的建表 SQL 原文。
_LEGACY_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint BLOB,
    metadata BLOB,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);
CREATE TABLE IF NOT EXISTS writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    value BLOB,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);
"""


def _user_version(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _write_legacy_store(db_path: Path, rows: int = 1) -> Path:
    """造一个「迁移前写的」checkpoint 库：2.0.11 的两张表 + ``rows`` 行快照。

    ``PRAGMA user_version`` 保持 sqlite 默认的 0 —— 这正是检测的判据：本仓库
    挂载成功后会把它打成 ``CHECKPOINT_FORMAT_VERSION``，而迁移前的文件不可能有。
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_LEGACY_SCHEMA)
        for i in range(rows):
            conn.execute(
                "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id,"
                " parent_checkpoint_id, type, checkpoint, metadata)"
                " VALUES (?, '', ?, NULL, 'msgpack', NULL, NULL)",
                (f"legacy-{i}", f"1ef00000-0000-0000-0000-{i:012d}"),
            )
        conn.commit()
    finally:
        conn.close()
    assert _user_version(db_path) == 0, "fixture 必须造出未打标的库"
    return db_path


class _LogCollector(logging.Handler):
    """``get_logger()`` 把 propagate 关掉了，pytest 的 caplog 收不到这些记录，
    因此直接把 handler 挂在目标 logger 上。"""

    def __init__(self) -> None:
        super().__init__()
        self.records: List[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def messages(self, min_level: int = logging.ERROR) -> List[str]:
        return [r.getMessage() for r in self.records if r.levelno >= min_level]


@pytest.fixture
def tm_logs():
    """Collect TaskManager log records for the duration of one test."""
    logger = logging.getLogger("agent.task_manager")
    collector = _LogCollector()
    logger.addHandler(collector)
    try:
        yield collector
    finally:
        logger.removeHandler(collector)


def _interrupted_task(settings, task_id: str) -> None:
    """直接落一条 INTERRUPTED 记录（resume 的入口条件），不跑任何图。"""
    now = datetime.now(timezone.utc).isoformat()
    Persistence(settings).save_task(
        Task(
            id=task_id,
            title=f"interrupted {task_id}",
            user_input="demo input",
            status=TaskStatus.INTERRUPTED,
            created_at=now,
            updated_at=now,
        )
    )


def _wait_terminal(tm, task_id: str, timeout: float = 15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = tm.get_task(task_id)
        if task is not None and task.status.value in (
            "COMPLETED",
            "FAILED",
            "INTERRUPTED",
        ):
            return task
        time.sleep(0.05)
    return tm.get_task(task_id)


class TestPreMigrationStoreGuard:
    """旧格式快照：检测 → 告警 → 拒绝挂载（迁移 spec 用户故事 8）。"""

    def test_legacy_store_is_refused_with_actionable_error(self, tmp_path, tm_logs):
        settings = make_settings(tmp_path, checkpoint_enabled=True)
        db = _write_legacy_store(settings.checkpoint_path / "checkpoints.sqlite")

        # 拒绝挂载 ≠ 拒绝启动：构造过程必须不抛。
        tm = make_manager(settings, MockLLMClient())

        assert tm._checkpointer is None
        errors = tm_logs.messages(logging.ERROR)
        assert errors, "旧格式库必须被 ERROR 级告警，不能静默"
        assert any(str(db) in m for m in errors), errors
        # 提示文案要把人指向文档，而不是只说“坏了”。
        assert any("docs/migration-langgraph-1x.md" in m for m in errors), errors

    def test_resume_over_legacy_store_is_refused_as_no_checkpoint(self, tmp_path):
        settings = make_settings(tmp_path, checkpoint_enabled=True)
        _write_legacy_store(settings.checkpoint_path / "checkpoints.sqlite")
        _interrupted_task(settings, "t-legacy")
        tm = make_manager(settings, MockLLMClient())

        # 拒绝挂载后走既有的「no checkpoint → 409」路径，闸门不被静默绕过。
        with pytest.raises(RuntimeError) as excinfo:
            tm.resume("t-legacy")
        assert "no checkpoint" in str(excinfo.value)

    def test_new_tasks_still_run_with_legacy_store_present(self, tmp_path):
        """拒绝挂载只影响 resume：服务照常起、新任务照常跑完。"""
        settings = make_settings(tmp_path, checkpoint_enabled=True)
        _write_legacy_store(settings.checkpoint_path / "checkpoints.sqlite")
        tm = make_manager(settings, MockLLMClient(final_answer="done anyway"))

        task_id = tm.create_task(title="fresh", user_input="hello")
        task = _wait_terminal(tm, task_id)

        assert task.status == TaskStatus.COMPLETED
        assert task.final_answer == "done anyway"

    def test_empty_legacy_store_is_accepted(self, tmp_path):
        """0 行的旧库无可腐蚀数据，放行（否则全新环境会被自己的空文件挡住）。"""
        settings = make_settings(tmp_path, checkpoint_enabled=True)
        _write_legacy_store(settings.checkpoint_path / "checkpoints.sqlite", rows=0)

        tm = make_manager(settings, MockLLMClient())

        assert tm._checkpointer is not None

    def test_fresh_store_is_stamped_on_mount(self, tmp_path):
        settings = make_settings(tmp_path, checkpoint_enabled=True)
        db = settings.checkpoint_path / "checkpoints.sqlite"
        assert not db.exists()

        make_manager(settings, MockLLMClient())

        assert _user_version(db) == CHECKPOINT_FORMAT_VERSION

    def test_own_writes_are_not_mistaken_for_legacy_on_restart(self, tmp_path, tm_logs):
        """假阳性会直接废掉 P3：本版本写的库，重启后必须仍被接受。"""
        settings = make_settings(tmp_path, checkpoint_enabled=True)
        tm1 = make_manager(settings, MockLLMClient(final_answer="first era"))
        task_id = tm1.create_task(title="ckpt", user_input="hello")
        assert _wait_terminal(tm1, task_id).status == TaskStatus.COMPLETED
        tm1.shutdown()

        db = settings.checkpoint_path / "checkpoints.sqlite"
        assert _user_version(db) == CHECKPOINT_FORMAT_VERSION

        tm2 = make_manager(settings, MockLLMClient())

        assert tm2._checkpointer is not None
        assert not [m for m in tm_logs.messages(logging.ERROR) if str(db) in m]


# ── Issue #7：durability="sync"（本次迁移显式采用的 1.x 新特性）───────────


class _StoreProbeTool(BaseTool):
    """在 tool 节点里像 crash-rescue 进程那样直接读 checkpoint 库。

    不复用 TaskManager 的连接，而是自己开一个：这就是“另一个进程能不能接着
    跑”的真实读法。读到的东西存在 ``observed`` 里给测试断言。
    """

    name = "store_probe"
    description = "probe the checkpoint store from inside a running graph"
    args_schema: Dict[str, Any] = {"type": "object", "properties": {}}
    requires_confirm = False
    retryable = False
    circuit_breaker = False

    def __init__(self, db_path: Path) -> None:
        super().__init__(None)
        self.db_path = db_path
        self.observed: Dict[str, Any] = {}

    def run(self, **kwargs: Any) -> ToolResult:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        try:
            threads = [
                r[0] for r in conn.execute("SELECT DISTINCT thread_id FROM checkpoints")
            ]
            latest: Dict[str, Any] = {}
            saver = SqliteSaver(conn)
            for tid in threads:
                tup = saver.get_tuple({"configurable": {"thread_id": tid}})
                if tup is not None:
                    latest = (tup.checkpoint or {}).get("channel_values", {}) or {}
            self.observed = {
                "threads": threads,
                "last_action": latest.get("_last_action"),
                "pending_tool_calls": list(latest.get("_current_tool_calls") or []),
            }
        finally:
            conn.close()
        return ToolResult(success=True, data=dict(self.observed))


class TestDurabilitySync:
    """``durability="sync"`` 是 resume 契约的地基。

    实测（每种模式 12 次重复，详见 docs/migration-langgraph-1x.md §5）：
    ``sync`` → 后一个 superstep 12/12 读得到前一个 superstep 的快照；
    ``async``（= 1.x 默认）→ 0/12；``exit`` → 0/12（运行中一行都不写）。
    两个方向都是确定的，所以下面两条断言不是 flaky：把 task_manager 的
    ``_DURABILITY`` 改回默认值，它们必红。
    """

    def test_sync_makes_the_previous_superstep_durable(self, tmp_path):
        """框架层保证：sync 下上一个 superstep 的写入已落盘。"""
        from langgraph.graph import END, START, StateGraph

        conn = sqlite3.connect(str(tmp_path / "dur.sqlite"), check_same_thread=False)
        saver = SqliteSaver(conn)
        cfg = _config("t-dur")
        seen: Dict[str, Any] = {}

        class _S(AgentState):
            pass

        def first(state: AgentState) -> dict:
            return {"_last_action": "tool_call"}

        def second(state: AgentState) -> dict:
            tup = saver.get_tuple(cfg)
            vals = (tup.checkpoint or {}).get("channel_values", {}) if tup else {}
            seen["durable"] = vals.get("_last_action") == "tool_call"
            return {}

        g = StateGraph(_S)
        g.add_node("first", first)
        g.add_node("second", second)
        g.add_edge(START, "first")
        g.add_edge("first", "second")
        g.add_edge("second", END)
        app = g.compile(checkpointer=saver)

        app.invoke({"task_id": "t-dur"}, cfg, durability="sync")
        conn.close()

        assert seen["durable"] is True

    def test_run_path_persists_the_executor_write_before_the_tool_node(self, tmp_path):
        """生产路径（TaskManager.run）确实要了 sync。

        探针工具在 tool 节点里读库，断言 executor 那个 superstep 的写入
        （``_last_action`` / ``_current_tool_calls``）已经在磁盘上——这正是
        stop() 之后另一个进程能不能续跑的前提。
        """
        settings = make_settings(tmp_path, checkpoint_enabled=True)
        probe = _StoreProbeTool(settings.checkpoint_path / "checkpoints.sqlite")
        mock = MockLLMClient(
            plan=["probe the store"],
            tool_calls=[{"id": "c1", "name": "store_probe", "arguments": {}}],
            final_answer="probed",
        )
        tm = TaskManager(
            settings,
            EventBus(),
            Persistence(settings),
            llm_client=mock,
            tools=[probe],
        )
        try:
            task_id = tm.create_task(title="probe", user_input="hello")
            task = _wait_terminal(tm, task_id)
        finally:
            tm.shutdown()

        assert task.status == TaskStatus.COMPLETED, task.error
        assert probe.observed.get("threads"), "tool 节点执行时库里应已有本任务的 thread"
        assert probe.observed.get("last_action") == "tool_call", probe.observed
        calls = probe.observed.get("pending_tool_calls") or []
        assert calls and calls[0].get("tool_name") == "store_probe", probe.observed
