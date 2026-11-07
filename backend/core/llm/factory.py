"""One LLM client factory for every provider protocol (D-35).

`ModelStore` persists the provider protocol; this module is the single seam
that turns (protocol, base_url, model, key, D-36 params) into a concrete
`LLMClient`. Both the settings seed path (`openai_compat.create_llm_client`)
and the per-task path (`TaskManager._build_llm`) go through it, so a future
protocol is added in one place.

`transport` exists for offline adapter tests (`httpx.MockTransport`);
production callers never pass it.
"""

from __future__ import annotations

from typing import Any

from .anthropic_native import AnthropicClient
from .client import LLMClient
from .protocols import (
    PROTOCOL_ANTHROPIC,
    PROTOCOL_OPENAI,
    SUPPORTED_PROTOCOLS,
)


def build_llm_client(
    *,
    protocol: str,
    base_url: str,
    model: str,
    key: str,
    timeout: float | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    thinking: bool | None = None,
    reasoning_effort: str | None = None,
    transport: Any = None,
) -> LLMClient:
    """Build the client for `protocol`; unknown protocols fail readably."""
    if protocol == PROTOCOL_ANTHROPIC:
        return AnthropicClient(
            base_url,
            key,
            model,
            timeout,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
            transport=transport,
        )
    if protocol == PROTOCOL_OPENAI:
        # Lazy import: `openai_compat` imports this factory in its seed path.
        from .openai_compat import OpenAICompatibleClient

        return OpenAICompatibleClient(
            base_url,
            key,
            model,
            timeout=timeout,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
        )
    raise ValueError(
        f"不支持的协议：{protocol!r}；可选：{', '.join(SUPPORTED_PROTOCOLS)}"
    )
