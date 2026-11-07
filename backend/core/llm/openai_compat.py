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
from .protocols import DEFAULT_TEMPERATURE, PROTOCOL_OPENAI

logger = get_logger("llm.openai")


class OpenAICompatibleClient(LLMClient):
    """Thin wrapper around the OpenAI SDK configured for any compatible host."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float | None = None,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        thinking: bool | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        from openai import OpenAI

        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        # D-36: provider/model config defaults; a per-call kwarg still wins.
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.thinking = thinking
        self.reasoning_effort = reasoning_effort
        # Issue #13: the SDK default is 600s, so a single stalled request could
        # eat an entire task budget and surface as "timed out" instead of a
        # fast failure the caller can retry / degrade on.
        self._client = OpenAI(base_url=base_url, api_key=api_key or "EMPTY", timeout=timeout)
        logger.info("OpenAICompatibleClient ready: model=%s base_url=%s", model, base_url)

    def _sampling_payload(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """D-36 request knobs: config defaults, per-call kwargs win.

        `temperature` falls back to the documented 0.2 when neither the
        provider/entry config nor the caller sets it (behaviour-compatible with
        the pre-D-36 hard-coded value). `thinking` / `reasoning_effort` go
        through the SDK's `extra_body` because they are vendor-specific wire
        fields rather than part of the OpenAI chat schema.
        """
        temperature = kwargs.get("temperature", self.temperature)
        payload: Dict[str, Any] = {
            "temperature": DEFAULT_TEMPERATURE if temperature is None else temperature
        }
        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        extra_body: Dict[str, Any] = {}
        if self.thinking is not None:
            extra_body["thinking"] = self.thinking
        if self.reasoning_effort is not None:
            extra_body["reasoning_effort"] = self.reasoning_effort
        if extra_body:
            payload["extra_body"] = extra_body
        return payload

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
        payload.update(self._sampling_payload(kwargs))

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
        payload.update(self._sampling_payload(kwargs))

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
    instead of the built-in demo (#108), so a full tool-call sequence can be
    replayed offline without an API key. An unreadable path falls back to the
    built-in script, so the default offline behaviour is unchanged either way.
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
    # Same factory as the per-task path (D-35): the settings seed constructs an
    # OpenAI-compatible client, but the dispatch itself lives in one place.
    from .factory import build_llm_client

    return build_llm_client(
        protocol=PROTOCOL_OPENAI,
        base_url=base_url,
        model=model,
        key=api_key,
        timeout=settings.llm_request_timeout_sec,
    )


