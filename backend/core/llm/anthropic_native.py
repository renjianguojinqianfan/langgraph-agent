"""Anthropic native adapter (D-35): httpx, non-streaming ``complete()``.

The orchestration kernel speaks one OpenAI-style message shape (system / user /
assistant with ``tool_calls`` / ``role: tool`` with ``tool_call_id``). This
module translates it to the Anthropic Messages API content blocks and back:
text / tool_use / tool_result out, text / tool_use / thinking in.

Scope of this slice (D-35):
* only **non-streaming** ``complete()``; ``stream()`` raises a readable error;
* ``thinking`` / ``reasoning_effort`` are **refused at build time** — the
  tool loop needs the previous turn's thinking block *with its signature*,
  which the OpenAI-shaped history cannot preserve (ADR 0011);
* the API key goes in ``x-api-key`` and never appears in an error message.

``transport`` is the offline test seam (``httpx.MockTransport``); production
callers go through :func:`backend.core.llm.factory.build_llm_client`.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List

from .client import LLMClient, LLMResponse
from .protocols import DEFAULT_TEMPERATURE

ANTHROPIC_VERSION = "2023-06-01"
#: Messages API requires max_tokens; 4096 keeps a normal non-streaming answer
#: from being cut mid-way (D-35; entry/provider may override).
ANTHROPIC_DEFAULT_MAX_TOKENS = 4096


class AnthropicClient(LLMClient):
    """Non-streaming Anthropic Messages client over httpx."""

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
        transport: Any = None,
    ) -> None:
        base = (base_url or "").strip().rstrip("/")
        if not base:
            raise ValueError("Anthropic base_url is required")
        if not api_key:
            raise ValueError(
                "Anthropic provider has no API key: paste one in the settings page "
                "or set the environment variable the provider names"
            )
        # thinking=False means "explicitly off" (the settings toggle default),
        # so only an enabled thinking switch is refused here; the same holds for
        # a configured reasoning_effort (there is no pass-through field on the
        # Messages API). The store rejects these combinators even earlier.
        if thinking is True:
            raise ValueError(
                "Anthropic 适配器暂不支持 thinking：工具循环需要保留 thinking 签名，"
                "留后续片（见 ADR 0011）"
            )
        if reasoning_effort is not None:
            raise ValueError(
                "Anthropic 适配器暂不支持 reasoning_effort：工具循环需要保留 "
                "thinking 签名，留后续片（见 ADR 0011）"
            )
        self.base_url = base
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._transport = transport

    def _messages_url(self) -> str:
        """Accept both documented base_url spellings (D-35).

        Bare host -> ".../v1/messages"; a base_url already ending in "/v1" ->
        ".../v1/messages" (i.e. append "/messages" only).
        """
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/messages"
        return f"{self.base_url}/v1/messages"

    def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        payload = self._build_payload(messages, tools, kwargs)
        import httpx

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        try:
            with httpx.Client(
                timeout=self.timeout,
                transport=self._transport,
                follow_redirects=False,
            ) as client:
                response = client.post(self._messages_url(), json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            # The client may have no configured timeout (httpx default applies);
            # never render that as "(Nones)".
            budget = f"（{self.timeout}s）" if self.timeout is not None else ""
            raise RuntimeError(
                f"Anthropic 请求超时{budget}：检查 API 地址是否可达。"
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"无法连接 Anthropic API：{type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise RuntimeError(self._http_error(response))
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError("Anthropic 返回的不是合法 JSON。") from exc
        return self._parse_response(data)

    def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Iterator[LLMResponse]:
        raise NotImplementedError(
            "Anthropic 适配器本章只支持非流式 complete()（D-35）"
        )

    # ── request mapping ──
    def _build_payload(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None,
        kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        # A per-call max_tokens=None must not reach int(None): missing / None
        # both fall back to the configured value, then to the documented 4096.
        max_tokens = kwargs.get("max_tokens")
        if max_tokens is None:
            max_tokens = (
                self.max_tokens
                if self.max_tokens is not None
                else ANTHROPIC_DEFAULT_MAX_TOKENS
            )
        temperature = kwargs.get("temperature", self.temperature)
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": self._messages_payload(messages),
            "max_tokens": int(max_tokens),
            "temperature": (
                float(temperature)
                if temperature is not None
                else DEFAULT_TEMPERATURE
            ),
        }
        system = self._system_text(messages)
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = self._tools_payload(tools)
        return payload

    @staticmethod
    def _system_text(messages: List[Dict[str, Any]]) -> str:
        parts: List[str] = []
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                raise ValueError(f"messages[{index}] must be an object")
            if message.get("role") != "system":
                continue
            content = message.get("content")
            if not isinstance(content, str):
                raise ValueError(
                    f"messages[{index}] system content must be a string"
                )
            if content:
                parts.append(content)
        return "\n\n".join(parts)

    def _messages_payload(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        converted: List[Dict[str, Any]] = []
        pending_tool_results: List[Dict[str, Any]] = []

        def flush_tool_results() -> None:
            if pending_tool_results:
                converted.append(
                    {"role": "user", "content": list(pending_tool_results)}
                )
                pending_tool_results.clear()

        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                raise ValueError(f"messages[{index}] must be an object")
            role = message.get("role")
            if role == "system":
                continue  # carried separately in `system`
            if role == "tool":
                pending_tool_results.append(self._tool_result_block(message, index))
                continue
            flush_tool_results()
            if role == "user":
                converted.append(
                    {"role": "user", "content": self._user_blocks(message, index)}
                )
            elif role == "assistant":
                converted.append(
                    {"role": "assistant", "content": self._assistant_blocks(message, index)}
                )
            else:
                raise ValueError(
                    f"messages[{index}] has unsupported role {role!r} "
                    "(expected system / user / assistant / tool)"
                )
        flush_tool_results()
        return converted

    @staticmethod
    def _text_block(content: Any, *, where: str) -> List[Dict[str, Any]]:
        if not isinstance(content, str):
            raise ValueError(f"{where} content must be a string")
        return [{"type": "text", "text": content}] if content else []

    def _user_blocks(self, message: Dict[str, Any], index: int) -> List[Dict[str, Any]]:
        blocks = self._text_block(message.get("content") or "", where=f"messages[{index}]")
        if not blocks:
            raise ValueError(f"messages[{index}] user content is empty")
        return blocks

    def _assistant_blocks(
        self, message: Dict[str, Any], index: int
    ) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = []
        content = message.get("content")
        if content is not None:
            blocks.extend(
                self._text_block(content, where=f"messages[{index}]")
            )
        for position, call in enumerate(message.get("tool_calls") or []):
            blocks.append(self._tool_use_block(call, index=index, position=position))
        if not blocks:
            raise ValueError(
                f"messages[{index}] assistant has neither content nor tool_calls"
            )
        return blocks

    @staticmethod
    def _tool_result_block(
        message: Dict[str, Any], index: int
    ) -> Dict[str, Any]:
        tool_use_id = message.get("tool_call_id")
        if not tool_use_id:
            raise ValueError(
                f"messages[{index}] tool message is missing tool_call_id"
            )
        content = message.get("content")
        text = (
            content
            if isinstance(content, str)
            else json.dumps(content, ensure_ascii=False)
            if content is not None
            else ""
        )
        return {
            "type": "tool_result",
            "tool_use_id": str(tool_use_id),
            "content": text,
        }

    @staticmethod
    def _tool_use_block(
        call: Any, *, index: int, position: int
    ) -> Dict[str, Any]:
        if not isinstance(call, dict):
            raise ValueError(
                f"messages[{index}].tool_calls[{position}] must be an object"
            )
        call_id = call.get("id")
        function = call.get("function") or {}
        if not isinstance(function, dict):
            raise ValueError(
                f"messages[{index}].tool_calls[{position}].function must be an object"
            )
        name = function.get("name")
        if not call_id or not name:
            raise ValueError(
                f"messages[{index}].tool_calls[{position}] needs id and function.name"
            )
        raw_arguments = function.get("arguments")
        if isinstance(raw_arguments, dict):
            arguments = raw_arguments
        elif isinstance(raw_arguments, str):
            try:
                arguments = json.loads(raw_arguments or "{}")
            except ValueError as exc:
                raise ValueError(
                    f"messages[{index}].tool_calls[{position}] arguments "
                    "are not valid JSON"
                ) from exc
        else:
            raise ValueError(
                f"messages[{index}].tool_calls[{position}] arguments must be a "
                "JSON object or a JSON string"
            )
        if not isinstance(arguments, dict):
            raise ValueError(
                f"messages[{index}].tool_calls[{position}] arguments must be an object"
            )
        return {
            "type": "tool_use",
            "id": str(call_id),
            "name": str(name),
            "input": arguments,
        }

    @staticmethod
    def _tools_payload(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        converted: List[Dict[str, Any]] = []
        for position, tool in enumerate(tools):
            if not isinstance(tool, dict):
                raise ValueError(f"tools[{position}] must be an object")
            function = tool.get("function") or {}
            if not isinstance(function, dict) or not function.get("name"):
                raise ValueError(f"tools[{position}] needs function.name")
            schema = function.get("parameters")
            if schema is None:
                schema = {"type": "object", "properties": {}}
            if not isinstance(schema, dict):
                raise ValueError(f"tools[{position}].function.parameters must be an object")
            entry: Dict[str, Any] = {
                "name": str(function["name"]),
                "input_schema": schema,
            }
            description = function.get("description")
            if description:
                entry["description"] = str(description)
            converted.append(entry)
        return converted

    # ── response mapping ──
    @staticmethod
    def _parse_response(data: Any) -> LLMResponse:
        if not isinstance(data, dict):
            raise RuntimeError("Anthropic 响应格式不符：不是 JSON 对象。")
        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []
        blocks = data.get("content")
        if blocks is not None and not isinstance(blocks, list):
            raise RuntimeError("Anthropic 响应格式不符：content 不是数组。")
        for block in blocks or []:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                content_parts.append(str(block.get("text") or ""))
            elif block_type == "thinking":
                reasoning_parts.append(str(block.get("thinking") or ""))
            elif block_type == "tool_use":
                arguments = block.get("input")
                tool_calls.append(
                    {
                        "id": str(block.get("id") or ""),
                        "name": str(block.get("name") or ""),
                        "arguments": arguments if isinstance(arguments, dict) else {},
                    }
                )
        return LLMResponse(
            content="".join(content_parts),
            tool_calls=tool_calls,
            reasoning_content="".join(reasoning_parts),
            raw=data,
        )

    @staticmethod
    def _http_error(response: Any) -> str:
        detail = ""
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                detail = str(error.get("message") or "").strip()
        if response.status_code in (401, 403):
            prefix = "认证失败"
        elif response.status_code == 429:
            prefix = "请求被限流"
        elif 400 <= response.status_code < 500:
            prefix = "请求被拒绝"
        else:
            prefix = "Anthropic 服务错误"
        suffix = f"：{detail[:500]}" if detail else "。"
        return f"{prefix}（HTTP {response.status_code}）{suffix}"
