"""Sub-agent collaboration (P1 item 2).

The main agent can delegate work to **isolated** sub-agents. Isolation means:

* every subtask runs with its **own** :class:`AgentState` (independent
  ``messages`` / ``plan`` / ``steps`` / ``artifacts``);
* every subtask runs with its **own** :class:`AgentRuntime` built over the
  *same* tool set and LLM, using a **simplified graph** (``mode="subtask"`` —
  no risk / confirm / subagent_split, so subtasks can never recurse or block on
  confirmations that cannot route back to the parent);
* subtask internal events are published on the **subtask's own EventBus
  channel** (``<parent>:sub:<hex>``), never on the parent channel; the parent
  channel only receives the three summary events (``subtask_start`` /
  ``subtask_result`` / ``subtask_failed``);
* subtasks do **not** attach a ``TraceRecorder`` and are **not** persisted as
  :class:`Task` records — only the folded summary lands in the parent
  ``Task.subtasks``.

Two entry points:

* :meth:`SubAgentExecutor.run_subtask` — single subtask (used by the
  ``spawn_subagent`` tool);
* :meth:`SubAgentExecutor.run_plan_with_subtasks` — built-in scenario dispatch
  ("调研+报告" -> research + writing subtasks), parallelised by a thread pool
  sized by ``subagent_max_concurrency`` (1 = serial, 2 = parallel).
"""

from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ...config import Settings
from ...utils.logging import get_logger
from .state import AgentState

logger = get_logger("agent.subagent")

#: Built-in split scenarios. The backend matches the *user input* against
#: ``match`` keywords and deterministically emits one spec per ``subtasks``
#: entry (no LLM orchestration required).
DEFAULT_SPLIT_SCENARIOS: List[Dict[str, Any]] = [
    {
        "name": "research_and_report",
        "match": ["调研", "研究", "写报告", "出报告", "报告", "文档", "research", "report"],
        "subtasks": [
            {
                "name": "研究子任务",
                "instruction_template": (
                    "检索并收集关于“{topic}”的资料与事实素材，输出结构化要点清单，"
                    "不要写最终报告。"
                ),
            },
            {
                "name": "写作子任务",
                "instruction_template": (
                    "基于研究素材撰写一份结构化报告，保存为 Markdown 文件，"
                    "并输出最终总结。"
                ),
            },
        ],
    },
]


@dataclass
class SubTaskSpec:
    """A single subtask to be executed in isolation."""

    subtask_id: str
    name: str
    instruction: str
    parent_task_id: str = ""


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


def _extract_topic(user_input: str) -> str:
    """Heuristic topic extraction for the built-in research scenario."""
    text = (user_input or "").strip()
    for marker in ("调研", "研究", "写报告", "出报告"):
        idx = text.find(marker)
        if idx >= 0:
            return text[idx + len(marker):].strip(" ：:，。,.、") or text
    return text


def split_plan_for_scenario(user_input: str, plan: List[Any]) -> List[SubTaskSpec]:
    """Return subtask specs when ``user_input`` matches a built-in scenario.

    ``plan`` is accepted for symmetry / future heuristic use; the current
    built-in scenarios are driven by the user input alone. Returns ``[]`` when
    no scenario matches (zero regression).
    """
    text = (user_input or "").lower()
    for scenario in DEFAULT_SPLIT_SCENARIOS:
        if any(k in text for k in scenario["match"]):
            topic = _extract_topic(user_input)
            specs: List[SubTaskSpec] = []
            for st in scenario["subtasks"]:
                subtask_id = f"{scenario['name']}:sub:{uuid.uuid4().hex[:8]}"
                instruction = st["instruction_template"].format(topic=topic)
                specs.append(
                    SubTaskSpec(
                        subtask_id=subtask_id,
                        name=st["name"],
                        instruction=instruction,
                        parent_task_id="",
                    )
                )
            logger.info("split_plan_for_scenario matched %s (topic=%r)", scenario["name"], topic)
            return specs
    return []


