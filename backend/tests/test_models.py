"""M1: model profiles and credentials (ADR 0010 decisions 7–8)."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from backend.core.llm.client import LLMResponse, MockLLMClient
from backend.main import app
from backend.services.model_store import ModelStore
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
    assert profile.base_url == "https://api.openai.com/v1"  # no llm_base_url in tests
    assert (settings.data_path / "models.json").is_file()


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


def test_subagent_uses_the_parent_task_model(tmp_path):
    settings = make_settings(tmp_path, use_mock_llm=False)
    made = []

    def factory(profile, key):
        made.append(profile.id)
        return _FakeClient(profile, key)

    tm = make_manager(settings, None, llm_factory=factory)
    second = tm.models.add(name="Second", base_url="https://example.test/v1", model="m2")
    task_id = tm.create_task("m", "hi", model=second.id)
    assert tm.llm_for_task_id(task_id).model == "m2"


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
        assert saved.json()["data"] == {"ok": True, "key_set": True}
        listing = client.get("/api/models")
        assert SENTINEL not in listing.text
        entry = next(m for m in listing.json()["data"]["models"] if m["id"] == model_id)
        assert entry["key_set"] is True
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


def test_unknown_model_on_task_create_is_rejected(tmp_path):
    client, tm = _client(tmp_path, None)
    try:
        r = client.post("/api/tasks", json={"input": "hi", "model": "nope"})
        assert r.status_code == 400
    finally:
        client.__exit__(None, None, None)
