"""Task lifecycle orchestration.

Owns task creation, runs the LangGraph graph on a background thread, and
exposes stop / query APIs. The graph nodes publish events through the
:class:`EventBus` and register artifacts through :class:`Persistence`. A
``stop_requested`` flag (checked at each node entry) guarantees the ≤2s
interrupt requirement (P0-9).
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, cast

from langgraph.types import Durability

from ..api.schemas import (
    Artifact,
    ChatTurn,
    PlanStep,
    StepRecord,
    SubTask,
    Task,
    TaskMode,
    TaskStatus,
)
from ..config import Settings
from ..core.agent.graph import build_graph
from ..core.agent.nodes import AgentRuntime
from ..core.agent.state import AgentState
from ..core.agent.subagent import SubAgentExecutor
from ..core.kb.knowledge_base import KnowledgeBase, set_kb_instance
from ..core.llm.client import LLMClient
from ..core.llm.openai_compat import create_aux_llm_client, create_llm_client
from ..core.tools.base import BaseTool
from ..core.tools.kb_tools import KbQueryTool, MemorySearchTool
from ..core.tools.registry import build_tools, discover_plugins
from ..core.tools.subagent_tool import SpawnSubagentTool
from ..utils.logging import get_logger
from .event_bus import EventBus
from .persistence import Persistence
from .snapshots import cleanup_expired, restore_task, set_current_task_id
from .trace import TraceRecorder

if TYPE_CHECKING:  # 只给类型检查器：运行期 McpClientManager 由 _load_mcp_tools 懒加载
    from ..core.mcp.client import McpClientManager

logger = get_logger("task_manager")

# langgraph 1.x durability mode — the one 1.x feature this migration adopts
# (Issue #7; selection rationale in docs/migration-langgraph-1x.md §5).
#
# "sync" completes the checkpoint write before the next superstep starts, which
# is exactly what the resume contract needs: stop() -> INTERRUPTED has to leave
# a snapshot on disk that a rebuilt process can continue from. The 1.x default
# ("async") hands the write to a background executor — that is the race the
# double-probe in _has_checkpoint() was written around — and "exit" writes
# nothing until the run ends, which would break stop-resume outright.
# Measured over 12 runs per mode: a node in superstep N+1 sees superstep N's
# checkpoint 12/12 under "sync", 0/12 under "async"/default, 0/12 under "exit".
_DURABILITY: Durability = "sync"

# Checkpoint store format stamp owned by this codebase (Issue #7 migration).
#
# langgraph's sqlite saver keeps no schema-version table of its own — 2.0.11
# and 3.1.1 run the very same ``CREATE TABLE IF NOT EXISTS`` script — so an
# upgraded stack cannot tell "written before the 0.2→1.2.x migration" from
# "written by it" without a marker we write ourselves. ``PRAGMA user_version``
# is an integer in the sqlite header that ``SqliteSaver`` never reads and never
# resets, which makes it the cheapest honest home. The A/B measurement behind
# this (3.1.1 *can* read 2.0.11 snapshots losslessly, and why we refuse anyway)
# is recorded in docs/migration-langgraph-1x.md §2.
#
# v2 = S5 (#139): planner/executor merged into one ``agent`` node. A v1
# snapshot was written by a different graph, so continuing its pregel schedule
# under this build is exactly the "never proven" case the stamp exists to
# refuse — the store is refused, not silently replayed.
CHECKPOINT_FORMAT_VERSION = 2

# Where the refusal message sends the operator.
_CHECKPOINT_FORMAT_DOC = "docs/migration-langgraph-1x.md"


def _count_store_rows(conn) -> int:
    """Rows across both checkpoint tables (either alone can pin a resume).

    ``writes`` holds the pending-task records a resumed superstep replays, so a
    store with only ``writes`` rows is not empty either. Table names come from a
    fixed literal tuple, never from input — one of the CVEs closed by 3.1.1 was
    exactly an injected filter key reaching SQL.
    """
    total = 0
    for table in ("checkpoints", "writes"):
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if exists:
            total += int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    return total


def _inspect_checkpoint_store(db_path: Path) -> tuple[bool, str, str]:
    """Decide whether an existing checkpoint sqlite file may be mounted.

    Returns ``(usable, kind, detail)``. ``kind`` is one of ``new`` / ``empty`` /
    ``stamped`` (usable) or ``legacy`` / ``older`` / ``newer`` / ``unreadable``
    (refused); the caller picks the operator-facing text from ``kind``, because
    "this predates the migration, move it away" and "this file is broken or
    locked, go
    fix the cause" call for opposite remediation.

    A store is usable only when it carries *exactly* this build's format stamp.
    An unstamped store with rows can only predate the migration, and a higher
    stamp means a newer build wrote it; either way resuming from it would bet
    the P3 contract on execution-position equivalence that was never proven
    (rationale + A/B measurement: docs/migration-langgraph-1x.md §2).

    Opened ``mode=ro`` on purpose: the last read-write connection to close on a
    WAL database makes sqlite fold ``-wal`` into the main file and drop the
    ``-wal`` / ``-shm`` sidecars — i.e. the mere act of *deciding to refuse*
    would rewrite the bytes an operator may still want to inspect with the old
    stack. Read-only also means no tables are ever created here.
    """
    if not db_path.exists():
        return True, "new", "no store yet"
    import sqlite3

    try:
        conn = sqlite3.connect(
            f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=10.0
        )
        try:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version == CHECKPOINT_FORMAT_VERSION:
                return True, "stamped", f"format v{version}"
            if version > CHECKPOINT_FORMAT_VERSION:
                return False, "newer", (
                    f"stamped format v{version} by a newer build (this build "
                    f"writes v{CHECKPOINT_FORMAT_VERSION})"
                )
            if version > 0:
                # Stamped by an older build of this codebase. Since #139 that is
                # a *different graph* (planner/executor → agent), so its schedule
                # is not something this build can vouch for either.
                return False, "older", (
                    f"stamped format v{version} by an older build (this build "
                    f"writes v{CHECKPOINT_FORMAT_VERSION})"
                )
            rows = _count_store_rows(conn)
            if rows == 0:
                return True, "empty", "no snapshots to corrupt"
            return False, "legacy", (
                f"{rows} pre-migration row(s) in checkpoints/writes, no format "
                f"stamp (this build writes v{CHECKPOINT_FORMAT_VERSION})"
            )
        finally:
            conn.close()
    except Exception as exc:  # corrupt / locked / not a sqlite file at all
        # Refuse rather than crash startup: an unreadable store must not take the
        # whole service down, and must not be mounted half-way either.
        return False, "unreadable", f"{type(exc).__name__}: {exc}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskManager:
    def __init__(
        self,
        settings: Settings,
        event_bus: EventBus,
        persistence: Persistence,
        llm_client: Optional[LLMClient] = None,
        tools: Optional[List[BaseTool]] = None,
    ) -> None:
        self.settings = settings
        self.event_bus = event_bus
        self.persistence = persistence
        # P0 item 3: auto-discover plugin tools before building the tool list.
        if settings.plugins_autoload:
            discover_plugins(settings.plugins_path)
        self._llm = llm_client or create_llm_client(settings)
        self._tools = tools if tools is not None else build_tools(settings)
        # P1 item 4: auxiliary model client (None when disabled -> degradation).
        self._aux_llm = create_aux_llm_client(settings)
        # P1 item 2: sub-agent executor (None when disabled).
        self._subagent = SubAgentExecutor(self, settings) if settings.subagent_enabled else None
        # P1 item 3: knowledge base singleton (empty instance when disabled).
        self._kb = KnowledgeBase(settings)
        set_kb_instance(self._kb)
        # P1 item 6: append OpenAPI-generated tools after the built-ins/plugins
        # (first-registered-wins conflict semantics, invalid spec -> warning only).
        self._load_openapi_tools(settings)
        # P2 item 1: connect MCP servers and append their tools (startup once).
        self._mcp: Optional[McpClientManager] = None
        self._load_mcp_tools(settings)
        # P2 item 2: append Git tools (git_enabled switch, no @register).
        self._load_git_tools(settings)
        # Issue #56: ``subagent_enabled`` now gates exactly one thing — the
        # ``spawn_subagent`` tool. It used to also mount the keyword-triggered
        # ``subagent_split`` node, which could dispatch a subtask (and its side
        # effects) with nobody approving it; that entry is gone, so "off" simply
        # keeps the tool off the face.
        if not settings.subagent_enabled:
            self._tools = [t for t in self._tools if t.name != "spawn_subagent"]
        self._tool_schemas = [t.to_openai_schema() for t in self._tools]
        self._wire_injected_tools()
        # P0 item 4: resident trace recorder (EventBus subscriber).
        self._trace: Optional[TraceRecorder] = TraceRecorder(settings) if settings.trace_enabled else None

        self._lock = threading.Lock()
        self._active_states: Dict[str, AgentState] = {}
        self._threads: Dict[str, threading.Thread] = {}
        # Issue #4: with a checkpointer mounted, langgraph hands nodes a
        # per-superstep COPY of the state, so mutating ``_active_states``
        # (legacy path) never reaches a running graph. This authoritative
        # flag is what nodes poll instead; cleared when the run finishes.
        # Sub-agent ids (Issue #71) enter the dict too and have no run
        # teardown to pop them — ``SubAgentExecutor._request_worker_stop``
        # clears those from the subtask future's done callback instead.
        self._stop_flags: Dict[str, bool] = {}
        # #139: a round-end "停下" is published by _finalize_terminal, while a
        # human stop publishes immediately from stop(). This set keeps the two
        # from announcing the same park twice (the event is identical by design:
        # ADR 0008 decision 5 — all stops look the same).
        self._interrupt_announced: set[str] = set()
        # Spec Issue #4 (D1/D2): durable checkpointer — one sqlite file per
        # settings identity (checkpoint_path), not per manager instance: the
        # resume contract requires a rebuilt TaskManager to see snapshots
        # written by its predecessor. Within one process, concurrent managers
        # on the same file rely on sqlite WAL locking.
        self._checkpointer: Any = None
        self._checkpoint_conn: Any = None
        if settings.checkpoint_enabled:
            self._mount_checkpointer(settings)
        # Spec Issue #4 (D5): crash recovery reconciliation — a fresh process
        # owns no execution threads, so persisted RUNNING tasks are orphans.
        self._reconcile_orphans()
        # P1-B (拍板 11): the start-up sweep — no scheduler host yet, so expired
        # task snapshot dirs are dropped by the process that starts next.
        cleanup_expired(settings=settings)

    def _mount_checkpointer(self, settings: Settings) -> None:
        """Mount the sqlite saver, or refuse a store this build cannot vouch for.

        Refusing leaves ``_checkpointer`` as ``None``: the service still starts
        and new tasks still run their FIRST round — but without a checkpointer
        there is nothing to resume or continue from, so ``resume`` and
        ``send_message`` answer 409 for every task (AGENTS.md「任务路由」: 两类
        409 拒绝不可放松) until the file is moved away. "New tasks are unaffected"
        is therefore false and must not be claimed. No automatic migration and
        no partial mount — a refused file is never written to, so moving it away
        is the whole remediation.
        """
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver

        ckpt_dir = settings.checkpoint_path
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        db_path = ckpt_dir / "checkpoints.sqlite"
        usable, kind, detail = _inspect_checkpoint_store(db_path)
        if not usable:
            self._log_refused_store(db_path, kind, detail)
            return
        conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            timeout=10.0,  # tolerate the predecessor's in-flight writes
        )
        # Stamp before the saver writes anything, so a store created by this
        # version is never again mistaken for a pre-migration one. A failed
        # stamp must degrade to "not mounted": letting it escape would turn a
        # read-only / locked / full data dir into a FastAPI startup crash,
        # strictly worse than pre-migration behavior (the old constructor never
        # wrote, so a bad store only ever failed one task inside run()).
        try:
            conn.execute(f"PRAGMA user_version = {int(CHECKPOINT_FORMAT_VERSION)}")
            conn.commit()
        except Exception as exc:
            try:
                conn.close()
            except Exception:  # pragma: no cover - defensive
                pass
            logger.error(
                "Checkpoint store %s is not writable (%s: %s); refusing to mount. "
                "Fix the cause (permissions / disk full / stale lock) — do NOT "
                "delete snapshots for this. Resume and continuation stay disabled "
                "for every task (new ones run one round, then cannot continue) "
                "until the store is writable.",
                db_path,
                type(exc).__name__,
                exc,
            )
            return
        self._checkpoint_conn = conn
        self._checkpointer = SqliteSaver(conn)
        logger.info("Checkpoint store mounted: %s (%s)", db_path, detail)

    def _log_refused_store(self, db_path: Path, kind: str, detail: str) -> None:
        """Refusal text per cause — the causes need opposite remediation.

        A pre-migration (or newer-format) store is void by design: move it away.
        An unreadable one is a permissions / integrity / locking problem: moving
        or deleting it destroys good snapshots and fixes nothing.
        """
        if kind == "unreadable":
            logger.error(
                "Checkpoint store %s is unreadable (%s); refusing to mount. This "
                "is not a migration artifact — check permissions / integrity / "
                "locks rather than deleting snapshots. Resume and continuation "
                "stay disabled until it is readable; new tasks run one round but "
                "cannot be continued either.",
                db_path,
                detail,
            )
            return
        logger.error(
            "Checkpoint store %s refused: %s. Snapshots written under a different "
            "checkpoint format are void by design — reading them back may well "
            "work, but continuing that pregel schedule under this scheduler was "
            "never proven, and data/ is disposable runtime state. Resume and "
            "continuation stay disabled for EVERY task (new ones included) until "
            "the file is moved away — a new task runs its first round, then "
            "parks with no checkpoint to continue from. Rationale and "
            "measurement: %s.",
            db_path,
            detail,
            _CHECKPOINT_FORMAT_DOC,
        )

    def _reconcile_orphans(self) -> None:
        """Mark persisted RUNNING/PENDING tasks as INTERRUPTED on startup.

        Runs exactly once per construction (the constructor), when no worker
        threads exist yet, so every non-terminal record in tasks.json is by
        definition an orphan of a previous process (PENDING covers the crash
        window between create_task() persisting and the thread flipping the
        status to RUNNING). Terminal statuses are left untouched.
        """
        reconciled = 0
        for task in self.persistence.list_all():
            if task.status not in (TaskStatus.RUNNING, TaskStatus.PENDING):
                continue
            try:
                orphan_status = task.status.value
                task.status = TaskStatus.INTERRUPTED
                task.updated_at = _now()
                self.persistence.save_task(task)
                reconciled += 1
                logger.warning(
                    "Orphaned %s task %s reconciled to INTERRUPTED",
                    orphan_status,
                    task.id,
                )
            except Exception:  # per-task isolation: one bad record can't stall the sweep
                logger.exception("Failed to reconcile orphan task %s", task.id)
        if reconciled:
            logger.info("Startup reconciliation completed: %d task(s).", reconciled)

    # ── P1 wiring of runtime-injected tools ──
    def _wire_injected_tools(self) -> None:
        """Give stateful tools their service references (subagent / KB)."""
        for t in self._tools:
            if isinstance(t, SpawnSubagentTool):
                t.executor = self._subagent
            elif isinstance(t, (MemorySearchTool, KbQueryTool)):
                t.kb = self._kb
            # McpTool holds its McpClientManager reference at construction time,
            # so it needs no wiring here.

    # ── P1 item 6: OpenAPI tool registration ──
    def _load_openapi_tools(self, settings: Settings) -> None:
        if not settings.openapi_enabled:
            return
        from ..core.tools.openapi_tool import OpenAPISpecError, build_tools_from_spec, load_openapi_spec

        source = (settings.openapi_spec_path or "").strip() or (settings.openapi_spec_url or "").strip()
        if not source:
            logger.info("openapi_enabled but no spec_path/spec_url configured; skipping.")
            return
        try:
            spec = load_openapi_spec(source)
            generated = build_tools_from_spec(spec, settings=settings)
        except OpenAPISpecError as exc:
            logger.warning("OpenAPI tools disabled: %s", exc)
            return
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("OpenAPI tools disabled (unexpected error): %s", exc)
            return
        existing = {t.name for t in self._tools}
        added = 0
        for t in generated:
            if t.name in existing:
                logger.warning(
                    "OpenAPI tool %r conflicts with an existing tool; keeping the existing one.",
                    t.name,
                )
                continue
            self._tools.append(t)
            existing.add(t.name)
            added += 1
        if added:
            logger.info("Registered %d OpenAPI-generated tool(s).", added)

    # ── P2 item 1: MCP client tool registration ──
    def _load_mcp_tools(self, settings: Settings) -> None:
        """Connect configured MCP servers and append their tools.

        Uses the same first-registered-wins conflict semantics as OpenAPI:
        ``_tools`` already contains built-ins/plugins/OpenAPI tools, so a name
        clash keeps the existing tool and logs a warning. A missing SDK or a
        failing server only logs a warning — startup always continues.
        """
        if not settings.mcp_enabled:
            logger.info("mcp_enabled=false; skipping MCP tools.")
            return
        from ..core.mcp.client import McpClientManager
        from ..core.tools.mcp_tool import McpTool

        manager = McpClientManager(settings)
        self._mcp = manager
        try:
            discovered = manager.connect_all()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("MCP connect_all failed: %s", exc)
            return
        if not discovered:
            return
        existing = {t.name for t in self._tools}
        added = 0
        for item in discovered:
            name = item.get("name", "")
            if not name:
                continue
            tool = McpTool(
                server_name=item.get("server", ""),
                tool_name=name,
                description=item.get("description", ""),
                input_schema=item.get("input_schema") or {},
                manager=manager,
                settings=settings,
                # #57: the server's own danger report; a missing key here would
                # silently turn every tool into "unknown" (= always ask).
                annotations=item.get("annotations") or {},
            )
            if tool.name in existing:
                logger.warning(
                    "MCP tool %r conflicts with an existing tool; keeping the existing one.",
                    tool.name,
                )
                continue
            self._tools.append(tool)
            existing.add(tool.name)
            added += 1
        if added:
            logger.info("Registered %d MCP tool(s).", added)

    # ── P2 item 2: Git tool registration ──
    def _load_git_tools(self, settings: Settings) -> None:
        """Append the Git tool set when ``git_enabled`` (never via @register)."""
        if not settings.git_enabled:
            logger.info("git_enabled=false; skipping Git tools.")
            return
        from ..core.tools.git_tools import build_git_tools

        generated = build_git_tools(settings)
        if not generated:
            return
        existing = {t.name for t in self._tools}
        added = 0
        for t in generated:
            if t.name in existing:
                logger.warning(
                    "Git tool %r conflicts with an existing tool; keeping the existing one.",
                    t.name,
                )
                continue
            self._tools.append(t)
            existing.add(t.name)
            added += 1
        if added:
            logger.info("Registered %d Git tool(s).", added)

    # ── P2 item 1: graceful shutdown ──
    def shutdown(self) -> None:
        """Release process-level resources (MCP child processes).

        Idempotent: calling it multiple times, or constructing a new
        :class:`TaskManager` over an old one, never leaks connections.
        """
        mcp = getattr(self, "_mcp", None)
        if mcp is not None:
            try:
                mcp.cleanup()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("MCP cleanup failed: %s", exc)
            self._mcp = None
        # Let in-flight workers finish committing their checkpoints BEFORE the
        # sqlite connection goes away — closing under a writing thread is what
        # corrupted the shared WAL file and crashed native sqlite3.
        for th in list(getattr(self, "_threads", {}).values()):
            if th is not threading.current_thread() and th.is_alive():
                th.join(timeout=5.0)
        conn = getattr(self, "_checkpoint_conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("checkpoint connection close failed: %s", exc)
            self._checkpointer = None
            self._checkpoint_conn = None

    # ── creation / query ──
    def create_task(
        self,
        title: Optional[str],
        user_input: str,
        mode: str = TaskMode.BUILD.value,
    ) -> str:
        """Start one task (ADR 0008 decision 1: one task = one conversation).

        ``mode`` is the sticky task switch; default is build (decision 3).
        """
        task_id = uuid.uuid4().hex
        now = _now()
        task = Task(
            id=task_id,
            title=(title or user_input[:40]).strip(),
            user_input=user_input,
            status=TaskStatus.PENDING,
            mode=TaskMode(mode),
            conversation=[ChatTurn(role="user", content=user_input)],
            created_at=now,
            updated_at=now,
        )
        # P0 item 4: attach the trace recorder before any event is published so
        # the JSONL captures task_created onwards.
        if self._trace is not None:
            self._trace.attach(self.event_bus, task_id)
        self.persistence.save_task(task)
        self.event_bus.publish(
            task_id,
            "task_created",
            {"task_id": task_id, "title": task.title, "status": task.status.value},
        )
        self._start_run(task_id)
        return task_id

    def get_task(self, task_id: str) -> Optional[Task]:
        return self.persistence.load_task(task_id)

    def list_tasks(self, limit: int = 50) -> List[Task]:
        return self.persistence.list_tasks(limit)

    def get_artifact(self, task_id: str, artifact_id: str) -> Optional[bytes]:
        return self.persistence.read_artifact(artifact_id)

    # ── run loop ──
    def _start_run(self, task_id: str) -> None:
        t = threading.Thread(target=self.run, args=(task_id,), daemon=True)
        self._threads[task_id] = t
        t.start()

    def run(self, task_id: str) -> None:
        # P1-B: account every sandbox write on this worker thread to this task
        # (the contextvar dies with the thread; sub-agents inherit it).
        set_current_task_id(task_id)
        task = self.persistence.load_task(task_id)
        if task is None:
            logger.warning("run: unknown task %s", task_id)
            return

        task.status = TaskStatus.RUNNING
        task.updated_at = _now()
        self.persistence.save_task(task)

        state: AgentState = {
            "task_id": task_id,
            "mode": task.mode.value,
            "messages": [{"role": "user", "content": task.user_input}],
            "plan": [p.model_dump() for p in task.plan],
            "steps": [],
            "artifacts": [],
            "status": "RUNNING",
            "stop_requested": False,
            "step_index": 0,
            "round_steps": 0,
            "final_answer": "",
            "error": "",
            "_last_action": "",
            "_current_tool_calls": [],
            "subtasks": [],
            "_is_subtask": False,
        }
        self._active_states[task_id] = state

        try:
            runtime = AgentRuntime(
                task_id=task_id,
                task_manager=self,
                llm=self._llm,
                tools=self._tools,
                tool_schemas=self._tool_schemas,
                max_steps=self.settings.max_steps,
                aux_llm=self._aux_llm,
            )
            graph = build_graph(runtime, checkpointer=self._checkpointer)
            final = graph.invoke(
                state, self._thread_config(task_id), **self._invoke_kwargs()
            )

            task = self.persistence.load_task(task_id) or task
            self._finalize_terminal(task, final)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("run() crashed for task %s", task_id)
            task.status = TaskStatus.FAILED
            task.error = str(exc)
            task.updated_at = _now()
            self.persistence.save_task(task)
            self.event_bus.publish(
                task_id, "task_failed", {"task_id": task_id, "error": str(exc)}
            )
        finally:
            self._active_states.pop(task_id, None)
            self._threads.pop(task_id, None)
            self._stop_flags.pop(task_id, None)
            self._interrupt_announced.discard(task_id)
            # P0 item 4: always close the trace (success / failure / interrupt)
            # so the JSONL terminates with a trace_end line.
            if self._trace is not None:
                self._trace.close(task_id)

    # ── control ──
    def stop(self, task_id: str) -> Dict[str, Any]:
        """Ask the live run to stop; a parked task is left alone.

        The stop flag is authoritative for checkpointer-backed runs (nodes poll
        it because their state dicts are per-superstep copies). It is set ONLY
        when the run is still live: on a parked/completed record nothing would
        pop it (a task's flag is cleared in its run teardown), so a harmless
        second click — or a stop against a parked task — would otherwise poison
        the next continuation, which the first node entry would then abort.
        """
        task = self.persistence.load_task(task_id)
        live = task is not None and task.status in (
            TaskStatus.RUNNING,
            TaskStatus.PENDING,
        )
        st = self._active_states.get(task_id)
        if st is not None or live:
            if st is not None:
                st["stop_requested"] = True
            self._stop_flags[task_id] = True

        if live and task is not None:
            task.status = TaskStatus.INTERRUPTED
            task.updated_at = _now()
            self.persistence.save_task(task)
            # Claim the announcement before publishing: _finalize_terminal will
            # see the claim and stay quiet, so a human stop is one event, not two.
            if self._claim_interrupt(task_id):
                self.event_bus.publish(
                    task_id,
                    "task_interrupted",
                    {"task_id": task_id, "status": TaskStatus.INTERRUPTED.value},
                )
            return {"ok": True, "status": TaskStatus.INTERRUPTED.value}
        return {"ok": True, "status": task.status.value if task else "UNKNOWN"}

    def _claim_interrupt(self, task_id: str) -> bool:
        """Claim the one ``task_interrupted`` announcement for this run.

        ``stop()`` and ``_finalize_terminal`` can race inside the window where
        finalize has published but not yet persisted the parked status; claiming
        under the manager lock makes "one stop, one event" hold either way.
        """
        with self._lock:
            if task_id in self._interrupt_announced:
                return False
            self._interrupt_announced.add(task_id)
            return True

    def is_stop_flagged(self, task_id: str) -> bool:
        """Peek (not consume) the authoritative stop signal — nodes poll this
        every cycle. A task's own flag is popped in run
        teardown; a sub-agent's is popped by the executor's done callback
        (Issue #71), since a subtask never enters ``_active_states``."""
        return self._stop_flags.get(task_id, False)

    # ── spec Issue #4: checkpoint-backed resume ──
    def _invoke_kwargs(self) -> Dict[str, Any]:
        """Extra ``invoke`` kwargs: durability is only asked for when mounted.

        langgraph 1.2.11 warns that ``durability`` has no effect without a
        checkpointer — and then trips over it anyway (``SyncPregelLoop`` has no
        ``_put_checkpoint_fut``, so a ``"sync"`` run dies with AttributeError on
        the first superstep). A refused pre-migration store leaves us in exactly
        that no-checkpointer state, and it must degrade to "resume unavailable",
        never to "tasks crash". Caught by
        test_new_tasks_run_one_round_with_refused_store_but_cannot_continue.
        """
        return {"durability": _DURABILITY} if self._checkpointer is not None else {}

    def _thread_config(self, task_id: str) -> Dict[str, Any]:
        """LangGraph runnable config under the thread_id == task_id convention.

        Also lifts the default recursion limit (25). The loop runs 2 nodes per
        cycle (agent→tool), so ×8 leaves ample headroom — the limit is a ceiling,
        not a budget.
        """
        return {
            "configurable": {"thread_id": task_id},
            "recursion_limit": self.settings.max_steps * 8 + 20,
        }

    def _has_checkpoint(self, task_id: str) -> bool:
        """True when a durable snapshot exists under thread_id == task_id.

        Double-probes with a short grace pause: the first superstep's put can
        land a few hundred ms after stop() flips the persisted status, so one
        confirmation round avoids false "legacy / no checkpoint" errors.
        """
        if self._checkpointer is None:
            return False
        for attempt in range(2):
            try:
                if self._checkpointer.get_tuple(self._thread_config(task_id)) is not None:
                    return True
            except Exception:
                logger.exception("checkpoint lookup failed for %s", task_id)
                return False
            if attempt == 0:
                time.sleep(0.3)
        return False

    def _finalize_terminal(self, task: Task, final: Dict[str, Any]) -> None:
        """Map a finished run onto the persisted task record.

        Shared by run(), resume() and _continue_run(). Publishes the outcome
        BEFORE persisting the settled status so an observer polling get_task()
        never sees it ahead of the event.

        Since #139 there is no COMPLETED: a run that ended (round done, human
        stop, spent budget) is ``INTERRUPTED`` (停下), an error is ``FAILED``.
        A human stop already published ``task_interrupted`` from stop(); every
        other park publishes it here — one event per stop, whichever caused it.
        """
        status = final.get("status") or "FAILED"
        task.status = TaskStatus(status)
        task.plan = [PlanStep(**p) for p in final.get("plan", [])]
        task.steps = [StepRecord(**s) for s in final.get("steps", [])]
        task.artifacts = [Artifact(**a) for a in final.get("artifacts", [])]
        task.conversation = self._conversation_from(final.get("messages"))
        task.final_answer = final.get("final_answer", "")
        task.error = final.get("error")
        task.subtasks = [SubTask(**st) for st in final.get("subtasks", [])]
        task.updated_at = _now()
        if status == "FAILED":
            self.event_bus.publish(
                task.id,
                "task_failed",
                {"task_id": task.id, "error": task.error or "unknown error"},
            )
        elif self._claim_interrupt(task.id):
            # Claim-and-publish under one contract, so a stop() racing this
            # finalize cannot also announce the same park.
            self.event_bus.publish(
                task.id,
                "task_interrupted",
                {"task_id": task.id, "status": status},
            )
        self.persistence.save_task(task)

    @staticmethod
    def _conversation_from(messages: Any) -> List[ChatTurn]:
        """User/assistant text turns only (tool traffic lives in ``steps``).

        Assistant turns that carried tool calls are skipped: their ``content``
        is typically a preamble ("Let me look…") and the tool records already
        narrate that step. Empty content is dropped so the stream has no blank
        bubbles.
        """
        turns: List[ChatTurn] = []
        for m in messages or []:
            role = m.get("role")
            if role not in ("user", "assistant"):
                continue
            if role == "assistant" and m.get("tool_calls"):
                continue
            content = str(m.get("content") or "").strip()
            if content:
                turns.append(ChatTurn(role=role, content=content))
        return turns

    def resume(self, task_id: str) -> Dict[str, Any]:
        """Continue an INTERRUPTED task from its last durable checkpoint.

        Validates synchronously (state + live-thread + checkpoint existence),
        then hands execution to a worker thread mirroring create_task().
        Raises RuntimeError with an actionable message when resuming is
        impossible — callers map that to HTTP 409. The whole validate-and-
        claim section runs under ``self._lock`` as a compare-and-set so two
        concurrent resumes can never both spawn workers on one thread_id.
        """
        thread = self._threads.get(task_id)
        if thread is not None and thread.is_alive():
            # stop() persists INTERRUPTED while the worker is still unwinding
            # through the finish node and committing its last checkpoints.
            # Bounded join lets an instant resume never race those final
            # writes (worst case the caller waits out a shutdown, reported 409).
            thread.join(timeout=5.0)
            if thread.is_alive():
                raise RuntimeError(f"task {task_id} is still shutting down")

        has_checkpoint = self._has_checkpoint(task_id)
        # ── claim: single check-and-set under the manager lock ──
        with self._lock:
            task = self.persistence.load_task(task_id)
            if task is None:
                raise RuntimeError(f"task {task_id} not found")
            if task.status != TaskStatus.INTERRUPTED:
                raise RuntimeError(
                    f"task {task_id} is {task.status.value}, "
                    "only INTERRUPTED tasks can be resumed"
                )
            live = self._threads.get(task_id)
            if live is not None and live.is_alive():
                raise RuntimeError(f"task {task_id} is already running")
            if not has_checkpoint:
                raise RuntimeError(
                    f"no checkpoint found for task {task_id} "
                    "(legacy run before checkpoint persistence or checkpoints disabled)"
                )
            # Flip to RUNNING inside the lock: any second resume() now sees
            # RUNNING and is rejected; a concurrent stop() flips it back and
            # _resume_run re-checks before proceeding.
            task.status = TaskStatus.RUNNING
            task.updated_at = _now()
            self.persistence.save_task(task)
            self.event_bus.publish(
                task_id,
                "task_resumed",
                {"task_id": task_id, "status": TaskStatus.RUNNING.value},
            )

        # Reopen the audit trail for the continued era.
        if self._trace is not None:
            self._trace.attach(self.event_bus, task_id)

        t = threading.Thread(target=self._resume_run, args=(task_id,), daemon=True)
        self._threads[task_id] = t
        t.start()
        return {"ok": True, "status": TaskStatus.RUNNING.value}

    def _read_restorable_state(
        self,
        graph: Any,
        task_id: str,
        attempts: int = 5,
        pause: float = 0.2,
    ) -> Dict[str, Any]:
        """Read the checkpoint channel values to resume from, tolerating a
        transient empty read.

        ``resume()`` admits a task only after ``_has_checkpoint`` saw a durable
        snapshot (``get_tuple(...) is not None``), but the execution-side read
        here uses a stricter criterion -- non-empty ``channel_values``. Under CI
        / IO load the freshly mounted store can expose the checkpoint tuple a
        beat before its values are readable, so a single read can come back
        empty for a checkpoint that genuinely exists (Issue #23). Re-read a
        bounded number of times before giving up, mirroring ``_has_checkpoint``'s
        double-probe, so a legitimately-admitted resume never spuriously FAILEDs.

        This retries ONLY the read. Every reject semantic (non-INTERRUPTED, no
        checkpoint) is enforced earlier in ``resume()`` and is untouched here; a
        state still empty after the budget raises the original
        ``empty checkpoint state`` error.
        """
        values: Dict[str, Any] = {}
        for attempt in range(max(1, attempts)):
            snap = graph.get_state(self._thread_config(task_id))
            values = dict(snap.values or {})
            if values:
                return values
            if attempt < attempts - 1:
                time.sleep(pause)
        raise RuntimeError(f"empty checkpoint state for task {task_id}")

    def _resume_run(self, task_id: str) -> None:
        # P1-B: this worker thread accounts its writes to the same task.
        set_current_task_id(task_id)
        try:
            # A stop() between our RUNNING flip and this point would otherwise
            # be swallowed by the restored control flags below — re-check the
            # persisted status and yield to the (newer) interrupt instead.
            current = self.persistence.load_task(task_id)
            if current is None or current.status != TaskStatus.RUNNING:
                logger.info("Resume of %s superseded by newer state; aborting.", task_id)
                return

            runtime = AgentRuntime(
                task_id=task_id,
                task_manager=self,
                llm=self._llm,
                tools=self._tools,
                tool_schemas=self._tool_schemas,
                max_steps=self.settings.max_steps,
                aux_llm=self._aux_llm,
            )
            graph = build_graph(runtime, checkpointer=self._checkpointer)

            # Bounded re-read (Issue #23): resume() already proved a durable
            # checkpoint exists, but under load the freshly mounted store can
            # expose the tuple a beat before its channel values are readable.
            # Retrying the read keeps a legitimately-admitted resume from
            # spuriously FAILEDing; reject semantics are untouched.
            values = self._read_restorable_state(graph, task_id)
            # cast 在运行时是恒等函数：快照读回来是个普通 dict，而 _active_states
            # 的类型面是 AgentState（TypedDict），这里只把两者对齐，不改任何行为。
            restored = cast(AgentState, values)
            # Reset *control* flags only; plan / steps / artifacts survive.
            # Since #139 one invoke is one round: the per-round budget restarts
            # and the stale answer/error must not leak into this round's finish.
            # The persisted plan is the user-editable copy (set_plan), so it
            # seeds over the checkpoint just like a message-driven continuation.
            restored["plan"] = [p.model_dump() for p in current.plan]
            restored["status"] = "RUNNING"
            restored["stop_requested"] = False
            restored["_current_tool_calls"] = []
            restored["_last_action"] = ""
            restored["final_answer"] = ""
            restored["error"] = ""
            restored["round_steps"] = 0
            restored["mode"] = current.mode.value
            self._active_states[task_id] = restored

            final = graph.invoke(
                restored, self._thread_config(task_id), **self._invoke_kwargs()
            )

            task = self.persistence.load_task(task_id)
            if task is None:
                raise RuntimeError(f"task {task_id} vanished during resume")
            self._finalize_terminal(task, final)
        except Exception as exc:
            logger.exception("resume() crashed for task %s", task_id)
            task = self.persistence.load_task(task_id)
            if task is not None:
                task.status = TaskStatus.FAILED
                task.error = str(exc)
                task.updated_at = _now()
                self.persistence.save_task(task)
            self.event_bus.publish(
                task_id, "task_failed", {"task_id": task_id, "error": str(exc)}
            )
        finally:
            self._active_states.pop(task_id, None)
            self._threads.pop(task_id, None)
            self._stop_flags.pop(task_id, None)
            self._interrupt_announced.discard(task_id)
            if self._trace is not None:
                self._trace.close(task_id)

    # ── #139: multi-turn continuation (one task = one conversation) ──
    def send_message(self, task_id: str, text: str) -> Dict[str, Any]:
        """Say one more thing to a parked task and continue from there.

        ADR 0008 decisions 1 / 4 / 5: a round ends with no tool call, the task
        parks, and the next message resumes the SAME conversation (thread_id and
        checkpoint included) instead of starting a new task. Raises RuntimeError
        for callers to map to 409, like resume().
        """
        text = (text or "").strip()
        if not text:
            raise RuntimeError("message is empty")
        return self._continue(task_id, user_text=text)

    def replan(self, task_id: str) -> Dict[str, Any]:
        """Run one planning round **without** flipping the sticky switch.

        The「重新规划」button is an action, not a mode change: ADR 0008 decision 3
        says a finished plan does not silently re-plan, and the next typed
        message should execute. The plan round therefore borrows plan mode for
        one invoke and restores the task's own mode afterwards.

        Nothing synthetic is persisted: the plan-mode system prompt already
        asks for a plan, and the conversation on disk is the context to revise.
        For this one-off round only, ``AgentRuntime.replan`` appends a
        request-only user anchor to the LLM request (never written to the task),
        so the model does not read the previous "execute" turn as the current
        instruction. The round is retried once when the model answers with text
        instead of a JSON plan (real-model review: the unanchored round drifted
        into an injected skill, and provider markup could otherwise land in the
        conversation).
        """
        return self._continue(
            task_id, user_text=None, one_off_mode=TaskMode.PLAN
        )

    def set_mode(self, task_id: str, mode: str) -> Dict[str, Any]:
        """Flip the sticky task switch; it takes effect on the next round."""
        try:
            chosen = TaskMode(mode)
        except ValueError:
            raise RuntimeError(f"unknown mode {mode!r} (expected plan or build)") from None
        with self._lock:
            task = self.persistence.load_task(task_id)
            if task is None:
                raise RuntimeError(f"task {task_id} not found")
            task.mode = chosen
            task.updated_at = _now()
            self.persistence.save_task(task)
        return {"ok": True, "mode": chosen.value}

    def set_plan(self, task_id: str, plan: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Replace the editable plan of a parked task (pivot item 1).

        The persisted ``Task.plan`` is the source of truth for user edits: the
        next continuation seeds ``state["plan"]`` from it, so "改完随后续消息
        生效" is structural rather than a rule the caller has to remember.
        """
        # Same window as resume/rollback: stop() settles the record before the
        # worker commits its last state, and finalize would overwrite this edit
        # with the checkpoint's plan. Wait the worker out rather than racing it.
        thread = self._threads.get(task_id)
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
            if thread.is_alive():
                raise RuntimeError(f"task {task_id} is still shutting down")
        with self._lock:
            task = self.persistence.load_task(task_id)
            if task is None:
                raise RuntimeError(f"task {task_id} not found")
            if task.status != TaskStatus.INTERRUPTED:
                raise RuntimeError(
                    f"task {task_id} is {task.status.value}; "
                    "the plan can only be edited while the task is parked "
                    "(INTERRUPTED)"
                )
            steps: List[PlanStep] = []
            for position, raw in enumerate(plan, 1):
                description = str((raw or {}).get("description") or "").strip()
                if not description:
                    continue
                status = str((raw or {}).get("status") or "pending")
                steps.append(
                    PlanStep(index=len(steps) + 1, description=description, status=status)
                )
            task.plan = steps
            task.updated_at = _now()
            self.persistence.save_task(task)
        payload = [p.model_dump() for p in task.plan]
        self.event_bus.publish(task_id, "plan_update", {"plan": payload})
        return {"ok": True, "plan": payload}

    def _continue(
        self,
        task_id: str,
        *,
        user_text: Optional[str],
        one_off_mode: Optional[TaskMode] = None,
    ) -> Dict[str, Any]:
        """Validate, claim and start one continuation round.

        Same compare-and-set discipline as resume(): the status flip happens
        under the manager lock so two concurrent senders can never both spawn a
        worker on one thread_id. A continuation always needs a durable
        checkpoint — that is what "接着跑，含进程重启后" is built on.

        ``one_off_mode`` borrows the other mode for **this round only** (the
        replan button); the sticky ``Task.mode`` is untouched.
        """
        thread = self._threads.get(task_id)
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
            if thread.is_alive():
                raise RuntimeError(f"task {task_id} is still shutting down")

        has_checkpoint = self._has_checkpoint(task_id)
        with self._lock:
            task = self.persistence.load_task(task_id)
            if task is None:
                raise RuntimeError(f"task {task_id} not found")
            if task.status != TaskStatus.INTERRUPTED:
                raise RuntimeError(
                    f"task {task_id} is {task.status.value}; "
                    "only a parked (INTERRUPTED) task can continue"
                )
            live = self._threads.get(task_id)
            if live is not None and live.is_alive():
                raise RuntimeError(f"task {task_id} is already running")
            if not has_checkpoint:
                raise RuntimeError(
                    f"no checkpoint found for task {task_id} "
                    "(checkpoints disabled, this task predates them, or the "
                    "store was refused for its format — see the startup log)"
                )
            if user_text is not None:
                task.conversation.append(ChatTurn(role="user", content=user_text))
            task.status = TaskStatus.RUNNING
            task.updated_at = _now()
            self.persistence.save_task(task)
            self.event_bus.publish(
                task_id,
                "task_resumed",
                {"task_id": task_id, "status": TaskStatus.RUNNING.value},
            )
            snapshot = task.model_dump(mode="json")

        if self._trace is not None:
            self._trace.attach(self.event_bus, task_id)

        t = threading.Thread(
            target=self._continue_run,
            args=(task_id, user_text, one_off_mode),
            daemon=True,
        )
        self._threads[task_id] = t
        t.start()
        return {"ok": True, "status": TaskStatus.RUNNING.value, "task": snapshot}

    def _continue_run(
        self,
        task_id: str,
        user_text: Optional[str],
        one_off_mode: Optional[TaskMode] = None,
    ) -> None:
        """Run one more round on an existing conversation."""
        set_current_task_id(task_id)
        try:
            current = self.persistence.load_task(task_id)
            if current is None or current.status != TaskStatus.RUNNING:
                logger.info("Continuation of %s superseded by newer state; aborting.", task_id)
                return

            runtime = AgentRuntime(
                task_id=task_id,
                task_manager=self,
                llm=self._llm,
                tools=self._tools,
                tool_schemas=self._tool_schemas,
                max_steps=self.settings.max_steps,
                aux_llm=self._aux_llm,
                # The「重新规划」button borrows a plan round; anchor that round
                # to the current task so a long injected AGENTS / skills
                # context cannot make the model plan an unrelated task.
                replan=(one_off_mode is TaskMode.PLAN),
            )
            graph = build_graph(runtime, checkpointer=self._checkpointer)

            # The previous run ended at END, so this invoke starts a fresh pass
            # from START over the restored channels — the conversation keeps
            # growing instead of being replayed or reset.
            values = self._read_restorable_state(graph, task_id)
            restored = cast(AgentState, values)
            if user_text is not None:
                restored.setdefault("messages", []).append(
                    {"role": "user", "content": user_text}
                )
            # The persisted plan is the user-editable copy (set_plan): seed it
            # over the checkpoint so an edit takes effect this round.
            restored["plan"] = [p.model_dump() for p in current.plan]
            # A one-off round (replan) borrows the other mode without touching
            # the sticky task switch.
            restored["mode"] = (one_off_mode or current.mode).value
            restored["status"] = "RUNNING"
            restored["stop_requested"] = False
            restored["_current_tool_calls"] = []
            restored["_last_action"] = ""
            restored["final_answer"] = ""
            restored["error"] = ""
            # Budget is per round; step_index stays cumulative for record ids.
            restored["round_steps"] = 0
            self._active_states[task_id] = restored

            final = graph.invoke(
                restored, self._thread_config(task_id), **self._invoke_kwargs()
            )

            task = self.persistence.load_task(task_id)
            if task is None:
                raise RuntimeError(f"task {task_id} vanished during continuation")
            self._finalize_terminal(task, final)
        except Exception as exc:
            logger.exception("continuation crashed for task %s", task_id)
            task = self.persistence.load_task(task_id)
            if task is not None:
                task.status = TaskStatus.FAILED
                task.error = str(exc)
                task.updated_at = _now()
                self.persistence.save_task(task)
            self.event_bus.publish(
                task_id, "task_failed", {"task_id": task_id, "error": str(exc)}
            )
        finally:
            self._active_states.pop(task_id, None)
            self._threads.pop(task_id, None)
            self._stop_flags.pop(task_id, None)
            self._interrupt_announced.discard(task_id)
            if self._trace is not None:
                self._trace.close(task_id)

    # ── P1-B: workspace rollback (docs/specs/p1-b-rollback.md) ──
    def rollback(self, task_id: str) -> Dict[str, Any]:
        """Restore the sandbox to its pre-task state from the before-image ledger.

        File-only by design (拍板 9): the task record and the graph checkpoints
        are never touched, so rollback stays fully decoupled from resume.
        Refuses active tasks (拍板 12③) — RUNNING/PENDING by status, plus the
        stop() window where the record already settled but the worker is still
        unwinding its final writes. Raises RuntimeError; the route maps it to
        409, the same synchronous style as resume.
        """
        task = self.persistence.load_task(task_id)
        if task is None:
            raise RuntimeError(f"task {task_id} not found")
        live = self._threads.get(task_id)
        if task.status in (TaskStatus.RUNNING, TaskStatus.PENDING) or (
            live is not None and live.is_alive()
        ):
            raise RuntimeError(
                f"task {task_id} is {task.status.value}; "
                "rollback requires a settled task (stop it first)"
            )
        # Reopen the audit trail for the rollback era (same pattern as resume):
        # the trace recorder detached when the run finished, so the event would
        # otherwise reach SSE subscribers but never the JSONL.
        if self._trace is not None:
            self._trace.attach(self.event_bus, task_id)
        try:
            result = restore_task(task_id, settings=self.settings)
            self.event_bus.publish(
                task_id, "task_rollback", {"task_id": task_id, **result}
            )
        finally:
            if self._trace is not None:
                self._trace.close(task_id)
        return result

    # ── artifacts ──
    def has_active_state(self, task_id: str) -> bool:
        """Whether the task's live run state (and with it, its artifact ledger)
        still exists.

        ``run()``'s finally pops ``_active_states[task_id]``, so once a task has
        finished there is no ledger left to register into. A subtask hand-back
        that arrives that late (parent stopped, or the subtask overran the
        timeout window) has to be refused loudly instead of minting a record
        that lands nowhere (issue #76); this predicate is how the hand-back asks
        without reaching into the private dict.
        """
        return task_id in self._active_states

    def add_artifact(self, task_id: str, path: Path) -> Artifact:
        """Register a file the task produced; one record per task and file.

        Dedupe lives here rather than at the two call sites (the kernel's tool
        node and the sub-agent's hand-back) because both funnel through this
        method (#72). Skipping the second registration skips a record, its
        ``artifact_created`` event and a redundant ``add_document`` call — the
        knowledge base is already idempotent per resolved path, so what a
        duplicate used to cost is a spurious product row, not a double index.

        Two settled semantics callers rely on: the key is per **task** — two
        tasks touching the same file each own a record — and a hit returns the
        record as first registered, i.e. its ``size`` / ``created_at`` are the
        ones from the initial write. A subtask write's single registration is the
        hand-back under the parent id (#76 — the tool node no longer registers
        under the subtask's own id). A task with no active state has no
        registry to dedupe against and registers as before (mint + event, no
        ledger); the subtask hand-back refuses that case loudly instead of
        calling in (see :meth:`has_active_state`), and landing the record on a
        finished task is ticket #106's call.

        The lookup and the append are one atomic step under ``self._lock`` (the
        same manager lock the resume claim uses), because subtasks run on a pool:
        two of them handing the same path back to one parent can arrive at the
        same microsecond, and an unlocked check-then-act would let both miss.
        """
        resolved = str(Path(path).resolve())
        # Only the check-mint-append is guarded. The event and the knowledge-base
        # ingest stay outside the lock so no SSE subscriber or file-index callback
        # ever runs while it is held (and no caller of this method holds it, so
        # the non-reentrant lock cannot be taken twice on one path).
        with self._lock:
            st = self._active_states.get(task_id)
            if st is not None:
                for existing in st.get("artifacts", []):
                    if existing.get("path") and str(Path(existing["path"]).resolve()) == resolved:
                        return Artifact(**existing)
            art = self.persistence.register_artifact(path)
            if st is not None:
                st.setdefault("artifacts", []).append(art.model_dump())
        self.event_bus.publish(task_id, "artifact_created", art.model_dump())
        # P1 item 3: auto-index text artifacts into the knowledge base.
        # Failure is only a warning — it must never break the task.
        if self._kb is not None and self.settings.kb_auto_index_artifacts:
            try:
                self._kb.add_document(path)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("kb.add_document failed for %s: %s", path, exc)
        return art
