"""M1: model profiles and credentials (ADR 0010 decisions 7–8)."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from backend.core.llm.client import LLMResponse, MockLLMClient
from backend.main import app
from backend.services.model_store import CredentialsError, ModelStore
from backend.tests.conftest import make_manager, make_settings

SENTINEL = "model-key-sentinel-do-not-leak"


def _store(tmp_path, **overrides):
    settings = make_settings(tmp_path, **overrides)
    return ModelStore(settings, home_dir=tmp_path / "home"), settings


def test_seeded_from_env_settings(tmp_path):
    store, settings = _store(tmp_path)
    assert store.default_id == "default"
    profile = store.get("default")
    assert profile is not None
    assert profile.model == settings.llm_model
    assert profile.base_url == "https://api.deepseek.com/v1"  # DeepSeek is the only preset
    assert profile.preset is True
    assert (settings.data_path / "models.json").is_file()


def test_custom_base_url_is_not_a_preset(tmp_path):
    store, _ = _store(tmp_path, llm_base_url="https://gw.test/v1")
    assert store.get("default").preset is False


def test_crud_default_and_delete_guard(tmp_path):
    store, _ = _store(tmp_path)
    second = store.add(name="Second", base_url="https://example.test/v1", model="m2")
    store.update(second.id, name="Second edited", model="m2b")
    assert store.get(second.id).name == "Second edited"
    store.set_default(second.id)
    assert store.default_id == second.id
    store.delete(second.id)
    assert store.default_id == "default"
    try:
        store.delete("default")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("deleting the last profile must fail")


def test_key_order_is_pasted_then_environment(tmp_path, monkeypatch):
    store, _ = _store(tmp_path)
    profile = store.get("default")
    monkeypatch.setenv(profile.api_key_env, "env-key")
    assert store.key_for(profile.id) == "env-key"

    store.set_key(profile.id, SENTINEL)
    assert store.key_for(profile.id) == SENTINEL
    assert store.key_set(profile.id) is True

    store.set_key(profile.id, "")
    assert store.key_for(profile.id) == "env-key"


def test_key_source_is_reported_without_the_key(tmp_path, monkeypatch):
    store, _ = _store(tmp_path)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert store.key_source("default") == "none"
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    assert store.key_source("default") == "env"

    store.set_key("default", SENTINEL)
    entry = store.public_profile("default")
    assert entry["key_source"] == "pasted"
    assert entry["key_set"] is True
    assert SENTINEL not in json.dumps(entry)


def test_update_rejects_without_mutating(tmp_path):
    store, _ = _store(tmp_path)
    before = asdict(store.get("default"))
    with pytest.raises(ValueError):
        store.update("default", base_url="")
    assert asdict(store.get("default")) == before


def test_resolve_does_not_fall_back_for_an_explicit_unknown_id(tmp_path):
    store, _ = _store(tmp_path)
    assert store.resolve("deleted") is None
    assert store.resolve(None).id == "default"


def test_corrupt_models_json_is_quarantined_not_overwritten(tmp_path):
    store, settings = _store(tmp_path)
    store.data_file.write_text("{not json", encoding="utf-8")
    reloaded = ModelStore(settings, home_dir=tmp_path / "home")
    assert reloaded.get("default") is not None
    assert list(settings.data_path.glob("models.json.corrupt-*"))


def test_key_never_in_models_json(tmp_path, monkeypatch):
    store, _ = _store(tmp_path)
    store.set_key("default", SENTINEL)
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
    second = tm.models.add(name="Second", base_url="https://example.test/v1", model="m2")
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
    second = tm.models.add(name="Second", base_url="https://example.test/v1", model="m2")
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


def test_profile_edit_and_key_change_replace_the_cached_client(tmp_path):
    settings = make_settings(tmp_path, use_mock_llm=False)
    made = []

    def factory(profile, key):
        made.append((profile.base_url, key))
        return _FakeClient(profile, key)

    tm = make_manager(settings, None, llm_factory=factory)
    task_id = tm.create_task("m", "hi")
    _wait(tm, task_id)
    tm.llm_for_task_id(task_id)
    before = len(made)

    tm.update_model_profile("default", base_url="https://new.test/v1")
    tm.llm_for_task_id(task_id)
    assert len(made) == before + 1
    assert made[-1][0] == "https://new.test/v1"

    tm.set_model_key("default", "sk-new")
    tm.llm_for_task_id(task_id)
    assert made[-1][1] == "sk-new"


def test_deleted_profile_makes_a_pinned_task_fail_loudly(tmp_path):
    settings = make_settings(tmp_path, use_mock_llm=False)
    tm = make_manager(settings, None, llm_factory=lambda profile, key: _FakeClient(profile, key))
    second = tm.models.add(name="Second", base_url="https://example.test/v1", model="m2")
    task_id = tm.create_task("m", "hi", model=second.id)
    _wait(tm, task_id)

    tm.delete_model_profile(second.id)
    with pytest.raises(RuntimeError, match="no longer exists"):
        tm.llm_for_task_id(task_id)


class _LeakyClient:
    model = "leaky"

    def __init__(self, key):
        self.key = key

    def complete(self, messages, tools=None, **kwargs):
        raise RuntimeError(f"401 Authorization: Bearer {self.key}")


def test_provider_error_key_never_reaches_task_trace_logs_or_sse(tmp_path, caplog):
    settings = make_settings(tmp_path, use_mock_llm=False, trace_enabled=True)
    store = ModelStore(settings, home_dir=tmp_path / "home")
    store.set_key("default", SENTINEL)
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


def test_a_rotated_key_stays_redacted(tmp_path):
    settings = make_settings(tmp_path, use_mock_llm=False)
    tm = make_manager(settings, None, llm_factory=lambda profile, key: _FakeClient(profile, key))
    old = "old-key-sentinel"
    tm.set_model_key("default", old)
    tm.llm_for_task_id("no-task")  # a live client now holds the old key
    assert tm.redact(f"bearer {old}") == "bearer [redacted]"

    tm.set_model_key("default", "new-key-sentinel")

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


def test_deleting_the_rounds_profile_marks_the_task_failed(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, use_mock_llm=False)
    tm = make_manager(settings, None, llm_factory=lambda profile, key: _FakeClient(profile, key))
    second = tm.models.add(name="Second", base_url="https://example.test/v1", model="m2")
    monkeypatch.setattr(tm, "_start_run", lambda task_id: None)
    task_id = tm.create_task("m", "hi", model=second.id)
    tm.delete_model_profile(second.id)

    tm.run(task_id)

    task = tm.get_task(task_id)
    assert task is not None
    assert task.status.value == "FAILED"
    assert "no longer exists" in (task.error or "")


def test_profile_edit_cannot_reinsert_the_old_cached_client(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, use_mock_llm=False)
    made = []

    def factory(profile, key):
        made.append(profile.base_url)
        return _FakeClient(profile, key)

    tm = make_manager(settings, None, llm_factory=factory)
    second = tm.models.add(name="Second", base_url="https://old.test/v1", model="m2")
    task_id = tm.create_task("m", "hi", model=second.id)
    _wait(tm, task_id)
    tm.llm_for_task_id(task_id)  # cache the old address

    original = tm.models.resolve
    entered = threading.Event()
    release = threading.Event()
    calls = {"n": 0}

    def slow_resolve(model_id):
        calls["n"] += 1
        profile = original(model_id)
        if calls["n"] == 1:
            entered.set()
            assert release.wait(5)
        return profile

    monkeypatch.setattr(tm.models, "resolve", slow_resolve)
    reader = threading.Thread(target=lambda: tm.llm_for_task_id(task_id))
    reader.start()
    assert entered.wait(5)
    updater = threading.Thread(
        target=lambda: tm.update_model_profile(second.id, base_url="https://new.test/v1")
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
    tm = make_manager(settings, None, llm_factory=lambda profile, key: _LeakyClient(key))
    tm.set_model_key("default", SENTINEL)

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
    assert store.get("default").preset is True
    updated = store.update("default", base_url="https://custom.test/v1")
    assert updated.preset is False


def test_profile_validation_rejects_bad_urls_and_env_names(tmp_path):
    store, _ = _store(tmp_path)
    with pytest.raises(ValueError):
        store.add(name="x", base_url="ftp://example.test", model="m")
    with pytest.raises(ValueError):
        store.add(name="x", base_url="https://example.test", model="m", api_key_env="not a name")
    with pytest.raises(ValueError):
        store.update("default", api_key_env="1INVALID")


def test_delete_revokes_the_pasted_key(tmp_path):
    store, _ = _store(tmp_path)
    second = store.add(name="Second", base_url="https://example.test/v1", model="m2")
    store.set_key(second.id, SENTINEL)
    store.delete(second.id)
    assert SENTINEL not in store.credentials_file.read_text(encoding="utf-8")


def test_test_model_sends_one_request_through_the_factory(tmp_path):
    settings = make_settings(tmp_path, use_mock_llm=False)
    calls = []

    def factory(profile, key):
        calls.append((profile.id, key))
        return _FakeClient(profile, key)

    tm = make_manager(settings, None, llm_factory=factory)
    tm.set_model_key("default", "sk-test")
    assert tm.test_model("default") == {"ok": True}
    assert calls[-1] == ("default", "sk-test")


def test_tool_output_echoing_a_key_is_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", SENTINEL)
    settings = make_settings(tmp_path, use_mock_llm=False, trace_enabled=True)
    script = MockLLMClient(
        plan=["p"],
        tool_calls=[{"id": "c1", "name": "bash", "arguments": {"command": "echo $LLM_API_KEY"}}],
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
        tool_calls=[{"id": "c1", "name": "bash", "arguments": {"command": f"echo {SENTINEL}"}}],
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
        if tc.tool_name == "bash"
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
        assert SENTINEL not in client.get("/api/models").text
        created = client.post(
            "/api/models",
            json={"name": "Bailian", "base_url": "https://dashscope.test/v1", "model": "qwen"},
        )
        assert created.status_code == 200
        model_id = created.json()["data"]["id"]
        saved = client.put(f"/api/models/{model_id}/key", json={"key": SENTINEL})
        assert saved.json()["data"] == {"ok": True, "key_set": True, "key_source": "pasted"}
        listing = client.get("/api/models")
        assert SENTINEL not in listing.text
        entry = next(m for m in listing.json()["data"]["models"] if m["id"] == model_id)
        assert entry["key_set"] is True
        assert entry["key_source"] == "pasted"
        assert "key" not in entry

        task_id = tm.create_task("t", "hi")
        task = client.get(f"/api/tasks/{task_id}")
        assert SENTINEL not in task.text
        assert client.put(f"/api/tasks/{task_id}/model", json={"model": model_id}).status_code == 200
        assert client.put(f"/api/tasks/{task_id}/model", json={"model": "nope"}).status_code == 409
        assert client.post(f"/api/models/{model_id}/default").status_code == 200
        assert client.get("/api/models").json()["data"]["default"] == model_id
        tested = client.post(f"/api/models/{model_id}/test")
        assert tested.status_code == 200
        assert SENTINEL not in tested.text
    finally:
        client.__exit__(None, None, None)


def test_model_profile_update_and_delete_endpoints(tmp_path):
    client, tm = _client(tmp_path, None)
    try:
        created = client.post(
            "/api/models",
            json={"name": "Temp", "base_url": "https://example.test/v1", "model": "m1"},
        )
        assert created.status_code == 200
        model_id = created.json()["data"]["id"]

        updated = client.put(
            f"/api/models/{model_id}", json={"name": "Renamed", "model": "m2"}
        )
        assert updated.status_code == 200
        entry = next(
            m
            for m in client.get("/api/models").json()["data"]["models"]
            if m["id"] == model_id
        )
        assert entry["name"] == "Renamed"
        assert entry["model"] == "m2"

        removed = client.delete(f"/api/models/{model_id}")
        assert removed.status_code == 200
        assert all(
            m["id"] != model_id
            for m in client.get("/api/models").json()["data"]["models"]
        )
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


def test_credentials_write_failure_is_readable_and_keeps_the_profile(
    tmp_path, monkeypatch, caplog
):
    store, _ = _store(tmp_path)
    second = store.add(name="Second", base_url="https://example.test/v1", model="m2")
    store.set_key(second.id, SENTINEL)

    _deny_credentials_write(monkeypatch)

    with caplog.at_level("WARNING"):
        with pytest.raises(CredentialsError) as excinfo:
            store.set_key(second.id, "sk-new")
    message = str(excinfo.value)
    assert str(store.credentials_file) in message
    assert "environment variable" in message
    # AC (#244): the detail reaches the log as well as the HTTP response.
    assert "credentials write failed" in caplog.text
    assert str(store.credentials_file) in caplog.text
    # The failed write left the previous pasted key intact.
    assert store.key_for(second.id) == SENTINEL

    # Revoking a key is part of deleting a profile; failing to revoke must not
    # drop the profile (which would leave an orphaned secret on disk).
    with pytest.raises(CredentialsError):
        store.delete(second.id)
    assert store.get(second.id) is not None


def test_key_write_failure_returns_409_with_reason(tmp_path, monkeypatch):
    client, tm = _client(tmp_path, None)
    try:
        created = client.post(
            "/api/models",
            json={"name": "Temp", "base_url": "https://example.test/v1", "model": "m1"},
        )
        model_id = created.json()["data"]["id"]

        _deny_credentials_write(monkeypatch)

        response = client.put(f"/api/models/{model_id}/key", json={"key": "sk-test"})
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert str(tm.models.credentials_file) in detail
        assert "environment variable" in detail
        assert tm.models.get(model_id) is not None
    finally:
        client.__exit__(None, None, None)

# ── #243 P2-2 (D-33): models.json v2 + v1 migration ──
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


def test_v1_models_json_migrates_to_providers_and_catalog(tmp_path):
    settings, original = _write_v1_models(tmp_path)
    home = tmp_path / "home"
    credentials = home / ".qianfan" / "credentials.json"
    credentials.parent.mkdir(parents=True)
    credentials.write_text(json.dumps({"ddddeeeeffff": SENTINEL}), encoding="utf-8")

    store = ModelStore(settings, home_dir=home)

    raw = json.loads(store.data_file.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 2
    assert raw["default"] == "aaaabbbbcccc"
    providers = {provider["id"]: provider for provider in raw["providers"]}
    assert set(providers) == {"aaaabbbbcccc", "ddddeeeeffff"}
    assert providers["aaaabbbbcccc"] == {
        "id": "aaaabbbbcccc",
        "name": "DeepSeek",
        "protocol": "openai-compatible",
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "LLM_API_KEY",
        "preset": True,
    }
    assert providers["ddddeeeeffff"]["api_key_env"] == "DASHSCOPE_API_KEY"
    assert providers["ddddeeeeffff"]["preset"] is False
    entries = {entry["id"]: entry for entry in raw["models"]}
    assert entries["aaaabbbbcccc"] == {
        "id": "aaaabbbbcccc",
        "provider_id": "aaaabbbbcccc",
        "model": "deepseek-chat",
        "name": "DeepSeek",
        "source": "migrated",
    }
    assert entries["ddddeeeeffff"]["provider_id"] == "ddddeeeeffff"
    assert entries["ddddeeeeffff"]["model"] == "qwen-max"

    # Backup: written before the rewrite, byte-identical to the v1 file.
    backup = store.data_file.with_name("models.json.bak-v1")
    assert backup.is_file()
    assert backup.read_text(encoding="utf-8") == original

    # Task.model / default / credentials keys keep working unchanged.
    assert store.default_id == "aaaabbbbcccc"
    assert store.get("aaaabbbbcccc").model == "deepseek-chat"
    assert store.get("ddddeeeeffff").base_url == "https://dashscope.test/v1"
    assert store.key_for("ddddeeeeffff") == SENTINEL
    assert store.key_source("ddddeeeeffff") == "pasted"
    assert SENTINEL not in store.data_file.read_text(encoding="utf-8")
    assert json.loads(credentials.read_text(encoding="utf-8")) == {"ddddeeeeffff": SENTINEL}

    # Legacy flat projection keeps the old fields and adds provider_id/protocol.
    listing = store.list_public()
    projected = next(m for m in listing["models"] if m["id"] == "ddddeeeeffff")
    assert projected["provider_id"] == "ddddeeeeffff"
    assert projected["protocol"] == "openai-compatible"
    assert projected["base_url"] == "https://dashscope.test/v1"
    assert projected["model"] == "qwen-max"
    assert projected["key_set"] is True
    assert projected["key_source"] == "pasted"


def test_v1_migration_is_idempotent_and_backs_up_once(tmp_path):
    settings, original = _write_v1_models(tmp_path)
    home = tmp_path / "home"
    store = ModelStore(settings, home_dir=home)
    after_first = store.data_file.read_bytes()
    backups = settings.data_path.glob("models.json.bak-v1")
    assert [backup.name for backup in backups] == ["models.json.bak-v1"]

    reloaded = ModelStore(settings, home_dir=home)

    # A v2 file is read as-is: no rewrite and no second backup.
    assert reloaded.data_file.read_bytes() == after_first
    assert [backup.name for backup in settings.data_path.glob("models.json.bak-v1")] == [
        "models.json.bak-v1"
    ]
    backup = reloaded.data_file.with_name("models.json.bak-v1")
    assert backup.read_text(encoding="utf-8") == original
    assert reloaded.list_public()["default"] == "aaaabbbbcccc"


def test_existing_v1_backup_is_never_overwritten(tmp_path):
    settings, _ = _write_v1_models(tmp_path)
    backup = settings.data_path / "models.json.bak-v1"
    backup.write_text("first migration backup", encoding="utf-8")

    store = ModelStore(settings, home_dir=tmp_path / "home")

    assert store.default_id == "aaaabbbbcccc"
    assert backup.read_text(encoding="utf-8") == "first migration backup"


def test_v1_default_falls_back_to_the_first_profile_when_unknown(tmp_path):
    settings, _ = _write_v1_models(tmp_path, default="does-not-exist")
    store = ModelStore(settings, home_dir=tmp_path / "home")
    assert store.default_id == "aaaabbbbcccc"


def test_migrated_profile_ids_still_pin_tasks(tmp_path):
    settings, _ = _write_v1_models(tmp_path)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    task_id = tm.create_task("m", "hi", model="ddddeeeeffff")
    assert (tm.get_task(task_id).model or "") == "ddddeeeeffff"


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

    assert store.get("default") is not None
    assert list(settings.data_path.glob("models.json.corrupt-*"))
    assert json.loads(store.data_file.read_text(encoding="utf-8"))["schema_version"] == 2


def test_v1_backup_is_a_byte_for_byte_copy(tmp_path):
    settings = make_settings(tmp_path)
    settings.data_path.mkdir(parents=True, exist_ok=True)
    payload = {"default": "aaaabbbbcccc", "models": _V1_PROFILES}
    original = (
        json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8").replace(b"\r\n", b"\n")
    )
    (settings.data_path / "models.json").write_bytes(original)

    store = ModelStore(settings, home_dir=tmp_path / "home")

    backup = store.data_file.with_name("models.json.bak-v1")
    assert backup.read_bytes() == original


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
    assert store.get(entry.id) is None
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
    with pytest.raises(ValueError):
        store.add(name="Blank", base_url="https://example.test/v1", model="")
    with pytest.raises(ValueError):
        store.add(name="Blank", base_url="https://example.test/v1", model="   ")
    assert [m["id"] for m in store.list_public()["models"]] == ["default"]

    second = store.add(name="Second", base_url="https://example.test/v1", model=" m2 ")
    assert store.get(second.id).model == "m2"
    before = asdict(store.get(second.id))
    with pytest.raises(ValueError):
        store.update(second.id, model="")
    assert asdict(store.get(second.id)) == before


def test_a_valid_model_still_exists_after_a_reload(tmp_path):
    store, settings = _store(tmp_path)
    second = store.add(name="Second", base_url="https://example.test/v1", model="m2")

    reloaded = ModelStore(settings, home_dir=tmp_path / "home")

    entry = reloaded.get(second.id)
    assert entry is not None
    assert entry.model == "m2"


def test_models_api_rejects_an_empty_model(tmp_path):
    client, tm = _client(tmp_path, None)
    try:
        blank = client.post(
            "/api/models",
            json={"name": "Blank", "base_url": "https://example.test/v1", "model": ""},
        )
        assert blank.status_code == 400
        valid = client.post(
            "/api/models",
            json={"name": "Ok", "base_url": "https://example.test/v1", "model": "m1"},
        )
        assert valid.status_code == 200
        model_id = valid.json()["data"]["id"]
        assert client.put(f"/api/models/{model_id}", json={"model": "  "}).status_code == 400
        entry = next(
            m for m in client.get("/api/models").json()["data"]["models"] if m["id"] == model_id
        )
        assert entry["model"] == "m1"
        # The pre-fix path accepted "" and the next load silently dropped it.
        reloaded = ModelStore(tm.settings, home_dir=tm.settings.data_path / "home")
        assert reloaded.get(model_id) is not None
        assert reloaded.get(model_id).model == "m1"
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
    assert store.get("claude-model").protocol == "anthropic"


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

    assert store.get("default") is not None
    assert list(settings.data_path.glob("models.json.corrupt-*"))


def test_newer_schema_version_is_moved_aside_not_rewritten(tmp_path):
    settings = make_settings(tmp_path)
    settings.data_path.mkdir(parents=True, exist_ok=True)
    future = json.dumps({"schema_version": 99, "providers": [], "models": []})
    (settings.data_path / "models.json").write_text(future, encoding="utf-8")

    store = ModelStore(settings, home_dir=tmp_path / "home")

    assert store.get("default") is not None
    quarantined = list(settings.data_path.glob("models.json.corrupt-*"))
    assert quarantined
    assert quarantined[0].read_text(encoding="utf-8") == future


def test_migration_keeps_the_v1_file_when_the_backup_cannot_be_written(
    tmp_path, monkeypatch
):
    settings, _ = _write_v1_models(tmp_path)
    before = (settings.data_path / "models.json").read_bytes()
    import pathlib

    def deny(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(pathlib.Path, "write_bytes", deny)

    store = ModelStore(settings, home_dir=tmp_path / "home")

    # Fail-closed: the migrated view is served from memory ...
    assert store.default_id == "aaaabbbbcccc"
    assert store.get("aaaabbbbcccc").model == "deepseek-chat"
    # ... while the v1 file stays byte-for-byte as it was: no v2 rewrite and
    # no missing backup to restore from (the next start retries the migration).
    assert store.data_file.read_bytes() == before
    assert not store.data_file.with_name("models.json.bak-v1").exists()


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


def test_a_pending_backup_blocks_later_v2_writes_until_it_succeeds(tmp_path, monkeypatch):
    import pathlib

    settings, _ = _write_v1_models(tmp_path)
    original = (settings.data_path / "models.json").read_bytes()
    real_write_bytes = pathlib.Path.write_bytes

    def deny_backup(path, data):
        if path.name == "models.json.bak-v1":
            raise PermissionError(13, "Permission denied")
        return real_write_bytes(path, data)

    monkeypatch.setattr(pathlib.Path, "write_bytes", deny_backup)
    store = ModelStore(settings, home_dir=tmp_path / "home")

    # Fail-closed at load: the v1 file stays, no backup, no v2 write.
    assert store.data_file.read_bytes() == original
    assert not store.data_file.with_name("models.json.bak-v1").exists()

    # A later, unrelated mutation must not quietly write v2 without the backup.
    with pytest.raises(RuntimeError, match="back up"):
        store.set_default("ddddeeeeffff")
    assert store.data_file.read_bytes() == original
    assert not store.data_file.with_name("models.json.bak-v1").exists()

    # Once the backup path is writable the same mutation retries and succeeds.
    monkeypatch.setattr(pathlib.Path, "write_bytes", real_write_bytes)
    store.set_default("ddddeeeeffff")

    backup = store.data_file.with_name("models.json.bak-v1")
    assert backup.read_bytes() == original
    raw = json.loads(store.data_file.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 2
    assert raw["default"] == "ddddeeeeffff"
