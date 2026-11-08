"""#267 P2-4a: reasoning visibility (D-38) + cross-provider stripping (D-39).

The raw reasoning must stay on the protocol message so the next turn can
replay it; only the redacted, bounded copy may reach SSE / StepRecord / trace.
D-39 keeps that replay honest across a model switch: same provider+protocol
keeps the trace, a different provider/protocol strips it from the request copy
while the display copy in StepRecord.reasoning survives.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any, Dict, List

from backend.core.agent.nodes import AgentRuntime
from backend.core.llm.client import LLMClient, LLMResponse, MockLLMClient
from backend.services.task_manager import REASONING_TRUNCATE_LIMIT
from backend.tests.conftest import make_manager, make_settings

SENTINEL = "reasoning-key-sentinel-do-not-leak"
MARKER = "_reasoning_from"


def _wait_parked(tm, task_id, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = tm.get_task(task_id)
        if task is not None and task.status.value in ("INTERRUPTED", "FAILED"):
            return task
        time.sleep(0.02)
    raise AssertionError("task never parked")


class _ReasoningLLM(LLMClient):
    """Scripted client that records every request's message list."""

    def __init__(self, responses: List[LLMResponse]) -> None:
        self._responses = list(responses)
        self.requests: List[List[Dict[str, Any]]] = []

    def complete(self, messages, tools=None, **kwargs) -> LLMResponse:
        self.requests.append([dict(message) for message in messages])
        if not self._responses:
            return LLMResponse(content="done")
        return self._responses.pop(0)

    def stream(self, messages, tools=None, **kwargs):
        yield self.complete(messages, tools, **kwargs)


class _RecordingBus:
    def __init__(self) -> None:
        self.events: List[tuple[str, Dict[str, Any]]] = []

    def publish(self, task_id, event_type, data):
        self.events.append((event_type, dict(data)))


def _state() -> Dict[str, Any]:
    return {
        "step_index": 0,
        "steps": [],
        "messages": [{"role": "user", "content": "hi"}],
        "mode": "build",
    }


# ── D-38: display copy (redact + truncate) ──
def test_redact_reasoning_redacts_keys_and_truncates_at_8000(tmp_path):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient())
    tm.models.set_provider_key("default", SENTINEL)

    assert tm.redact_reasoning(SENTINEL + " plan") == "[redacted] plan"
    assert tm.redact_reasoning("a" * REASONING_TRUNCATE_LIMIT) == (
        "a" * REASONING_TRUNCATE_LIMIT
    )
    truncated = tm.redact_reasoning("a" * (REASONING_TRUNCATE_LIMIT + 1))
    assert len(truncated) == REASONING_TRUNCATE_LIMIT


def test_step_reasoning_is_redacted_and_truncated_on_persist(tmp_path):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient())
    tm.models.set_provider_key("default", SENTINEL)

    step = tm._redact_step(
        {
            "index": 1,
            "round": 1,
            "thought": "answer",
            "reasoning": SENTINEL + "b" * (REASONING_TRUNCATE_LIMIT + 100),
            "tool_calls": [],
            "status": "done",
        }
    )

    assert SENTINEL not in step["reasoning"]
    assert "[redacted]" in step["reasoning"]
    assert len(step["reasoning"]) == REASONING_TRUNCATE_LIMIT


def test_final_answer_event_carries_optional_redacted_reasoning(tmp_path):
    bus = _RecordingBus()

    def redact(text: str) -> str:
        return text.replace(SENTINEL, "[redacted]")

    tm = SimpleNamespace(
        settings=make_settings(tmp_path),
        event_bus=bus,
        redact=redact,
        redact_reasoning=redact,
    )
    runtime = AgentRuntime(
        "t-reasoning",
        tm,
        llm=_ReasoningLLM(
            [LLMResponse(content="done", reasoning_content=SENTINEL + " think")]
        ),
        tools=[],
        tool_schemas=[],
        reasoning_scope="default:openai-compatible",
    )

    state = _state()
    runtime.agent(state)

    finals = [data for kind, data in bus.events if kind == "final_answer"]
    assert finals[-1]["answer"] == "done"
    assert finals[-1]["reasoning"] == "[redacted] think"
    assert SENTINEL not in json.dumps([data for _, data in bus.events])
    # D-38 rule 2: the raw trace stays on the protocol message in state.
    assert state["steps"][-1]["reasoning"] == SENTINEL + " think"

    # A non-thinking model emits exactly the pre-#267 payload shape.
    quiet_bus = _RecordingBus()
    quiet_tm = SimpleNamespace(
        settings=make_settings(tmp_path),
        event_bus=quiet_bus,
        redact=redact,
        redact_reasoning=redact,
    )
    quiet_runtime = AgentRuntime(
        "t-quiet",
        quiet_tm,
        llm=_ReasoningLLM([LLMResponse(content="ok")]),
        tools=[],
        tool_schemas=[],
        reasoning_scope="default:openai-compatible",
    )
    quiet_state = _state()
    quiet_runtime.agent(quiet_state)
    quiet_finals = [data for kind, data in quiet_bus.events if kind == "final_answer"]
    assert quiet_finals[-1] == {"answer": "ok"}
    assert quiet_state["steps"][-1]["reasoning"] == ""


