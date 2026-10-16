"""LLM abstraction layer.

The orchestration kernel depends only on :class:`LLMClient`. New providers are
added by subclassing :class:`LLMClient` (see ``openai_compat.py``). A scripted
:class:`MockLLMClient` is provided so the whole stack can run offline without
any API key — this is what the smoke test and the ``use_mock_llm`` flag use, and
``mock_llm_script_path`` lets a JSON file script that mock instead (#108), which
is how a confirmation-requiring call reaches the gate with no key configured.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List

from ...config import PROJECT_ROOT
from ...utils.logging import get_logger

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

    @abstractmethod
    def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Run one completion. ``tools`` follows the OpenAI function schema."""
        raise NotImplementedError

    @abstractmethod
    def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Iterator[LLMResponse]:
        """Yield incremental completions (best-effort streaming)."""
        raise NotImplementedError


class MockLLMClient(LLMClient):
    """Offline, deterministic client driven by a scripted plan.

    How it is used by the orchestration graph:

    * A **planner** call (``tools is None``) returns ``self.plan`` as JSON.
    * An **executor** call (``tools`` provided) returns one scripted tool call
      per turn, then finally a ``final_answer`` once the script is exhausted.

    This lets the LangGraph loop run end-to-end without any network access.
    """

    def __init__(
        self,
        plan: List[str] | None = None,
        tool_calls: List[Dict[str, Any]] | None = None,
        final_answer: str = "Task finished (mock).",
    ) -> None:
        self.plan = plan or ["Analyse the request", "Act", "Return the result"]
        self.tool_calls = tool_calls or []
        self.final_answer = final_answer
        self._executor_turn = 0

    def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        if not tools:
            # Planner phase: hand back the plan as a JSON array of steps.
            return LLMResponse(content=json.dumps(self.plan, ensure_ascii=False))
        # Executor phase: emit scripted tool calls, then a final answer.
        if self._executor_turn < len(self.tool_calls):
            tc = self.tool_calls[self._executor_turn]
            self._executor_turn += 1
            logger.info("MockLLM executor -> tool_call %s", tc.get("name"))
            return LLMResponse(content="", tool_calls=[tc])
        logger.info("MockLLM executor -> final_answer")
        return LLMResponse(content=self.final_answer)

    def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Iterator[LLMResponse]:
        yield self.complete(messages, tools, **kwargs)

    def reset(self) -> None:
        """Reset the executor turn counter (useful between tasks in tests)."""
        self._executor_turn = 0


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

    The file lets an offline script exercise a real tool round (``code_exec``,
    a git write tool, ``spawn_subagent``) — in the browser — without any LLM key.
    Shape (every key optional; an absent one keeps :class:`MockLLMClient`'s own
    default)::

        {
          "plan": ["..."],
          "tool_calls": [{"id": "call_1", "name": "code_exec", "arguments": {...}}],
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


# Auxiliary-model role markers. The aux client is scripted *per role* so the
# offline test suite can assert which auxiliary task (summary / risk / tool
# choice) actually consumed the aux model — and prove that when aux is disabled
# no auxiliary call is ever made (``call_count == 0``).
#
# ``_detect_role`` scans the *whole* message text — including any history
# transcript embedded in the prompt (the summary prompt carries the history it
# is compressing) — and returns the first marker it hits, so two markers can
# collide. The order below is an explicit tie-break for that case, not a
# guarantee that a collision cannot happen.
_ROLE_MARKERS: Dict[str, List[str]] = {
    "risk": ["风险", "risk"],
    "summary": ["摘要", "压缩", "summary"],
    "tool_choice": ["工具选择", "候选工具", "filter_tool"],
}


class MockAuxLLMClient(LLMClient):
    """Offline, scripted auxiliary-model client.

    Auxiliary tasks (context summarisation, risk semantic analysis, tool
    pre-selection) are routed to this client when ``aux_llm_enabled`` and
    ``aux_llm_use_mock`` are both set. Responses are selected by detecting the
    task role in the prompt (see :data:`_ROLE_MARKERS`), falling back to
    ``default``. :attr:`call_count` / :attr:`roles_called` let QA assert that
    the aux model was (or was not) actually consumed. :attr:`requests` keeps the
    messages themselves so a test can read what the aux model was *told*.
    """

    def __init__(
        self,
        role_responses: Dict[str, str] | None = None,
        default: str = "",
    ) -> None:
        self.role_responses: Dict[str, str] = role_responses or {}
        self.default: str = default
        self.call_count: int = 0
        self.roles_called: List[str] = []
        self.requests: List[List[Dict[str, Any]]] = []

    def _detect_role(self, messages: List[Dict[str, Any]]) -> str:
        text = " ".join(str(m.get("content") or "") for m in messages)
        for role, markers in _ROLE_MARKERS.items():
            if any(marker in text for marker in markers):
                return role
        return "default"

    def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        self.call_count += 1
        role = self._detect_role(messages)
        self.roles_called.append(role)
        self.requests.append(list(messages))
        content = self.role_responses.get(role, self.default)
        logger.info("MockAuxLLM complete -> role=%s", role)
        return LLMResponse(content=content)

    def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Iterator[LLMResponse]:
        yield self.complete(messages, tools, **kwargs)

    def reset(self) -> None:
        """Reset counters (useful between tasks in tests)."""
        self.call_count = 0
        self.roles_called = []
        self.requests = []
