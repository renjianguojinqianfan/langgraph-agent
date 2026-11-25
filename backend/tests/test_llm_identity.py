"""#351: model requests identify qianfan and, on OpenCode, carry a session.

The adapter seam is the real HTTP layer (``httpx.MockTransport``), so these
tests assert the headers actually sent, not adapter-internal state. The runtime
seam uses a recording ``LLMClient`` to prove the task id travels as an explicit
``session_id`` argument through the main round, subagents, compaction and the
settings-page "test model" action.
"""

from __future__ import annotations

from typing import Any, Dict, List

import httpx
import pytest

from backend.config import Settings
from backend.core.agent.nodes import AgentRuntime
from backend.core.llm.anthropic_native import AnthropicClient
from backend.core.llm.client import LLMClient, LLMResponse
from backend.core.llm.identity import is_opencode_base_url
from backend.core.llm.openai_compat import OpenAICompatibleClient
from backend.services.model_store import Provider, discover_provider_models
from backend.tests.conftest import make_agent_host, make_manager, make_settings

USER_AGENT = "qianfan/0.1.0"


def _openai_ok() -> Dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hi"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _anthropic_ok() -> Dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "hello"}],
        "model": "claude-3",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _openai_client(base_url: str, seen: List[httpx.Request]) -> OpenAICompatibleClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_openai_ok())

    return OpenAICompatibleClient(
        base_url, "sk-test", "m", 5, transport=httpx.MockTransport(handler)
    )


def test_only_the_opencode_hosts_are_recognized():
    assert is_opencode_base_url("https://opencode.ai/inference/go/openai/v1")
    assert is_opencode_base_url("https://www.opencode.ai/inference/go/anthropic")
    assert not is_opencode_base_url("https://api.opencode.ai/v1")
    assert not is_opencode_base_url("https://opencode.ai.evil.test/v1")
    assert not is_opencode_base_url("not a url")


def test_openai_compatible_requests_carry_the_qianfan_user_agent():
    seen: List[httpx.Request] = []
    _openai_client("https://gw.test/v1", seen).complete(
        [{"role": "user", "content": "hi"}]
    )

    assert seen[0].headers["user-agent"] == USER_AGENT
    assert "x-opencode-session" not in seen[0].headers


@pytest.mark.parametrize(
    "base_url",
    [
        "https://opencode.ai/inference/go/openai/v1",
        "https://www.opencode.ai/inference/go/openai/v1",
    ],
)
def test_openai_compatible_sends_the_session_header_only_to_opencode(base_url):
    seen: List[httpx.Request] = []
    _openai_client(base_url, seen).complete(
        [{"role": "user", "content": "hi"}], session_id="task-1"
    )

    assert seen[0].headers["user-agent"] == USER_AGENT
    assert seen[0].headers["x-opencode-session"] == "task-1"


def test_openai_compatible_preserves_a_caller_session_header():
    seen: List[httpx.Request] = []
    _openai_client("https://opencode.ai/inference/go/openai/v1", seen).complete(
        [{"role": "user", "content": "hi"}],
        session_id="task-1",
        extra_headers={"X-OpenCode-Session": "caller-value"},
    )

    assert seen[0].headers["x-opencode-session"] == "caller-value"


def test_openai_compatible_does_not_send_the_session_header_elsewhere():
    seen: List[httpx.Request] = []
    _openai_client("https://gw.test/v1", seen).complete(
        [{"role": "user", "content": "hi"}], session_id="task-1"
    )

    assert "x-opencode-session" not in seen[0].headers


def test_openai_compatible_does_not_fabricate_a_session_header():
    seen: List[httpx.Request] = []
    _openai_client("https://opencode.ai/inference/go/openai/v1", seen).complete(
        [{"role": "user", "content": "hi"}]
    )

    assert "x-opencode-session" not in seen[0].headers


def _anthropic_client(base_url: str, seen: List[httpx.Request]) -> AnthropicClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_anthropic_ok())

    return AnthropicClient(
        base_url, "sk-ant", "claude-3", 5, transport=httpx.MockTransport(handler)
    )


def test_anthropic_requests_keep_x_api_key_and_carry_the_user_agent():
    seen: List[httpx.Request] = []
    _anthropic_client("https://api.anthropic.com", seen).complete(
        [{"role": "user", "content": "hi"}]
    )

    assert seen[0].headers["x-api-key"] == "sk-ant"
    assert "authorization" not in seen[0].headers
    assert seen[0].headers["user-agent"] == USER_AGENT
    assert "x-opencode-session" not in seen[0].headers


