"""Agent node implementation (the single loop of #139 / ADR 0008).

:class:`AgentRuntime` owns the per-task references the nodes need (the
``TaskManager`` for events & persistence, the ``LLMClient``, and the resolved
tool instances) and exposes each graph node as a bound method. Graph edges are
declared in :mod:`backend.core.agent.graph`.

Since #139 there is **one** LLM node, :meth:`AgentRuntime.agent`. The task's
mode chooses what it gets (ADR 0008 decisions 2 / 3 / 7):

* ``plan`` — ``PLANNER_SYSTEM`` and the ``explore`` tier only (read-only);
  a round with no tool call returns the plan;
* ``build`` — ``EXECUTOR_SYSTEM`` (plus the current plan) and every mounted
  tool; dangerous calls are refused by the S2 hook (:mod:`.policy`).

A round ends when the node returns no tool call, which is also the loop's exit:
the ball goes back to the human and the task parks (never "completed").
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...config import get_settings
from ...utils.logging import get_logger
from ..tools.base import BaseTool, ToolResult
from . import inject
from .context import compress_messages, evict_tool_results
from .policy import DangerPolicy
from .prompts import (
    EXECUTOR_SYSTEM,
    PLANNER_SYSTEM,
    REPLAN_ANCHOR,
    REPLAN_RETRY,
    REPLAN_USER_ANCHOR,
    render_plan_section,
)
from .state import AgentState

logger = get_logger("agent.runtime")

#: The task-level mode switch (ADR 0008 decision 3). Default is build.
PLAN_MODE = "plan"
BUILD_MODE = "build"

# ── settled tool-call statuses (issue #100) ─────────────────────────────────
#
# The three statuses a call can carry once ``tool_node`` has finished dealing
# with it — executed, failed, or skipped. The ``skipped`` bucket now comes from
# the S2 danger hook (#136): ``agent`` settles a rule-hit call on the spot,
# still routes to ``tool``, and this guard is what keeps the tool node from
# dispatching it a second time.
TERMINAL_TOOL_CALL_STATUSES = frozenset({"success", "failed", "skipped"})

# ── dangling tool calls (issue #46) ──────────────────────────────────────────
# The single reason written on both faces of a call that was never executed: the
# record (``error``, and so the persisted StepRecord) and the conversation (the
# ``role: tool`` reply the protocol demands). One text, one source — same shape as
# ``_feed_tool_failure`` (#95).
DANGLING_SWEEP_TEXT = "interrupted before execution; swept at loop entry"

#: Provider leakage seen on a real model: a plan round can come back as bare
#: tool-protocol closing tags with no call and no text. It is not something a
#: human should read, so the re-plan path retries it and never persists it.
_TOOL_MARKUP_ONLY = re.compile(
    r"^\s*(?:</?(?:parameter|invoke|function_call|tool_call)\b[^>]*>\s*)+$",
    re.IGNORECASE,
)


def _is_tool_markup_only(text: str) -> bool:
    """True for provider leakage like ``</parameter></invoke>``."""
    return bool(_TOOL_MARKUP_ONLY.fullmatch(text or ""))


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
        parent_task_id: str = "",
        replan: bool = False,
    ) -> None:
        self.task_id = task_id
        self.parent_task_id = parent_task_id
        #: One-off「重新规划」round (the input bar's button): anchor the plan
        #: prompt to the current task and retry once if the model answers with
        #: text instead of a JSON plan. False for ordinary plan rounds.
        self.replan = replan
        # Issue #60: ``TaskManager.stop()`` only flips the id it was handed, so a
        # subtask has to watch its own id *and* the parent's. Watching the parent
        # alone would break ``stop(subtask_id)``.
        self._stop_watch_ids = [task_id] + ([parent_task_id] if parent_task_id else [])
        self.tm = task_manager
        self.llm = llm
        self.tools: Dict[str, BaseTool] = {t.name: t for t in tools}
        self.tool_schemas = tool_schemas
        # #139: plan mode gets the declared read-only tier (`explore`), not a
        # second hand-written list — the tier vocabulary is unchanged (AC3) and
        # `spawn_subagent` is naturally absent from it. Intersected with what is
        # actually mounted, exactly like a subtask's face.
        from .subagent import EXPLORE_TIER, tier_face_names

        self._explore_names = frozenset(tier_face_names(self.tools.keys(), EXPLORE_TIER))
        self.max_steps = max_steps
        self._te: Any = None  # lazily built ToolExecutor (see tool_executor)
        self._aux = aux_llm  # injected aux client (may be None)
        self._aux_computed = aux_llm is not None
        # P1-A′: discover + merge once per task (task-level cache); mid-task edits
        # to AGENTS.md intentionally do not take effect. Subtask graphs share this
        # __init__, so they receive the same block with no extra code.
        settings = getattr(getattr(self, "tm", None), "settings", None) or get_settings()
        # S2 (#136): the danger rule table is compiled once per task, same snapshot
        # discipline as the injection block below — mid-task edits take effect on
        # the NEXT task. An empty table compiles to zero rules and `check` always
        # returns None, so the agent's decision path is unchanged (#136 AC1).
        self.danger_policy: DangerPolicy = DangerPolicy.from_settings(settings)
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
        made for context summaries. Built at most once per runtime.
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

    def _build_messages(
        self, state: AgentState, system: str, *, include_plan: bool = False
    ) -> List[Dict[str, Any]]:
        """Build the message list sent to the LLM, compressing history first.

        Compression happens exactly here (both modes) so every LLM call sees an
        up-to-date, bounded context. Under the budget the messages are returned
        untouched. The P1-A′ injection block rides on the *system* message and
        is deliberately outside the compression budget.

        ``include_plan`` appends the rendering of ``state["plan"]`` after that
        block, with the same rights: system-side, outside the budget, never
        evicted or compressed. It is re-derived from the live state each call,
        so a re-plan replaces the section instead of adding a second copy. Both
        modes pass it: build mode reads the plan as its brief (#119), plan mode
        revises what is already there instead of planning from scratch.
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
        plan_block = render_plan_section(state.get("plan") or []) if include_plan else ""
        return [{"role": "system", "content": system + self._inject_block + plan_block}, *compressed]

    def _parse_plan(self, content: str) -> Optional[List[Dict[str, Any]]]:
        """Parse a plan-mode reply as a JSON array of steps; ``None`` if it is not one.

        Only an actual JSON array (fenced ```json blocks tolerated) counts as a
        plan. Everything else is the model **talking to the user** — most often
        asking the clarifying question ADR 0008 decision 1 expects it to ask.
        Turning a question into a one-line "plan" (the old line fallback) both
        destroyed the previous plan and swallowed the question.
        """
        raw = (content or "").strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines).strip()
        try:
            data = json.loads(raw)
        except Exception:
            return None
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return None
        steps: List[Dict[str, Any]] = []
        for item in data:
            if isinstance(item, dict):
                desc = item.get("description") or item.get("step") or item.get("text")
            else:
                desc = item
            description = str(desc or "").strip()
            if description:
                steps.append(
                    {
                        "index": len(steps) + 1,
                        "description": description,
                        "status": "pending",
                    }
                )
        return steps or None

    # ── nodes ──
    def agent(self, state: AgentState) -> AgentState:
        """The one LLM node (ADR 0008 decision 2).

        Creates this round's step, then hands to the mode's round helper. A
        round ends when the helper returns without a tool call — ``finish``
        parks the task, i.e. the ball goes back to the human (decision 4/5).
        """
        if self._stopped(state):
            state["_last_action"] = "stop"
            return state
        # The loop's LLM entry — post-resume it is the very first request out the
        # door, so the orphan sweep runs here.
        self._sweep_dangling_tool_calls(state)

        state["step_index"] = state.get("step_index", 0) + 1
        # The step budget is per round, not per task: a conversation may last
        # many rounds, and a cumulative counter would brick it after max_steps
        # calls. ``step_index`` stays cumulative so step records/events keep a
        # unique index across the whole task.
        state["round_steps"] = state.get("round_steps", 0) + 1
        idx = state["step_index"]
        step: Dict[str, Any] = {
            "index": idx,
            "thought": "",
            "tool_calls": [],
            "status": "running",
        }
        state.setdefault("steps", []).append(step)

        mode = state.get("mode") or BUILD_MODE
        thought = f"Step {idx}: planning." if mode == PLAN_MODE else f"Step {idx}: acting."
        step["thought"] = thought
        self._publish("step_start", {"index": idx, "thought": thought})

        if mode == PLAN_MODE:
            return self._plan_round(state, step)
        return self._build_round(state, step)

    def _plan_round(self, state: AgentState, step: Dict[str, Any]) -> AgentState:
        """Plan mode: read-only face; no tool call means the round is over.

        A JSON plan array is the round's product (``state["plan"]`` +
        ``plan_update``). Any other text is the model talking to the user — most
        often the clarifying question ADR 0008 decision 1 explicitly allows —
        and leaves the existing plan untouched.

        The one-off re-plan round (:attr:`replan`) is stricter: its prompt is
        anchored to the current task and a non-plan reply is retried once, so
        the「重新规划」button cannot silently drift into another task or write
        provider protocol leakage into the conversation.
        """
        face = self._tool_face(PLAN_MODE)
        schemas = [t.to_openai_schema() for t in face.values()]
        system = PLANNER_SYSTEM + (REPLAN_ANCHOR if self.replan else "")
        resp = self._complete_plan(state, system, schemas)
        if resp is None:
            return state
        if resp.tool_calls:
            return self._handle_tool_calls(state, step, resp)

        plan = self._parse_plan(resp.content)
        if plan is None and self.replan:
            # The button promises a plan. One stricter retry keeps a malformed
            # or off-plan reply from becoming the round's answer.
            logger.info("re-plan round returned no JSON plan; retrying once")
            retry = self._complete_plan(state, system + REPLAN_RETRY, schemas)
            if retry is None:
                return state
            if retry.tool_calls:
                return self._handle_tool_calls(state, step, retry)
            resp = retry
            plan = self._parse_plan(resp.content)

        if plan:
            state["plan"] = plan
            self._publish("plan_update", {"plan": plan})
            answer = f"计划已更新（{len(plan)} 步）。"
        elif self.replan:
            # The button promises a plan. After the retry, never surface raw
            # model text (reasoning dumps / provider markup) as a turn.
            answer = "未能生成新计划，请再试一次。"
        elif _is_tool_markup_only(resp.content):
            # Never persist provider protocol leakage as a conversation turn.
            answer = "(no content)"
        else:
            # Not a plan: keep the previous plan and surface what the model
            # actually said (e.g. a question) instead of inventing a step.
            answer = (resp.content or "").strip() or "(no content)"
        state["final_answer"] = answer
        assistant_turn: Dict[str, Any] = {"role": "assistant", "content": answer}
        # Issue #45: thinking-mode providers require the trace back on the next
        # turn; the plan round is no exception (a missing trace 400s forever).
        if resp.reasoning_content:
            assistant_turn["reasoning_content"] = resp.reasoning_content
        state.setdefault("messages", []).append(assistant_turn)
        step["thought"] = answer
        self._publish("final_answer", {"answer": answer})
        state["_last_action"] = "final_answer"
        return state

    def _complete_plan(
        self,
        state: AgentState,
        system: str,
        schemas: List[Dict[str, Any]],
    ) -> Optional[Any]:
        """One plan-mode LLM call; ``None`` means the error path already settled."""
        messages = self._build_messages(state, system, include_plan=True)
        if self.replan:
            # Request-only anchor: the last real user turn said "execute"; this
            # round is the「重新规划」action. Never persisted into the task.
            messages.append({"role": "user", "content": REPLAN_USER_ANCHOR})
        try:
            return self.llm.complete(messages, tools=schemas)
        except Exception as exc:
            logger.exception("agent LLM error (plan mode)")
            state["error"] = f"Plan-mode LLM error: {exc}"
            state["final_answer"] = f"[error] {exc}"
            state["_last_action"] = "final_answer"
            return None

    def _build_round(self, state: AgentState, step: Dict[str, Any]) -> AgentState:
        """Build mode: every mounted tool, with the current plan as a brief."""
        try:
            resp = self.llm.complete(
                self._build_messages(state, EXECUTOR_SYSTEM, include_plan=True),
                tools=self.tool_schemas,
            )
        except Exception as exc:
            logger.exception("agent LLM error (build mode)")
            state["error"] = f"Build-mode LLM error: {exc}"
            state["final_answer"] = f"[error] {exc}"
            state["_last_action"] = "final_answer"
            return state

        if resp.tool_calls:
            return self._handle_tool_calls(state, step, resp)

        answer = resp.content or "(no content)"
        state["final_answer"] = answer
        final_turn: Dict[str, Any] = {"role": "assistant", "content": answer}
        if resp.reasoning_content:  # Issue #45, same rule as the tool-call turn
            final_turn["reasoning_content"] = resp.reasoning_content
        state.setdefault("messages", []).append(final_turn)
        step["thought"] = answer
        self._publish("final_answer", {"answer": answer})
        state["_last_action"] = "final_answer"
        return state

    def _handle_tool_calls(
        self,
        state: AgentState,
        step: Dict[str, Any],
        resp: Any,
    ) -> AgentState:
        """Record this round's calls, apply the danger hook, route to ``tool``.

        Shared by both modes on purpose: the S2 refusal (#136) and the
        tool-protocol bookkeeping (#45 / #46) must not fork with the mode.
        """
        tcs: List[Dict[str, Any]] = []
        for tc in resp.tool_calls:
            name = tc.get("name", "")
            args = tc.get("arguments", {}) or {}
            # S2 (#136): the one hook. A rule hit DENIES this call outright —
            # the tool never runs and the model gets {block, reason} back.
            # The args are passed by reference and never written (#136 AC3).
            block_reason = self.danger_policy.check(name, args)
            rec = {
                "id": tc.get("id") or f"call_{state['step_index']}_{len(tcs)}",
                "tool_name": name,
                "input": args,
                "output": None,
                # S2 (#136): a rule hit settles the call right here — the status
                # verb and the reason are the same two fields every other
                # non-executed call carries (``skipped`` + ``error``).
                "status": "skipped" if block_reason is not None else "pending",
                "error": block_reason or "",
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
            step["tool_calls"].append(rec)
            self._publish("tool_call", rec)
            if rec["status"] == "skipped":
                # S2 (#136): a policy refusal is delivered here, at the point of
                # decision, and never handed to the tool node.
                self._publish("tool_result", rec)
                self._feed_blocked(state, rec)

        state["_last_action"] = "tool_call"
        return state

    def _tool_face(self, mode: str) -> Dict[str, BaseTool]:
        """The tools this mode may dispatch (ADR 0008 decision 7).

        Enforced at dispatch, not only in the advertised schemas: a model that
        hallucinates ``write`` while in plan mode gets a refusal from
        :meth:`tool_node`, never a side effect.
        """
        if mode == PLAN_MODE:
            return {n: t for n, t in self.tools.items() if n in self._explore_names}
        return self.tools

    def _sweep_dangling_tool_calls(self, state: AgentState) -> None:
        """Answer every tool call that never got an answer (issue #46).

        A stop / crash mid-round leaves an assistant ``tool_calls`` turn with no
        ``role: tool`` reply and a record stuck on ``pending`` — so the next
        request violates the OpenAI tool-calling protocol, and the persisted task
        shows a call that will never resolve. (The other gap that used to feed
        this hole — a stop while parked on the confirm gate — is gone with the
        gate, #138.)

        Run at the **single** LLM entry (``agent``), not only before the tool
        round: the first request after a resume is the agent's, so a guard that
        only sits before dispatch would still send the orphan. Only ``pending``
        records get reclassified, and a record that already carries a verdict
        keeps its own words onto the message face too — one text, one source
        (#95), otherwise the model is told「interrupted before execution」about a
        call that ran and failed.
        """
        messages = state.get("messages") or []
        answered = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
        orphans: List[str] = []
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            for tc in msg.get("tool_calls") or []:
                cid = tc.get("id")
                if cid and cid not in answered:
                    orphans.append(cid)
                    answered.add(cid)
        if not orphans:
            return

        by_id = {r.get("id"): r for r in self._call_records(state)}
        for cid in orphans:
            rec = by_id.get(cid)
            if rec is not None and rec.get("status") == "pending":
                rec["status"] = "skipped"
                rec["error"] = DANGLING_SWEEP_TEXT
            own = str((rec or {}).get("error") or "")
            self._feed_tool_failure(state, {"id": cid, "error": own or DANGLING_SWEEP_TEXT})
        logger.info("task %s swept %d dangling tool call(s) at loop entry", self.task_id, len(orphans))


    def tool_node(self, state: AgentState) -> AgentState:
        if self._stopped(state):
            state["_last_action"] = "stop"
            return state

        tcs = state.get("_current_tool_calls", []) or []
        step = state["steps"][-1] if state.get("steps") else None
        # #139: dispatch is limited to the mode's face. Plan mode advertises the
        # explore tier, and a hallucinated name outside it is refused here — the
        # face is a structural boundary, not a suggestion in the schema list.
        face = self._tool_face(state.get("mode") or BUILD_MODE)

        for rec in tcs:
            if rec["status"] in TERMINAL_TOOL_CALL_STATUSES:
                # A call that already carries a verdict is left alone (issue #100).
                # In this round the settled records are exactly the ones ``agent``
                # refused via the S2 danger hook (#136): it publishes their result and
                # still routes to this node, so without this guard a blocked call would
                # be looked up and dispatched here. ``rec["status"]`` was written in
                # place, so the check is per ``rec`` and idempotent; retries are a
                # different ledger, closed inside ``ToolExecutor.dispatch`` by
                # ``with_retry``.
                continue

            tool = face.get(rec["tool_name"])
            if tool is None:
                rec["status"] = "failed"
                if rec["tool_name"] in self.tools:
                    rec["error"] = (
                        f"tool not available in plan mode: {rec['tool_name']} "
                        "(planning is read-only)"
                    )
                else:
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
            # register it again), and the path must be a real file — a directory
            # is not a deliverable (ADR 0004), and before #17 a result
            # advertising one registered as a phantom 0-byte product.
            #
            # A subtask's write is registered by the hand-back in
            # ``subagent._exec_one`` under the PARENT id only (#76). Registering
            # here as well would mint a record no ledger can hold — a subtask
            # never has an ``_active_states`` entry — so its whole cost used to
            # be a phantom ``artifact_created`` on the subtask channel plus a
            # redundant KB ingest.
            if (
                tool.registers_artifact
                and result.success
                and isinstance(result.data, dict)
                and result.data.get("path")
                and not state.get("_is_subtask")
            ):
                p = Path(result.data["path"])
                if p.is_file():
                    self.tm.add_artifact(self.task_id, p)

            # #70 裁定 A：spawn 路径把子任务的折叠回传回登进图状态（``state["subtasks"]``
            # → ``Task.subtasks``，拷贝行在 ``_finalize_terminal``，前端 SubtaskList 与
            # REST 字段读的就是它）。生产者随 #56 删入口 B 一起断了，字段自此恒空——
            # 「看着有、其实没有」的骗人面。只认 ``SubTaskResult.to_dict()`` 的回传
            # （带 ``subtask_id``）：参数校验与 #74 档位拒绝的早退 ``data`` 为 None，
            # 一次都没派出，不登。completed 与 failed 同登——派出去又失败的子任务
            # 在面板上是「失败的一条」，不是「暂无子任务」。
            if (
                rec["tool_name"] == "spawn_subagent"
                and isinstance(result.data, dict)
                and "subtask_id" in result.data
            ):
                state.setdefault("subtasks", []).append(dict(result.data))

        state["_last_action"] = "tool_done"
        if step is not None:
            step["status"] = "done"
        return state

    def _feed_tool_reply(self, state: AgentState, rec: Dict[str, Any], payload: Dict[str, Any]) -> None:
        """Append the ``role: tool`` reply for ``rec`` with ``payload`` as its body.

        One envelope for every verdict — the protocol only demands that each
        assistant ``tool_calls`` entry gets exactly one reply (#46); the *body*
        is what differs (a failure's #95, a policy refusal's #136).
        """
        state.setdefault("messages", []).append(
            {
                "role": "tool",
                "tool_call_id": rec["id"],
                "content": json.dumps(payload, ensure_ascii=False, default=str),
            }
        )

    def _feed_tool_failure(self, state: AgentState, rec: Dict[str, Any]) -> None:
        """Answer one un-successful call on the conversation face (issue #95).

        Every un-successful call that is not a policy refusal shares this line
        — unknown tool, a tool outside the plan-mode face, executed and failed,
        and the dangling sweep — and each has just written its reason onto
        ``rec["error"]``, the very field the ``tool_result`` event publishes: the
        model and the human read one text from one source. A tool that failed
        without saying anything still gets a word, because an empty reason reads
        as little as the old bare ``null`` did. The success path never comes
        through here.
        """
        self._feed_tool_reply(state, rec, {"ok": False, "error": rec["error"] or "tool failed"})

    def _feed_blocked(self, state: AgentState, rec: Dict[str, Any]) -> None:
        """Answer a policy-blocked call on the conversation face (#136).

        A different *body* from :meth:`_feed_tool_failure` on purpose: the model
        is told it is not allowed to run this one, in the ``{block, reason}``
        shape ADR 0008 decision 6 asks for, so it re-plans instead of retrying (a
        plain ``{ok: false, error: ...}`` reads like a transient failure). The
        reason is the string already on ``rec["error"]`` — the same text the
        ``tool_result`` event publishes, which the agent always sets on a hit.
        """
        self._feed_tool_reply(state, rec, {"block": True, "reason": rec["error"]})

    def _call_records(self, state: AgentState) -> List[Dict[str, Any]]:
        """Every tool-call record of this run, across all rounds."""
        return [
            tc
            for st in (state.get("steps") or [])
            for tc in (st.get("tool_calls") or [])
        ]

    def finish(self, state: AgentState) -> AgentState:
        """Park the run — there is no "completed" state (ADR 0008 decision 5).

        A round that answered and a human stop land in the same place:
        ``INTERRUPTED`` (停下, 等你说话). The task can always be continued by
        sending another message. Only an error is ``FAILED``.

        The step record is marked ``done`` only when this round actually produced
        an answer; a stop or a spent step budget leaves it ``failed`` (it did not
        complete what it started).
        """
        if state.get("stop_requested"):
            state["status"] = "INTERRUPTED"
        elif state.get("error"):
            state["status"] = "FAILED"
        else:
            state["status"] = "INTERRUPTED"
        if state.get("steps"):
            answered = bool(state.get("final_answer")) and not state.get("error")
            state["steps"][-1]["status"] = "done" if answered else "failed"
        return state