class SubAgentExecutor:
    """Runs isolated subtasks on a bounded thread pool."""

    def __init__(self, task_manager: Any, settings: Settings) -> None:
        self.tm = task_manager
        self.settings = settings
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, int(settings.subagent_max_concurrency))
        )

    # ── public entry points ──
    def run_subtask(
        self,
        spec: SubTaskSpec,
        publish: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> SubTaskResult:
        """Run a single subtask synchronously (spawn_subagent tool path)."""
        return self._exec_one(spec, publish)

    def run_plan_with_subtasks(
        self,
        parent_task_id: str,
        user_input: str,
        plan: List[Any],
        publish: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> List[SubTaskResult]:
        """Dispatch the built-in scenario's subtasks (optionally parallel).

        Publishes ``subtask_start`` before submitting and
        ``subtask_result`` / ``subtask_failed`` as each future resolves. A
        subtask failure never crashes the parent — it is folded into the
        results list.
        """
        specs = split_plan_for_scenario(user_input, plan)
        if not specs:
            return []
        for spec in specs:
            spec.parent_task_id = parent_task_id

        results: List[SubTaskResult] = []
        futures: List[tuple] = []
        for spec in specs:
            if publish is not None:
                publish(
                    "subtask_start",
                    {
                        "subtask_id": spec.subtask_id,
                        "name": spec.name,
                        "status": "running",
                        "parent_task_id": parent_task_id,
                    },
                )
            futures.append(
                (spec, self._pool.submit(self._exec_one, spec, publish))
            )

        for spec, fut in futures:
            try:
                res: SubTaskResult = fut.result(timeout=self.settings.subagent_timeout_sec)
            except Exception as exc:  # timeout / worker crash
                logger.warning("subtask %s failed: %s", spec.subtask_id, exc)
                res = SubTaskResult(
                    subtask_id=spec.subtask_id,
                    name=spec.name,
                    status="failed",
                    error=f"subtask timeout or worker error: {exc}",
                )
            results.append(res)
            if publish is not None:
                if res.status == "completed":
                    publish(
                        "subtask_result",
                        {
                            "subtask_id": res.subtask_id,
                            "name": res.name,
                            "status": res.status,
                            "summary": res.summary,
                            "artifacts": res.artifacts,
                        },
                    )
                else:
                    publish(
                        "subtask_failed",
                        {
                            "subtask_id": res.subtask_id,
                            "name": res.name,
                            "error": res.error or "unknown error",
                        },
                    )
        return results

    # ── internals ──
    def _subtask_tools(self):
        """Tool list for subtasks — same as the parent minus ``spawn_subagent``.

        This is the recursion guard: a subtask can never spawn another subtask.
        """
        return [t for t in self.tm._tools if t.name != "spawn_subagent"]

    def _exec_one(
        self,
        spec: SubTaskSpec,
        publish: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> SubTaskResult:
        """Execute one subtask in its own runtime + state + simplified graph.

        Internal events are published on the subtask's own EventBus channel
        (``spec.subtask_id``) — they never reach the parent channel.
        """
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
            tools = self._subtask_tools()
            schemas = [t.to_openai_schema() for t in tools]
            # Lazy imports: `nodes`/`graph` are part of a pre-existing import
            # cycle (graph -> nodes -> tools -> subagent_tool -> subagent), so
            # module-level imports here would dead-lock when ``backend.main``
            # is imported first (the uvicorn entry point).
            from .nodes import AgentRuntime

            runtime = AgentRuntime(
                task_id=spec.subtask_id,
                task_manager=self.tm,
                llm=self.tm._llm,
                tools=tools,
                tool_schemas=schemas,
                max_steps=self.settings.max_steps,
                aux_llm=getattr(self.tm, "_aux_llm", None),
                subagent_executor=None,  # never recurse
                confirm_enabled=False,  # subtask internal: no human confirm
            )
            from .graph import build_graph

            graph = build_graph(runtime, mode="subtask")
            final = graph.invoke(state)

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

            return SubTaskResult(
                subtask_id=spec.subtask_id,
                name=spec.name,
                status="completed",
                summary=summary,
                artifacts=unique,
                tool_calls_executed=tool_count,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("subtask %s crashed", spec.subtask_id)
            return SubTaskResult(
                subtask_id=spec.subtask_id,
                name=spec.name,
                status="failed",
                error=str(exc),
            )