def test_anthropic_sends_the_session_header_only_to_opencode():
    seen: List[httpx.Request] = []
    _anthropic_client("https://www.opencode.ai/inference/go/anthropic", seen).complete(
        [{"role": "user", "content": "hi"}], session_id="task-1"
    )

    assert seen[0].headers["x-api-key"] == "sk-ant"
    assert seen[0].headers["x-opencode-session"] == "task-1"


def test_anthropic_does_not_send_the_session_header_elsewhere_or_without_one():
    seen: List[httpx.Request] = []
    _anthropic_client("https://api.anthropic.com", seen).complete(
        [{"role": "user", "content": "hi"}], session_id="task-1"
    )
    _anthropic_client("https://opencode.ai/inference/go/anthropic", seen).complete(
        [{"role": "user", "content": "hi"}]
    )

    assert all("x-opencode-session" not in request.headers for request in seen)


def test_model_discovery_carries_the_user_agent_without_a_session_header():
    seen: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": "m1"}]})

    candidates = discover_provider_models(
        Provider(
            id="opencode",
            name="OpenCode Go",
            protocol="openai-compatible",
            base_url="https://opencode.ai/inference/go/openai/v1",
            api_key_env="OPENCODE_API_KEY",
        ),
        "sk-x",
        transport=httpx.MockTransport(handler),
    )

    assert candidates == [{"model": "m1", "name": "m1"}]
    assert seen[0].headers["user-agent"] == USER_AGENT
    assert "x-opencode-session" not in seen[0].headers


class _SessionRecordingLLM(LLMClient):
    """Records the explicit session id each call receives."""

    model = "recording"
    protocol = "mock"

    def __init__(self) -> None:
        self.session_ids: List[str] = []

    def _complete(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
        *,
        session_id: str = "",
        **kwargs: Any,
    ) -> LLMResponse:
        self.session_ids.append(session_id)
        return LLMResponse(content="done")


def _agent_state() -> Dict[str, Any]:
    return {
        "messages": [{"role": "user", "content": "hi"}],
        "steps": [],
        "step_index": 0,
        "round_no": 1,
        "round_steps": 0,
        "status": "RUNNING",
    }


def _runtime(
    settings: Settings,
    llm: LLMClient,
    *,
    task_id: str = "task-main",
    trace_source: str = "main",
    trace_owner: str | None = None,
) -> AgentRuntime:
    return AgentRuntime(
        task_id=task_id,
        task_manager=make_agent_host(settings),
        llm=llm,
        tools=[],
        tool_schemas=[],
        trace_source=trace_source,
        trace_owner=trace_owner,
    )


def test_main_rounds_reuse_the_task_id_as_the_session_id(tmp_path):
    llm = _SessionRecordingLLM()
    runtime = _runtime(make_settings(tmp_path), llm)

    runtime.agent(_agent_state())
    runtime.agent(_agent_state())

    assert llm.session_ids == ["task-main", "task-main"]


def test_subagent_rounds_use_the_parent_task_id(tmp_path):
    llm = _SessionRecordingLLM()
    runtime = _runtime(
        make_settings(tmp_path),
        llm,
        task_id="sub-1",
        trace_source="subagent",
        trace_owner="task-main",
    )

    runtime.agent(_agent_state())

    assert llm.session_ids == ["task-main"]


def test_compaction_uses_the_parent_task_id(tmp_path):
    settings = make_settings(
        tmp_path,
        context_compress_strategy="summarize",
        context_token_budget=10,
        context_keep_recent=1,
        context_max_messages=100,
    )
    llm = _SessionRecordingLLM()
    runtime = _runtime(
        settings,
        llm,
        task_id="sub-1",
        trace_source="subagent",
        trace_owner="task-main",
    )

    runtime._build_messages(
        {
            "messages": [
                {"role": "user" if index % 2 == 0 else "assistant", "content": "x" * 4000}
                for index in range(12)
            ],
            "step_index": 0,
            "round_no": 1,
            "steps": [],
        },
        "SYS",
    )

    assert llm.session_ids == ["task-main"]


def test_test_model_uses_a_fresh_temporary_session_id(tmp_path):
    settings = make_settings(tmp_path, use_mock_llm=False)
    clients: List[_SessionRecordingLLM] = []

    def factory(_model: Any, _key: str) -> _SessionRecordingLLM:
        client = _SessionRecordingLLM()
        clients.append(client)
        return client

    tm = make_manager(settings, None, llm_factory=factory)
    tm.update_provider("default", key="sk-test")

    assert tm.test_model("default") == {"ok": True}
    assert tm.test_model("default") == {"ok": True}

    first, second = (client.session_ids[0] for client in clients)
    assert first and second
    assert first != second
