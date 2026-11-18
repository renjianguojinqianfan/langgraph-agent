"""The host surface the agent nodes rely on (ADR 0013 / #330).

``TaskManager`` is the production host. The nodes used to accept ``Any`` and
paper over a missing member with ``getattr`` fallbacks — a shape the test
doubles drove, and one that would also hide a real wiring mistake. This module
declares the surface once so mypy and the doubles agree with production.

Kept out of :mod:`backend.services.task_manager` because ``task_manager``
imports the nodes; a nodes -> task_manager import would close that cycle.
"""

from __future__ import annotations

from typing import List, Optional, Protocol

from ...api.schemas import Task
from ...config import Settings
from ...services.event_bus import EventBus
from ..llm.client import LLMClient
from ..tools.base import BaseTool


class AgentHost(Protocol):
    """What an :class:`~backend.core.agent.nodes.AgentRuntime` may read."""

    settings: Settings
    event_bus: EventBus

    def is_stop_flagged(self, task_id: str) -> bool:
        """The authoritative stop signal, polled at every node entry."""
        ...

    def redact(self, text: str) -> str:
        """Strip known keys before text reaches state / SSE / trace."""
        ...

    def redact_reasoning(self, text: str) -> str:
        """The redacted, bounded display copy of a reasoning trace."""
        ...


class SubAgentHost(AgentHost, Protocol):
    """The wider surface a subtask executor needs from the same host."""

    packages: frozenset[str]
    injection_fragments: frozenset[str]

    @property
    def tool_face(self) -> List[BaseTool]:
        """The mounted tool instances (a subtask narrows this by tier)."""
        ...

    def request_stop(self, task_id: str) -> None:
        """Set the stop flag for a run that has no teardown of its own."""
        ...

    def clear_stop(self, task_id: str) -> None:
        """Clear a flag set by :meth:`request_stop`."""
        ...

    def round_llm_for(self, task_id: str) -> Optional[tuple[str, LLMClient]]:
        """This round's pinned (model id, client), or None before it is pinned."""
        ...

    def get_task(self, task_id: str) -> Optional[Task]:
        """The live task record, or None when it does not exist."""
        ...

    def model_id_for(self, task: Optional[Task]) -> str:
        """The model entry id a task runs with."""
        ...

    def llm_for_task_id(self, task_id: str) -> LLMClient:
        """The client for a task's configured model."""
        ...

    def reasoning_scope_for(self, model_id: Optional[str]) -> str:
        """Provider+protocol identity used for reasoning replay (D-39)."""
        ...
