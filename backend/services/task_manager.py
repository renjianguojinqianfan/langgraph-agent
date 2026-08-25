"""Task lifecycle orchestration.

Owns task creation, runs the LangGraph graph on a background thread, and
exposes stop / confirm / query APIs. The graph nodes publish events through the
:class:`EventBus` and register artifacts through :class:`Persistence`. A
``stop_requested`` flag (checked at each node entry) guarantees the ≤2s
interrupt requirement (P0-9).
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..api.schemas import (
    Artifact,
    PlanStep,
    RiskItem,
    StepRecord,
    SubTask,
    Task,
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
from .trace import TraceRecorder

logger = get_logger("task_manager")


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
        self._mcp = None
        self._load_mcp_tools(settings)
        # P2 item 2: append Git tools (git_enabled switch, no @register).
        self._load_git_tools(settings)
        self._tool_schemas = [t.to_openai_schema() for t in self._tools]
        self._wire_injected_tools()
        # P0 item 4: resident trace recorder (EventBus subscriber).
        self._trace: Optional[TraceRecorder] = TraceRecorder(settings) if settings.trace_enabled else None

        self._lock = threading.Lock()
        self._active_states: Dict[str, AgentState] = {}
        self._confirm_state: Dict[str, Dict[str, Any]] = {}
        self._threads: Dict[str, threading.Thread] = {}

    # ── P1 wiring of runtime-injected tools ──
    def _wire_injected_tools(self) -> None:
        """Give stateful tools their service references (subagent / KB)."""
        for t in self._tools:
            if isinstance(t, SpawnSubagentTool):
                t.executor = self._subagent
            elif isinstance(t, (MemorySearchTool, KbQueryTool)):
                t.kb = self._kb
            # P2 item 1: McpTool already holds its McpClientManager reference at
            # construction time; this hook stays as a defensive no-op so every
            # injected-tool family funnels through one place.
            elif getattr(t, "needs_per_call_confirm", False) and hasattr(t, "_manager"):
                pass

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

    # ── creation / query ──
    def create_task(self, title: Optional[str], user_input: str) -> str:
        task_id = uuid.uuid4().hex
        now = _now()
        task = Task(
            id=task_id,
            title=(title or user_input[:40]).strip(),
            user_input=user_input,
            status=TaskStatus.PENDING,
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
        task = self.persistence.load_task(task_id)
        if task is None:
            logger.warning("run: unknown task %s", task_id)
            return

        task.status = TaskStatus.RUNNING
        task.updated_at = _now()
        self.persistence.save_task(task)

        state: AgentState = {
            "task_id": task_id,
            "messages": [{"role": "user", "content": task.user_input}],
            "plan": [],
            "steps": [],
            "artifacts": [],
            "status": "RUNNING",
            "stop_requested": False,
            "pending_confirm": {},
            "step_index": 0,
            "final_answer": "",
            "error": "",
            "_last_action": "",
            "_current_tool_calls": [],
            "_confirmed_ids": [],
            "_rejected_ids": [],
            "_needs_confirm": False,
            "risk_report": [],
            "_risk_blocked": False,
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
                subagent_executor=self._subagent,
                confirm_enabled=True,
            )
            graph = build_graph(runtime, mode="main")
            final = graph.invoke(state)

            status = final.get("status") or "FAILED"
            task.status = TaskStatus(status)
            task.plan = [PlanStep(**p) for p in final.get("plan", [])]
            task.steps = [StepRecord(**s) for s in final.get("steps", [])]
            task.artifacts = [Artifact(**a) for a in final.get("artifacts", [])]
            task.final_answer = final.get("final_answer", "")
            task.error = final.get("error")
            task.risk_report = [RiskItem(**it) for it in final.get("risk_report", [])]
            task.subtasks = [SubTask(**st) for st in final.get("subtasks", [])]
            task.updated_at = _now()
            # Publish the terminal event BEFORE persisting the terminal status.
            # Otherwise an observer polling get_task() could see COMPLETED/FAILED
            # and replay the event buffer in the (tiny) window before the event
            # is published — a pre-existing race surfaced by the smoke test.
            if status == "COMPLETED":
                self.event_bus.publish(
                    task_id, "task_completed", {"task_id": task_id, "status": status}
                )
            elif status == "FAILED":
                self.event_bus.publish(
                    task_id,
                    "task_failed",
                    {"task_id": task_id, "error": task.error or "unknown error"},
                )
            # INTERRUPTED already published by stop().
            self.persistence.save_task(task)
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
            self._confirm_state.pop(task_id, None)
            # P0 item 4: always close the trace (success / failure / interrupt)
            # so the JSONL terminates with a trace_end line.
            if self._trace is not None:
                self._trace.close(task_id)

    # ── control ──
    def stop(self, task_id: str) -> Dict[str, Any]:
        st = self._active_states.get(task_id)
        if st is not None:
            st["stop_requested"] = True
        # Wake any pending confirmation wait so the loop can unwind promptly.
        cs = self._confirm_state.get(task_id)
        if cs is not None:
            cs["event"].set()

        task = self.persistence.load_task(task_id)
        if task and task.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            task.status = TaskStatus.INTERRUPTED
            task.updated_at = _now()
            self.persistence.save_task(task)
            self.event_bus.publish(
                task_id,
                "task_interrupted",
                {"task_id": task_id, "status": TaskStatus.INTERRUPTED.value},
            )
            return {"ok": True, "status": TaskStatus.INTERRUPTED.value}
        return {"ok": True, "status": task.status.value if task else "UNKNOWN"}

    # ── human confirmation (P1-2) ──
    def request_confirm(self, task_id: str, tool_call_id: str):
        import threading

        ev = threading.Event()
        self._confirm_state[task_id] = {
            "tool_call_id": tool_call_id,
            "event": ev,
            "result": None,
        }
        return ev

    def consume_confirm(self, task_id: str, tool_call_id: str) -> bool:
        cs = self._confirm_state.get(task_id)
        if cs and cs["tool_call_id"] == tool_call_id:
            return bool(cs["result"])
        return False

    def confirm(self, task_id: str, tool_call_id: str, approved: bool) -> bool:
        cs = self._confirm_state.get(task_id)
        if not cs or cs["tool_call_id"] != tool_call_id:
            return False
        cs["result"] = approved
        cs["event"].set()
        return True

    # ── artifacts ──
    def add_artifact(self, task_id: str, path: Path) -> Artifact:
        art = self.persistence.register_artifact(path)
        st = self._active_states.get(task_id)
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
