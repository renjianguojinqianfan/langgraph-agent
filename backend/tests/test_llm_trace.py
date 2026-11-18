"""#333 S7: model calls are captured into the existing task trace.

The seam under test is ``LLMClient.complete`` plus the provider HTTP hooks:
a real ``httpx.MockTransport`` answers, the trace sink sees the actual request
and response per attempt, and the SSE summary stays lightweight.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Dict, List

import httpx
import pytest

from backend.core.agent.nodes import AgentRuntime
from backend.core.llm.anthropic_native import AnthropicClient
from backend.core.llm.client import MockLLMClient
from backend.core.llm.openai_compat import OpenAICompatibleClient
from backend.core.llm.trace import llm_call_scope
from backend.services.event_bus import EventBus
from backend.services.trace import TraceRecorder
from backend.tests.conftest import make_agent_host, make_settings


def _sink(events: List[Dict[str, Any]]):
    def record(event_type: str, data: Dict[str, Any]) -> bool:
        events.append({"type": event_type, "data": data})
        return True

    return record


def _openai_ok(content: str = "hi") -> Dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
    }


def _anthropic_ok(text: str = "hello") -> Dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "model": "claude-3",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 4, "output_tokens": 5},
    }


def _openai_client(handler) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        "https://gw.test/v1",
        "sk-canary",
        "m",
        5,
        transport=httpx.MockTransport(handler),
    )


def _final(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    completed = [event["data"] for event in events if not event["data"]["pending"]]
    assert completed, events
    return completed[-1]


def test_openai_success_records_actual_request_response_and_usage():
    actual: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        actual.append(request)
        return httpx.Response(200, json=_openai_ok())

    events: List[Dict[str, Any]] = []
    published: List[Dict[str, Any]] = []
    client = _openai_client(handler)
    with llm_call_scope(
        owner_task_id="task-1",
        source="main",
        round_no=2,
        step=5,
        sink=_sink(events),
        publish_summary=published.append,
        redact=lambda text: text.replace("sk-canary", "[redacted]"),
    ):
        response = client.complete(
            [{"role": "user", "content": "hello canary sk-canary"}],
            tools=[{"type": "function", "function": {"name": "shell", "parameters": {}}}],
        )

    assert response.content == "hi"
    assert len(actual) == 1
    wire = json.loads(actual[0].content)
    record = _final(events)
    assert record["protocol"] == "openai-compatible"
    assert record["owner_task_id"] == "task-1"
    assert record["source"] == "main"
    assert record["round"] == 2
    assert record["step"] == 5
    assert record["status"] == "ok"
    assert record["attempt"] == 1
    assert record["request"]["url"] == "https://gw.test/v1/chat/completions"
    assert record["request"]["body"]["model"] == wire["model"]
    assert record["request"]["body"]["messages"][0]["content"] == "hello canary [redacted]"
    assert record["request"]["body"]["tools"] == wire["tools"]
    assert "authorization" not in {key.lower() for key in record["request"]["headers"]}
    assert "sk-canary" not in json.dumps(record, ensure_ascii=False)
    assert "sk-canary" in actual[0].content.decode("utf-8")  # the real request survived
    assert record["response"]["status"] == 200
    assert record["response"]["body"]["choices"][0]["message"]["content"] == "hi"
    assert record["usage"] == {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}
    assert record["truncated"] is False

    summary = published[-1]
    assert summary["call_id"] == record["call_id"]
    assert summary["status"] == "ok"
    assert summary["task_id"] == "task-1"
    assert summary["attempts"] == 1
    assert "request" not in summary and "response" not in summary
    assert "sk-canary" not in json.dumps(summary, ensure_ascii=False)


def test_openai_http_error_records_the_response_and_error(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key sk-canary"}})

    events: List[Dict[str, Any]] = []
    client = _openai_client(handler)
    with caplog.at_level("INFO"):
        with llm_call_scope(
            owner_task_id="task-1",
            source="main",
            sink=_sink(events),
            redact=lambda text: text.replace("sk-canary", "[redacted]"),
        ):
            with pytest.raises(Exception):
                client.complete([{"role": "user", "content": "hi"}])

    record = _final(events)
    assert record["status"] == "error"
    assert record["response"]["status"] == 401
    assert "sk-canary" not in json.dumps(record, ensure_ascii=False)
    assert "sk-canary" not in caplog.text
    assert record["usage"] is None


def test_sdk_retries_are_separate_attempts_of_one_call():
    seen = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen
        seen += 1
        if seen == 1:
            return httpx.Response(500, json={"error": {"message": "retry me"}})
        return httpx.Response(200, json=_openai_ok("second"))

    events: List[Dict[str, Any]] = []
    client = _openai_client(handler)
    with llm_call_scope(owner_task_id="task-1", source="main", sink=_sink(events)):
        assert client.complete([{"role": "user", "content": "hi"}]).content == "second"

    final_rows = [event["data"] for event in events if not event["data"]["pending"]]
    assert [(row["attempt"], row["status"]) for row in final_rows] == [
        (1, "error"),
        (2, "ok"),
    ]
    assert len({row["call_id"] for row in final_rows}) == 1


def test_timeout_is_recorded_as_an_error_without_inventing_a_response():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow", request=request)

    events: List[Dict[str, Any]] = []
    client = _openai_client(handler)
    with llm_call_scope(owner_task_id="task-1", source="main", sink=_sink(events)):
        with pytest.raises(Exception):
            client.complete([{"role": "user", "content": "hi"}])

    record = _final(events)
    assert record["status"] == "error"
    assert record["response"] is None
    assert record["error"]


def test_anthropic_uses_the_same_record_contract():
    actual: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        actual.append(request)
        return httpx.Response(200, json=_anthropic_ok())

    events: List[Dict[str, Any]] = []
    client = AnthropicClient(
        "https://api.anthropic.com",
        "sk-ant-canary",
        "claude-3",
        5,
        transport=httpx.MockTransport(handler),
    )
    with llm_call_scope(
        owner_task_id="task-2",
        source="subagent",
        sink=_sink(events),
        redact=lambda text: text.replace("sk-ant-canary", "[redacted]"),
    ):
        response = client.complete([{"role": "user", "content": "hi"}])

    assert response.content == "hello"
    wire = json.loads(actual[0].content)
    record = _final(events)
    assert record["protocol"] == "anthropic"
    assert record["source"] == "subagent"
    assert record["request"]["url"] == "https://api.anthropic.com/v1/messages"
    assert record["request"]["body"]["model"] == wire["model"]
    assert "x-api-key" not in {key.lower() for key in record["request"]["headers"]}
    assert "sk-ant-canary" not in json.dumps(record, ensure_ascii=False)
    assert record["response"]["body"]["content"][0]["text"] == "hello"
    assert record["usage"] == {"input_tokens": 4, "output_tokens": 5}


def test_mock_calls_are_marked_without_faking_an_http_attempt():
    events: List[Dict[str, Any]] = []
    with llm_call_scope(owner_task_id="task-3", source="compaction", sink=_sink(events)):
        assert MockLLMClient(final_answer="mock done").complete(
            [{"role": "user", "content": "summarise"}]
        ).content == "mock done"

    record = _final(events)
    assert record["protocol"] == "mock"
    assert record["status"] == "ok"
    assert record["attempt"] == 1
    assert record["response"] is None
    assert record["usage"] is None
    assert record["request"]["messages"][0]["content"] == "summarise"


def test_two_tasks_sharing_one_client_keep_their_own_attribution():
    barrier = threading.Barrier(2)
    records: Dict[str, List[Dict[str, Any]]] = {"a": [], "b": []}

    def handler(request: httpx.Request) -> httpx.Response:
        barrier.wait(timeout=5)
        body = json.loads(request.content)
        return httpx.Response(200, json=_openai_ok(body["messages"][0]["content"]))

    client = _openai_client(handler)

    def run(owner: str) -> None:
        with llm_call_scope(owner_task_id=owner, source="main", sink=_sink(records[owner])):
            assert client.complete([{"role": "user", "content": owner}]).content == owner

    threads = [threading.Thread(target=run, args=(owner,)) for owner in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    for owner in ("a", "b"):
        row = _final(records[owner])
        assert row["owner_task_id"] == owner
        assert row["request"]["body"]["messages"][0]["content"] == owner


def test_a_failed_trace_write_does_not_change_the_model_result(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openai_ok("still works"))

    def broken_sink(_event_type: str, _data: Dict[str, Any]) -> bool:
        return False

    client = _openai_client(handler)
    with caplog.at_level("WARNING", logger="llm.trace"):
        with llm_call_scope(owner_task_id="task-4", source="main", sink=broken_sink):
            assert client.complete([{"role": "user", "content": "hi"}]).content == "still works"

    assert "llm_call not recorded" in caplog.text


def test_the_placeholder_survives_as_aborted_when_the_call_is_interrupted():
    events: List[Dict[str, Any]] = []
    recorder = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal recorder
        # Simulate a process death after the request left but before a response:
        # the placeholder written by the request hook is all that remains.
        recorder = None
        raise KeyboardInterrupt

    client = _openai_client(handler)
    with llm_call_scope(owner_task_id="task-5", source="main", sink=_sink(events)):
        with pytest.raises(KeyboardInterrupt):
            client.complete([{"role": "user", "content": "hi"}])

    assert [event["data"]["status"] for event in events] == ["aborted", "aborted"]
    assert events[0]["data"]["pending"] is True
    assert events[1]["data"]["pending"] is False
    assert events[1]["data"]["response"] is None


def test_trace_recorder_log_event_follows_the_existing_lifecycle(tmp_path):
    settings = make_settings(tmp_path)
    recorder = TraceRecorder(settings)
    recorder.attach(EventBus(), "t1")

    assert recorder.log_event("t1", "llm_call", {"call_id": "c1"}) is True
    recorder.close("t1")
    path = recorder.file_path("t1")
    before = path.read_text(encoding="utf-8")
    assert recorder.log_event("t1", "llm_call", {"call_id": "late"}) is False
    assert path.read_text(encoding="utf-8") == before

    path.unlink()
    assert recorder.log_event("t1", "llm_call", {"call_id": "after-delete"}) is False
    assert not path.exists()


def test_main_subagent_and_compaction_calls_are_attributed(tmp_path):
    settings = make_settings(
        tmp_path,
        context_compress_strategy="summarize",
        context_token_budget=10,
        context_keep_recent=1,
        context_max_messages=100,
    )
    bus = EventBus()
    recorder = TraceRecorder(settings)
    recorder.attach(bus, "t1")
    summaries: List[Dict[str, Any]] = []
    host = make_agent_host(
        settings,
        bus,
        trace_sink=lambda task_id: (
            lambda event_type, data: recorder.log_event(task_id, event_type, data)
        ),
        publish_llm_call=lambda task_id, payload: summaries.append(payload),
    )
    runtime = AgentRuntime(
        task_id="t1",
        task_manager=host,
        llm=MockLLMClient(final_answer="main done"),
        tools=[],
        tool_schemas=[],
    )
    runtime.agent(
        {
            "messages": [{"role": "user", "content": "hi"}],
            "steps": [],
            "step_index": 0,
            "round_no": 1,
            "round_steps": 0,
            "status": "RUNNING",
        }
    )

    subagent = AgentRuntime(
        task_id="sub1",
        task_manager=host,
        llm=MockLLMClient(final_answer="sub done"),
        tools=[],
        tool_schemas=[],
        trace_source="subagent",
        trace_owner="t1",
    )
    subagent.agent(
        {
            "messages": [{"role": "user", "content": "do it"}],
            "steps": [],
            "step_index": 0,
            "round_no": 1,
            "round_steps": 0,
            "status": "RUNNING",
        }
    )

    runtime._build_messages(
        {
            "messages": [
                {"role": "user" if index % 2 == 0 else "assistant", "content": "x" * 4000}
                for index in range(12)
            ],
            "step_index": 9,
            "round_no": 5,
            "steps": [],
        },
        "SYS",
    )
    recorder.close("t1")

    rows = [
        json.loads(line)["data"]
        for line in recorder.file_path("t1").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    calls = [row for row in rows if row.get("call_id") and not row.get("pending")]
    sources = {(row["source"], row["owner_task_id"]) for row in calls}
    assert ("main", "t1") in sources
    assert ("subagent", "t1") in sources
    assert ("compaction", "t1") in sources
    compaction = next(row for row in calls if row["source"] == "compaction")
    assert compaction["round"] == 5
    assert compaction["step"] == 9
    assert summaries and all("task_id" in item for item in summaries)
