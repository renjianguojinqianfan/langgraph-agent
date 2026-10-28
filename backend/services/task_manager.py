"""Task lifecycle orchestration.

Owns task creation, runs the LangGraph graph on a background thread, and
exposes stop / query APIs. The graph nodes publish events through the
:class:`EventBus`; task records persist through :class:`Persistence`. A
``stop_requested`` flag (checked at each node entry) guarantees the ≤2s
interrupt requirement (P0-9).
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, cast

from langgraph.types import Durability

from ..api.schemas import (
    ChatTurn,
    StepRecord,
    SubTask,
    Task,
    TaskMode,
    TaskStatus,
)
from ..config import Settings
from ..core.agent.graph import build_graph
from ..core.agent.nodes import AgentRuntime
from ..core.agent.policy import DangerPolicy, warn_unmatched_rules
from ..core.agent.state import AgentState
from ..core.agent.subagent import SubAgentExecutor
from ..core.llm.client import LLMClient
from ..core.llm.openai_compat import create_llm_client
from ..core.runtime_context import (
    get_current_task_id,
    normalize_workspace,
    reset_runtime_context,
    set_runtime_context,
    workspace_key,
)
from ..core.tools.base import BaseTool
from ..core.tools.bash import BashTool
from ..core.tools.registry import build_tools
from ..core.tools.subagent_tool import SpawnSubagentTool
from ..utils.logging import get_logger
from . import plan_file
from .event_bus import EventBus
from .model_store import ModelStore
from .persistence import Persistence
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
        default_workspace: Optional[Path] = None,
        model_store: Optional[ModelStore] = None,
        llm_factory: Optional[Callable[[Any, str], LLMClient]] = None,
    ) -> None:
        self.settings = settings
        self.event_bus = event_bus
        self.persistence = persistence
        #: Where a task without an explicit workspace runs. ``backend.main``
        #: passes the launch directory (ADR 0010); a directly constructed
        #: manager falls back to the historical sandbox root.
        self._default_workspace = normalize_workspace(
            default_workspace or settings.artifacts_path
        )
        self.models = model_store or ModelStore(settings)
        self._injected_llm = llm_client
        self._llm = llm_client or create_llm_client(settings)
        self._llm_factory = llm_factory
        self._llm_clients: Dict[str, LLMClient] = {}
        #: Keys a live/cached client may still hold after a rotation. Memory
        #: only: the point is redaction, not a second credentials store.
        self._known_secrets: set[str] = set()
        #: The (profile, client) a round is pinned to; subagents inherit it.
        self._round_models: Dict[str, tuple[str, LLMClient]] = {}
        self._tools = tools if tools is not None else build_tools(settings)
        # P1 item 2: sub-agent executor (None when disabled).
        self._subagent = SubAgentExecutor(self, settings) if settings.subagent_enabled else None
        # P2 item 1: connect MCP servers and append their tools (startup once).
        self._mcp: Optional[McpClientManager] = None
        self._load_mcp_tools(settings)
        # Issue #56: ``subagent_enabled`` now gates exactly one thing — the
        # ``spawn_subagent`` tool. It used to also mount the keyword-triggered
        # ``subagent_split`` node, which could dispatch a subtask (and its side
        # effects) with nobody approving it; that entry is gone, so "off" simply
        # keeps the tool off the face.
        if not settings.subagent_enabled:
            self._tools = [t for t in self._tools if t.name != "spawn_subagent"]
        self._tool_schemas = [t.to_openai_schema() for t in self._tools]
        self._wire_injected_tools()
        # #145: dead rules never fire — the same silent-loss family as a broken
        # table, one rule wide. The mount face is stable at manager construction
        # (MCP appended, subagent switch applied), so check it once here;
        # rationale and the per-run false-positive argument live in
        # warn_unmatched_rules.
        warn_unmatched_rules(
            DangerPolicy.from_settings(settings).rules,
            [tool.name for tool in self._tools],
        )
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
        """Give stateful tools their service references (subagent, bash stop)."""
        for t in self._tools:
            if isinstance(t, SpawnSubagentTool):
                t.executor = self._subagent
            elif isinstance(t, BashTool):
                # Nodes are synchronous: a stop clicked while a command is
                # running is only observable from inside the tool. The flag
                # stays authoritative (is_stop_flagged), and the run loop polls
                # this on the worker thread.
                t.stop_checker = self._current_task_stopped
            # McpTool holds its McpClientManager reference at construction time,
            # so it needs no wiring here.

    def _current_task_stopped(self) -> bool:
        """Whether the task bound to this worker thread has a stop request."""
        task_id = get_current_task_id()
        if not task_id:
            return False
        return self.is_stop_flagged(task_id)

    # ── P2 item 1: MCP client tool registration ──
    def _load_mcp_tools(self, settings: Settings) -> None:
        """Connect configured MCP servers and append their tools.

        ``_tools`` already contains the built-ins, so a name clash keeps the
        existing tool and logs a warning. A missing SDK or a failing server only
        logs a warning — startup always continues.
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
        with self._lock:
            self._known_secrets.clear()
            self._round_models.clear()

    # ── creation / query ──
    @staticmethod
    def _require_workspace(task: Task) -> None:
        if not Path(task.workspace).is_dir():
            raise RuntimeError(f"task workspace no longer exists: {task.workspace}")

    def _build_llm(self, profile: Any, key: str) -> LLMClient:
        if key:
            self._known_secrets.add(key)
        if self._llm_factory is not None:
            return self._llm_factory(profile, key)
        from ..core.llm.openai_compat import OpenAICompatibleClient

        return OpenAICompatibleClient(
            profile.base_url, key, profile.model, self.settings.llm_request_timeout_sec
        )

    def _llm_for(self, task: Optional[Task]) -> LLMClient:
        """The task's model client, cached per profile (ADR 0010 decision 7)."""
        if self._injected_llm is not None:
            return self._injected_llm
        if self.settings.use_mock_llm:
            return self._llm
        requested = task.model if task is not None else ""
        # Resolve, read the key, check the cache and build under one lock: a
        # concurrent profile edit invalidates the cache before this method can
        # insert a client built from the pre-edit profile.
        with self._lock:
            profile = self.models.resolve(requested)
            if profile is None:
                # A pinned profile that no longer exists must not silently
                # reroute the conversation to another provider.
                raise RuntimeError(
                    f"model profile {requested!r} no longer exists; switch the task's model"
                )
            client = self._llm_clients.get(profile.id)
            if client is None:
                client = self._build_llm(profile, self.models.key_for(profile.id))
                self._llm_clients[profile.id] = client
        return client

    def _pin_round_model(self, task: Task) -> LLMClient:
        """Fix this round's client; a subagent must not re-read the task record."""
        llm = self._llm_for(task)
        profile_id = self.model_id_for(task)
        with self._lock:
            self._round_models[task.id] = (profile_id, llm)
        return llm

    def round_llm_for(self, task_id: str) -> Optional[tuple[str, LLMClient]]:
        with self._lock:
            return self._round_models.get(task_id)

    def llm_for_task_id(self, task_id: str) -> LLMClient:
        return self._llm_for(self.persistence.load_task(task_id))

    def model_id_for(self, task: Optional[Task]) -> str:
        """The profile id a task runs with; empty task -> the global default."""
        return (task.model if task is not None else "") or self.models.default_id

    def redact(self, text: str) -> str:
        """Strip every known key from text before it is logged, stored or sent."""
        secrets = set(self._known_secrets) | self.models.secret_values()
        for secret in secrets:
            if secret:
                text = text.replace(secret, "[redacted]")
        return text

    def set_model(self, task_id: str, model_id: str) -> Dict[str, Any]:
        """Switch a task's model; the next message uses it."""
        if self.models.get(model_id) is None:
            raise RuntimeError(f"unknown model {model_id!r}")
        with self._lock:
            task = self.persistence.load_task(task_id)
            if task is None:
                raise RuntimeError(f"task {task_id} not found")
            task.model = model_id
            task.updated_at = _now()
            self.persistence.save_task(task)
        return {"ok": True, "model": model_id}

    # Profile mutations go through the manager so the per-profile client cache
    # cannot keep serving the old address / key after an edit.
    def add_model_profile(self, **fields: Any) -> Any:
        with self._lock:
            return self.models.add(**fields)

    def set_default_model_profile(self, model_id: str) -> None:
        with self._lock:
            self.models.set_default(model_id)

    def update_model_profile(self, model_id: str, **fields: Any) -> Any:
        with self._lock:
            old_key = self.models.key_for(model_id)
            profile = self.models.update(model_id, **fields)
            self._llm_clients.pop(model_id, None)
            if old_key:
                self._known_secrets.add(old_key)
        return profile

    def delete_model_profile(self, model_id: str) -> None:
        with self._lock:
            old_key = self.models.key_for(model_id)
            self.models.delete(model_id)
            self._llm_clients.pop(model_id, None)
            if old_key:
                self._known_secrets.add(old_key)

    def set_model_key(self, model_id: str, key: str) -> None:
        with self._lock:
            old_key = self.models.key_for(model_id)
            self.models.set_key(model_id, key)
            self._llm_clients.pop(model_id, None)
            if old_key:
                self._known_secrets.add(old_key)

    def test_model(self, model_id: str) -> Dict[str, Any]:
        """Send one minimal request; the key never appears in the answer."""
        profile = self.models.get(model_id)
        if profile is None:
            raise KeyError(model_id)
        if self.settings.use_mock_llm:
            return {"ok": True, "note": "mock mode"}
        key = self.models.key_for(model_id)
        if not key:
            return {"ok": False, "error": "key not set"}
        try:
            self._build_llm(profile, key).complete(
                [{"role": "user", "content": "ping"}], max_tokens=1
            )
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": self.redact(str(exc))}

    @staticmethod
    def _seed_plan(task: Task) -> Dict[str, Any]:
        """Plan state from the file, bumping the version when it changed."""
        text = plan_file.read_plan(Path(task.workspace), task.id)
        digest = plan_file.plan_hash(text)
        version = task.plan_version
        if digest != task.plan_hash and text.strip():
            version += 1
        return {
            "plan_text": text,
            "plan_version": version,
            "plan_hash": digest,
        }

    def create_task(
        self,
        title: Optional[str],
        user_input: str,
        mode: str = TaskMode.BUILD.value,
        workspace: Optional[str] = None,
        model: Optional[str] = None,
    ) -> str:
        """Start one task (ADR 0008 decision 1: one task = one conversation).

        ``mode`` is the sticky task switch; default is build (decision 3).
        ``workspace`` is the page-supplied directory; absent -> the launch dir.
        ``model`` is a profile id; absent -> the global default.
        """
        chosen = normalize_workspace(workspace or self._default_workspace)
        if not Path(chosen).is_dir():
            raise ValueError(f"workspace does not exist: {chosen}")
        model_id = model or self.models.default_id
        if self.models.get(model_id) is None:
            raise ValueError(f"unknown model: {model_id}")
        task_id = uuid.uuid4().hex
        now = _now()
        task = Task(
            id=task_id,
            title=(title or user_input[:40]).strip(),
            user_input=user_input,
            workspace=chosen,
            model=model_id,
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

    def list_tasks(self, limit: int = 50, workspace: Optional[str] = None) -> List[Task]:
        target = normalize_workspace(workspace) if workspace else self._default_workspace
        return self.persistence.list_tasks(limit, workspace=target)

    def list_all_tasks(self, limit: int = 50) -> List[Task]:
        return self.persistence.list_tasks(limit)

    # ── delete history (#241) ──
    def _task_is_live(self, task_id: str, task: Task) -> bool:
        """Status alone is not enough: stop() flips the record while unwinding."""
        if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            return True
        thread = self._threads.get(task_id)
        return thread is not None and thread.is_alive()

    def delete_task(self, task_id: str) -> Dict[str, Any]:
        """Delete one task's history; never touches the workspace files.

        The live check and the record removal share ``self._lock`` with the
        resume()/continue() claim: a concurrent continuation either wins the
        lock first (we then see RUNNING and 409) or losses it and re-loads a
        missing record (it 409s). The worker can therefore never start after
        the record is gone and "resurrect" a deleted task.
        """
        with self._lock:
            task = self.persistence.load_task(task_id)
            if task is None:
                raise KeyError(task_id)
            if self._task_is_live(task_id, task):
                raise RuntimeError(f"task {task_id} is still running; stop it first")
            # Remove the record before any irreversible cleanup: the UI is
            # consistent as soon as this returns, and the loser of a claim race
            # can no longer find a task to start.
            self.persistence.delete_task(task_id)
        cleanup = self._cleanup_task_artifacts(task_id)
        return {"ok": True, "cleanup": cleanup}

    def delete_workspace_tasks(self, workspace: str) -> int:
        """Delete every task record under one workspace; disk files stay."""
        target = workspace_key(workspace)
        with self._lock:
            tasks = [
                task
                for task in self.persistence.list_all()
                if workspace_key(task.workspace) == target
            ]
            live = [task.id for task in tasks if self._task_is_live(task.id, task)]
            if live:
                raise RuntimeError(
                    f"{len(live)} task(s) in {workspace} are still running; stop them first"
                )
            deleted = self.persistence.delete_tasks([task.id for task in tasks])
        for task in tasks:
            self._cleanup_task_artifacts(task.id)
        return deleted

    def _cleanup_task_artifacts(self, task_id: str) -> Dict[str, bool]:
        """Best-effort checkpoint/trace removal; the record is the source of truth."""
        self.kill_background(task_id)
        checkpoint_removed = False
        if self._checkpointer is not None:
            try:
                self._checkpointer.delete_thread(task_id)
                checkpoint_removed = True
            except Exception:
                logger.exception("checkpoint cleanup failed for %s", task_id)
        trace_removed = False
        if self._trace is not None:
            try:
                self._trace.file_path(task_id).unlink(missing_ok=True)
                trace_removed = True
            except OSError:
                logger.warning("trace cleanup failed for %s", task_id, exc_info=True)
        self.event_bus.drop(task_id)
        with self._lock:
            self._active_states.pop(task_id, None)
            self._stop_flags.pop(task_id, None)
            self._round_models.pop(task_id, None)
            self._interrupt_announced.discard(task_id)
            self._threads.pop(task_id, None)
        return {"checkpoint": checkpoint_removed, "trace": trace_removed}

    # ── run loop ──
    def _start_run(self, task_id: str) -> None:
        t = threading.Thread(target=self.run, args=(task_id,), daemon=True)
        self._threads[task_id] = t
        t.start()

    def run(self, task_id: str) -> None:
        # P1-B / ADR 0010: this worker thread is bound to the task and its
        # workspace; tools read both from the runtime context.
        task = self.persistence.load_task(task_id)
        if task is None:
            logger.warning("run: unknown task %s", task_id)
            return
        tokens = set_runtime_context(task_id, Path(task.workspace))

        task.status = TaskStatus.RUNNING
        task.updated_at = _now()
        self.persistence.save_task(task)

        try:
            # The directory may have disappeared while the task was queued.
            # Fail before any file tool can recreate its root.
            self._require_workspace(task)
            seeded = self._seed_plan(task)
            state: AgentState = {
                "task_id": task_id,
                "mode": task.mode.value,
                "messages": [{"role": "user", "content": task.user_input}],
                "plan_text": seeded["plan_text"],
                "plan_version": seeded["plan_version"],
                "plan_hash": seeded["plan_hash"],
                "steps": [],
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

            runtime = AgentRuntime(
                task_id=task_id,
                task_manager=self,
                llm=self._pin_round_model(task),
                tools=self._tools,
                tool_schemas=self._tool_schemas,
                max_steps=self.settings.max_steps,
                model_profile=self.model_id_for(task),
            )
            graph = build_graph(runtime, checkpointer=self._checkpointer)
            final = graph.invoke(
                state, self._thread_config(task_id), **self._invoke_kwargs()
            )

            task = self.persistence.load_task(task_id) or task
            self._finalize_terminal(task, final)
        except Exception as exc:  # pragma: no cover - defensive
            task.status = TaskStatus.FAILED
            task.error = self.redact(str(exc))
            logger.error("run() crashed for task %s: %s", task_id, task.error)
            task.updated_at = _now()
            self.persistence.save_task(task)
            self.event_bus.publish(
                task_id, "task_failed", {"task_id": task_id, "error": task.error}
            )
        finally:
            self._active_states.pop(task_id, None)
            self._threads.pop(task_id, None)
            self._stop_flags.pop(task_id, None)
            self._round_models.pop(task_id, None)
            self._interrupt_announced.discard(task_id)
            reset_runtime_context(tokens)
            # P0 item 4: always close the trace (success / failure / interrupt)
            # so the JSONL terminates with a trace_end line.
            if self._trace is not None:
                self._trace.close(task_id)

    # ── control ──
    def kill_background(self, task_id: str) -> int:
        """Reap bash background trees owned by ``task_id`` (S1 / #148 F3)."""
        killed = 0
        for tool in self._tools:
            if isinstance(tool, BashTool):
                killed += tool.kill_background(task_id)
        return killed

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
        # Background trees outlive their call; a parked task still owns them.
        self.kill_background(task_id)
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

    def _redact_step(self, step: Dict[str, Any]) -> Dict[str, Any]:
        """Persist tool arguments redacted; dispatch already saw the raw copy."""

        def redact_value(value: Any) -> Any:
            if isinstance(value, str):
                return self.redact(value)
            if isinstance(value, list):
                return [redact_value(item) for item in value]
            if isinstance(value, dict):
                return {self.redact(str(k)): redact_value(v) for k, v in value.items()}
            return value

        calls = []
        for call in step.get("tool_calls") or []:
            call = dict(call)
            if "input" in call:
                call["input"] = redact_value(call["input"])
            calls.append(call)
        return {**step, "tool_calls": calls}

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
        task.steps = [StepRecord(**self._redact_step(s)) for s in final.get("steps", [])]
        task.plan_version = int(final.get("plan_version", task.plan_version))
        task.plan_hash = str(final.get("plan_hash", task.plan_hash))
        task.conversation = self._conversation_from(final.get("messages"))
        task.final_answer = self.redact(final.get("final_answer", "") or "")
        task.error = self.redact(final["error"]) if final.get("error") else None
        task.subtasks = [SubTask(**st) for st in final.get("subtasks", [])]
        task.updated_at = _now()
        # Flip the status last: a poll between assignments must never see
        # "parked" carrying the previous round's steps (the redaction walk
        # above widened that window).
        task.status = TaskStatus(status)
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
            if plan_file.is_plan_message(m.get("content")):
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
            self._require_workspace(task)
            # Read the plan before accepting the round: a non-UTF-8 file is a
            # readable 409 here, not a worker crash after the resume is taken.
            self._seed_plan(task)
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
            # Reopen the audit trail before the boundary event so the JSONL
            # captures task_resumed. This does the file open + subscribe under
            # the claim lock, but manager -> trace -> bus stays acyclic.
            if self._trace is not None:
                self._trace.attach(self.event_bus, task_id)
            self.event_bus.publish(
                task_id,
                "task_resumed",
                {"task_id": task_id, "status": TaskStatus.RUNNING.value},
            )

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
        tokens = None
        try:
            # A stop() between our RUNNING flip and this point would otherwise
            # be swallowed by the restored control flags below — re-check the
            # persisted status and yield to the (newer) interrupt instead.
            current = self.persistence.load_task(task_id)
            if current is None or current.status != TaskStatus.RUNNING:
                logger.info("Resume of %s superseded by newer state; aborting.", task_id)
                return
            tokens = set_runtime_context(task_id, Path(current.workspace))

            runtime = AgentRuntime(
                task_id=task_id,
                task_manager=self,
                llm=self._pin_round_model(current),
                tools=self._tools,
                tool_schemas=self._tool_schemas,
                max_steps=self.settings.max_steps,
                model_profile=self.model_id_for(current),
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
            # Reset *control* flags only; plan / steps survive.
            # Since #139 one invoke is one round: the per-round budget restarts
            # and the stale answer/error must not leak into this round's finish.
            # The plan file is the source of truth; seed it over the checkpoint.
            seeded = self._seed_plan(current)
            restored["plan_text"] = seeded["plan_text"]
            restored["plan_version"] = max(
                seeded["plan_version"], int(restored.get("plan_version", 0))
            )
            restored["plan_hash"] = seeded["plan_hash"]
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
            message = self.redact(str(exc))
            logger.error("resume() crashed for task %s: %s", task_id, message)
            task = self.persistence.load_task(task_id)
            if task is not None:
                task.status = TaskStatus.FAILED
                task.error = message
                task.updated_at = _now()
                self.persistence.save_task(task)
            self.event_bus.publish(
                task_id, "task_failed", {"task_id": task_id, "error": message}
            )
        finally:
            self._active_states.pop(task_id, None)
            self._threads.pop(task_id, None)
            self._stop_flags.pop(task_id, None)
            self._round_models.pop(task_id, None)
            self._interrupt_announced.discard(task_id)
            if tokens is not None:
                reset_runtime_context(tokens)
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

    def get_plan(self, task_id: str) -> Dict[str, Any]:
        task = self.persistence.load_task(task_id)
        if task is None:
            raise RuntimeError(f"task {task_id} not found")
        text = plan_file.read_plan(Path(task.workspace), task_id)
        return {"text": text, "version": task.plan_version if text.strip() else 0}

    def set_plan(self, task_id: str, text: str) -> Dict[str, Any]:
        """Replace the plan file of a parked task; the next round rereads it.

        A failed task counts as parked (B1): its checkpoint is intact and the
        next message can continue from it, so the plan stays editable.
        """
        # Same window as resume: stop() settles the record before the worker
        # commits its last state. Wait the worker out rather than racing it.
        thread = self._threads.get(task_id)
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
            if thread.is_alive():
                raise RuntimeError(f"task {task_id} is still shutting down")
        with self._lock:
            task = self.persistence.load_task(task_id)
            if task is None:
                raise RuntimeError(f"task {task_id} not found")
            if task.status not in (TaskStatus.INTERRUPTED, TaskStatus.FAILED):
                raise RuntimeError(
                    f"task {task_id} is {task.status.value}; "
                    "the plan can only be edited while the task is parked "
                    "(INTERRUPTED or FAILED)"
                )
            self._require_workspace(task)
            try:
                plan_file.write_plan(Path(task.workspace), task_id, text)
            except ValueError as exc:
                raise RuntimeError(str(exc)) from None
            digest = plan_file.plan_hash(text)
            if text.strip() and digest != task.plan_hash:
                task.plan_version += 1
            task.plan_hash = digest
            task.updated_at = _now()
            self.persistence.save_task(task)
            version = task.plan_version
        return {"ok": True, "version": version}

    def _continue(self, task_id: str, *, user_text: Optional[str]) -> Dict[str, Any]:
        """Validate, claim and start one continuation round.

        Same compare-and-set discipline as resume(): the status flip happens
        under the manager lock so two concurrent senders can never both spawn a
        worker on one thread_id. A continuation always needs a durable
        checkpoint — that is what "接着跑，含进程重启后" is built on.

        A failed task counts as parked (B1 #219): its record keeps the
        checkpoint of the failed round, so one more message continues the same
        conversation instead of forcing a new task. ``resume`` stays
        INTERRUPTED-only; only this message path admits FAILED.
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
            if task.status not in (TaskStatus.INTERRUPTED, TaskStatus.FAILED):
                raise RuntimeError(
                    f"task {task_id} is {task.status.value}; "
                    "only a parked (INTERRUPTED or FAILED) task can continue"
                )
            self._require_workspace(task)
            # Precheck the plan file for the same reason as resume(): the API
            # must reject a continuation before the user message is persisted.
            self._seed_plan(task)
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
            # B1 (#219): a new round starts clean; the previous failure reason
            # must not linger on a task that is running again.
            task.error = None
            task.updated_at = _now()
            self.persistence.save_task(task)
            # Same attach-before-publish contract as resume().
            if self._trace is not None:
                self._trace.attach(self.event_bus, task_id)
            self.event_bus.publish(
                task_id,
                "task_resumed",
                {"task_id": task_id, "status": TaskStatus.RUNNING.value},
            )
            snapshot = task.model_dump(mode="json")

        t = threading.Thread(
            target=self._continue_run,
            args=(task_id, user_text),
            daemon=True,
        )
        self._threads[task_id] = t
        t.start()
        return {"ok": True, "status": TaskStatus.RUNNING.value, "task": snapshot}

    def _continue_run(self, task_id: str, user_text: Optional[str]) -> None:
        """Run one more round on an existing conversation."""
        tokens = None
        try:
            current = self.persistence.load_task(task_id)
            if current is None or current.status != TaskStatus.RUNNING:
                logger.info("Continuation of %s superseded by newer state; aborting.", task_id)
                return
            tokens = set_runtime_context(task_id, Path(current.workspace))

            runtime = AgentRuntime(
                task_id=task_id,
                task_manager=self,
                llm=self._pin_round_model(current),
                tools=self._tools,
                tool_schemas=self._tool_schemas,
                max_steps=self.settings.max_steps,
                model_profile=self.model_id_for(current),
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
            # The plan file is the source of truth; seed it over the checkpoint.
            seeded = self._seed_plan(current)
            restored["plan_text"] = seeded["plan_text"]
            restored["plan_version"] = max(
                seeded["plan_version"], int(restored.get("plan_version", 0))
            )
            restored["plan_hash"] = seeded["plan_hash"]
            restored["mode"] = current.mode.value
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
            message = self.redact(str(exc))
            logger.error("continuation crashed for task %s: %s", task_id, message)
            task = self.persistence.load_task(task_id)
            if task is not None:
                task.status = TaskStatus.FAILED
                task.error = message
                task.updated_at = _now()
                self.persistence.save_task(task)
            self.event_bus.publish(
                task_id, "task_failed", {"task_id": task_id, "error": message}
            )
        finally:
            self._active_states.pop(task_id, None)
            self._threads.pop(task_id, None)
            self._stop_flags.pop(task_id, None)
            self._round_models.pop(task_id, None)
            self._interrupt_announced.discard(task_id)
            if tokens is not None:
                reset_runtime_context(tokens)
            if self._trace is not None:
                self._trace.close(task_id)
