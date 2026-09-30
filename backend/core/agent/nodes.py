"""Agent node implementations (Planner / Executor / Tool / Reflect / Confirm).

:class:`AgentRuntime` owns the per-task references the nodes need (the
``TaskManager`` for events & persistence, the ``LLMClient``, and the resolved
tool instances) and exposes each graph node as a bound method. Graph edges are
declared in :mod:`backend.core.agent.graph`.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, cast

from ...config import get_settings
from ...utils.logging import get_logger
from ..tools.base import BaseTool, ToolResult
from ..tools.http_api import WRITE_METHODS
from . import inject
from .context import compress_messages, evict_tool_results
from .prompts import EXECUTOR_SYSTEM, PLANNER_SYSTEM
from .state import AgentState

logger = get_logger("agent.runtime")

# ── confirmation terminal states (issue #57 AC5) ─────────────────────────────
#
# Before #57 the gate had ONE failure state: ``consume_confirm`` answered False
# and that meant「未批准即拒绝」, so an explicit "no", a human who never answered
# and a stop() that woke the wait all folded into the same record and the same
# ``rejected by user`` text. Codex models these as an enum (Denied / TimedOut /
# Abort) rather than a bool's shadow; these are the same three names.
#
# The bucket is written onto the tool-call record itself (``confirm_outcome``),
# which is what the ``tool_result`` event, the trace, the run manifest and the
# persisted ``ToolCallRecord`` all carry.
CONFIRM_APPROVED = "approved"
CONFIRM_DENIED = "denied"
CONFIRM_TIMED_OUT = "timed_out"
CONFIRM_ABORTED = "aborted"
#: Evaluation mode (#57 AC6): the gate still fires, an armed verdict is simply
#: pre-supplied by the harness. Distinct from ``approved`` — no human said so.
CONFIRM_AUTO_APPROVED = "auto_approved"

# ── settled tool-call statuses (issue #100) ─────────────────────────────────
#
# The three statuses a call can carry once ``tool_node`` has finished dealing
# with it — executed, failed, or skipped by the gate. ``_current_tool_calls``
# lives across a whole round and these are written in place on the record, so
# this set is what「已执行」means on the loop entry: no second ledger needed.
TERMINAL_TOOL_CALL_STATUSES = frozenset({"success", "failed", "skipped"})


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
        confirm_enabled: bool = True,
        auto_approve: bool = False,
        parent_task_id: str = "",
    ) -> None:
        self.task_id = task_id
        self.parent_task_id = parent_task_id
        # Issue #60: ``TaskManager.stop()`` only flips the id it was handed, so a
        # subtask has to watch its own id *and* the parent's. Watching the parent
        # alone would break ``stop(subtask_id)``.
        self._stop_watch_ids = [task_id] + ([parent_task_id] if parent_task_id else [])
        self.tm = task_manager
        self.llm = llm
        self.tools: Dict[str, BaseTool] = {t.name: t for t in tools}
        self.tool_schemas = tool_schemas
        self.max_steps = max_steps
        self.confirm_enabled = confirm_enabled  # False for subtask graphs (no human_confirm)
        # Issue #57 AC6: 评测态只把「必问」换成放行（终态记成 auto_approved），
        # 判定面、事件面、显式拒绝一概照旧。与 confirm_enabled 是两件事：
        # 后者是「这台 runtime 根本没有闸门」（子任务图），前者是「有人不在」。
        self.auto_approve = auto_approve
        self._te: Any = None  # lazily built ToolExecutor (see tool_executor)
        self._aux = aux_llm  # injected aux client (may be None)
        self._aux_computed = aux_llm is not None
        # P1-A′: discover + merge once per task (task-level cache); mid-task edits
        # to AGENTS.md intentionally do not take effect. Subtask graphs share this
        # __init__, so they receive the same block with no extra code.
        settings = getattr(getattr(self, "tm", None), "settings", None) or get_settings()
        # Issue #57 AC5: 等一个判决要有上界，否则「超时未答」这一档不可达。和下面的
        # 注入块一样按任务快照一次——任务中途改 confirm_timeout_sec 不生效。
        self.confirm_timeout_sec: float = settings.confirm_timeout_sec
        self._inject_block: str = (
            inject.build_inject_block(settings)
            if getattr(settings, "context_inject_enabled", True)
            else ""
        )

    # ── helpers ──
    def _publish(self, event_type: str, data: Dict[str, Any]) -> None:
        self.tm.event_bus.publish(self.task_id, event_type, data)

    def _stopped(self, state: AgentState) -> bool:
        """Authoritative stop check (Issue #4).

        With a checkpointer mounted, langgraph hands each superstep a COPY of
        the state, so a ``stop()`` that flips the manager's legacy
        ``_active_states`` dict is invisible inside a running graph. Nodes
        therefore also poll the manager-level flag; when it fires we write it
        back into THIS run's copy so downstream conditional edges see it.
        """
        if state.get("stop_requested"):
            return True
        tm = getattr(self, "tm", None)
        checker = getattr(tm, "is_stop_flagged", None)  # test fakes may not ship it
        if callable(checker) and any(checker(watched) for watched in self._stop_watch_ids):
            state["stop_requested"] = True
            return True
        return False

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
        the messages are returned untouched. The P1-A′ injection block rides on
        the *system* message and is deliberately outside the compression budget.
        """
        settings = getattr(getattr(self, "tm", None), "settings", None) or get_settings()
        messages = state.get("messages", []) or []
        # Trace pointer shared by eviction and the in-band squeeze (#49), so both
        # write the same 留痕 contract.
        trace_ref = (
            str(settings.trace_path / f"{self.task_id}.jsonl") if settings.trace_enabled else ""
        )
        # T1.4: move the bulky stuff out first (mechanical, zero LLM calls), then
        # decide whether compression is still needed — evict → compress is a
        # fixed order. Failures degrade to "no eviction" and never block the run.
        if settings.context_evict_enabled:
            try:
                messages, evictions = evict_tool_results(
                    messages,
                    threshold_chars=settings.context_evict_threshold_chars,
                    protect_recent=settings.context_evict_protect_recent,
                    head_chars=settings.context_evict_head_chars,
                    tail_chars=settings.context_evict_tail_chars,
                    trace_ref=trace_ref,
                )
                if evictions:
                    state["messages"] = messages
                    for ev in evictions:
                        self._publish(
                            "tool_result_evicted",
                            {"step_index": state.get("step_index", 0), **ev},
                        )
            except Exception:  # pragma: no cover - defensive
                logger.warning("tool-result eviction failed; continuing without it", exc_info=True)
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
            trace_ref=trace_ref,
            head_chars=settings.context_evict_head_chars,
            tail_chars=settings.context_evict_tail_chars,
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
                        "band_evicted": meta["band_evicted"],
                        "converged": meta["converged"],
                        "context_tokens": meta["context_tokens"],
                        "strategy": meta["strategy"],
                    },
                )
            except Exception:  # pragma: no cover - event is optional
                logger.warning("failed to publish context_compressed event", exc_info=True)
        return [{"role": "system", "content": system + self._inject_block}, *compressed]

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
        if self._stopped(state):
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

        # 正常分支产出的永远是 PlanStep dict；异常分支改用 state["error"] 承载
        # 失败原因、plan 留空（Issue #12），故这里不再需要 List[Any] 的放宽。
        plan: List[Dict[str, Any]]
        try:
            resp = self.llm.complete(self._build_messages(state, PLANNER_SYSTEM))
            plan = self._parse_plan(resp.content)
        except Exception as exc:
            logger.exception("planner LLM error")
            # Issue #12: surface the real cause instead of degrading it into a
            # string plan item. ``state["error"]`` makes ``finish`` report FAILED
            # (and ``graph._after_planner`` short-circuits to finish), so the
            # provider 403 / network error stays visible instead of being
            # replaced by a secondary TypeError when the plan is persisted.
            state["error"] = f"Planner LLM error: {exc}"
            plan = []
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
        if self._stopped(state):
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
        """Blocking plan-level confirmation used by ``risk_policy=pause``.

        #57 的终态分档只覆盖 per-call 闸门：这里的返回值只有 plan 一个消费者，
        没有可标注的 tool-call 记录，也就没有「事件与状态里可区分」的落点。
        #57 AC6 的评测态在这里同样成立——不等人，但也不冒充人批过。
        """
        if self.auto_approve:
            # 评测态：不向一个不在场的人发问（#57 AC6 的「必问 → 放行」）。
            return True
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
        while not ev.is_set() and not self._stopped(state):
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

    def executor(self, state: AgentState) -> AgentState:
        if self._stopped(state):
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
                    # P2 item 1: MCP tools run a per-call risk judgement (the
                    # server's self-reported destructiveHint / readOnlyHint, with
                    # mcp_force_confirm as the override — issue #57). Duck-typed
                    # on `needs_per_call_confirm` so this node never imports
                    # McpTool. A judgement that cannot run fails CLOSED — it
                    # requires confirmation rather than silently bypassing the
                    # gate.
                    if tool and getattr(tool, "needs_per_call_confirm", False):
                        try:
                            # cast 在运行时是恒等函数：BaseTool 不声明 _needs_confirm
                            # （McpTool 才有），这里只把鸭子类型交代给 mypy。
                            if cast(Any, tool)._needs_confirm(args):
                                need_confirm = True
                        except Exception:
                            need_confirm = True
                            logger.warning(
                                "MCP confirm judgement failed for %s; requiring confirmation",
                                tool.name,
                                exc_info=True,
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
            assistant_turn: Dict[str, Any] = {
                "role": "assistant",
                "content": resp.content or "",
                "tool_calls": openai_tool_calls,
            }
            # Issue #45: thinking-mode endpoints (DeepSeek official) reject the
            # *next* request when the previous turn's reasoning trace is missing.
            # Written only when non-empty — providers that never report it must
            # keep sending exactly the same message shape as before.
            if resp.reasoning_content:
                assistant_turn["reasoning_content"] = resp.reasoning_content
            state.setdefault("messages", []).append(assistant_turn)
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
            final_turn: Dict[str, Any] = {"role": "assistant", "content": answer}
            if resp.reasoning_content:  # Issue #45, same rule as the tool-call turn
                final_turn["reasoning_content"] = resp.reasoning_content
            state.setdefault("messages", []).append(final_turn)
            if step is not None:
                step["thought"] = answer
            self._publish("final_answer", {"answer": answer})
            state["_last_action"] = "final_answer"
        return state

    def tool_node(self, state: AgentState) -> AgentState:
        if self._stopped(state):
            state["_last_action"] = "stop"
            return state

        tcs = state.get("_current_tool_calls", []) or []
        step = state["steps"][-1] if state.get("steps") else None
        confirmed = state.get("_confirmed_ids", []) or []
        rejected = state.get("_rejected_ids", []) or []

        for rec in tcs:
            if rec["status"] in TERMINAL_TOOL_CALL_STATUSES:
                # 第三道守卫：已经交代过的调用不再动（issue #100）。一轮里 ≥2 个
                # 待确认项时，人逐个批准会让图沿 ``human_confirm -> tool`` 那条静态边
                # 重入本节点（graph.py ``_after_tool`` 只要还有没判决的就回闸门），而
                # ``rec["status"]`` 是上一轮原地写成的终态、``_current_tool_calls``
                # 全程活着。少了这一道，前两道守卫（已拒绝 / 尚未批准）都拦不住那个
                # 已经批过并跑完的调用：它会被原样再 dispatch 一次 —— 写类工具写两遍、
                # 命令跑两遍，同一 ``tool_call_id`` 长出两条 tool message，「批准一次」
                # 成了「执行 N 次」，闸门反把自己要保的那件事破掉。
                # 幂等按 ``rec`` 记，与调用是否过闸门无关；重试不在这里 —— retryable
                # 的账在 ``ToolExecutor.dispatch`` 里由 ``with_retry`` 闭环，从不借道
                # 图重入（resilience.py，本票零触碰）。
                continue
            if rec["need_confirm"] and rec["id"] in rejected:
                rec["status"] = "skipped"
                rec["error"] = self._confirm_skip_text(rec)
                self._publish("tool_result", rec)
                self._feed_tool_failure(state, rec)
                continue
            if rec["need_confirm"] and rec["id"] not in confirmed:
                # Not yet authorised; skip (should be reached via human_confirm first).
                continue

            tool = self.tools.get(rec["tool_name"])
            if tool is None:
                rec["status"] = "failed"
                rec["error"] = f"unknown tool: {rec['tool_name']}"
                self._publish("tool_result", rec)
                self._feed_tool_failure(state, rec)
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

            # Feed the tool result back into the conversation: the payload
            # verbatim on success, the reason on failure (#95 — a bare ``null``
            # told the model nothing, so every next turn guessed at what went
            # wrong).
            if result.success:
                state.setdefault("messages", []).append(
                    {"role": "tool", "tool_call_id": rec["id"], "content": json.dumps(result.data, ensure_ascii=False, default=str)}
                )
            else:
                self._feed_tool_failure(state, rec)

            # Register an artifact when a producing tool wrote a file on disk.
            # Two guards, one per axis: only a ``registers_artifact`` tool is a
            # source of products (#72 — a ``read`` of the same file used to
            # register it again), and the path must be a real file, since a
            # result advertising a directory would otherwise land in
            # verification as a 0-byte「产物为空」and loop a healthy task back
            # (#17).
            if (
                tool.registers_artifact
                and result.success
                and isinstance(result.data, dict)
                and result.data.get("path")
            ):
                p = Path(result.data["path"])
                if p.is_file():
                    self.tm.add_artifact(self.task_id, p)

        state["_last_action"] = "tool_done"
        if step is not None:
            step["status"] = "done"
        return state

    def _feed_tool_failure(self, state: AgentState, rec: Dict[str, Any]) -> None:
        """Answer one un-successful call on the conversation face (issue #95).

        All three buckets share this line — human-rejected, unknown tool,
        executed and failed — and each has just written its reason onto
        ``rec["error"]``, the very field the ``tool_result`` event publishes: the
        model and the human read one text from one source. A tool that failed
        without saying anything still gets a word, because an empty reason reads
        as little as the old bare ``null`` did. The success path never comes
        through here.
        """
        state.setdefault("messages", []).append(
            {
                "role": "tool",
                "tool_call_id": rec["id"],
                "content": json.dumps(
                    {"ok": False, "error": rec["error"] or "tool failed"},
                    ensure_ascii=False,
                    default=str,
                ),
            }
        )

    def _confirm_skip_text(self, rec: Dict[str, Any]) -> str:
        """三档未批准各自的文案（#57 AC5）。

        只有 ``denied`` 留着 ``rejected by user`` ——那是唯一一句真话。从前三种
        情形共用这一句，于是「人没答」「人说不」「被 stop 折回来」在日志里同形，
        事后无法复原是哪一种（同 #57 取证里子任务那侧的合并捕获）。
        没有 ``confirm_outcome`` 的记录（从未进过闸门）按旧文案走，不凭空安新终态。
        """
        outcome = rec.get("confirm_outcome") or CONFIRM_DENIED
        if outcome == CONFIRM_TIMED_OUT:
            return f"no human verdict before the {self.confirm_timeout_sec:g}s confirm timeout"
        if outcome == CONFIRM_ABORTED:
            return "interrupted by stop while awaiting confirmation"
        return "rejected by user"

    def _ask_human(self, state: AgentState, target: Dict[str, Any]) -> str:
        """Wait for one verdict on ``target`` and name the terminal state.

        分档的判据顺序是这张表的骨架：**先问「人给没给过判决」，再问「是被谁
        打断的」**。``consume_confirm`` 回 None 表示人不在这个点上答过——它不是
        一次拒绝，也不该冒充拒绝（#57 之前正是这一步折成了「未批准即拒绝」）。
        """
        event = self.tm.request_confirm(self.task_id, target["id"])
        self._publish(
            "human_confirm_required",
            {"tool_call_id": target["id"], "tool_name": target["tool_name"], "input": target["input"]},
        )
        # Block until the user decides, the wait expires, or the task is stopped
        # (≤2s responsiveness). Issue #4: poll the manager-level flag as well —
        # ``state`` is a per-superstep copy under a checkpointer, so a stop() that
        # mutates the legacy dict is invisible here unless refreshed from the source.
        deadline = time.monotonic() + self.confirm_timeout_sec
        while not event.is_set() and not state.get("stop_requested"):
            if self._stopped(state):
                break
            if time.monotonic() >= deadline:
                break
            event.wait(0.2)
        verdict = self.tm.consume_confirm(self.task_id, target["id"])
        if verdict:
            return CONFIRM_APPROVED
        if verdict is False:
            return CONFIRM_DENIED
        # No verdict: whoever ended the wait owns the bucket. The authoritative
        # manager flag decides, because ``state`` may be a stale copy (#4).
        stop_checker = getattr(getattr(self, "tm", None), "is_stop_flagged", None)
        stop_forced = state.get("stop_requested") or (
            callable(stop_checker) and stop_checker(self.task_id)
        )
        return CONFIRM_ABORTED if stop_forced else CONFIRM_TIMED_OUT

    def human_confirm_node(self, state: AgentState) -> AgentState:
        """Ask about one un-confirmed gated call, then record the terminal state.

        #57 AC5: 拒绝 / 超时未答 / 停止打断 是三档，不是一个布尔的影子。分档写回
        记录体（``confirm_outcome``），事件、状态、持久化三面同源。

        #57 AC6: 评测态（``auto_approve``）不再整块旁路判定 —— 判定照算、闸门照进、
        终态照播（只播 ``human_confirm_resolved``；向一个不在场的人发问是伪造事件），
        换掉的只有「等一个不出现的人」这一格，记成 ``auto_approved``。显式拒绝与
        ``_needs_confirm`` 重算块都不经过这条路。
        """
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

        if self.auto_approve:
            outcome = CONFIRM_AUTO_APPROVED
        else:
            outcome = self._ask_human(state, target)

        # Written on the record itself: the tool_result event, the trace, the run
        # manifest and the persisted ToolCallRecord all carry this dict (#57 AC5).
        target["confirm_outcome"] = outcome
        # The asked/replied pair (opencode names its two events the same way):
        # the outcome needs its own event because a 停止打断 run never reaches the
        # tool node, so no ``tool_result`` would ever carry the label. No ``input``
        # here on purpose: the trace does not redact, and the arguments are already
        # on the ``tool_call`` event under the same id — a second copy would only
        # double the sensitive surface with no reader.
        self._publish(
            "human_confirm_resolved",
            {
                "tool_call_id": target["id"],
                "tool_name": target.get("tool_name", ""),
                "outcome": outcome,
            },
        )
        if outcome != CONFIRM_APPROVED and outcome != CONFIRM_AUTO_APPROVED:
            logger.warning(
                "confirmation for %s ended %s (task %s)",
                target["tool_name"],
                outcome,
                self.task_id,
            )
        if outcome in (CONFIRM_APPROVED, CONFIRM_AUTO_APPROVED):
            state.setdefault("_confirmed_ids", []).append(target["id"])
        else:
            state.setdefault("_rejected_ids", []).append(target["id"])
            if outcome == CONFIRM_ABORTED:
                # Issue #4: record when the decision was forced by a stop (no
                # human verdict). The event is shared between approvals and
                # stop-wakeups and ``state`` may be a stale copy under a
                # checkpointer, so the authoritative manager flag decides.
                #
                # ``timed_out`` deliberately does NOT set this marker (#57 AC5):
                # it is the resume guard's channel 1, and its documented meaning
                # is "parked at the gate awaiting a verdict" — a timed-out gate
                # is no longer awaiting anything, the run moved on and the call
                # landed in ``_rejected_ids`` (which is exactly what guard
                # channel 2 checks, and it says *not parked*). Writing it would
                # turn one unanswered dialog into a task that can never be
                # resumed, with a message blaming a stop that never happened.
                state["pending_confirm"] = {
                    "tool_call_id": target["id"],
                    "tool_name": target.get("tool_name", ""),
                    "input": target.get("input", {}),
                }
        # NOTE (red line): the `_needs_confirm` recomputation below is the P0
        # dead-loop fix — DO NOT MODIFY this block.

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
        """P0-B completion verification (Issue #25).

        A model's "I'm done" claim (``_last_action == "final_answer"``) is not
        trusted blindly: re-check the concrete deliverables with deterministic
        code — the strongest independence, since ``Path.exists()`` cannot be
        argued with the way an LLM self-critique can ("structural hallucination").
        On failure, inject the evidence and loop back to the planner by flipping
        ``_last_action`` to ``verify_failed`` (the existing ``_after_reflect``
        router already sends any non-``final_answer`` to ``planner``); after
        ``verify_max_retries`` degrade to COMPLETED with a ``degraded`` marker
        rather than failing a possibly-genuinely-done task.

        Reject semantics, the ``_needs_confirm`` recompute block, and the graph
        topology are untouched. Error paths are skipped (``finish`` maps them to
        FAILED). With ``verify_enabled`` false this is the original no-op shell.
        """
        if self._stopped(state):
            return state
        if state.get("error"):
            # Executor LLM errors also set final_answer; finish judges them FAILED.
            # Verifying an erroring task would only churn the loop and change the
            # error semantics (review 漏洞1).
            return state
        settings = getattr(getattr(self, "tm", None), "settings", None) or get_settings()
        if not getattr(settings, "verify_enabled", True):
            return state  # gate off == original empty-shell behaviour (zero regression)
        if state.get("_last_action") != "final_answer":
            return state  # only verify a claimed completion

        failures = self._verify_completion(state)
        attempts = int(state.get("_verify_attempts", 0) or 0)
        max_retries = int(getattr(settings, "verify_max_retries", 2))

        if not failures:
            state["_verification"] = {
                "passed": True, "failures": [], "attempts": attempts, "degraded": False,
            }
            self._publish("verification", dict(state["_verification"], task_id=self.task_id))
            return state  # _last_action stays final_answer -> _after_reflect -> finish

        if attempts >= max_retries:
            # Q4: degrade, never FAIL — a false negative must not kill a real task.
            state["_verification"] = {
                "passed": False, "failures": failures, "attempts": attempts, "degraded": True,
            }
            self._publish("verification", dict(state["_verification"], task_id=self.task_id))
            return state  # keep final_answer -> finish -> COMPLETED (degraded)

        # Loop back with evidence: flip _last_action so the existing router sends
        # the flow to planner; the injected message tells the model what to fix.
        state["_verify_attempts"] = attempts + 1
        state["_verification"] = {
            "passed": False, "failures": failures, "attempts": attempts + 1, "degraded": False,
        }
        feedback = (
            "完成验证未通过：" + "；".join(failures) +
            "。任务尚未可验证地完成，请据此修正后继续；在真正满足前不要重复声称完成。"
        )
        state.setdefault("messages", []).append({"role": "system", "content": feedback})
        self._publish(
            "verification", dict(state["_verification"], task_id=self.task_id, loop_back=True)
        )
        state["_last_action"] = "verify_failed"
        return state

    def _verify_completion(self, state: AgentState) -> List[str]:
        """Deterministic completion checks; returns failure reasons ([] == verified).

        S1 — every artifact registered this run must still exist and be non-empty.
        S2 (triple-narrowed) — zero successful product AND zero successful tool
        call AND >=1 failed tool call: "everything failed yet claims done". The
        narrowing keeps a pure-Q&A task with one flaky search failure from being
        flagged. Artifacts are read from the manager's authoritative side (P3
        copy semantics) with a getattr guard for SimpleNamespace test fakes.

        Deliberately NOT checked here (P1): completeness — a deliverable that was
        required but never produced (needs declared expectations, spec §八).
        """
        failures: List[str] = []
        tm_states = getattr(self.tm, "_active_states", None) or {}
        run_state = tm_states.get(self.task_id, {}) or {}
        artifacts = run_state.get("artifacts", []) or []
        ok_artifacts = 0
        for art in artifacts:
            path = art.get("path") if isinstance(art, dict) else getattr(art, "path", None)
            if not path:
                continue
            p = Path(path)
            if not p.exists():
                failures.append(f"产物缺失：{p.name}")
            elif p.stat().st_size == 0:
                failures.append(f"产物为空：{p.name}")
            else:
                ok_artifacts += 1
        statuses = [
            tc.get("status")
            for st in (state.get("steps") or [])
            for tc in (st.get("tool_calls") or [])
        ]
        n_success = sum(1 for s in statuses if s == "success")
        n_failed = sum(1 for s in statuses if s == "failed")
        if ok_artifacts == 0 and n_success == 0 and n_failed >= 1:
            failures.append(
                f"全程 {n_failed} 个工具调用失败且无任何成功产物，却声称完成"
            )
        return failures

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
