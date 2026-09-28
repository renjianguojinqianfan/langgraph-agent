"""Tests for the LLM abstraction layer.

Covers :class:`MockLLMClient` behaviour and the :func:`create_llm_client`
factory returning the correct implementation for each provider configuration.
All of this runs offline (no API keys, no network).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, List

from backend.config import Settings
from backend.core.agent.nodes import AgentRuntime
from backend.core.agent.state import AgentState
from backend.core.llm.client import (
    LLMClient,
    LLMResponse,
    MockLLMClient,
    make_default_mock_client,
)
from backend.core.llm.openai_compat import (
    OpenAICompatibleClient,
    create_llm_client,
)
from backend.services.event_bus import EventBus


# ── LLMResponse ──
def test_llm_response_defaults():
    resp = LLMResponse(content="hi")
    assert resp.content == "hi"
    assert resp.tool_calls == []
    assert resp.raw == {}


def test_llm_response_carries_tool_calls_and_raw():
    tc = {"id": "1", "name": "write", "arguments": {}}
    resp = LLMResponse(content="", tool_calls=[tc], raw={"x": 1})
    assert resp.tool_calls == [tc]
    assert resp.raw == {"x": 1}


# ── MockLLMClient behaviour ──
def test_mock_planner_returns_plan_as_json():
    """A planner call (no tools) returns the plan as a JSON array of steps."""
    mock = MockLLMClient(plan=["step A", "step B"], tool_calls=[], final_answer="done")
    resp = mock.complete(messages=[{"role": "user", "content": "go"}], tools=None)
    assert resp.tool_calls == []  # planner phase never emits tool calls
    parsed = json.loads(resp.content)
    assert parsed == ["step A", "step B"]


def test_mock_executor_emits_scripted_tool_calls_then_final_answer():
    """Executor calls return one scripted tool call per turn, then final_answer."""
    tool_calls = [
        {"id": "c1", "name": "write", "arguments": {"path": "a.txt"}},
        {"id": "c2", "name": "code_exec", "arguments": {"language": "python", "code": "1+1"}},
    ]
    mock = MockLLMClient(plan=["p"], tool_calls=tool_calls, final_answer="all done")

    r1 = mock.complete([], tools=[{"type": "function"}])
    r2 = mock.complete([], tools=[{"type": "function"}])
    r3 = mock.complete([], tools=[{"type": "function"}])

    assert r1.tool_calls[0]["name"] == "write"
    assert r2.tool_calls[0]["name"] == "code_exec"
    assert r3.tool_calls == []            # script exhausted
    assert r3.content == "all done"       # now a final answer


def test_mock_resets_executor_turn_counter():
    mock = MockLLMClient(plan=["p"], tool_calls=[{"id": "c1", "name": "write", "arguments": {}}], final_answer="x")
    assert mock.complete([], tools=[{}]).tool_calls  # c1
    assert mock.complete([], tools=[{}]).content == "x"  # exhausted
    mock.reset()
    assert mock.complete([], tools=[{}]).tool_calls[0]["id"] == "c1"  # back to start


def test_mock_stream_yields_single_completion():
    # With tools provided the mock is in *executor* mode; once the scripted
    # tool-call list is exhausted it returns the final answer. (tools=None would
    # be interpreted as the *planner* phase and return the plan JSON instead.)
    mock = MockLLMClient(plan=["p"], tool_calls=[], final_answer="streamed")
    chunks = list(mock.stream([], tools=[{"type": "function"}]))
    assert len(chunks) == 1
    assert chunks[0].content == "streamed"


def test_make_default_mock_client_is_runnable():
    mock = make_default_mock_client()
    assert isinstance(mock, MockLLMClient)
    assert any(tc["name"] == "web_search" for tc in mock.tool_calls)
    assert any(tc["name"] == "write" for tc in mock.tool_calls)


# ── create_llm_client factory ──
def test_factory_returns_mock_when_use_mock_llm_true():
    s = Settings(use_mock_llm=True, llm_provider="openai")
    client = create_llm_client(s)
    assert isinstance(client, MockLLMClient)


def test_factory_returns_openai_client_for_openai_provider():
    s = Settings(use_mock_llm=False, llm_provider="openai", llm_base_url="", llm_api_key="sk-test")
    client = create_llm_client(s)
    assert isinstance(client, OpenAICompatibleClient)
    assert "api.openai.com" in client.base_url


def test_factory_returns_openai_client_for_deepseek_provider():
    s = Settings(use_mock_llm=False, llm_provider="deepseek", llm_base_url="", llm_api_key="sk-test")
    client = create_llm_client(s)
    assert isinstance(client, OpenAICompatibleClient)
    assert "deepseek" in client.base_url


def test_factory_returns_openai_client_for_ollama_provider():
    s = Settings(use_mock_llm=False, llm_provider="ollama", llm_base_url="", llm_api_key="")
    client = create_llm_client(s)
    assert isinstance(client, OpenAICompatibleClient)
    assert "11434" in client.base_url  # local Ollama default port


def test_factory_uses_explicit_base_url_when_provided():
    s = Settings(use_mock_llm=False, llm_provider="openai", llm_base_url="https://my.gw/v1", llm_api_key="x")
    client = create_llm_client(s)
    assert client.base_url == "https://my.gw/v1"


def test_llm_client_passes_bounded_request_timeout(monkeypatch):
    """Issue #13: one hung provider request must not eat the whole run budget —
    the SDK's own default is 600s."""
    import openai

    captured: dict = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
    s = Settings(use_mock_llm=False, llm_base_url="https://gw/v1", llm_api_key="x", llm_model="m")
    create_llm_client(s)
    assert captured.get("timeout") == s.llm_request_timeout_sec
    assert 0 < s.llm_request_timeout_sec < 600, "must stay below the SDK default"


