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
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ...services import plan_file
from ...services.plan_file import extract_plan, plan_hash, write_plan
from ...utils.logging import get_logger
from ..capability_packages import INJECTION_SKILLS
from ..capability_packages import (
    injection_fragments as resolve_injection_fragments,
)
from ..llm.trace import llm_call_scope, redact_structure
from ..runtime_context import (
    reset_current_round,
    set_current_round,
    workspace_root,
)
from ..tools.base import BaseTool, ToolResult
from . import inject
from .context import compress_messages, evict_tool_results
from .host import AgentHost
from .policy import DangerPolicy
from .prompts import EXECUTOR_SYSTEM, planner_system
from .state import AgentState

if TYPE_CHECKING:  # capture seam; the store lives in services and must not be
    # imported by the graph's hot path for typing only.
    from ...services.changes import ChangesRecorder

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
#: human should read, so the round never persists it as an answer.
_TOOL_MARKUP_ONLY = re.compile(
    r"^\s*(?:</?(?:parameter|invoke|function_call|tool_call)\b[^>]*>\s*)+$",
    re.IGNORECASE,
)


def _is_tool_markup_only(text: str) -> bool:
    """True for provider leakage like ``</parameter></invoke>``."""
    return bool(_TOOL_MARKUP_ONLY.fullmatch(text or ""))


def _plan_note_text(version: int) -> str:
    return plan_file.plan_note_text(version)


def _is_plan_note(message: Dict[str, Any]) -> bool:
    return message.get("role") == "assistant" and plan_file.is_plan_note(
        message.get("content")
    )


def _drop_plan_notes(messages: List[Dict[str, Any]]) -> None:
    messages[:] = [m for m in messages if not _is_plan_note(m)]


def _ensure_plan_note(messages: List[Dict[str, Any]], version: int) -> None:
    """Keep exactly one gray note, carrying the current version."""
    expected = _plan_note_text(version)
    notes = [m for m in messages if _is_plan_note(m)]
    if len(notes) == 1 and str(notes[0].get("content") or "") == expected:
        return
    _drop_plan_notes(messages)
    messages.append({"role": "assistant", "content": expected})


