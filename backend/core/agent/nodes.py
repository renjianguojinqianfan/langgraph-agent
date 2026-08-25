"""Agent node implementations (Planner / Executor / Tool / Reflect / Confirm).

:class:`AgentRuntime` owns the per-task references the nodes need (the
``TaskManager`` for events & persistence, the ``LLMClient``, and the resolved
tool instances) and exposes each graph node as a bound method. Graph edges are
declared in :mod:`backend.core.agent.graph`.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List

from ...config import get_settings
from ...utils.logging import get_logger
from ..tools.base import BaseTool, ToolResult
from ..tools.http_api import WRITE_METHODS
from .context import compress_messages
from .prompts import EXECUTOR_SYSTEM, PLANNER_SYSTEM
from .state import AgentState

logger = get_logger("agent.runtime")


def _fold_subtask_summaries(results) -> str:
    """Build a compact folded summary of subtask results.

    Only this compact summary is appended to the main ``messages`` — subtask
    internal assistant/tool messages never reach the parent context (the
    token volume is bounded well below the subtask's full message history).
    """
    if not results:
        return ""
    lines = []
    for r in results:
        if getattr(r, "status", "") == "completed":
            lines.append(f"[子任务 {r.name}] 完成。摘要：{r.summary}")
        else:
            lines.append(f"[子任务 {r.name}] 失败：{getattr(r, 'error', '') or 'unknown error'}")
    text = "\n".join(lines)
    # Hard cap so the folded block is at most ~1/10 of a typical subtask log.
    return text[:1000]


class AgentRuntime:
    """Per-task runtime that backs the LangGraph nodes."""

    def __init__(
        self,
        task_id: str,
        task_manager: Any,
        llm: Any,
        tools: List[BaseTool],
        tool_schemas: List[Dict[str, Any]],
        max_steps: int = 15,
        aux_llm: Any = None,
        subagent_executor: Any = None,
        confirm_enabled: bool = True,
    ) -> None:
        self.task_id = task_id
        self.tm = task_manager
        self.llm = llm
        self.tools: Dict[str, BaseTool] = {t.name: t for t in tools}
        self.tool_schemas = tool_schemas
        self.max_steps = max_steps
        self.confirm_enabled = confirm_enabled  # False for subtask graphs (no human_confirm)
        self._te: Any = None  # lazily built ToolExecutor (see tool_executor)
        self._aux = aux_llm  # injected aux client (may be None)
        self._aux_computed = aux_llm is not None
        self.subagent_executor = subagent_executor  # SubAgentExecutor (main mode only)

    # ── helpers ──
    def _publish(self, event_type: str, data: Dict[str, Any]) -> None:
        self.tm.event_bus.publish(self.task_id, event_type, data)

    @property
    def tool_executor(self) -> "Any":
        """Lazily constructed :class:`ToolExecutor` (robust to ``tm=None``).

        Built on first use so ``AgentRuntime(task_manager=None, ...)`` still
        compiles a graph without touching settings / event bus.
        """
        if self._te is None:
            from ..tools.resilience import ToolExecutor

            settings = getattr(getattr(self, "tm", None), "settings", None) or get_settings()
            self._te = ToolExecutor(settings, publish_fn=self._publish)
        return self._te

    @property
    def aux_llm(self) -> Any:
        """Lazily constructed auxiliary LLM client (robust to ``tm=None``).

        When ``aux_llm_enabled=False`` (or the config is incomplete) this
        returns ``None`` — the degradation path — so no extra LLM call is ever
        made for summaries / risk semantics. Built at most once per runtime.
        """
        if not self._aux_computed:
            self._aux_computed = True
            settings = getattr(getattr(self, "tm", None), "settings", None) or get_settings()
            if settings.aux_llm_enabled:
                from ..llm.openai_compat import get_aux_llm

                self._aux = get_aux_llm(settings, main_llm=self.llm)
            else:
                self._aux = None
        return self._aux

    def _build_messages(self, state: AgentState, system: str) -> List[Dict[str, Any]]:
        """Build the message list sent to the LLM, compressing history first.

        Compression happens exactly here (shared by planner and executor) so
        every LLM call sees an up-to-date, bounded context. Under the budget
        the messages are returned untouched.
        """
        settings = getattr(getattr(self, "tm", None), "settings", None) or get_settings()
        messages = state.get("messages", []) or []
        # P1 item 4: summarisation prefers the aux model; without aux it falls
        # back to the main model (the pre-P1 behaviour) — never an extra call.
        llm_for_summary = self.aux_llm or self.llm
        compressed, meta = compress_messages(
            messages,
            budget=settings.context_token_budget,
            keep_recent=settings.context_keep_recent,
            max_messages=settings.context_max_messages,
            strategy=settings.context_compress_strategy,
            llm=llm_for_summary if settings.context_compress_strategy == "summarize" else None,
            summary_max_tokens=settings.context_summary_max_tokens,
        )
        state["context_tokens"] = meta["context_tokens"]
        if meta["compressed"]:
            state["messages"] = compressed
            state["compressed"] = True
            try:
                self._publish(
                    "context_compressed",
                    {
                        "step_index": state.get("step_index", 0),
                        "dropped": meta["dropped"],
                        "context_tokens": meta["context_tokens"],
                        "strategy": meta["strategy"],
                    },
                )
            except Exception:  # pragma: no cover - event is optional
                logger.warning("failed to publish context_compressed event", exc_info=True)
        return [{"role": "system", "content": system}, *compressed]

    def _parse_plan(self, content: str) -> List[Dict[str, Any]]:
        content = (content or "").strip()
        try:
            data = json.loads(content)
        except Exception:
            data = [ln.strip("- ").strip() for ln in content.splitlines() if ln.strip()]
        steps: List[Dict[str, Any]] = []
        if isinstance(data, list):
            for i, item in enumerate(data, 1):
                if isinstance(item, dict):
                    desc = item.get("description") or item.get("step") or str(item)
                else:
                    desc = str(item)
                steps.append({"index": i, "description": desc, "status": "pending"})
        else:
            steps.append({"index": 1, "description": str(data), "status": "pending"})
        return steps

    # ── nodes ──
    def planner(self, state: AgentState) -> AgentState:
        if state.get("stop_requested"):
            state["_last_action"] = "stop"
            return state

        state["step_index"] = state.get("step_index", 0) + 1
        idx = state["step_index"]
        step: Dict[str, Any] = {
            "index": idx,
            "thought": "",
            "tool_calls": [],
            "status": "running",
        }
        state.setdefault("steps", []).append(step)

        try:
            resp = self.llm.complete(self._build_messages(state, PLANNER_SYSTEM))
            plan = self._parse_plan(resp.content)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("planner LLM error")
            plan = [f"Planner error: {exc}"]
        state["plan"] = plan
        self._publish("plan_update", {"plan": plan})

        thought = f"Step {idx}: planning."
        step["thought"] = thought
        self._publish("step_start", {"index": idx, "thought": thought})
        state["_last_action"] = "plan"
        return state

    # ── P1 item 1: planning-phase risk scan (EHRB layer 1) ──
    def risk_scan(self, state: AgentState) -> AgentState:
        """Scan the latest plan before the executor runs anything.

        * ``risk_scan_enabled=False`` -> skip entirely (zero regression).
        * Keyword scan (+ optional semantic analysis via the aux model).
        * Publishes ``risk_report`` (full) and ``risk_found`` (per hit).
        * ``risk_policy=confirm``: high-risk round sets ``_risk_blocked`` so the
          executor raises ``need_confirm`` -> the existing P0 human_confirm flow.
        * ``risk_policy=pause``: a single plan-level blocking confirmation
          (confirm key ``risk_plan``); rejection marks high steps skipped.

        The P0 ``_needs_confirm`` recomputation is deliberately untouched.
        """
        if state.get("stop_requested"):
            state["_last_action"] = "stop"
            return state
        settings = getattr(getattr(self, "tm", None), "settings", None) or get_settings()
        state["risk_report"] = []
        state["_risk_blocked"] = False
        if not settings.risk_scan_enabled:
            state["_last_action"] = "plan"
            return state

        from .risk import RiskScanner

        scanner = RiskScanner(
            settings,
            aux_llm=self.aux_llm,
            semantic_enabled=settings.risk_semantic_enabled,
        )
        items = scanner.scan(state.get("plan", []) or [])
        state["risk_report"] = items
        self._publish(
            "risk_report",
            {
                "items": items,
                "policy": settings.risk_policy,
                "semantic_enabled": settings.risk_semantic_enabled,
            },
        )
        hits = [it for it in items if it["level"] in ("high", "medium")]
        for it in hits:
            self._publish("risk_found", it)

        has_high = any(it["level"] == "high" for it in items)
        if settings.risk_policy == "pause" and has_high:
            # Single plan-level blocking confirmation; no new state machine.
            if self._plan_confirm(state, items):
                state["_risk_blocked"] = False
            else:
                state["_risk_blocked"] = False
                self._mark_high_steps_skipped(state, items)
        else:
            # Default confirm policy: per-call confirmation via the executor.
            state["_risk_blocked"] = has_high
        state["_last_action"] = "plan"
        return state

    def _plan_confirm(self, state: AgentState, items: List[Dict[str, Any]]) -> bool:
        """Blocking plan-level confirmation used by ``risk_policy=pause``."""
        if self.tm is None:
            return False
        key = "risk_plan"
        ev = self.tm.request_confirm(self.task_id, key)
        self._publish(
            "human_confirm_required",
            {
                "tool_call_id": key,
                "tool_name": "risk_scan",
                "input": {"items": items},
            },
        )
        while not ev.is_set() and not state.get("stop_requested"):
            ev.wait(0.2)
        return bool(self.tm.consume_confirm(self.task_id, key))

    def _mark_high_steps_skipped(self, state: AgentState, items: List[Dict[str, Any]]) -> None:
        """Mark high-risk plan steps as skipped after a rejected pause."""
        plan = state.get("plan", []) or []
        for it in items:
            if it["level"] != "high":
                continue
            idx = it["step_index"] - 1
            if 0 <= idx < len(plan):
                step = plan[idx]
                if isinstance(step, dict):
                    step["status"] = "skipped"
                else:
                    plan[idx] = {"index": idx + 1, "description": str(step), "status": "skipped"}
        state["plan"] = plan

    # ── P1 item 2: sub-agent split (built-in scenario dispatch) ──
    def subagent_split(self, state: AgentState) -> AgentState:
        """Detect a built-in subtask scenario and dispatch isolated subtasks.

        Runs only in ``mode="main"`` graphs (subtask graphs omit this node,
        which is the recursion guard). When no scenario matches — or the
        executor is unavailable / disabled — the flow simply proceeds to the
        executor (zero regression). Sub-agent summary events are published on
        the main channel; subtask *internal* events stay on their own channel.
        """
        if state.get("stop_requested"):
            state["_last_action"] = "stop"
            return state
        state["subtasks"] = state.get("subtasks", []) or []
        settings = getattr(getattr(self, "tm", None), "settings", None) or get_settings()
        executor = self.subagent_executor
        if not settings.subagent_enabled or executor is None or state.get("_is_subtask"):
            state["_last_action"] = "plan"
            return state

        user_input = ""
        for m in state.get("messages", []) or []:
            if m.get("role") == "user":
                user_input = str(m.get("content", ""))
                break
        plan = state.get("plan", []) or []
        results = executor.run_plan_with_subtasks(
            parent_task_id=self.task_id,
            user_input=user_input,
            plan=plan,
            publish=self._publish,
        )
        if not results:
            state["_last_action"] = "plan"
            return state

        state["subtasks"] = [r.to_dict() for r in results]
        folded = _fold_subtask_summaries(results)
        if folded:
            state.setdefault("messages", []).append(
                {"role": "assistant", "content": folded}
            )
        state["_last_action"] = "plan"
        return state

    def executor(self, state: AgentState) -> AgentState:
        if state.get("stop_requested"):
            state["_last_action"] = "stop"
            return state

        step = state["steps"][-1] if state.get("steps") else None
        try:
            resp = self.llm.complete(
                self._build_messages(state, EXECUTOR_SYSTEM), tools=self.tool_schemas
            )
        except Exception as exc:
            logger.exception("executor LLM error")
            state["error"] = f"Executor LLM error: {exc}"
            state["final_answer"] = f"[error] {exc}"
            state["_last_action"] = "final_answer"
            return state

        if resp.tool_calls:
            tcs: List[Dict[str, Any]] = []
            # P1 item 1: a high-risk round (risk_policy=confirm) raises
            # need_confirm for every tool call of this round; the P0
            # human_confirm flow then asks the user before any execution.
            risk_blocked = bool(state.get("_risk_blocked"))
            for tc in resp.tool_calls:
                name = tc.get("name", "")
                args = tc.get("arguments", {}) or {}
                tool = self.tools.get(name)
                need_confirm = False
                if self.confirm_enabled:
                    need_confirm = bool(tool.requires_confirm) if tool else False
                    if tool and tool.name == "http_request" and str(args.get("method", "")).upper() in WRITE_METHODS:
                        need_confirm = True
                    # P2 item 1: MCP tools run a per-call risk judgement (write-
                    # like heuristic + mcp_force_confirm override). Duck-typed on
                    # `needs_per_call_confirm` so this node never imports McpTool.
                    # A judgement failure only warns — it never blocks execution.
                    if tool and getattr(tool, "needs_per_call_confirm", False):
                        try:
                            if tool._needs_confirm(args):
                                need_confirm = True
                        except Exception:
                            logger.warning(
                                "MCP confirm judgement failed for %s", tool.name, exc_info=True
                            )
                    if risk_blocked:
                        need_confirm = True
                rec = {
                    "id": tc.get("id") or f"call_{state['step_index']}_{len(tcs)}",
                    "tool_name": name,
                    "input": args,
                    "output": None,
                    "status": "pending",
                    "error": "",
                    "need_confirm": need_confirm,
                    "confirmed": False,
                }
                tcs.append(rec)

            state["_current_tool_calls"] = tcs
            # Record the assistant turn so the LLM keeps context.
            openai_tool_calls = [
                {
                    "id": r["id"],
                    "type": "function",
                    "function": {"name": r["tool_name"], "arguments": json.dumps(r["input"], ensure_ascii=False)},
                }
                for r in tcs
            ]
            state.setdefault("messages", []).append(
                {"role": "assistant", "content": resp.content or "", "tool_calls": openai_tool_calls}
            )
            for rec in tcs:
                if step is not None:
                    step["tool_calls"].append(rec)
                self._publish("tool_call", rec)

            needs_confirm = any(r["need_confirm"] for r in tcs)
            state["_needs_confirm"] = needs_confirm
            state["_last_action"] = "tool_call"
        else:
            answer = resp.content or "(no content)"
            state["final_answer"] = answer
            state.setdefault("messages", []).append({"role": "assistant", "content": answer})
            if step is not None:
                step["thought"] = answer
            self._publish("final_answer", {"answer": answer})
            state["_last_action"] = "final_answer"
        return state

    def tool_node(self, state: AgentState) -> AgentState:
        if state.get("stop_requested"):
            state["_last_action"] = "stop"
            return state

        tcs = state.get("_current_tool_calls", []) or []
        step = state["steps"][-1] if state.get("steps") else None
        confirmed = state.get("_confirmed_ids", []) or []
        rejected = state.get("_rejected_ids", []) or []

        for rec in tcs:
            if rec["need_confirm"] and rec["id"] in rejected:
                rec["status"] = "skipped"
                rec["error"] = "rejected by user"
                self._publish("tool_result", rec)
                continue
            if rec["need_confirm"] and rec["id"] not in confirmed:
                # Not yet authorised; skip (should be reached via human_confirm first).
                continue

            tool = self.tools.get(rec["tool_name"])
            if tool is None:
                rec["status"] = "failed"
                rec["error"] = f"unknown tool: {rec['tool_name']}"
                self._publish("tool_result", rec)
                continue

            try:
                result: ToolResult = self.tool_executor.dispatch(tool, **rec["input"])
            except Exception as exc:  # final safety net
                logger.exception("tool %s crashed", rec["tool_name"])
                result = ToolResult(success=False, error=str(exc))

            rec["status"] = "success" if result.success else "failed"
            rec["output"] = result.data
            rec["error"] = result.error or ""
            rec["circuit_open"] = result.circuit_open
            rec["retries"] = result.retries
            self._publish("tool_result", rec)

            # Feed the tool result back into the conversation.
            state.setdefault("messages", []).append(
                {"role": "tool", "tool_call_id": rec["id"], "content": json.dumps(result.data, ensure_ascii=False, default=str)}
            )

            # Register an artifact if the tool produced a file on disk.
            if result.success and isinstance(result.data, dict) and result.data.get("path"):
                p = Path(result.data["path"])
                if p.exists():
                    self.tm.add_artifact(self.task_id, p)

        state["_last_action"] = "tool_done"
        if step is not None:
            step["status"] = "done"
        return state

    def human_confirm_node(self, state: AgentState) -> AgentState:
        tcs = state.get("_current_tool_calls", []) or []
        confirmed = state.get("_confirmed_ids", []) or []
        rejected = state.get("_rejected_ids", []) or []
        target = next(
            (r for r in tcs if r["need_confirm"] and r["id"] not in confirmed and r["id"] not in rejected),
            None,
        )
        if target is None:
            state["_last_action"] = "tool_done"
            return state

        event = self.tm.request_confirm(self.task_id, target["id"])
        self._publish(
            "human_confirm_required",
            {"tool_call_id": target["id"], "tool_name": target["tool_name"], "input": target["input"]},
        )
        # Block until the user decides or the task is stopped (≤2s responsiveness).
        while not event.is_set() and not state.get("stop_requested"):
            event.wait(0.2)
        approved = self.tm.consume_confirm(self.task_id, target["id"])
        if approved:
            state.setdefault("_confirmed_ids", []).append(target["id"])
        else:
            state.setdefault("_rejected_ids", []).append(target["id"])

        # Recompute whether any pending confirmation remains. Previously the
        # node only appended to _confirmed_ids/_rejected_ids but left
        # state["_needs_confirm"] as True, so graph.py's _after_tool kept
        # routing the flow back into human_confirm -> GraphRecursionError on
        # single-tool flows. For such flows this must now become False so the
        # flow proceeds to reflect and completes normally.
        confirmed_ids = state.get("_confirmed_ids", []) or []
        rejected_ids = state.get("_rejected_ids", []) or []
        state["_needs_confirm"] = any(
            r["need_confirm"] and r["id"] not in confirmed_ids and r["id"] not in rejected_ids
            for r in tcs
        )
        return state

    def reflect(self, state: AgentState) -> AgentState:
        # Pure routing decision; actual branching handled by after_reflect().
        return state

    def finish(self, state: AgentState) -> AgentState:
        if state.get("stop_requested"):
            state["status"] = "INTERRUPTED"
        elif state.get("error"):
            state["status"] = "FAILED"
        elif state.get("final_answer"):
            state["status"] = "COMPLETED"
        else:
            state["status"] = "FAILED"
        if state.get("steps"):
            state["steps"][-1]["status"] = "done" if state["status"] == "COMPLETED" else "failed"
        return state
