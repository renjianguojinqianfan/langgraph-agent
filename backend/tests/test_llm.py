"""Tests for the LLM abstraction layer.

Covers :class:`MockLLMClient` behaviour and the :func:`create_llm_client`
factory returning the correct implementation for each provider configuration.
All of this runs offline (no API keys, no network).
"""

from __future__ import annotations

import json

from backend.config import Settings
from backend.core.llm.client import (
    LLMResponse,
    MockLLMClient,
    make_default_mock_client,
)
from backend.core.llm.openai_compat import (
    OpenAICompatibleClient,
    create_llm_client,
)


# ── LLMResponse ──
def test_llm_response_defaults():
    resp = LLMResponse(content="hi")
    assert resp.content == "hi"
    assert resp.tool_calls == []
    assert resp.raw == {}


def test_llm_response_carries_tool_calls_and_raw():
    tc = {"id": "1", "name": "file_io", "arguments": {}}
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
        {"id": "c1", "name": "file_io", "arguments": {"action": "write", "path": "a.txt"}},
        {"id": "c2", "name": "code_exec", "arguments": {"language": "python", "code": "1+1"}},
    ]
    mock = MockLLMClient(plan=["p"], tool_calls=tool_calls, final_answer="all done")

    r1 = mock.complete([], tools=[{"type": "function"}])
    r2 = mock.complete([], tools=[{"type": "function"}])
    r3 = mock.complete([], tools=[{"type": "function"}])

    assert r1.tool_calls[0]["name"] == "file_io"
    assert r2.tool_calls[0]["name"] == "code_exec"
    assert r3.tool_calls == []            # script exhausted
    assert r3.content == "all done"       # now a final answer


def test_mock_resets_executor_turn_counter():
    mock = MockLLMClient(plan=["p"], tool_calls=[{"id": "c1", "name": "file_io", "arguments": {}}], final_answer="x")
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
    assert any(tc["name"] == "file_io" for tc in mock.tool_calls)


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