# ── Issue #45 A：reasoning_content 回传 ──
# DeepSeek 官方端点在 thinking 模式下**强制**要求把上一轮 assistant 的
# ``reasoning_content`` 原样带回，否则第二轮起 400。下面三条路径全部离线：
# 假 ``chat.completions.create`` / 假 LLM 逐轮记录请求，不联网、零 Key。

_TOOL_CALL: Dict[str, Any] = {
    "id": "c1",
    "name": "write",
    "arguments": {"path": "a.txt", "content": "x"},
}


class _RecordingLLM(LLMClient):
    """按轮返回脚本化响应，并把每次收到的 messages 记下来。"""

    def __init__(self, responses: List[LLMResponse]) -> None:
        self._responses = list(responses)
        self.requests: List[List[Dict[str, Any]]] = []

    def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        self.requests.append([dict(m) for m in messages])
        return self._responses.pop(0)

    def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
        **kwargs: Any,
    ):
        yield self.complete(messages, tools, **kwargs)


def _runtime(settings: Settings, llm: LLMClient) -> AgentRuntime:
    """A runtime whose executor can be driven directly (no tools, no confirm gate)."""
    tm = SimpleNamespace(settings=settings, event_bus=EventBus(), add_artifact=lambda *a: None)
    return AgentRuntime(
        task_id="t45",
        task_manager=tm,
        llm=llm,
        tools=[],
        tool_schemas=[{"type": "function"}],
        confirm_enabled=False,
    )


def _state() -> AgentState:
    return {
        "step_index": 1,
        "steps": [{"index": 1, "thought": "", "tool_calls": [], "status": "running"}],
        "messages": [{"role": "user", "content": "写一个文件"}],
    }


def _assistants(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [m for m in messages if m.get("role") == "assistant"]


def _stub_completion(monkeypatch, client: OpenAICompatibleClient, message: SimpleNamespace) -> None:
    """Replace the SDK call so ``complete()`` sees exactly ``message`` back."""

    def _create(**_kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], model_dump=lambda: {})

    monkeypatch.setattr(client._client.chat.completions, "create", _create)


def test_llm_response_reasoning_content_defaults_to_empty():
    """基线：默认构造为 ``""``，现役构造点与 nodes.py 的组装都不必改。"""
    assert LLMResponse().reasoning_content == ""


def test_openai_compat_complete_reads_reasoning_content(monkeypatch):
    client = OpenAICompatibleClient(
        base_url="https://api.deepseek.com/v1", api_key="sk-test", model="deepseek-flash"
    )
    _stub_completion(
        monkeypatch,
        client,
        SimpleNamespace(content="答案", tool_calls=None, reasoning_content="先拆解任务"),
    )
    resp = client.complete([{"role": "user", "content": "hi"}])
    assert resp.reasoning_content == "先拆解任务"


def test_openai_compat_complete_normalises_missing_reasoning_content(monkeypatch):
    """属性缺失与显式 null 两种形态都归一成 ``""``（DashScope / OpenAI 现状）。"""
    client = OpenAICompatibleClient(
        base_url="https://dashscope/v1", api_key="sk-test", model="deepseek-v4.1-flash"
    )

    _stub_completion(monkeypatch, client, SimpleNamespace(content="答案", tool_calls=None))
    assert client.complete([{"role": "user", "content": "hi"}]).reasoning_content == ""

    _stub_completion(
        monkeypatch, client, SimpleNamespace(content="答案", tool_calls=None, reasoning_content=None)
    )
    assert client.complete([{"role": "user", "content": "hi"}]).reasoning_content == ""


def test_executor_sends_previous_reasoning_content_back(settings):
    """本 bug 的回归测试：第 2 轮请求里的 assistant 消息带上第 1 轮的思考内容。"""
    llm = _RecordingLLM(
        [
            LLMResponse(content="", tool_calls=[_TOOL_CALL], reasoning_content="先想清楚写什么"),
            LLMResponse(content="已经写好了"),
        ]
    )
    rt = _runtime(settings, llm)
    state = _state()
    rt.executor(state)  # turn 1 -> tool call
    rt.executor(state)  # turn 2

    assert _assistants(llm.requests[1])[-1]["reasoning_content"] == "先想清楚写什么"


def test_executor_final_answer_turn_carries_reasoning_content(settings):
    """executor 的两处 assistant 组装（工具轮 / 最终答案轮）都回传。"""
    llm = _RecordingLLM(
        [
            LLMResponse(content="", tool_calls=[_TOOL_CALL], reasoning_content="思考一"),
            LLMResponse(content="已经写好了", reasoning_content="思考二"),
        ]
    )
    rt = _runtime(settings, llm)
    state = _state()
    rt.executor(state)
    rt.executor(state)

    assert [a.get("reasoning_content") for a in _assistants(state["messages"])] == ["思考一", "思考二"]


def test_executor_omits_reasoning_content_key_when_provider_has_none(settings):
    """护栏：不回该字段的端点必须逐字节保持现状——请求里不出现这个键。"""
    llm = _RecordingLLM(
        [
            LLMResponse(content="", tool_calls=[_TOOL_CALL]),
            LLMResponse(content="已经写好了"),
        ]
    )
    rt = _runtime(settings, llm)
    state = _state()
    rt.executor(state)
    rt.executor(state)

    assert "reasoning_content" not in _assistants(llm.requests[1])[-1]
    assert all("reasoning_content" not in a for a in _assistants(state["messages"]))
