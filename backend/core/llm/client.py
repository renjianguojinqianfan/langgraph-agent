"""LLM abstraction layer.

The orchestration kernel depends only on :class:`LLMClient`. New providers are
added by subclassing :class:`LLMClient` (see ``openai_compat.py``). A scripted
:class:`MockLLMClient` is provided so the whole stack can run offline without
any API key — this is what the smoke test and the ``use_mock_llm`` flag use, and
``mock_llm_script_path`` lets a JSON file script that mock instead (#108), so a
full tool-call sequence can be replayed offline with no key configured.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from ...config import PROJECT_ROOT
from ...utils.logging import get_logger
from ..agent.prompts import PLANNER_MARKER
from .trace import (
    LLMCallRecorder,
    current_scope,
    reset_active_call,
    set_active_call,
)

logger = get_logger("llm")


@dataclass
class LLMResponse:
    """Normalised response returned by every :class:`LLMClient`."""

    content: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    # Issue #45: thinking-mode providers (DeepSeek official endpoint) return the
    # model's reasoning trace *and* require it back on the next turn — a request
    # without it is a 400. Empty string means "this provider does not report
    # reasoning content", so every other provider's wire format stays as-is.
    reasoning_content: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


class LLMClient(ABC):
    """Provider-agnostic chat/function-calling interface."""

    #: Model name for trace / UI; clients that know their model override it.
    model: str = ""
    #: Protocol label carried by trace records (``mock`` for offline runs).
    protocol: str = ""

    def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
        *,
        session_id: str = "",
        **kwargs: Any,
    ) -> LLMResponse:
        """Run one completion; when a call scope is seated, record the call.

        ``session_id`` is the caller-owned conversation identity (task id,
        parent task id for subagents/compaction, or a temporary id for probes).
        It is explicit — adapters must not infer it from trace scope — and is
        only put on the wire by providers that require it.

        The logical call id and immutable owner/source/round/step come from the
        scope; provider adapters see the active recorder through a contextvar
        and feed it the actual HTTP request/response per SDK attempt.
        """
        scope = current_scope()
        if scope is None:
            return self._complete(messages, tools, session_id=session_id, **kwargs)
        recorder = LLMCallRecorder(scope, self.model, self.protocol)
        recorder.set_fallback_request(self._request_snapshot(messages, tools, kwargs))
        token = set_active_call(recorder)
        try:
            response = self._complete(messages, tools, session_id=session_id, **kwargs)
        except Exception as exc:
            recorder.finish("error", str(exc))
            raise
        except BaseException as exc:
            recorder.finish("aborted", str(exc))
            raise
        else:
            recorder.finish("ok")
            return response
        finally:
            reset_active_call(token)

    @abstractmethod
    def _complete(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
        *,
        session_id: str = "",
        **kwargs: Any,
    ) -> LLMResponse:
        """Provider implementation behind :meth:`complete`'s recording wrapper."""
        raise NotImplementedError

    def _request_snapshot(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None,
        kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Adapter-prepared request used only when no HTTP hook fired.

        Production HTTP paths are captured from the transport; this fallback
        (Mock, stubbed SDK tests) must not pretend to be a network request.
        """
        return {"model": self.model, "messages": messages, "tools": tools or []}


class MockLLMClient(LLMClient):
    """Offline, deterministic client driven by a scripted plan.

    How it is used by the single ``agent`` node (issue #139):

    * remaining scripted ``tool_calls`` are emitted one per turn;
    * then a **plan-mode** call (system prompt is ``PLANNER_SYSTEM``) returns
      the plan block, and a build-mode call returns the final answer.

    The mode is read from the system prompt only. The retired rule treated an
    empty tool face as "planner", which misread an empty-face build round as a
    plan (#330): plan mode can mount an empty explore face just as well.

    This lets the LangGraph loop run end-to-end without any network access, in
    either mode.
    """

    protocol = "mock"

    def __init__(
        self,
        plan: List[str] | None = None,
        tool_calls: List[Dict[str, Any]] | None = None,
        final_answer: str = "Task finished (mock).",
    ) -> None:
        self.plan = plan or ["Analyse the request", "Act", "Return the result"]
        self.tool_calls = tool_calls or []
        self.final_answer = final_answer
        self._turn = 0

    @staticmethod
    def _is_plan_phase(messages: List[Dict[str, Any]]) -> bool:
        head = str((messages or [{}])[0].get("content") or "")
        return PLANNER_MARKER in head

    def _complete(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
        *,
        session_id: str = "",
        **kwargs: Any,
    ) -> LLMResponse:
        if self._turn < len(self.tool_calls):
            tc = self.tool_calls[self._turn]
            self._turn += 1
            logger.info("MockLLM agent -> tool_call %s", tc.get("name"))
            return LLMResponse(content="", tool_calls=[tc])
        if self._is_plan_phase(messages):
            logger.info("MockLLM agent -> plan (%d step(s))", len(self.plan))
            body = "\n".join(f"{i}. {step}" for i, step in enumerate(self.plan, 1))
            return LLMResponse(
                content=f"<qianfan-plan>\n# Plan\n\n{body}\n</qianfan-plan>"
            )
        logger.info("MockLLM agent -> final_answer")
        return LLMResponse(content=self.final_answer)

    def reset(self) -> None:
        """Reset the turn counter (useful between tasks in tests)."""
        self._turn = 0


def make_default_mock_client() -> MockLLMClient:
    """A self-contained demo script usable when ``use_mock_llm=True``."""
    return MockLLMClient(
        plan=[
            "Search the web for the requested topic",
            "Write a short report file with the findings",
            "Return a final answer summarising the result",
        ],
        tool_calls=[
            {
                "id": "call_search",
                "name": "web_search",
                "arguments": {"query": "latest trends in autonomous AI agents", "max_results": 3},
            },
            {
                "id": "call_write",
                "name": "write",
                "arguments": {
                    "path": "report.txt",
                    "content": "Autonomous agents are trending. (mock report)",
                },
            },
        ],
        final_answer="I researched the topic and wrote the findings to report.txt.",
    )


def make_mock_client_from_script(script_path: str | Path) -> MockLLMClient:
    """Build a :class:`MockLLMClient` from a JSON script file (issue #108).

    The file lets an offline script exercise a real tool round (``shell``,
    ``web_search``, ``spawn_subagent``) — in the browser — without any LLM key.
    Shape (every key optional; an absent one keeps :class:`MockLLMClient`'s own
    default)::

        {
          "plan": ["..."],
          "tool_calls": [{"id": "call_1", "name": "shell", "arguments": {...}}],
          "final_answer": "..."
        }

    A relative ``script_path`` resolves against :data:`PROJECT_ROOT` (same convention
    as the data / artifact dirs), so pointing at a repo-relative file works no matter
    where the server was started.

    Raises :class:`Exception` (``OSError`` / ``ValueError``) on an unreadable or
    malformed file; the caller owns the fallback — :func:`create_llm_client` keeps the
    built-in demo script, so a bad path can never change the offline default behaviour.
    """
    path = Path(script_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"mock LLM script {path} must be a JSON object")

    tool_calls: List[Dict[str, Any]] = []
    entries = raw.get("tool_calls") or []
    if not isinstance(entries, list):
        raise ValueError("`tool_calls` must be a JSON array")
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not str(entry.get("name") or ""):
            raise ValueError(f"tool_calls[{index}] needs a non-empty `name`")
        arguments = entry.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ValueError(f"tool_calls[{index}] `arguments` must be a JSON object")
        tool_calls.append(
            {
                "id": str(entry.get("id") or f"call_script_{index}"),
                "name": str(entry["name"]),
                "arguments": arguments,
            }
        )

    script: Dict[str, Any] = {"tool_calls": tool_calls}
    plan = raw.get("plan")
    if plan is not None:
        if not isinstance(plan, list):
            raise ValueError("`plan` must be a JSON array of step strings")
        script["plan"] = [str(step) for step in plan]
    final_answer = raw.get("final_answer")
    if final_answer is not None:
        script["final_answer"] = str(final_answer)
    logger.info("MockLLM script loaded: %s (%d tool call(s))", path, len(tool_calls))
    return MockLLMClient(**script)


