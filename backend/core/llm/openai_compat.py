"""OpenAI-compatible LLM client and provider factory.

Works with any OpenAI-compatible host; DeepSeek is the only built-in preset.
Configure the endpoint, model and key as a model profile (``data/models.json``,
ADR 0010); ``LLM_*`` only seeds the first profile. The OpenAI Python SDK is
used in compatibility mode.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List

from ...config import Settings
from ...utils.logging import get_logger
from .client import (
    LLMClient,
    LLMResponse,
    make_default_mock_client,
    make_mock_client_from_script,
)

logger = get_logger("llm.openai")


class OpenAICompatibleClient(LLMClient):
    """Thin wrapper around the OpenAI SDK configured for any compatible host."""

    def __init__(
        self, base_url: str, api_key: str, model: str, timeout: float | None = None
    ) -> None:
        from openai import OpenAI

        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        # Issue #13: the SDK default is 600s, so a single stalled request could
        # eat an entire task budget and surface as "timed out" instead of a
        # fast failure the caller can retry / degrade on.
        self._client = OpenAI(base_url=base_url, api_key=api_key or "EMPTY", timeout=timeout)
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
            # Issue #45: ``reasoning_content`` is not on the OpenAI schema, so it
            # arrives as an extra SDK attribute (or not at all). ``or ""`` folds
            # both "attribute missing" and explicit null into the empty string.
            reasoning_content=getattr(msg, "reasoning_content", None) or "",
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


# M1 / ADR 0010: DeepSeek is the only built-in preset. Other OpenAI-compatible
# hosts are configured per profile with an explicit base_url.
_PROVIDER_PRESETS: Dict[str, Dict[str, str]] = {
    "deepseek": {"base_url": "https://api.deepseek.com/v1"},
}


def create_llm_client(settings: Settings) -> LLMClient:
    """Build the configured :class:`LLMClient`.

    Returns :class:`MockLLMClient` when ``use_mock_llm`` is set (offline mode).
    With ``mock_llm_script_path`` set, that mock is driven by the JSON script file
    instead of the built-in demo (#108 — a script can carry a confirm-requiring
    call, which is what lets the browser drive the gate without an API key). An
    unreadable path falls back to the built-in script, so the default offline
    behaviour is unchanged either way.
    """
    if settings.use_mock_llm:
        if settings.mock_llm_script_path:
            try:
                return make_mock_client_from_script(settings.mock_llm_script_path)
            except Exception as exc:
                logger.warning(
                    "mock_llm_script_path %r unusable (%s); using the built-in demo script",
                    settings.mock_llm_script_path,
                    exc,
                )
        logger.info("Using MockLLMClient (use_mock_llm=True)")
        return make_default_mock_client()

    preset = _PROVIDER_PRESETS.get(settings.llm_provider)
    if preset is None and not settings.llm_base_url:
        logger.warning(
            "llm_provider %r has no preset; falling back to DeepSeek (set llm_base_url for a custom host)",
            settings.llm_provider,
        )
    base_url = settings.llm_base_url or _PROVIDER_PRESETS["deepseek"]["base_url"]
    api_key = settings.llm_api_key
    model = settings.llm_model
    if not api_key:
        logger.warning("llm_api_key is empty — live calls will fail until configured.")
    return OpenAICompatibleClient(
        base_url=base_url, api_key=api_key, model=model, timeout=settings.llm_request_timeout_sec
    )