# ── D-39: provenance marker + request-copy stripping ──
def test_request_history_keeps_same_scope_and_strips_foreign_reasoning(tmp_path):
    runtime = AgentRuntime(
        "t",
        SimpleNamespace(settings=make_settings(tmp_path), event_bus=_RecordingBus()),
        llm=MockLLMClient(),
        tools=[],
        tool_schemas=[],
        reasoning_scope="p1:openai-compatible",
    )
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "a",
            "reasoning_content": "keep",
            MARKER: "p1:openai-compatible",
        },
        {
            "role": "assistant",
            "content": "b",
            "reasoning_content": "foreign-provider",
            MARKER: "p2:openai-compatible",
        },
        {
            "role": "assistant",
            "content": "c",
            "reasoning_content": "foreign-protocol",
            MARKER: "p1:anthropic",
        },
        {"role": "assistant", "content": "d", "reasoning_content": "legacy-no-marker"},
    ]

    history = runtime._request_history(messages)

    assert history[1]["reasoning_content"] == "keep"
    assert "reasoning_content" not in history[2]
    assert "reasoning_content" not in history[3]
    # Pre-upgrade checkpoints have no provenance: sent as-is (documented boundary).
    assert history[4]["reasoning_content"] == "legacy-no-marker"
    dumped = json.dumps(history)
    assert MARKER not in dumped
    # state.messages keeps the raw trace + marker untouched.
    assert messages[1]["reasoning_content"] == "keep"
    assert messages[1][MARKER] == "p1:openai-compatible"


def test_same_provider_multi_round_replays_and_cross_provider_strips(
    tmp_path, monkeypatch
):
    settings = make_settings(tmp_path, checkpoint_enabled=True)
    llm = _ReasoningLLM(
        [
            LLMResponse(content="answer 1", reasoning_content="reasoning 1"),
            LLMResponse(content="answer 2", reasoning_content="reasoning 2"),
            LLMResponse(content="answer 3", reasoning_content="reasoning 3"),
        ]
    )
    tm = make_manager(settings, llm)

    events: List[tuple[str, Dict[str, Any]]] = []
    original_publish = tm.event_bus.publish

    def record(task_id, event_type, data):
        events.append((event_type, dict(data)))
        return original_publish(task_id, event_type, data)

    monkeypatch.setattr(tm.event_bus, "publish", record)

    task_id = tm.create_task("t", "first")
    _wait_parked(tm, task_id)
    assert [step.reasoning for step in tm.get_task(task_id).steps] == ["reasoning 1"]

    # Round 2 on the same provider: the previous trace is replayed verbatim.
    tm.send_message(task_id, "second")
    _wait_parked(tm, task_id)
    round_two = llm.requests[1]
    assert any(
        message.get("reasoning_content") == "reasoning 1" for message in round_two
    )
    assert MARKER not in json.dumps(round_two)

    # Round 3 after switching to a different provider: old traces are stripped
    # from the request copy, but the display copies stay on the step records.
    tm.models.add_provider(
        id="other", name="Other", base_url="https://other.test/v1"
    )
    other_entry = tm.models.add_model("other", model="m")
    tm.set_model(task_id, other_entry.id)
    tm.send_message(task_id, "third")
    _wait_parked(tm, task_id)

    round_three = llm.requests[2]
    assert all("reasoning_content" not in message for message in round_three)
    assert MARKER not in json.dumps(round_three)

    task = tm.get_task(task_id)
    assert [step.reasoning for step in task.steps] == [
        "reasoning 1",
        "reasoning 2",
        "reasoning 3",
    ]

    # The internal marker never leaves the graph: not in SSE payloads, not in
    # the persisted task JSON, not in the trace log.
    assert MARKER not in json.dumps([data for _, data in events])
    assert MARKER not in json.dumps(task.model_dump(mode="json"))
    assert MARKER not in (settings.data_path / "tasks.json").read_text(encoding="utf-8")
    trace_files = list(settings.trace_path.glob("*.jsonl"))
    for trace_file in trace_files:
        assert MARKER not in trace_file.read_text(encoding="utf-8")
    finals = [data for kind, data in events if kind == "final_answer"]
    assert [data.get("reasoning") for data in finals] == [
        "reasoning 1",
        "reasoning 2",
        "reasoning 3",
    ]