def _tool_reply_message(rec: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    """The one ``role: tool`` envelope every verdict is delivered in (#46).

    Only the body differs — the tool output on success, ``{ok: false, error}``
    (#95), or ``{block: true, reason}`` (#136); the envelope and its JSON
    encoding stay byte-identical across call sites.
    """
    return {
        "role": "tool",
        "tool_call_id": rec["id"],
        "content": json.dumps(payload, ensure_ascii=False, default=str),
    }


class AgentRuntime:
    """Per-task runtime that backs the LangGraph nodes."""

    def __init__(
        self,
        task_id: str,
        task_manager: AgentHost,
        llm: Any,
        tools: List[BaseTool],
        tool_schemas: List[Dict[str, Any]],
        max_steps: int = 15,
        parent_task_id: str = "",
        model_profile: str = "",
        reasoning_scope: str = "",
        packages: Optional[frozenset[str]] = None,
        injection_fragments: Optional[frozenset[str]] = None,
        trace_source: str = "main",
        trace_owner: Optional[str] = None,
        changes: Optional["ChangesRecorder"] = None,
    ) -> None:
        self.task_id = task_id
        self.parent_task_id = parent_task_id
        self.model_profile = model_profile
        #: S7: trace attribution for every model call this runtime makes.
        #: A subagent reports under the parent task with source="subagent".
        self.trace_source = trace_source
        self.trace_owner = trace_owner or task_id
        #: D-39: "<provider_id>:<protocol>" of the model this round runs with.
        #: Assistant reasoning is only replayed to the same scope; the value
        #: tags message provenance and never leaves the graph.
        self.reasoning_scope = reasoning_scope
        # Issue #60: ``TaskManager.stop()`` only flips the id it was handed, so a
        # subtask has to watch its own id *and* the parent's. Watching the parent
        # alone would break ``stop(subtask_id)``.
        self._stop_watch_ids = [task_id] + ([parent_task_id] if parent_task_id else [])
        self.tm = task_manager
        self.llm = llm
        #: #343: captures successful write/edit results per round. Optional so
        #: every existing AgentHost test double keeps working unchanged.
        self.changes = changes
        self.tools: Dict[str, BaseTool] = {t.name: t for t in tools}
        self.tool_schemas = tool_schemas
        # #139: plan mode gets the declared read-only tier (`explore`), not a
        # second hand-written list — the tier vocabulary is unchanged (AC3) and
        # `spawn_subagent` is naturally absent from it. Intersected with what is
        # actually mounted, exactly like a subtask's face.
        from .subagent import EXPLORE_TIER, tier_face_names

        self._explore_names = frozenset(tier_face_names(self.tools.keys(), EXPLORE_TIER))
        self.max_steps = max_steps
        # P1-A′: discover + merge once per task (task-level cache); mid-task edits
        # to AGENTS.md intentionally do not take effect. Subtask graphs share this
        # __init__, so they receive the same block with no extra code.
        settings = self.tm.settings
        # S2 (#136): the danger rule table is compiled once per task, same snapshot
        # discipline as the injection block below — mid-task edits take effect on
        # the NEXT task. An empty table compiles to zero rules and `check` always
        # returns None, so the agent's decision path is unchanged (#136 AC1).
        self.danger_policy: DangerPolicy = DangerPolicy.from_settings(settings)
        # D-56: the skills package owns the skills-catalogue injection fragment.
        # The manager passes its resolved set; direct runtime constructions may
        # pass only `packages` (fragments are then derived from the same manifest)
        # or neither, which keeps the pre-#67 full-injection behaviour.
        if injection_fragments is None and packages is not None:
            injection_fragments = resolve_injection_fragments(packages)
        include_skills = (
            injection_fragments is None or INJECTION_SKILLS in injection_fragments
        )
        # Same rule for the prompt text: a disabled subagent package (or the
        # legacy subagent_enabled switch) must not be advertised in the planner
        # context (neither tool call nor delegation).
        self._planner_system: str = planner_system(
            packages,
            subagent_enabled=settings.subagent_enabled,
        )
        self._inject_block: str = (
            inject.build_inject_block(settings, include_skills=include_skills)
            if settings.context_inject_enabled
            else ""
        )

    # ── helpers ──
    @staticmethod
    def _revision(state: AgentState) -> Dict[str, Any]:
        """The explicit revision S8 stamps on every runtime event."""
        return {
            "round": int(state.get("round_no", 1) or 1),
            "step": int(state.get("step_index", 0) or 0),
            "plan_version": int(state.get("plan_version", 0) or 0),
        }

    def _publish(
        self, event_type: str, data: Dict[str, Any], state: AgentState
    ) -> None:
        """Publish one runtime event stamped with its explicit revision.

        S8 (#334): clients decide whether an event is newer from
        ``(round, step, plan_version)``; a step-array length is not a sequence.
        State is required so no call site can forget the stamp.
        """
        self.tm.event_bus.publish(
            self.task_id, event_type, {**data, **self._revision(state)}
        )

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
        if any(self.tm.is_stop_flagged(watched) for watched in self._stop_watch_ids):
            state["stop_requested"] = True
            return True
        return False

    def _build_messages(self, state: AgentState, system: str) -> List[Dict[str, Any]]:
        """Build the message list sent to the LLM, compressing history first.

        Compression happens exactly here (both modes) so every LLM call sees a
        bounded context; eviction and compression may rewrite the history even
        when nothing is over budget. The P1-A′ injection block rides on the
        *system* message and is deliberately outside the compression budget.

        The plan file is checked only at the two moments G-34 allows: the first
        call of a round (``round_steps == 1``) and immediately after compression.
        A mid-round edit therefore takes effect on the next round, never on the
        next call. The plan enters the conversation as one message at the end
        (C6) and is outside the compression input, so compression cannot drop
        it; when the version changes, exactly one gray note records it.
        """
        settings = self.tm.settings
        # The previous call's injected plan is stale input for eviction,
        # compression and the summarizer; the current file is appended below.
        messages = plan_file.strip_plan_messages(state.get("messages", []) or [])
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
                            state,
                        )
            except Exception:  # pragma: no cover - defensive
                logger.warning("tool-result eviction failed; continuing without it", exc_info=True)
        # Compression summaries use the main model (ADR 0009: no separate
        # summarisation model).
        with self._llm_scope("compaction", state):
            compressed, meta = compress_messages(
                messages,
                budget=settings.context_token_budget,
                keep_recent=settings.context_keep_recent,
                max_messages=settings.context_max_messages,
                strategy=settings.context_compress_strategy,
                llm=self.llm
                if settings.context_compress_strategy == "summarize"
                else None,
                summary_max_tokens=settings.context_summary_max_tokens,
                trace_ref=trace_ref,
                head_chars=settings.context_evict_head_chars,
                tail_chars=settings.context_evict_tail_chars,
                redact=self._redact,
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
                        "model": getattr(self.llm, "model", ""),
                        "model_profile": self.model_profile,
                    },
                    state,
                )
            except Exception:  # pragma: no cover - event is optional
                logger.warning("failed to publish context_compressed event", exc_info=True)
        # G-34: the file is checked only when a round starts (the manager has
        # already seeded state from it) and after compression. Every other call
        # sends the version the round started with.
        refresh_plan = state.get("round_steps", 0) <= 1 or meta["compressed"]
        if refresh_plan:
            workspace = workspace_root(settings)
            try:
                plan_text = plan_file.read_plan(workspace, self.task_id)
            except plan_file.PlanIOError as exc:
                # A transient lock (Windows AV scanner, an editor holding the
                # file) must not fail the whole round: keep the plan this round
                # started with and warn. A PERSISTENT failure still surfaces
                # where the user can act on it — GET /plan answers 409 and the
                # frontend blocks saving over a plan it could not read (#279
                # AC3 end-to-end guard, unchanged by this fallback) — and a
                # decode error (corrupt content) still fails the round via
                # PlanError, so corruption is never silently ignored.
                logger.warning(
                    "plan refresh failed for %s (%s); continuing with the "
                    "round's current plan",
                    self.task_id,
                    exc,
                )
                plan_text = str(state.get("plan_text", ""))
            else:
                digest = plan_file.plan_hash(plan_text)
                if digest != state.get("plan_hash", ""):
                    state["plan_hash"] = digest
                    if plan_text.strip():
                        state["plan_version"] = int(state.get("plan_version", 0)) + 1
                state["plan_text"] = plan_text
                if plan_text.strip():
                    _ensure_plan_note(compressed, int(state.get("plan_version", 0)))
                else:
                    _drop_plan_notes(compressed)
        else:
            plan_text = str(state.get("plan_text", ""))
        plan_file.sync_plan_message(compressed, plan_text)
        state["messages"] = compressed
        history = self._request_history(compressed)
        return [{"role": "system", "content": system + self._inject_block}, *history]

    def _redact(self, text: str) -> str:
        """Strip known keys before an error reaches state / SSE / trace."""
        return self.tm.redact(text)

    def _llm_scope(self, source: str, state: AgentState) -> Any:
        """Attribution for one model call (S7): owner, source, round, step."""
        revision = self._revision(state)
        return llm_call_scope(
            owner_task_id=self.trace_owner,
            source=source,
            round_no=int(revision["round"]),
            step=int(revision["step"]),
            sink=self.tm.trace_sink(self.trace_owner),
            # S8: the summary is a live event like any other; it carries the
            # same revision fields as every other runtime event.
            publish_summary=lambda payload: self.tm.publish_llm_call(
                self.trace_owner, {**payload, **revision}
            ),
            redact=self._redact,
        )

    def _reasoning_for_display(self, text: str) -> str:
        """D-38: redacted + bounded reasoning copy for SSE / persistence.

        The host owns the single truncation seam (TaskManager.redact_reasoning).
        """
        if not text:
            return ""
        return self.tm.redact_reasoning(text)

    def _stamp_reasoning(self, turn: Dict[str, Any], reasoning: str) -> None:
        """Attach raw reasoning for protocol replay + its D-39 provenance.

        The `_reasoning_from` marker is internal to `state.messages`: the
        request builder strips it, and SSE / trace / StepRecord only ever see
        the redacted display copy. Messages saved before this marker existed
        (upgraded checkpoints) carry no provenance and are sent as-is; a
        cross-provider switch on such a checkpoint is a documented boundary
        (no one-off migration).
        """
        if not reasoning:
            return
        turn["reasoning_content"] = reasoning
        turn["_reasoning_from"] = self.reasoning_scope

    def _request_history(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Copies safe for the provider: provenance removed, foreign reasoning dropped.

        D-39: a turn's reasoning may only be replayed to the provider+protocol
        that produced it (DeepSeek thinking mode 400s without its own trace;
        Anthropic uses a different block shape). Same scope keeps
        `reasoning_content`; any other scope drops it from the request copy
        only — `state.messages` and the persisted display copy are untouched.
        """
        history: List[Dict[str, Any]] = []
        for message in messages:
            if "_reasoning_from" not in message:
                history.append(message)
                continue
            copy = dict(message)
            source = copy.pop("_reasoning_from", "")
            if source != self.reasoning_scope:
                copy.pop("reasoning_content", None)
            history.append(copy)
        return history

    def _final_answer_payload(
        self, answer: str, reasoning: str
    ) -> Dict[str, Any]:
        """D-38: final_answer keeps its old shape unless there is reasoning.

        The optional field is the redacted / bounded display copy; a
        non-thinking model emits exactly the pre-#267 payload, so the frontend
        never has to render an empty reasoning block.
        """
        payload: Dict[str, Any] = {"answer": answer}
        display = self._reasoning_for_display(reasoning)
        if display:
            payload["reasoning"] = display
        return payload

    def _public_record(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        """A copy safe for SSE / trace: the args are redacted, dispatch is not."""
        return {**rec, "input": self._redact_value(rec.get("input"))}

    def _redact_value(self, value: Any) -> Any:
        """Recursively redact strings inside a tool result payload."""
        return redact_structure(value, self._redact)

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
        # unique index across the whole task; ``round_no`` is what lets the UI
        # attribute a step to the answer that produced it (#265).
        state["round_steps"] = state.get("round_steps", 0) + 1
        idx = state["step_index"]
        round_no = int(state.get("round_no", 1) or 1)
        step: Dict[str, Any] = {
            "index": idx,
            "round": round_no,
            "thought": "",
            # D-38: raw reasoning lands here; _finalize_terminal persists the
            # redacted / truncated copy via TaskManager._redact_step.
            "reasoning": "",
            "tool_calls": [],
            "status": "running",
        }
        state.setdefault("steps", []).append(step)

        mode = state.get("mode") or BUILD_MODE
        thought = f"Step {idx}: planning." if mode == PLAN_MODE else f"Step {idx}: acting."
        step["thought"] = thought
        self._publish(
            "step_start",
            {
                "index": idx,
                "round": round_no,
                "thought": thought,
                "model": getattr(self.llm, "model", ""),
                "model_profile": self.model_profile,
            },
            state,
        )

        system = self._planner_system if mode == PLAN_MODE else EXECUTOR_SYSTEM
        schemas = (
            [t.to_openai_schema() for t in self._tool_face(PLAN_MODE).values()]
            if mode == PLAN_MODE
            else self.tool_schemas
        )
        resp = self._complete_round(state, mode, system, schemas)
        if resp is None:
            return state
        if resp.tool_calls:
            return self._handle_tool_calls(state, step, resp)
        if mode == PLAN_MODE:
            answer, updated = self._plan_answer(state, resp.content)
        else:
            # Strip before the ledger comparison: `_conversation_from` strips the
            # persisted message, so a trailing newline used to make the answer
            # never match `final_answer` and silently drop it from the ledger.
            answer = (resp.content or "").strip() or "(no content)"
            updated = False
        return self._settle_final_answer(
            state,
            step,
            answer,
            resp.reasoning_content,
            drop_previous_notes=updated,
        )

    def _plan_answer(
        self, state: AgentState, content: Optional[str]
    ) -> tuple[str, bool]:
        """Resolve a tool-free plan-mode reply into ``(answer, plan_updated)``.

        A reply wrapped in the agreement markers is stored as the plan file and
        bumped to the next version. Any other text is the model talking to the
        user (e.g. a clarifying question, ADR 0008 decision 1) and leaves the
        file untouched.
        """
        text = content or ""
        plan_text = extract_plan(text)
        if plan_text:
            workspace = workspace_root(self.tm.settings)
            try:
                write_plan(workspace, self.task_id, plan_text)
            except ValueError as exc:
                return f"[error] {exc}", False
            state["plan_text"] = plan_text
            state["plan_hash"] = plan_hash(plan_text)
            state["plan_version"] = int(state.get("plan_version", 0)) + 1
            return _plan_note_text(state["plan_version"]), True
        if _is_tool_markup_only(text):
            # Never persist provider protocol leakage as a conversation turn.
            return "(no content)", False
        return text.strip() or "(no content)", False

    def _complete_round(
        self,
        state: AgentState,
        mode: str,
        system: str,
        schemas: List[Dict[str, Any]],
    ) -> Optional[Any]:
        """One mode's LLM call; ``None`` means the error path already settled.

        Both modes share message building, trace attribution and the error
        settlement; only the system prompt and the offered schemas differ.
        """
        messages = self._build_messages(state, system)
        try:
            with self._llm_scope(self.trace_source, state):
                return self.llm.complete(messages, tools=schemas)
        except Exception as exc:
            message = self._redact(str(exc))
            logger.error("agent LLM error (%s mode): %s", mode, message)
            label = "Plan" if mode == PLAN_MODE else "Build"
            state["error"] = f"{label}-mode LLM error: {message}"
            state["final_answer"] = f"[error] {message}"
            state["_last_action"] = "final_answer"
            return None

    def _settle_final_answer(
        self,
        state: AgentState,
        step: Dict[str, Any],
        answer: str,
        reasoning: Optional[str],
        *,
        drop_previous_notes: bool = False,
    ) -> AgentState:
        """Close a round with the model's answer: state, turn, step and event."""
        answer = self._redact(answer)
        state["final_answer"] = answer
        assistant_turn: Dict[str, Any] = {"role": "assistant", "content": answer}
        # Issue #45: thinking-mode providers require the trace back on the next
        # turn; _stamp_reasoning also tags its D-39 provenance.
        self._stamp_reasoning(assistant_turn, reasoning or "")
        messages = state.setdefault("messages", [])
        if drop_previous_notes:
            # One gray "plan updated" line: replace the previous one.
            _drop_plan_notes(messages)
        messages.append(assistant_turn)
        step["thought"] = answer
        step["reasoning"] = reasoning or ""
        self._publish(
            "final_answer",
            self._final_answer_payload(answer, reasoning or ""),
            state,
        )
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
            if block_reason is not None:
                # Never dispatched: the args can be redacted here and persist.
                rec["input"] = self._redact_value(args)
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
        self._stamp_reasoning(assistant_turn, resp.reasoning_content)
        step["reasoning"] = resp.reasoning_content or ""
        state.setdefault("messages", []).append(assistant_turn)
        for rec in tcs:
            step["tool_calls"].append(rec)
            self._publish("tool_call", self._public_record(rec), state)
            if rec["status"] == "skipped":
                # S2 (#136): a policy refusal is delivered here, at the point of
                # decision, and never handed to the tool node.
                self._publish("tool_result", rec, state)
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

        The reply goes **in place** — after the assistant turn that asked and
        its already-answered replies, before any newer user turn (#255). A
        continuation appends the new user message before this sweep runs, so the
        old append-to-the-tail shape produced ``assistant → user → tool``.
        """
        messages = state.get("messages") or []
        answered = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
        by_id = {r.get("id"): r for r in self._call_records(state)}
        orphans: List[str] = []
        # Backwards: inserting at i+1 only shifts indexes we already visited.
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            if msg.get("role") != "assistant":
                continue
            missing = [
                tc.get("id")
                for tc in msg.get("tool_calls") or []
                if tc.get("id") and tc.get("id") not in answered
            ]
            if not missing:
                continue
            insert_at = i + 1
            while insert_at < len(messages) and messages[insert_at].get("role") == "tool":
                insert_at += 1
            replies: List[Dict[str, Any]] = []
            for cid in missing:
                answered.add(cid)
                orphans.append(cid)
                rec = by_id.get(cid)
                if rec is not None and rec.get("status") == "pending":
                    rec["status"] = "skipped"
                    rec["error"] = DANGLING_SWEEP_TEXT
                own = str((rec or {}).get("error") or "")
                replies.append(
                    self._tool_failure_message(
                        {"id": cid, "error": own or DANGLING_SWEEP_TEXT}
                    )
                )
            messages[insert_at:insert_at] = replies
        if not orphans:
            return
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
                # place, so the check is per ``rec`` and idempotent.
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
                rec["input"] = self._redact_value(rec.get("input"))
                self._publish("tool_result", rec, state)
                self._feed_tool_failure(state, rec)
                continue

            try:
                # #343: snapshot the before side only for a real write/edit
                # dispatch; blocked and unknown tools never reach this line.
                pending = (
                    self.changes.begin(
                        int(state.get("round_no", 1) or 1),
                        rec["tool_name"],
                        rec.get("input") or {},
                        str(rec.get("id") or ""),
                    )
                    if self.changes is not None
                    else None
                )
                # #343 review fix: a tool that spawns a sub-agent must hand the
                # parent round across the pool (spec §5.1 attribution) — the
                # subtask's own state round is always 1.
                round_token = set_current_round(
                    int(state.get("round_no", 1) or 1)
                )
                try:
                    result: ToolResult = tool.run(**rec["input"])
                finally:
                    reset_current_round(round_token)
            except Exception as exc:  # final safety net
                message = self._redact(str(exc))
                logger.error("tool %s crashed: %s", rec["tool_name"], message)
                result = ToolResult(success=False, error=message)
                pending = None

            # Capture only a successful write/edit. A failed call leaves no
            # record at all (the store is never told a failed touch happened).
            if pending is not None and result.success and self.changes is not None:
                self.changes.commit(pending)

            # Dispatch used the raw args; the record (SSE / trace / steps) keeps
            # only the redacted copy.
            rec["input"] = self._redact_value(rec.get("input"))
            rec["status"] = "success" if result.success else "failed"
            rec["output"] = self._redact_value(result.data)
            rec["error"] = self._redact(result.error or "")
            self._publish("tool_result", rec, state)

            # Feed the tool result back into the conversation: the payload
            # verbatim on success, the reason on failure (#95 — a bare ``null``
            # told the model nothing, so every next turn guessed at what went
            # wrong).
            if result.success:
                state.setdefault("messages", []).append(
                    _tool_reply_message(rec, rec["output"])
                )
            else:
                self._feed_tool_failure(state, rec)

            # #70 裁定 A：spawn 路径把子任务的折叠回传回登进图状态（``state["subtasks"]``
            # → ``Task.subtasks``，拷贝行在 ``_finalize_terminal``，前端 SubtaskList 与
            # REST 字段读的就是它）。#70 后由 spawn 路径重新登记；本块只登真正派出的
            # 结果。只认 ``SubTaskResult.to_dict()`` 的回传（带 ``subtask_id``）：参数
            # 校验与 #74 档位拒绝的早退 ``data`` 为 None，一次都没派出，不登。
            # completed 与 failed 同登——派出去又失败的子任务在面板上是「失败的一条」，
            # 不是「暂无子任务」。
            if (
                rec["tool_name"] == "spawn_subagent"
                and isinstance(rec["output"], dict)
                and "subtask_id" in rec["output"]
            ):
                state.setdefault("subtasks", []).append(dict(rec["output"]))

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
        state.setdefault("messages", []).append(_tool_reply_message(rec, payload))

    def _tool_failure_message(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        """The conversation-face reply for one un-successful call (one text, one source)."""
        return _tool_reply_message(
            rec, {"ok": False, "error": rec["error"] or "tool failed"}
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
        state.setdefault("messages", []).append(self._tool_failure_message(rec))

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

        A step this round left ``running`` is settled here: ``done`` when the
        round produced an answer, ``failed`` on a stop / spent budget / error.
        A step that already carries a verdict — ``tool_node`` finished it in
        this round, or it belongs to an earlier round (a stop at the loop entry
        skips the ``agent`` node's step creation) — is never rewritten (#255).
        """
        if state.get("stop_requested"):
            state["status"] = "INTERRUPTED"
        elif state.get("error"):
            state["status"] = "FAILED"
        else:
            state["status"] = "INTERRUPTED"
        steps = state.get("steps") or []
        if steps:
            last = steps[-1]
            same_round = last.get("round") == int(state.get("round_no", 1) or 1)
            if same_round and last.get("status") == "running":
                answered = bool(state.get("final_answer")) and not state.get("error")
                last["status"] = "done" if answered else "failed"
        return state
