"""M1: model profiles and credentials (ADR 0010 decisions 7–8)."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.core.llm.anthropic_native import AnthropicClient
from backend.core.llm.client import LLMClient, LLMResponse, MockLLMClient
from backend.core.llm.openai_compat import OpenAICompatibleClient
from backend.main import app
from backend.services.model_store import (
    PROTOCOL_ANTHROPIC,
    CredentialsError,
    ModelStore,
    UnsupportedModelsFileError,
)
from backend.tests.conftest import make_manager, make_settings

SENTINEL = "model-key-sentinel-do-not-leak"


def _store(tmp_path, **overrides):
    settings = make_settings(tmp_path, **overrides)
    return ModelStore(settings, home_dir=tmp_path / "home"), settings


def _add_provider_and_model(
    tm, *, provider_id="second", model="m2", base_url="https://example.test/v1"
):
    """One catalog entry under its own provider (the only model write face)."""
    tm.models.add_provider(
        id=provider_id,
        name=provider_id.title(),
        base_url=base_url,
        api_key_env="SECOND_KEY",
    )
    return tm.models.add_model(provider_id, model=model)


def test_seeded_from_env_settings(tmp_path):
    store, settings = _store(tmp_path)
    assert store.default_id == "default"
    resolved = store.resolve(None)
    assert resolved is not None
    assert resolved.model == settings.llm_model
    assert resolved.base_url == "https://api.deepseek.com/v1"  # DeepSeek is the only preset
    assert store.get_provider("default").preset is True
    assert (settings.data_path / "models.json").is_file()


def test_custom_base_url_is_not_a_preset(tmp_path):
    store, _ = _store(tmp_path, llm_base_url="https://gw.test/v1")
    assert store.get_provider("default").preset is False


def test_catalog_default_and_delete_guard(tmp_path):
    store, _ = _store(tmp_path)
    store.add_provider(id="p2", name="Second", base_url="https://example.test/v1")
    second = store.add_model("p2", model="m2")
    store.update_model("p2", second.id, name="Second edited", model="m2b")
    assert store.get_model(second.id).name == "Second edited"
    store.set_default(second.id)
    assert store.default_id == second.id
    store.delete_model("p2", second.id)
    assert store.default_id == "default"
    with pytest.raises(ValueError):
        store.delete_model("default", "default")


def test_key_order_is_pasted_then_environment(tmp_path, monkeypatch):
    store, _ = _store(tmp_path)
    provider = store.get_provider("default")
    monkeypatch.setenv(provider.api_key_env, "env-key")
    assert store.provider_key_for(provider.id) == "env-key"

    store.set_provider_key(provider.id, SENTINEL)
    assert store.provider_key_for(provider.id) == SENTINEL

    store.set_provider_key(provider.id, "")
    assert store.provider_key_for(provider.id) == "env-key"


def test_key_source_is_reported_without_the_key(tmp_path, monkeypatch):
    store, _ = _store(tmp_path)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert store.provider_public("default")["key_source"] == "none"
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    assert store.provider_public("default")["key_source"] == "env"

    store.set_provider_key("default", SENTINEL)
    entry = store.provider_public("default")
    assert entry["key_source"] == "pasted"
    assert entry["key_set"] is True
    assert SENTINEL not in json.dumps(entry)


def test_update_rejects_without_mutating(tmp_path):
    store, _ = _store(tmp_path)
    before = asdict(store.get_provider("default"))
    with pytest.raises(ValueError):
        store.update_provider("default", base_url="")
    assert asdict(store.get_provider("default")) == before


def test_resolve_does_not_fall_back_for_an_explicit_unknown_id(tmp_path):
    store, _ = _store(tmp_path)
    assert store.resolve("deleted") is None
    assert store.resolve(None).id == "default"


def test_resolve_returns_the_runtime_model_with_inherited_params(tmp_path):
    """The resolved model is the one runtime view a call is built from: the
    entry id plus provider address/protocol and the *effective* D-36 values
    (entry override else provider default; None = client default)."""
    store, _ = _store(tmp_path)
    store.add_provider(
        id="gw",
        name="GW",
        base_url="https://gw.test/v1",
        api_key_env="GW_KEY",
        temperature=0.3,
        max_tokens=1024,
        thinking=False,
        reasoning_effort="low",
    )
    entry = store.add_model("gw", model="m1", temperature=0.9)

    resolved = store.resolve(entry.id)
    assert resolved.id == entry.id
    assert resolved.provider_id == "gw"
    assert resolved.name == "m1"
    assert resolved.model == "m1"
    assert resolved.protocol == "openai-compatible"
    assert resolved.base_url == "https://gw.test/v1"
    assert resolved.api_key_env == "GW_KEY"
    assert resolved.temperature == 0.9  # entry override wins
    assert resolved.max_tokens == 1024  # provider default inherited
    assert resolved.thinking is False
    assert resolved.reasoning_effort == "low"


def test_resolved_model_builds_both_protocol_clients(tmp_path, monkeypatch):
    """One resolution path feeds both adapters: the client built for a task is
    the resolved protocol/address/model with the inherited parameters."""
    settings = make_settings(tmp_path, use_mock_llm=False)
    tm = make_manager(settings, None)
    monkeypatch.setattr(tm, "_start_run", lambda task_id: None)
    tm.models.add_provider(
        id="gw",
        name="GW",
        base_url="https://gw.test/v1",
        api_key_env="GW_KEY",
        temperature=0.4,
        max_tokens=512,
        reasoning_effort="high",
    )
    openai_entry = tm.models.add_model("gw", model="m1")
    tm.models.add_provider(
        id="claude",
        name="Claude",
        protocol=PROTOCOL_ANTHROPIC,
        base_url="https://api.anthropic.test/v1",
        api_key_env="CLAUDE_KEY",
        max_tokens=2048,
    )
    anthropic_entry = tm.models.add_model("claude", model="claude-3", temperature=0.5)
    tm.models.set_provider_key("claude", "sk-claude")

    task_id = tm.create_task("t", "hi", model=openai_entry.id)
    openai_client = tm.llm_for_task_id(task_id)
    assert isinstance(openai_client, OpenAICompatibleClient)
    assert openai_client.base_url == "https://gw.test/v1"
    assert openai_client.model == "m1"
    assert openai_client.temperature == 0.4
    assert openai_client.max_tokens == 512
    assert openai_client.reasoning_effort == "high"

    tm.set_model(task_id, anthropic_entry.id)
    anthropic_client = tm.llm_for_task_id(task_id)
    assert isinstance(anthropic_client, AnthropicClient)
    assert anthropic_client.base_url == "https://api.anthropic.test/v1"
    assert anthropic_client.model == "claude-3"
    assert anthropic_client.temperature == 0.5
    assert anthropic_client.max_tokens == 2048


def test_corrupt_models_json_is_quarantined_not_overwritten(tmp_path):
    store, settings = _store(tmp_path)
    store.data_file.write_text("{not json", encoding="utf-8")
    reloaded = ModelStore(settings, home_dir=tmp_path / "home")
    assert reloaded.get_model("default") is not None
    assert list(settings.data_path.glob("models.json.corrupt-*"))


def test_key_never_in_models_json(tmp_path, monkeypatch):
    store, _ = _store(tmp_path)
    store.set_provider_key("default", SENTINEL)
    assert SENTINEL not in store.data_file.read_text(encoding="utf-8")
    assert SENTINEL in store.credentials_file.read_text(encoding="utf-8")
    assert store.credentials_file.parent.name == ".qianfan"


class _FakeClient:
    def __init__(self, profile, key):
        self.model = profile.model
        self.key = key

    def complete(self, messages, tools=None, **kwargs):
        return LLMResponse(content="done")


def _wait(tm, task_id, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = tm.get_task(task_id)
        if task and task.status.value in ("INTERRUPTED", "FAILED"):
            return task
        time.sleep(0.05)
    raise AssertionError("task never parked")


def test_task_model_switch_applies_to_the_next_round(tmp_path):
    settings = make_settings(tmp_path, checkpoint_enabled=True, use_mock_llm=False)
    made = []

    def factory(profile, key):
        made.append(profile.id)
        return _FakeClient(profile, key)

    tm = make_manager(settings, None, llm_factory=factory)
    second = _add_provider_and_model(tm)
    task_id = tm.create_task("m", "hi", model=second.id)
    _wait(tm, task_id)
    assert made[-1] == second.id

    default_id = tm.models.default_id
    tm.set_model(task_id, default_id)
    tm.send_message(task_id, "again")
    _wait(tm, task_id)
    assert made[-1] == default_id
    events = [e for e in tm.event_bus.replay(task_id) if e["type"] == "step_start"]
    assert any(e["data"].get("model") for e in events)
    assert events[-1]["data"].get("model_profile") == default_id


def test_compression_event_and_trace_record_the_round_model(tmp_path):
    settings = make_settings(
        tmp_path,
        use_mock_llm=False,
        trace_enabled=True,
        checkpoint_enabled=True,
        context_token_budget=1,
        context_keep_recent=1,
    )
    tm = make_manager(
        settings, None, llm_factory=lambda profile, key: _FakeClient(profile, key)
    )
    default_id = tm.models.default_id
    task_id = tm.create_task("m", "a" * 2000)
    _wait(tm, task_id)
    tm.send_message(task_id, "x" * 2000)
    _wait(tm, task_id)

    compressed = [
        e for e in tm.event_bus.replay(task_id) if e["type"] == "context_compressed"
    ]
    assert compressed
    assert compressed[-1]["data"]["model_profile"] == default_id
    assert compressed[-1]["data"]["model"]

    lines = [
        json.loads(line)
        for line in (settings.trace_path / f"{task_id}.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    step_starts = [e for e in lines if e["type"] == "step_start"]
    assert step_starts
    assert step_starts[-1]["data"]["model_profile"] == default_id
    assert step_starts[-1]["data"]["model"]
    traced = [e for e in lines if e["type"] == "context_compressed"]
    assert traced
    assert traced[-1]["data"]["model_profile"] == default_id


def test_subagent_uses_the_parent_round_model(tmp_path):
    settings = make_settings(tmp_path, use_mock_llm=False)
    made = []

    def factory(profile, key):
        made.append(profile.id)
        return _FakeClient(profile, key)

    tm = make_manager(settings, None, llm_factory=factory)
    second = _add_provider_and_model(tm)
    task_id = tm.create_task("m", "hi", model=second.id)
    _wait(tm, task_id)
    parent = tm.get_task(task_id)
    tm._pin_round_model(parent)  # the parent round is running with "m2"
    tm.set_model(task_id, tm.models.default_id)  # a mid-round switch

    from backend.core.agent.subagent import SubAgentExecutor, SubTaskSpec

    result = SubAgentExecutor(tm, settings).run_subtask(
        SubTaskSpec(subtask_id="sub-1", name="s", instruction="say hi", parent_task_id=task_id)
    )

    assert result.status == "completed", result.error
    assert made[-1] == second.id  # parent A -> child A, not the switched default


def test_provider_edit_and_key_change_replace_the_cached_client(tmp_path):
    settings = make_settings(tmp_path, use_mock_llm=False)
    made = []

    def factory(model, key):
        made.append((model.base_url, key))
        return _FakeClient(model, key)

    tm = make_manager(settings, None, llm_factory=factory)
    task_id = tm.create_task("m", "hi")
    _wait(tm, task_id)
    tm.llm_for_task_id(task_id)
    before = len(made)

    tm.update_provider("default", base_url="https://new.test/v1")
    tm.llm_for_task_id(task_id)
    assert len(made) == before + 1
    assert made[-1][0] == "https://new.test/v1"

    tm.update_provider("default", key="sk-new")
    tm.llm_for_task_id(task_id)
    assert made[-1][1] == "sk-new"


def test_deleted_model_makes_a_pinned_task_fail_loudly(tmp_path):
    settings = make_settings(tmp_path, use_mock_llm=False)
    tm = make_manager(settings, None, llm_factory=lambda model, key: _FakeClient(model, key))
    second = _add_provider_and_model(tm)
    task_id = tm.create_task("m", "hi", model=second.id)
    _wait(tm, task_id)

    tm.delete_model_entry("second", second.id)
    with pytest.raises(RuntimeError, match="no longer exists"):
        tm.llm_for_task_id(task_id)


class _LeakyClient:
    model = "leaky"

    def __init__(self, key):
        self.key = key

    def complete(self, messages, tools=None, **kwargs):
        raise RuntimeError(f"401 Authorization: Bearer {self.key}")


class _LeakyRecordingClient(LLMClient):
    """A provider client behind the real ``LLMClient.complete`` recorder."""

    protocol = "openai-compatible"
    model = "leaky-recording"

    def __init__(self, key: str) -> None:
        self.key = key

    def _complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        raise RuntimeError(f"401 Authorization: Bearer {self.key}")


def test_provider_error_key_never_reaches_task_trace_logs_or_sse(tmp_path, caplog):
    settings = make_settings(tmp_path, use_mock_llm=False, trace_enabled=True)
    store = ModelStore(settings, home_dir=tmp_path / "home")
    store.set_provider_key("default", SENTINEL)
    tm = make_manager(
        settings,
        None,
        llm_factory=lambda profile, key: _LeakyClient(key),
        model_store=store,
    )
    with caplog.at_level("DEBUG"):
        task_id = tm.create_task("t", "hi")
        task = _wait(tm, task_id)

    assert task.status.value == "FAILED"
    trace = (settings.trace_path / f"{task_id}.jsonl").read_text(encoding="utf-8")
    events = json.dumps(tm.event_bus.replay(task_id), ensure_ascii=False)
    assert SENTINEL not in json.dumps(task.model_dump(), ensure_ascii=False)
    assert SENTINEL not in trace
    assert SENTINEL not in events
    assert SENTINEL not in caplog.text


def test_the_recorded_provider_error_is_redacted_by_the_manager(tmp_path, caplog):
    """The production recorder path redacts with the manager's known secrets.

    `_LeakyClient` above overrides `complete()` and never enters the S7
    wrapper; this client implements `_complete()`, so the real
    `AgentRuntime._llm_scope -> TraceRecorder` path sees the provider error.
    """
    settings = make_settings(tmp_path, use_mock_llm=False, trace_enabled=True)
    store = ModelStore(settings, home_dir=tmp_path / "home")
    store.set_provider_key("default", SENTINEL)
    tm = make_manager(
        settings,
        None,
        llm_factory=lambda profile, key: _LeakyRecordingClient(key),
        model_store=store,
    )
    with caplog.at_level("DEBUG"):
        task_id = tm.create_task("t", "hi")
        task = _wait(tm, task_id)

    assert task.status.value == "FAILED"
    trace = (settings.trace_path / f"{task_id}.jsonl").read_text(encoding="utf-8")
    records = [
        json.loads(line)
        for line in trace.splitlines()
        if line.strip() and json.loads(line).get("type") == "llm_call"
    ]
    assert records, "the recording wrapper must produce an llm_call record"
    assert any("[redacted]" in json.dumps(record, ensure_ascii=False) for record in records)
    assert SENTINEL not in trace
    assert SENTINEL not in json.dumps(tm.event_bus.replay(task_id), ensure_ascii=False)
    assert SENTINEL not in caplog.text


def test_a_rotated_key_stays_redacted(tmp_path):
    settings = make_settings(tmp_path, use_mock_llm=False)
    tm = make_manager(settings, None, llm_factory=lambda model, key: _FakeClient(model, key))
    old = "old-key-sentinel"
    tm.update_provider("default", key=old)
    tm.llm_for_task_id("no-task")  # a live client now holds the old key
    assert tm.redact(f"bearer {old}") == "bearer [redacted]"

    tm.update_provider("default", key="new-key-sentinel")

    assert tm.redact(f"bearer {old}") == "bearer [redacted]"
    assert tm.redact("bearer new-key-sentinel") == "bearer [redacted]"


def test_redaction_covers_tool_result_dict_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", SENTINEL)
    settings = make_settings(tmp_path, use_mock_llm=False)
    tm = make_manager(settings, None, llm_factory=lambda profile, key: _FakeClient(profile, key))
    from backend.core.agent.nodes import AgentRuntime

    runtime = AgentRuntime("t", tm, llm=MockLLMClient(final_answer="ok"), tools=[], tool_schemas=[])
    redacted = runtime._redact_value(
        {f"x-{SENTINEL}": [SENTINEL, {"nested": SENTINEL}], "ok": "value"}
    )

    assert SENTINEL not in json.dumps(redacted)
    assert redacted["ok"] == "value"


def test_deleting_the_rounds_model_marks_the_task_failed(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, use_mock_llm=False)
    tm = make_manager(settings, None, llm_factory=lambda model, key: _FakeClient(model, key))
    second = _add_provider_and_model(tm)
    monkeypatch.setattr(tm, "_start_run", lambda task_id: None)
    task_id = tm.create_task("m", "hi", model=second.id)
    tm.delete_model_entry("second", second.id)

    tm.run(task_id)

    task = tm.get_task(task_id)
    assert task is not None
    assert task.status.value == "FAILED"
    assert "no longer exists" in (task.error or "")


def test_provider_edit_cannot_reinsert_the_old_cached_client(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, use_mock_llm=False)
    made = []

    def factory(model, key):
        made.append(model.base_url)
        return _FakeClient(model, key)

    tm = make_manager(settings, None, llm_factory=factory)
    second = _add_provider_and_model(tm, base_url="https://old.test/v1")
    task_id = tm.create_task("m", "hi", model=second.id)
    _wait(tm, task_id)
    tm.llm_for_task_id(task_id)  # cache the old address

    original = tm.models.resolve
    entered = threading.Event()
    release = threading.Event()
    calls = {"n": 0}

    def slow_resolve(model_id):
        calls["n"] += 1
        resolved = original(model_id)
        if calls["n"] == 1:
            entered.set()
            assert release.wait(5)
        return resolved

    monkeypatch.setattr(tm.models, "resolve", slow_resolve)
    reader = threading.Thread(target=lambda: tm.llm_for_task_id(task_id))
    reader.start()
    assert entered.wait(5)
    updater = threading.Thread(
        target=lambda: tm.update_provider("second", base_url="https://new.test/v1")
    )
    updater.start()
    time.sleep(0.2)
    release.set()
    reader.join(5)
    updater.join(5)

    tm.llm_for_task_id(task_id)

    assert made[-1] == "https://new.test/v1"


def test_summarizer_failure_is_redacted(tmp_path, caplog):
    settings = make_settings(tmp_path, use_mock_llm=False)
    tm = make_manager(settings, None, llm_factory=lambda model, key: _LeakyClient(key))
    tm.update_provider("default", key=SENTINEL)

    from backend.core.agent.context import compress_messages

    messages = [
        {"role": "user", "content": "x" * 5000},
        {"role": "assistant", "content": "y" * 100},
        {"role": "user", "content": "z" * 100},
    ]
    with caplog.at_level("DEBUG"):
        compressed, _ = compress_messages(
            messages,
            budget=1,
            keep_recent=1,
            strategy="summarize",
            llm=_LeakyClient(SENTINEL),
            redact=tm.redact,
        )

    assert SENTINEL not in json.dumps(compressed)
    assert SENTINEL not in caplog.text


def test_update_clears_preset_when_the_address_changes(tmp_path):
    store, _ = _store(tmp_path)
    assert store.get_provider("default").preset is True
    updated = store.update_provider("default", base_url="https://custom.test/v1")
    assert updated.preset is False


def test_provider_validation_rejects_bad_urls_and_env_names(tmp_path):
    store, _ = _store(tmp_path)
    with pytest.raises(ValueError):
        store.add_provider(id="x", name="x", base_url="ftp://example.test")
    with pytest.raises(ValueError):
        store.add_provider(
            id="x", name="x", base_url="https://example.test", api_key_env="not a name"
        )
    with pytest.raises(ValueError):
        store.update_provider("default", api_key_env="1INVALID")


def test_deleting_a_provider_revokes_the_pasted_key(tmp_path):
    store, _ = _store(tmp_path)
    store.add_provider(id="second", name="Second", base_url="https://example.test/v1")
    store.add_model("second", model="m2")
    store.set_provider_key("second", SENTINEL)
    store.delete_provider("second")
    assert SENTINEL not in store.credentials_file.read_text(encoding="utf-8")


def test_test_model_sends_one_request_through_the_factory(tmp_path):
    settings = make_settings(tmp_path, use_mock_llm=False)
    calls = []

    def factory(model, key):
        calls.append((model.id, key))
        return _FakeClient(model, key)

    tm = make_manager(settings, None, llm_factory=factory)
    tm.update_provider("default", key="sk-test")
    assert tm.test_model("default") == {"ok": True}
    assert calls[-1] == ("default", "sk-test")


def test_tool_output_echoing_a_key_is_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", SENTINEL)
    settings = make_settings(tmp_path, use_mock_llm=False, trace_enabled=True)
    script = MockLLMClient(
        plan=["p"],
        tool_calls=[
            {
                "id": "c1",
                "name": "shell",
                "arguments": {"shell": "bash", "command": "echo $LLM_API_KEY"},
            }
        ],
        final_answer="done",
    )
    tm = make_manager(settings, script)
    task_id = tm.create_task("t", "run echo")
    task = _wait(tm, task_id)

    trace = (settings.trace_path / f"{task_id}.jsonl").read_text(encoding="utf-8")
    events = json.dumps(tm.event_bus.replay(task_id), ensure_ascii=False)
    assert SENTINEL not in json.dumps(task.model_dump(), ensure_ascii=False)
    assert SENTINEL not in trace
    assert SENTINEL not in events
    assert tm.redact(SENTINEL) == "[redacted]"


def test_tool_args_and_final_answer_are_redacted(tmp_path, monkeypatch):
    """The model can print a key into its args or its answer; neither ships."""
    monkeypatch.setenv("LLM_API_KEY", SENTINEL)
    settings = make_settings(tmp_path, use_mock_llm=False, trace_enabled=True)
    script = MockLLMClient(
        tool_calls=[
            {
                "id": "c1",
                "name": "shell",
                "arguments": {"shell": "bash", "command": f"echo {SENTINEL}"},
            }
        ],
        final_answer=f"the key is {SENTINEL}",
    )
    tm = make_manager(settings, script)
    task_id = tm.create_task("t", "run echo")
    task = _wait(tm, task_id)

    trace = (settings.trace_path / f"{task_id}.jsonl").read_text(encoding="utf-8")
    events = json.dumps(tm.event_bus.replay(task_id), ensure_ascii=False)
    dump = json.dumps(task.model_dump(), ensure_ascii=False)
    assert SENTINEL not in dump
    assert SENTINEL not in trace
    assert SENTINEL not in events
    # Dispatch still used the raw argument: the command ran and its echo was
    # redacted on the way back into the record.
    outputs = [
        tc.output
        for step in task.steps
        for tc in step.tool_calls
        if tc.tool_name == "shell"
    ]
    assert outputs and "[redacted]" in json.dumps(outputs, ensure_ascii=False)


def _client(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, checkpoint_enabled=True)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    client = TestClient(app, base_url="http://localhost")
    client.__enter__()
    getattr(client.app, "state").task_manager = tm
    return client, tm


def test_models_api_never_returns_the_key(tmp_path):
    client, tm = _client(tmp_path, None)
    try:
        created = client.post(
            "/api/providers",
            json={
                "id": "bailian",
                "name": "Bailian",
                "base_url": "https://dashscope.test/v1",
                "api_key_env": "DASHSCOPE_API_KEY",
            },
        )
        assert created.status_code == 200
        entry = client.post(
            "/api/providers/bailian/models", json={"model": "qwen"}
        ).json()["data"]
        model_id = entry["id"]
        saved = client.put("/api/providers/bailian", json={"key": SENTINEL})
        assert saved.status_code == 200
        assert saved.json()["data"]["key_set"] is True
        assert saved.json()["data"]["key_source"] == "pasted"
        assert "key" not in saved.json()["data"]
        assert SENTINEL not in saved.text
        card = next(
            p
            for p in client.get("/api/providers").json()["data"]["providers"]
            if p["id"] == "bailian"
        )
        assert card["key_set"] is True
        assert card["key_source"] == "pasted"
        assert SENTINEL not in client.get("/api/providers").text
        assert SENTINEL not in client.get("/api/providers/bailian/models").text

        task_id = tm.create_task("t", "hi")
        task = client.get(f"/api/tasks/{task_id}")
        assert SENTINEL not in task.text
        assert client.put(f"/api/tasks/{task_id}/model", json={"model": model_id}).status_code == 200
        assert client.put(f"/api/tasks/{task_id}/model", json={"model": "nope"}).status_code == 409
        assert client.post(f"/api/models/{model_id}/default").status_code == 200
        assert client.get("/api/providers").json()["data"]["default"] == model_id
        tested = client.post(f"/api/models/{model_id}/test")
        assert tested.status_code == 200
        assert SENTINEL not in tested.text
    finally:
        client.__exit__(None, None, None)


def test_catalog_entry_update_and_delete_endpoints(tmp_path):
    client, tm = _client(tmp_path, None)
    try:
        created = client.post(
            "/api/providers",
            json={"id": "temp", "name": "Temp", "base_url": "https://example.test/v1"},
        )
        assert created.status_code == 200
        model_id = client.post(
            "/api/providers/temp/models", json={"model": "m1"}
        ).json()["data"]["id"]

        updated = client.put(
            f"/api/providers/temp/models/{model_id}",
            json={"name": "Renamed", "model": "m2"},
        )
        assert updated.status_code == 200
        entry = next(
            m
            for m in client.get("/api/providers/temp/models").json()["data"]["models"]
            if m["id"] == model_id
        )
        assert entry["name"] == "Renamed"
        assert entry["model"] == "m2"

        removed = client.delete(f"/api/providers/temp/models/{model_id}")
        assert removed.status_code == 200
        assert all(
            m["id"] != model_id
            for m in client.get("/api/providers/temp/models").json()["data"]["models"]
        )
    finally:
        client.__exit__(None, None, None)


def test_legacy_flat_model_surface_is_gone(tmp_path):
    """ADR 0013 decision 1: the flat `/api/models` read/CRUD face is refused;
    only the provider/catalog face remains, plus the entry-level default and
    connection-test actions the settings page actually calls."""
    client, tm = _client(tmp_path, None)
    try:
        assert client.get("/api/models").status_code == 404
        assert (
            client.post(
                "/api/models",
                json={"name": "x", "base_url": "https://x.test/v1", "model": "m"},
            ).status_code
            == 404
        )
        assert client.put("/api/models/default", json={"name": "x"}).status_code == 404
        assert client.delete("/api/models/default").status_code == 404
        assert client.put("/api/models/default/key", json={"key": "sk"}).status_code == 404
        # Live actions keyed by the model entry id stay.
        assert client.post("/api/models/default/default").status_code == 200
        assert client.post("/api/models/default/test").status_code == 200
    finally:
        client.__exit__(None, None, None)


def test_unknown_model_on_task_create_is_rejected(tmp_path):
    client, tm = _client(tmp_path, None)
    try:
        r = client.post("/api/tasks", json={"input": "hi", "model": "nope"})
        assert r.status_code == 400
    finally:
        client.__exit__(None, None, None)


# ── #244: a locked-down credentials directory is a readable 4xx, not a 500 ──
def _deny_credentials_write(monkeypatch):
    import backend.services.model_store as model_store_module

    def deny(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(model_store_module.os, "open", deny)


def test_credentials_write_failure_is_readable_and_keeps_the_provider(
    tmp_path, monkeypatch, caplog
):
    store, _ = _store(tmp_path)
    store.add_provider(id="second", name="Second", base_url="https://example.test/v1")
    store.add_model("second", model="m2")
    store.set_provider_key("second", SENTINEL)

    _deny_credentials_write(monkeypatch)

    with caplog.at_level("WARNING"):
        with pytest.raises(CredentialsError) as excinfo:
            store.set_provider_key("second", "sk-new")
    message = str(excinfo.value)
    assert str(store.credentials_file) in message
    assert "environment variable" in message
    # AC (#244): the detail reaches the log as well as the HTTP response.
    assert "credentials write failed" in caplog.text
    assert str(store.credentials_file) in caplog.text
    # The failed write left the previous pasted key intact.
    assert store.provider_key_for("second") == SENTINEL

    # Revoking a key is part of deleting a provider; failing to revoke must not
    # drop the provider (which would leave an orphaned secret on disk).
    with pytest.raises(CredentialsError):
        store.delete_provider("second")
    assert store.get_provider("second") is not None


def test_key_write_failure_returns_409_with_reason(tmp_path, monkeypatch):
    client, tm = _client(tmp_path, None)
    try:
        created = client.post(
            "/api/providers",
            json={"id": "temp", "name": "Temp", "base_url": "https://example.test/v1"},
        )
        assert created.status_code == 200

        _deny_credentials_write(monkeypatch)

        response = client.put("/api/providers/temp", json={"key": "sk-test"})
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert str(tm.models.credentials_file) in detail
        assert "environment variable" in detail
        assert tm.models.get_provider("temp") is not None
    finally:
        client.__exit__(None, None, None)

# ── ADR 0013 decision 1: v1 models.json is refused, never migrated ──
_V1_PROFILES = [
    {
        "id": "aaaabbbbcccc",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key_env": "LLM_API_KEY",
        "preset": True,
    },
    {
        "id": "ddddeeeeffff",
        "name": "Bailian",
        "base_url": "https://dashscope.test/v1",
        "model": "qwen-max",
        "api_key_env": "DASHSCOPE_API_KEY",
        "preset": False,
    },
]


def _write_v1_models(tmp_path, default="aaaabbbbcccc"):
    settings = make_settings(tmp_path)
    settings.data_path.mkdir(parents=True, exist_ok=True)
    payload = {"default": default, "models": _V1_PROFILES}
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    (settings.data_path / "models.json").write_text(text, encoding="utf-8")
    return settings, text


def test_v1_models_json_is_refused_and_left_byte_for_byte(tmp_path):
    settings, _ = _write_v1_models(tmp_path)
    before = (settings.data_path / "models.json").read_bytes()

    with pytest.raises(UnsupportedModelsFileError) as excinfo:
        ModelStore(settings, home_dir=tmp_path / "home")

    message = str(excinfo.value)
    assert "models.json" in message
    assert "schema_version" in message
    # Refusal never rewrites, renames or backs up the unsupported file.
    assert (settings.data_path / "models.json").read_bytes() == before
    assert not (settings.data_path / "models.json.bak-v1").exists()
    assert not list(settings.data_path.glob("models.json.corrupt-*"))


def test_schema_version_1_is_refused_without_touching_the_file(tmp_path):
    settings = make_settings(tmp_path)
    settings.data_path.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "default": "aaaabbbbcccc", "models": _V1_PROFILES}
    original = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    (settings.data_path / "models.json").write_bytes(original)

    with pytest.raises(UnsupportedModelsFileError):
        ModelStore(settings, home_dir=tmp_path / "home")

    assert (settings.data_path / "models.json").read_bytes() == original


def test_seeded_models_json_uses_schema_v2(tmp_path):
    store, _ = _store(tmp_path)
    raw = json.loads(store.data_file.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 2
    assert raw["default"] == "default"
    assert [provider["id"] for provider in raw["providers"]] == ["default"]
    assert raw["providers"][0]["protocol"] == "openai-compatible"
    assert [entry["id"] for entry in raw["models"]] == ["default"]
    assert raw["models"][0]["provider_id"] == "default"


def test_bad_v2_file_is_quarantined_and_reseeded(tmp_path):
    settings = make_settings(tmp_path)
    settings.data_path.mkdir(parents=True, exist_ok=True)
    (settings.data_path / "models.json").write_text(
        json.dumps({"schema_version": 2, "providers": [], "models": []}),
        encoding="utf-8",
    )

    store = ModelStore(settings, home_dir=tmp_path / "home")

    assert store.get_model("default") is not None
    assert list(settings.data_path.glob("models.json.corrupt-*"))
    assert json.loads(store.data_file.read_text(encoding="utf-8"))["schema_version"] == 2


# ── store-level Provider / catalog CRUD (unwired by A2 yet) ──
def test_provider_crud_store_level(tmp_path):
    store, _ = _store(tmp_path)
    # The seeded provider is the built-in DeepSeek preset.
    assert store.provider_public("default")["preset"] is True

    provider = store.add_provider(
        id="bailian",
        name="Bailian",
        base_url="https://dashscope.test/v1",
        api_key_env="DASHSCOPE_API_KEY",
    )
    assert provider.id == "bailian"
    assert store.get_provider("bailian") == provider
    assert store.provider_public("bailian")["key_set"] is False

    with pytest.raises(ValueError):
        store.add_provider(id="Bad_ID", name="x", base_url="https://x.test/v1")
    with pytest.raises(ValueError):
        store.add_provider(id="bailian", name="x", base_url="https://x.test/v1")
    with pytest.raises(ValueError):
        store.add_provider(id="grpc", name="x", base_url="https://x.test/v1", protocol="grpc")
    with pytest.raises(ValueError):
        store.add_provider(id="ftp", name="x", base_url="ftp://x.test/v1")
    with pytest.raises(KeyError):
        store.update_provider("nope", name="x")
    with pytest.raises(KeyError):
        store.delete_provider("nope")

    updated = store.update_provider(
        "bailian", name="Renamed", base_url="https://dashscope.test/v2"
    )
    assert updated.id == "bailian"
    assert updated.name == "Renamed"
    assert store.get_provider("bailian").base_url == "https://dashscope.test/v2"

    # Editing the built-in preset address turns it into a custom provider.
    changed = store.update_provider("default", base_url="https://custom.test/v1")
    assert changed.preset is False

    store.update_provider("bailian", key=SENTINEL)
    public = store.provider_public("bailian")
    assert public["key_set"] is True
    assert public["key_source"] == "pasted"
    assert SENTINEL not in json.dumps(store.list_providers())
    assert SENTINEL not in store.data_file.read_text(encoding="utf-8")

    entry = store.add_model("bailian", model="m1")
    store.set_default(entry.id)
    store.delete_provider("bailian")
    assert store.get_provider("bailian") is None
    assert store.get_model(entry.id) is None
    assert store.default_id == "default"
    assert SENTINEL not in store.credentials_file.read_text(encoding="utf-8")


def test_model_catalog_store_level(tmp_path):
    store, _ = _store(tmp_path)
    store.add_provider(id="bailian", name="Bailian", base_url="https://dashscope.test/v1")
    first = store.add_model("bailian", model="qwen-max", name="Qwen Max")
    assert first.provider_id == "bailian"
    assert first.source == "manual"
    assert first.name == "Qwen Max"

    with pytest.raises(KeyError):
        store.add_model("nope", model="m")
    with pytest.raises(ValueError):
        store.add_model("bailian", model="qwen-max")
    with pytest.raises(ValueError):
        store.add_model("bailian", model="   ")

    # Same provider+model reuses the entry; a new model becomes manual.
    assert store.ensure_model_entry("bailian", "qwen-max").id == first.id
    raw = store.ensure_model_entry("bailian", "qwen-raw")
    assert raw.source == "manual"
    assert raw.model == "qwen-raw"
    assert [m["model"] for m in store.list_models("bailian")] == ["qwen-max", "qwen-raw"]

    store.set_default(raw.id)
    store.delete_model("bailian", raw.id)
    assert store.default_id != raw.id
    assert store.get_provider("bailian") is not None
    with pytest.raises(KeyError):
        store.delete_model("bailian", "nope")
    with pytest.raises(KeyError):
        store.list_models("nope")


def test_last_model_and_last_provider_are_protected(tmp_path):
    store, _ = _store(tmp_path)
    with pytest.raises(ValueError):
        store.delete_model("default", "default")
    with pytest.raises(ValueError):
        store.delete_provider("default")


# ── HIGH rework: an empty model must be rejected, not silently dropped ──
def test_models_with_an_empty_model_are_rejected_without_mutating(tmp_path):
    store, _ = _store(tmp_path)
    store.add_provider(id="p", name="P", base_url="https://example.test/v1")
    with pytest.raises(ValueError):
        store.add_model("p", model="")
    with pytest.raises(ValueError):
        store.add_model("p", model="   ")
    assert store.list_models("p") == []

    second = store.add_model("p", model=" m2 ")
    assert store.get_model(second.id).model == "m2"
    before = asdict(store.get_model(second.id))
    with pytest.raises(ValueError):
        store.update_model("p", second.id, model="")
    assert asdict(store.get_model(second.id)) == before


def test_a_valid_model_still_exists_after_a_reload(tmp_path):
    store, settings = _store(tmp_path)
    store.add_provider(id="second", name="Second", base_url="https://example.test/v1")
    second = store.add_model("second", model="m2")

    reloaded = ModelStore(settings, home_dir=tmp_path / "home")

    entry = reloaded.get_model(second.id)
    assert entry is not None
    assert entry.model == "m2"


def test_catalog_api_rejects_an_empty_model(tmp_path):
    client, tm = _client(tmp_path, None)
    try:
        created = client.post(
            "/api/providers",
            json={"id": "ok", "name": "Ok", "base_url": "https://example.test/v1"},
        )
        assert created.status_code == 200
        blank = client.post("/api/providers/ok/models", json={"model": ""})
        assert blank.status_code == 400
        valid = client.post("/api/providers/ok/models", json={"model": "m1"})
        assert valid.status_code == 200
        model_id = valid.json()["data"]["id"]
        assert (
            client.put(
                f"/api/providers/ok/models/{model_id}", json={"model": "  "}
            ).status_code
            == 400
        )
        entry = next(
            m
            for m in client.get("/api/providers/ok/models").json()["data"]["models"]
            if m["id"] == model_id
        )
        assert entry["model"] == "m1"
        # The pre-fix path accepted "" and the next load silently dropped it.
        reloaded = ModelStore(tm.settings, home_dir=tm.settings.data_path / "home")
        assert reloaded.get_model(model_id) is not None
        assert reloaded.get_model(model_id).model == "m1"
    finally:
        client.__exit__(None, None, None)


def _write_v2_file(tmp_path, payload):
    settings = make_settings(tmp_path)
    settings.data_path.mkdir(parents=True, exist_ok=True)
    (settings.data_path / "models.json").write_text(json.dumps(payload), encoding="utf-8")
    return settings


def test_v2_load_drops_unknown_protocols_and_keeps_anthropic(tmp_path):
    providers = [
        {
            "id": "keep",
            "name": "Keep",
            "protocol": "openai-compatible",
            "base_url": "https://keep.test/v1",
        },
        {
            "id": "weird",
            "name": "Weird",
            "protocol": "grpc",
            "base_url": "https://weird.test/v1",
        },
        {
            "id": "claude",
            "name": "Claude",
            "protocol": "anthropic",
            "base_url": "https://api.anthropic.test/v1",
        },
    ]
    models = [
        {"id": "keep", "provider_id": "keep", "model": "m1", "name": "M1", "source": "manual"},
        {
            "id": "weird-model",
            "provider_id": "weird",
            "model": "m2",
            "name": "M2",
            "source": "manual",
        },
        {
            "id": "claude-model",
            "provider_id": "claude",
            "model": "claude-3",
            "name": "C",
            "source": "manual",
        },
    ]
    settings = _write_v2_file(
        tmp_path,
        {
            "schema_version": 2,
            "default": "keep",
            "providers": providers,
            "models": models,
        },
    )

    store = ModelStore(settings, home_dir=tmp_path / "home")

    # An unknown protocol is dropped like any other unreadable entry; the
    # anthropic provider is storable (the adapter, and with it any use, lands
    # in PR B) and round-trips through the loader unchanged.
    assert set(store.providers) == {"keep", "claude"}
    assert set(store.model_entries) == {"keep", "claude-model"}
    assert store.default_id == "keep"
    assert store.resolve("claude-model").protocol == "anthropic"


def test_v2_file_with_only_unknown_protocols_is_quarantined(tmp_path):
    settings = _write_v2_file(
        tmp_path,
        {
            "schema_version": 2,
            "default": "weird",
            "providers": [
                {
                    "id": "weird",
                    "name": "Weird",
                    "protocol": "grpc",
                    "base_url": "https://weird.test/v1",
                }
            ],
            "models": [
                {
                    "id": "weird",
                    "provider_id": "weird",
                    "model": "m",
                    "name": "M",
                    "source": "manual",
                }
            ],
        },
    )

    store = ModelStore(settings, home_dir=tmp_path / "home")

    assert store.get_model("default") is not None
    assert list(settings.data_path.glob("models.json.corrupt-*"))


def test_newer_schema_version_is_moved_aside_not_rewritten(tmp_path):
    settings = make_settings(tmp_path)
    settings.data_path.mkdir(parents=True, exist_ok=True)
    future = json.dumps({"schema_version": 99, "providers": [], "models": []})
    (settings.data_path / "models.json").write_text(future, encoding="utf-8")

    store = ModelStore(settings, home_dir=tmp_path / "home")

    assert store.get_model("default") is not None
    quarantined = list(settings.data_path.glob("models.json.corrupt-*"))
    assert quarantined
    assert quarantined[0].read_text(encoding="utf-8") == future


def test_add_provider_rolls_back_when_the_key_cannot_be_written(tmp_path, monkeypatch):
    import backend.services.model_store as model_store_module

    store, _ = _store(tmp_path)
    original_open = model_store_module.os.open
    _deny_credentials_write(monkeypatch)

    with pytest.raises(CredentialsError):
        store.add_provider(
            id="bailian",
            name="Bailian",
            base_url="https://dashscope.test/v1",
            key="sk-test",
        )

    # No half-created card: not in memory, not on disk, not after a reload.
    assert store.get_provider("bailian") is None
    assert "bailian" not in store.data_file.read_text(encoding="utf-8")
    reloaded = ModelStore(store.settings, home_dir=tmp_path / "home")
    assert reloaded.get_provider("bailian") is None

    # With the credentials file writable again, the same id can be created.
    monkeypatch.setattr(model_store_module.os, "open", original_open)
    created = store.add_provider(
        id="bailian",
        name="Bailian",
        base_url="https://dashscope.test/v1",
        key="sk-test",
    )
    assert created.id == "bailian"
    assert store.provider_public("bailian")["key_set"] is True


# ── D-36: sampling fields (schema v2 backward compatible) ──
def test_v2_file_without_sampling_fields_loads_with_none(tmp_path):
    settings = _write_v2_file(
        tmp_path,
        {
            "schema_version": 2,
            "default": "keep",
            "providers": [
                {
                    "id": "keep",
                    "name": "Keep",
                    "protocol": "openai-compatible",
                    "base_url": "https://keep.test/v1",
                }
            ],
            "models": [
                {
                    "id": "keep",
                    "provider_id": "keep",
                    "model": "m1",
                    "name": "M1",
                    "source": "manual",
                }
            ],
        },
    )

    store = ModelStore(settings, home_dir=tmp_path / "home")

    resolved = store.resolve("keep")
    assert resolved is not None
    assert resolved.temperature is None
    assert resolved.max_tokens is None
    assert resolved.thinking is None
    assert resolved.reasoning_effort is None
    provider = store.provider_public("keep")
    assert provider["temperature"] is None
    assert provider["max_tokens"] is None
    assert provider["thinking"] is None
    assert provider["reasoning_effort"] is None
    entry = store.model_public("keep")
    assert entry["temperature"] is None
    assert entry["max_tokens"] is None
    assert entry["thinking"] is None
    assert entry["reasoning_effort"] is None
    # A later save writes the (still None) fields without breaking the reload.
    store.set_default("keep")
    reloaded = ModelStore(settings, home_dir=tmp_path / "home")
    assert reloaded.resolve("keep").temperature is None


def test_entry_sampling_params_override_provider_defaults(tmp_path):
    store, settings = _store(tmp_path)
    store.add_provider(
        id="bailian",
        name="Bailian",
        base_url="https://dashscope.test/v1",
        temperature=0.3,
        max_tokens=1024,
        thinking=False,
        reasoning_effort="low",
    )
    inherited = store.add_model("bailian", model="m1")
    inherited_model = store.resolve(inherited.id)
    assert inherited_model.temperature == 0.3
    assert inherited_model.max_tokens == 1024
    assert inherited_model.thinking is False
    assert inherited_model.reasoning_effort == "low"

    overridden = store.add_model(
        "bailian",
        model="m2",
        temperature=0.9,
        max_tokens=2048,
        thinking=True,
        reasoning_effort="high",
    )
    overridden_model = store.resolve(overridden.id)
    assert overridden_model.temperature == 0.9
    assert overridden_model.max_tokens == 2048
    assert overridden_model.thinking is True
    assert overridden_model.reasoning_effort == "high"

    # Provider default edits do not leak through an entry override.
    store.update_provider("bailian", temperature=0.1)
    assert store.resolve(overridden.id).temperature == 0.9
    assert store.resolve(inherited.id).temperature == 0.1

    store.update_model("bailian", overridden.id, temperature=0.5, max_tokens=512)
    reloaded = ModelStore(settings, home_dir=tmp_path / "home")
    assert reloaded.resolve(overridden.id).temperature == 0.5
    assert reloaded.resolve(overridden.id).max_tokens == 512


def test_sampling_params_are_validated_without_mutating(tmp_path):
    store, _ = _store(tmp_path)
    with pytest.raises(ValueError, match="temperature"):
        store.add_provider(
            id="bad-temp", name="x", base_url="https://x.test/v1", temperature=3
        )
    store.add_provider(id="bailian", name="Bailian", base_url="https://dashscope.test/v1")
    with pytest.raises(ValueError, match="max_tokens"):
        store.add_model("bailian", model="m", max_tokens=0)
    with pytest.raises(ValueError, match="thinking"):
        store.add_model("bailian", model="m", thinking="yes")
    with pytest.raises(ValueError, match="reasoning_effort"):
        store.add_model("bailian", model="m", reasoning_effort="max")

    entry = store.add_model("bailian", model="ok", temperature=0.4)
    before = asdict(store.get_model(entry.id))
    with pytest.raises(ValueError, match="temperature"):
        store.update_model("bailian", entry.id, temperature=-1)
    assert asdict(store.get_model(entry.id)) == before

    store.add_model("bailian", model="taken")
    with pytest.raises(ValueError, match="already exists"):
        store.update_model("bailian", entry.id, model="taken")


# ── PR #298 返工：写面拒绝不可运行组合 / 参数显式清空 / 温度按协议 ──
def test_anthropic_write_face_rejects_unrunnable_thinking(tmp_path):
    store, _ = _store(tmp_path)
    with pytest.raises(ValueError, match="thinking"):
        store.add_provider(
            id="claude",
            name="Claude",
            protocol=PROTOCOL_ANTHROPIC,
            base_url="https://api.anthropic.test/v1",
            thinking=True,
        )
    with pytest.raises(ValueError, match="reasoning_effort"):
        store.add_provider(
            id="claude",
            name="Claude",
            protocol=PROTOCOL_ANTHROPIC,
            base_url="https://api.anthropic.test/v1",
            reasoning_effort="high",
        )

    # An openai-compatible card may enable thinking; switching it to anthropic
    # while the field is still set must be refused at the write face.
    store.add_provider(
        id="gw",
        name="GW",
        base_url="https://gw.test/v1",
        thinking=True,
        reasoning_effort="high",
    )
    with pytest.raises(ValueError, match="thinking"):
        store.update_provider("gw", protocol=PROTOCOL_ANTHROPIC)
    store.update_provider("gw", thinking=None, reasoning_effort=None)
    assert store.update_provider("gw", protocol=PROTOCOL_ANTHROPIC).protocol == (
        PROTOCOL_ANTHROPIC
    )

    with pytest.raises(ValueError, match="thinking"):
        store.add_model("gw", model="m1", thinking=True)


def test_anthropic_write_face_checks_entry_overrides(tmp_path):
    store, _ = _store(tmp_path)
    store.add_provider(id="gw", name="GW", base_url="https://gw.test/v1")
    entry = store.add_model("gw", model="m1", thinking=True)
    with pytest.raises(ValueError, match="thinking"):
        store.update_provider("gw", protocol=PROTOCOL_ANTHROPIC)

    # Clearing only the entry override makes the provider switch runnable.
    store.update_model("gw", entry.id, thinking=None)
    assert store.update_provider("gw", protocol=PROTOCOL_ANTHROPIC).protocol == (
        PROTOCOL_ANTHROPIC
    )


def test_sampling_params_can_be_cleared_back_to_the_fallback(tmp_path):
    store, _ = _store(tmp_path)
    store.add_provider(
        id="p1",
        name="P1",
        base_url="https://gw.test/v1",
        temperature=0.3,
        max_tokens=1024,
    )
    entry = store.add_model(
        "p1", model="m1", temperature=0.9, max_tokens=2048, thinking=True
    )
    assert store.resolve(entry.id).temperature == 0.9

    # Explicit None clears an override; omitted fields keep their value.
    cleared = store.update_model(
        "p1", entry.id, temperature=None, max_tokens=None, thinking=None
    )
    assert cleared.temperature is None
    assert cleared.max_tokens is None
    assert cleared.thinking is None
    resolved = store.resolve(entry.id)
    assert resolved.temperature == 0.3  # provider fallback
    assert resolved.max_tokens == 1024
    assert resolved.thinking is None  # provider default is not set here

    kept = store.update_model("p1", entry.id, name="Renamed")
    assert kept.name == "Renamed"
    assert kept.temperature is None

    # Clearing the provider-level value falls back to the client default (None).
    store.update_provider("p1", temperature=None, max_tokens=None)
    assert store.resolve(entry.id).temperature is None
    assert store.resolve(entry.id).max_tokens is None


def test_temperature_range_is_protocol_specific(tmp_path):
    store, _ = _store(tmp_path)
    # openai-compatible keeps the 0..2 range
    store.add_provider(id="gw", name="GW", base_url="https://gw.test/v1", temperature=1.5)
    # anthropic only accepts 0..1
    with pytest.raises(ValueError, match="temperature"):
        store.add_provider(
            id="claude",
            name="Claude",
            protocol=PROTOCOL_ANTHROPIC,
            base_url="https://api.anthropic.test/v1",
            temperature=1.5,
        )
    store.add_provider(
        id="claude",
        name="Claude",
        protocol=PROTOCOL_ANTHROPIC,
        base_url="https://api.anthropic.test/v1",
    )
    with pytest.raises(ValueError, match="temperature"):
        store.add_model("claude", model="m", temperature=1.2)
    with pytest.raises(ValueError, match="temperature"):
        store.update_provider("gw", protocol=PROTOCOL_ANTHROPIC)

    store.update_provider("gw", temperature=0.9)
    assert store.update_provider("gw", protocol=PROTOCOL_ANTHROPIC).protocol == (
        PROTOCOL_ANTHROPIC
    )


def test_protocol_switch_revalidates_entry_level_temperature(tmp_path):
    """MEDIUM (PR #298 rework batch 2): an entry override legal for OpenAI (1.5)
    is above Anthropic's 0..1; switching protocol must refuse it."""
    store, _ = _store(tmp_path)
    store.add_provider(id="gw", name="GW", base_url="https://gw.test/v1")
    entry = store.add_model("gw", model="m1", temperature=1.5)
    with pytest.raises(ValueError, match="temperature"):
        store.update_provider("gw", protocol=PROTOCOL_ANTHROPIC)

    # Clearing the entry override lets the switch through (0.9 provider default
    # is legal for both protocols).
    store.update_provider("gw", temperature=0.9)
    store.update_model("gw", entry.id, temperature=None)
    assert store.update_provider("gw", protocol=PROTOCOL_ANTHROPIC).protocol == (
        PROTOCOL_ANTHROPIC
    )

    # A provider-level 1.5 is refused by the same switch too.
    store.add_provider(id="gw2", name="GW2", base_url="https://gw2.test/v1")
    store.add_model("gw2", model="m1")
    store.update_provider("gw2", temperature=1.5)
    with pytest.raises(ValueError, match="temperature"):
        store.update_provider("gw2", protocol=PROTOCOL_ANTHROPIC)
