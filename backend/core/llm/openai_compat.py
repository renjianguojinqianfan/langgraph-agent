"""OpenAI-compatible LLM client and provider factory.

Works with OpenAI, DeepSeek and local Ollama simply by changing
``base_url`` / ``api_key`` / ``model`` (see ``.env.example``). The OpenAI
Python SDK is used in compatibility mode.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List

from ...config import Settings
from ...utils.logging import get_logger
from .client import (
    LLMClient,
    LLMResponse,
    MockAuxLLMClient,
    MockLLMClient,
    make_default_mock_client,
)

logger = get_logger("llm.openai")


class OpenAICompatibleClient(LLMClient):
    """Thin wrapper around the OpenAI SDK configured for any compatible host."""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        from openai import OpenAI

        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self._client = OpenAI(base_url=base_url, api_key=api_key or "EMPTY")
        logger.info("OpenAICompatibleClient ready: model=%s base_url=%s", model, base_url)

    def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        payload: Dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        # User-tunable knobs
        payload["temperature"] = kwargs.get("temperature", 0.2)
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]

        resp = self._client.chat.completions.create(**payload)
        msg = resp.choices[0].message
        tool_calls: List[Dict[str, Any]] = []
        if getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(
                    {"id": tc.id, "name": tc.function.name, "arguments": args}
                )
        return LLMResponse(
            content=msg.content or "",
            tool_calls=tool_calls,
            raw=resp.model_dump() if hasattr(resp, "model_dump") else {},
        )

    def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Iterator[LLMResponse]:
        payload: Dict[str, Any] = {"model": self.model, "messages": messages, "stream": True}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        payload["temperature"] = kwargs.get("temperature", 0.2)

        stream = self._client.chat.completions.create(**payload)
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            content = getattr(delta, "content", "") or ""
            tool_calls: List[Dict[str, Any]] = []
            if getattr(delta, "tool_calls", None):
                for tc in delta.tool_calls:
                    tool_calls.append(
                        {
                            "id": getattr(tc, "id", "") or "",
                            "name": getattr(getattr(tc, "function", None), "name", "") or "",
                            "arguments": {},
                        }
                    )
            if content or tool_calls:
                yield LLMResponse(content=content, tool_calls=tool_calls)


_PROVIDER_PRESETS: Dict[str, Dict[str, str]] = {
    "openai": {"base_url": "https://api.openai.com/v1"},
    "deepseek": {"base_url": "https://api.deepseek.com/v1"},
    "ollama": {"base_url": "http://localhost:11434/v1"},
}


def create_llm_client(settings: Settings) -> LLMClient:
    """Build the configured :class:`LLMClient`.

    Returns :class:`MockLLMClient` when ``use_mock_llm`` is set (offline mode).
    """
    if settings.use_mock_llm:
        logger.info("Using MockLLMClient (use_mock_llm=True)")
        return make_default_mock_client()

    preset = _PROVIDER_PRESETS.get(settings.llm_provider, {})
    base_url = settings.llm_base_url or preset.get("base_url", "https://api.openai.com/v1")
    api_key = settings.llm_api_key
    model = settings.llm_model
    if not api_key:
        logger.warning("llm_api_key is empty — live calls will fail until configured.")
    return OpenAICompatibleClient(base_url=base_url, api_key=api_key, model=model)


def create_aux_llm_client(settings: Settings) -> LLMClient | None:
    """Build the configured auxiliary-model client, or ``None`` when disabled.

    P1 item 4 — auxiliary model division of labour. The aux model is an
    independent :class:`LLMClient` instance configured through the
    ``aux_llm_*`` settings. Returns ``None`` (degradation) when:

    * ``aux_llm_enabled`` is False (default — **zero extra LLM calls**);
    * ``aux_llm_use_mock`` is True (offline mock aux);
    * the model / api_key are missing for a live provider.
    """
    if not settings.aux_llm_enabled:
        logger.info("aux_llm disabled (aux_llm_enabled=False); degradation path active.")
        return None
    if settings.aux_llm_use_mock:
        logger.info("Using MockAuxLLMClient (aux_llm_use_mock=True)")
        return MockAuxLLMClient()
    if not settings.aux_llm_model or not settings.aux_llm_api_key:
        logger.info("aux_llm disabled: missing aux_llm_model/aux_llm_api_key.")
        return None
    preset = _PROVIDER_PRESETS.get(settings.aux_llm_provider, {})
    base_url = settings.aux_llm_base_url or preset.get("base_url", "https://api.openai.com/v1")
    return OpenAICompatibleClient(
        base_url=base_url,
        api_key=settings.aux_llm_api_key,
        model=settings.aux_llm_model,
    )


def get_aux_llm(settings: Settings, main_llm: Any = None) -> LLMClient | None:
    """Unified degradation guard for auxiliary-model consumers.

    Returns the configured aux client or ``None``. Consumers decide their own
    fallback (skip semantic analysis; use the main model for summaries; keep
    the full tool list for tool selection). ``main_llm`` is accepted for
    symmetry/future fallback logic but is **not** returned — when aux is
    unavailable the caller falls back explicitly.
    """
    aux = create_aux_llm_client(settings)
    if aux is not None:
        return aux
    return None
