"""LLM abstraction layer.

The orchestration kernel depends only on :class:`LLMClient`. New providers are
added by subclassing :class:`LLMClient` (see ``openai_compat.py``). A scripted
:class:`MockLLMClient` is provided so the whole stack can run offline without
any API key — this is what the smoke test and the ``use_mock_llm`` flag use.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List

from ...utils.logging import get_logger

logger = get_logger("llm")


@dataclass
class LLMResponse:
    """Normalised response returned by every :class:`LLMClient`."""

    content: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
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
                "name": "file_io",
                "arguments": {
                    "action": "write",
                    "path": "report.txt",
                    "content": "Autonomous agents are trending. (mock report)",
                },
            },
        ],
        final_answer="I researched the topic and wrote the findings to report.txt.",
    )


# Auxiliary-model role markers. The aux client is scripted *per role* so the
# offline test suite can assert which auxiliary task (summary / risk / tool
# choice) actually consumed the aux model — and prove that when aux is disabled
# no auxiliary call is ever made (``call_count == 0``).
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
    the aux model was (or was not) actually consumed.
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
