"""Sub-agent collaboration (P1 item 2; tool face pinned by issue #56).

The main agent can delegate work to **isolated** sub-agents. Isolation means:

* every subtask runs with its **own** :class:`AgentState` (independent
  ``messages`` / ``plan`` / ``steps`` / ``artifacts``);
* every subtask runs over a **declared capability tier** (:data:`TOOL_TIERS`),
  not over "whatever the parent had" — a subtask has no ``human_confirm`` node,
  so any tool that would need a human has to be structurally absent rather than
  present-and-refused;
* every subtask runs with its **own** :class:`AgentRuntime` using a
  **simplified graph** (``mode="subtask"`` — no risk / confirm nodes, so
  subtasks can never recurse or block on confirmations that cannot route back
  to the parent);
* subtask internal events are published on the **subtask's own EventBus
  channel** (``<parent>:sub:<hex>``), never on the parent channel.

One entry point: :meth:`SubAgentExecutor.run_subtask`, reached through the
``spawn_subagent`` tool. Issue #56 removed the second entry ("entry B", the
keyword-triggered ``subagent_split`` graph node): a subtask that fired because
the user's phrasing happened to contain "报告" was a side effect nobody approved.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ...config import Settings
from ...services.snapshots import get_current_task_id, set_current_task_id
from ...utils.logging import get_logger
from .state import AgentState

logger = get_logger("agent.subagent")

# ── capability tiers (issue #56) ─────────────────────────────────────────────
#
# A tier is a *name set*, and the face a subtask actually gets is that set
# intersected with the tools really mounted (``git_enabled=false`` therefore
# drops the git names from the tier with no code change). Everything outside
# the tiers is unreachable by construction: ``code_exec``, ``http_request``,
# the git write operations, MCP / OpenAPI / plugin tools, the legacy
# ``file_io`` and ``spawn_subagent`` itself.
#
# Nothing listed here may carry ``requires_confirm`` / ``needs_per_call_confirm``
# — the subtask graph has no confirm gate, so a gated member would either hang
# on a confirmation that can never arrive or, worse, run unconfirmed. The
# member list is held to that by ``test_qa_p1_subagent`` rather than by this
# comment.
EXPLORE_TIER = "explore"
EXECUTE_TIER = "execute"

#: Tier -> tool names. ``execute`` is ``explore`` plus the sandbox writes, and
#: derives from it so the two can never drift apart.
_EXPLORE_MEMBERS = [
    "read",
    "glob",
    "grep",
    "kb_query",
    "memory_search",
    "load_skill",
    "web_search",
    "git_status",
    "git_diff",
    "git_log",
    "git_branch",
]

TOOL_TIERS: Dict[str, List[str]] = {
    EXPLORE_TIER: _EXPLORE_MEMBERS,
    EXECUTE_TIER: [*_EXPLORE_MEMBERS, "write", "edit"],
}

#: What a subtask gets when it does not ask for a tier. Keeps the shipped
#: "subtask writes its report to disk" behaviour working.
DEFAULT_TIER = EXECUTE_TIER


class TierError(ValueError):
    """Unknown tier, or a ``tools`` narrowing that reaches outside the tier."""


def tier_names(tier: str) -> List[str]:
    """The declared members of ``tier``; :class:`TierError` on a bad name."""
    try:
        return TOOL_TIERS[tier]
    except KeyError:
        raise TierError(
            f"unknown tier {tier!r} (expected one of {sorted(TOOL_TIERS)})"
        ) from None


def tier_face_names(tool_names: Any, tier: str) -> List[str]:
    """Tier members ∩ actually-loaded tool names, sorted.

    Module-level on purpose: the run manifest's capability header lists the
    same face (issue #58 — «这次子代理能干什么»), so execution and
    observability share one implementation.
    """
    loaded = set(tool_names)
    return sorted(name for name in tier_names(tier) if name in loaded)


def resolve_tool_face(
    tools: List[Any],
    tier: str = DEFAULT_TIER,
    subset: Optional[List[str]] = None,
) -> List[Any]:
    """The tool instances one subtask may use.

    ``tier`` selects the declared capability tier; ``subset`` (the
    ``spawn_subagent`` ``tools`` argument) may only **narrow** it — a name
    outside the tier is refused with a reason instead of being silently
    dropped, because a silent drop would let the model believe it had handed
    the subtask a capability it never got.
    """
    members = set(tier_names(tier))
    if subset is not None:
        outside = sorted(set(subset) - members)
        if outside:
            raise TierError(
                f"tier {tier!r} does not include {outside}; the tools argument "
                f"can only narrow, never widen. Members: {sorted(members)}"
            )
        members &= set(subset)
    return [t for t in tools if t.name in members]


def subtask_owner(subtask_id: str) -> str:
    """The manifest owner label for one subtask's rounds/events.

    Lives here (not in ``run_manifest``) so the format has one home and the
    services layer imports it downstream — ``subagent`` cannot import back.
    """
    return f"subtask:{subtask_id}"


@dataclass
class SubTaskSpec:
    """A single subtask to be executed in isolation."""

    subtask_id: str
    name: str
    instruction: str
    parent_task_id: str = ""
    tier: str = DEFAULT_TIER
    #: Extra narrowing inside ``tier`` (issue #56: narrow-only, validated there).
    tools: Optional[List[str]] = None


@dataclass
class SubTaskResult:
    """Folded result of a subtask (what the parent context actually sees)."""

    subtask_id: str
    name: str
    status: str  # completed | failed
    summary: str = ""
    artifacts: List[str] = field(default_factory=list)
    error: str = ""
    tool_calls_executed: int = 0
    tool_face: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Return a dict compatible with the :class:`SubTask` schema."""
        return {
            "subtask_id": self.subtask_id,
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
            "artifacts": list(self.artifacts),
            "error": self.error or None,
        }


class SubAgentExecutor:
    """Runs isolated subtasks on a bounded thread pool."""

    def __init__(self, task_manager: Any, settings: Settings) -> None:
        self.tm = task_manager
        self.settings = settings
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, int(settings.subagent_max_concurrency))
        )

    # ── public entry points ──
    def run_subtask(self, spec: SubTaskSpec) -> SubTaskResult:
        """Run one subtask on the pool, bounded by ``subagent_timeout_sec``.

        The pool is what gives the wall-clock bound (a hung subtask must not
        wedge the parent's worker thread) and what makes
        ``subagent_max_concurrency`` mean anything; ``_exec_one`` re-seats the
        parent task id on the pool thread so its writes still land on the
        parent's rollback ledger (Issue #33 拍板 5).
        """
        future = self._pool.submit(self._exec_one, spec)
        try:
            return future.result(timeout=self.settings.subagent_timeout_sec)
        except Exception as exc:  # timeout / worker crash
            future.cancel()
            logger.warning("subtask %s aborted: %s", spec.subtask_id, exc)
            return SubTaskResult(
                subtask_id=spec.subtask_id,
                name=spec.name,
                status="failed",
                error=f"subtask timeout or worker error: {exc}",
            )

    def check_face(self, spec: SubTaskSpec) -> List[str]:
        """The tool names ``spec`` would get, without running anything.

        Lets the ``spawn_subagent`` tool reject a widening request (and hand the
        model the reason) before an LLM round or a tool call exists.
        """
        return sorted(t.name for t in self._subtask_tools(spec))

    # ── internals ──
    def _subtask_tools(self, spec: SubTaskSpec) -> List[Any]:
        """The declared tier of the parent face (see :func:`resolve_tool_face`)."""
        return resolve_tool_face(self.tm._tools, spec.tier, spec.tools)

    def _exec_one(self, spec: SubTaskSpec) -> SubTaskResult:
        """Execute one subtask in its own runtime + state + simplified graph.

        Internal events are published on the subtask's own EventBus channel
        (``spec.subtask_id``) — they never reach the parent channel.
        """
        # P1-B: the pool thread starts with a *fresh* context, so a subtask
        # submitted through the pool must re-seat the parent task id or its file
        # writes would miss the parent's rollback ledger (Issue #33 拍板 5:
        # 子代理写归主任务账本). The spawn_subagent path already inherits the id
        # on the parent's worker thread and passes an empty parent id.
        if spec.parent_task_id:
            set_current_task_id(spec.parent_task_id)
        try:
            state: AgentState = {
                "task_id": spec.subtask_id,
                "messages": [{"role": "user", "content": spec.instruction}],
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
                "_is_subtask": True,
            }
            tools = self._subtask_tools(spec)
            face = [t.name for t in tools]
            schemas = [t.to_openai_schema() for t in tools]
            # Lazy imports: `nodes`/`graph` are part of a pre-existing import
            # cycle (graph -> nodes -> tools -> subagent_tool -> subagent), so
            # module-level imports here would dead-lock when ``backend.main``
            # is imported first (the uvicorn entry point).
            from .nodes import AgentRuntime

            # #58: mirror this subtask's rounds and tool events into the
            # parent's run manifest (owner-tagged), and record the face it
            # actually got (#56). The spawn path carries an empty
            # ``spec.parent_task_id`` (it inherits the ledger id from the worker
            # thread's contextvar instead), hence the fallback — the same parent
            # the rollback ledger uses.
            manifest = getattr(self.tm, "_manifest", None)
            manifest_parent = spec.parent_task_id or get_current_task_id() or spec.subtask_id
            if manifest is not None:
                manifest.attach_subtask(
                    self.tm.event_bus,
                    manifest_parent,
                    spec.subtask_id,
                    tier=spec.tier,
                    face=face,
                )

            try:
                runtime = AgentRuntime(
                    task_id=spec.subtask_id,
                    task_manager=self.tm,
                    # #58: rounds land in the parent's manifest file, attributed
                    # to this subtask (shared wrap shape, subtask owner label).
                    llm=self.tm._wrap_llm_for_manifest(
                        manifest_parent, self.tm._llm, "main", subtask_owner(spec.subtask_id)
                    ),
                    tools=tools,
                    tool_schemas=schemas,
                    max_steps=self.settings.max_steps,
                    aux_llm=self.tm._wrap_llm_for_manifest(
                        manifest_parent,
                        getattr(self.tm, "_aux_llm", None),
                        "aux",
                        subtask_owner(spec.subtask_id),
                    ),
                    confirm_enabled=False,  # subtask internal: no human confirm
                    parent_task_id=spec.parent_task_id,  # Issue #60: obey the parent's stop
                )
                from .graph import build_graph

                graph = build_graph(runtime, mode="subtask")
                # The subtask topology runs 4 nodes per cycle; lift the default
                # recursion limit (25) so deep subtasks honor settings.max_steps.
                #
                # No checkpointer here, and deliberately no ``durability=`` either
                # (langgraph 1.x): subtasks are never resumed — they fold into the
                # parent's ``subtasks`` summary — and 1.x warns that durability has
                # no effect when no checkpointer is present.
                final = graph.invoke(
                    state,
                    {"recursion_limit": self.settings.max_steps * 4 + 10},
                )
            finally:
                # #58: the subtask's channel detaches here; its rounds and
                # tool events stay in the parent's file with their owner tags.
                if manifest is not None:
                    manifest.detach_subtask(spec.subtask_id)

            summary = final.get("final_answer", "") or "(no final answer)"
            # Collect artifact paths produced by subtask tool calls.
            paths: List[str] = []
            for s in final.get("steps", []) or []:
                for tc in s.get("tool_calls", []) or []:
                    out = tc.get("output")
                    if isinstance(out, dict) and out.get("path"):
                        paths.append(str(out["path"]))
            # De-duplicate, keep order, drop missing files.
            unique: List[str] = []
            for p in paths:
                if p not in unique:
                    unique.append(p)
            tool_count = sum(
                len(s.get("tool_calls", []) or []) for s in final.get("steps", []) or []
            )

            # Register subtask-produced files as artifacts of the parent task so
            # they remain downloadable from the main task view.
            if spec.parent_task_id:
                for p in unique:
                    try:
                        from pathlib import Path

                        path_obj = Path(p)
                        if path_obj.exists():
                            self.tm.add_artifact(spec.parent_task_id, path_obj)
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.warning("failed to register subtask artifact %s: %s", p, exc)

            # Propagate the subtask graph's terminal status: a subtask whose
            # graph ends FAILED (e.g. a planner LLM error after Issue #12) must
            # fold back as failed with the real cause, not as a silent
            # "completed" carrying an empty summary.
            graph_status = final.get("status") or "COMPLETED"
            completed = graph_status == "COMPLETED"
            return SubTaskResult(
                subtask_id=spec.subtask_id,
                name=spec.name,
                status="completed" if completed else "failed",
                summary=summary,
                artifacts=unique,
                error=""
                if completed
                else (final.get("error") or f"subtask graph ended {graph_status}"),
                tool_calls_executed=tool_count,
                tool_face=sorted(face),
            )
        except TierError as exc:
            # A bad tier / a widening request never started a run: no LLM call,
            # no tool, no side effect.
            logger.warning("subtask %s refused: %s", spec.subtask_id, exc)
            return SubTaskResult(
                subtask_id=spec.subtask_id,
                name=spec.name,
                status="failed",
                error=str(exc),
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("subtask %s crashed", spec.subtask_id)
            return SubTaskResult(
                subtask_id=spec.subtask_id,
                name=spec.name,
                status="failed",
                error=str(exc),
            )
