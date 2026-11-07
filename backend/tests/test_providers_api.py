"""#243 P2-2 A2 (D-34): /api/providers, model catalog, discovery, task switch."""

from __future__ import annotations

import time

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.core.llm.client import LLMResponse, MockLLMClient
from backend.main import app
from backend.services import model_store as model_store_module
from backend.services.model_store import (
    DISCOVER_MAX_BYTES,
    DISCOVER_TIMEOUT_SEC,
    PROTOCOL_ANTHROPIC,
    ModelStore,
    Provider,
    ProviderDiscoveryError,
    discover_provider_models,
)
from backend.tests.conftest import make_manager, make_settings

SENTINEL = "provider-api-key-sentinel-do-not-leak"
_TEST_ENV_NAME = "QIANFAN_TEST_PROVIDER_KEY"


def _client(tmp_path):
    settings = make_settings(tmp_path)
    tm = make_manager(settings, MockLLMClient(final_answer="ok"))
    client = TestClient(app, base_url="http://localhost")
    client.__enter__()
    client.app.state.task_manager = tm
    return client, tm


def _create_provider(client, provider_id="bailian", **overrides):
    body = {
        "id": provider_id,
        "name": "Bailian",
        "protocol": "openai-compatible",
        "base_url": "https://dashscope.test/v1",
        "api_key_env": _TEST_ENV_NAME,
    }
    body.update(overrides)
    response = client.post("/api/providers", json=body)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _wait_parked(tm, task_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = tm.get_task(task_id)
        if task is not None and task.status.value in ("INTERRUPTED", "FAILED"):
            return task
        time.sleep(0.02)
    raise AssertionError("task never parked")


# ── provider CRUD ──
def test_provider_crud_round_trip_never_echoes_the_key(tmp_path, monkeypatch):
    monkeypatch.delenv(_TEST_ENV_NAME, raising=False)
    client, tm = _client(tmp_path)
    try:
        created = _create_provider(client, key=SENTINEL)
        assert created["id"] == "bailian"
        assert created["protocol"] == "openai-compatible"
        assert created["base_url"] == "https://dashscope.test/v1"
        assert created["key_set"] is True
        assert created["key_source"] == "pasted"
        assert "key" not in created
        assert SENTINEL not in str(created)

        listing = client.get("/api/providers")
        assert listing.status_code == 200
        assert SENTINEL not in listing.text
        data = listing.json()["data"]
        assert data["default"] == "default"
        assert {provider["id"] for provider in data["providers"]} == {"default", "bailian"}

        updated = client.put(
            "/api/providers/bailian",
            json={"name": "Renamed", "base_url": "https://dashscope.test/v2"},
        )
        assert updated.status_code == 200
        assert updated.json()["data"]["name"] == "Renamed"
        assert updated.json()["data"]["base_url"] == "https://dashscope.test/v2"
        assert updated.json()["data"]["id"] == "bailian"
        assert updated.json()["data"]["key_set"] is True
        assert SENTINEL not in updated.text

        # D-34: the provider id is immutable; an id in the body is ignored.
        renamed = client.put("/api/providers/bailian", json={"id": "other", "name": "Still"})
        assert renamed.status_code == 200
        assert renamed.json()["data"]["id"] == "bailian"

        cleared = client.put("/api/providers/bailian", json={"key": ""})
        assert cleared.status_code == 200
        assert cleared.json()["data"]["key_set"] is False
        assert cleared.json()["data"]["key_source"] == "none"

        assert client.delete("/api/providers/bailian").status_code == 200
        assert client.put("/api/providers/bailian", json={"name": "x"}).status_code == 404
        assert client.delete("/api/providers/bailian").status_code == 404
    finally:
        client.__exit__(None, None, None)


def test_provider_validation_errors_map_to_400(tmp_path):
    client, tm = _client(tmp_path)
    try:
        bad_bodies = [
            {"id": "Bad_ID", "base_url": "https://example.test/v1"},
            {"id": "ok-id", "base_url": "ftp://example.test/v1"},
            {"id": "ok-id", "base_url": "https://example.test/v1", "protocol": "grpc"},
            {"id": "ok-id", "base_url": "https://example.test/v1", "api_key_env": "not a name"},
        ]
        for body in bad_bodies:
            response = client.post("/api/providers", json=body)
            assert response.status_code == 400, (body, response.text)

        _create_provider(client)
        duplicate = client.post(
            "/api/providers",
            json={"id": "bailian", "base_url": "https://other.test/v1"},
        )
        assert duplicate.status_code == 400
        assert "already exists" in duplicate.json()["detail"]

        # A2 keeps the anthropic write face closed until its adapter lands (PR B):
        # a stored-but-unimplemented protocol must not become task-selectable.
        refused = client.post(
            "/api/providers",
            json={
                "id": "claude",
                "base_url": "https://api.anthropic.test/v1",
                "protocol": PROTOCOL_ANTHROPIC,
            },
        )
        assert refused.status_code == 400
        assert "协议尚未实现" in refused.json()["detail"]
        ids = {p["id"] for p in client.get("/api/providers").json()["data"]["providers"]}
        assert "claude" not in ids
        assert (
            client.put(
                "/api/providers/bailian", json={"protocol": PROTOCOL_ANTHROPIC}
            ).status_code
            == 400
        )
    finally:
        client.__exit__(None, None, None)


# ── model catalog ──
def test_provider_catalog_crud_and_default_handling(tmp_path):
    client, tm = _client(tmp_path)
    try:
        _create_provider(client)
        first = client.post(
            "/api/providers/bailian/models", json={"model": "qwen-max", "name": "Qwen Max"}
        )
        assert first.status_code == 200
        entry = first.json()["data"]
        assert entry["provider_id"] == "bailian"
        assert entry["model"] == "qwen-max"
        assert entry["name"] == "Qwen Max"
        assert entry["source"] == "manual"
        assert entry["id"]

        listing = client.get("/api/providers/bailian/models")
        assert listing.status_code == 200
        assert [m["id"] for m in listing.json()["data"]["models"]] == [entry["id"]]
        assert listing.json()["data"]["default"] == "default"

        duplicate = client.post("/api/providers/bailian/models", json={"model": "qwen-max"})
        assert duplicate.status_code == 400
        assert client.get("/api/providers/nope/models").status_code == 404
        assert client.post("/api/providers/nope/models", json={"model": "m"}).status_code == 404

        second = client.post("/api/providers/bailian/models", json={"model": "qwen-plus"})
        second_id = second.json()["data"]["id"]
        assert client.post(f"/api/models/{second_id}/default").status_code == 200
        assert client.get("/api/providers/bailian/models").json()["data"]["default"] == second_id

        # Deleting the default entry moves the default back to a live entry.
        assert client.delete(f"/api/providers/bailian/models/{second_id}").status_code == 200
        assert client.get("/api/providers/bailian/models").json()["data"]["default"] != second_id
        assert client.delete("/api/providers/bailian/models/nope").status_code == 404

        # Deleting the provider removes its catalog entries too.
        assert client.delete("/api/providers/bailian").status_code == 200
        assert client.get("/api/providers/bailian/models").status_code == 404
        legacy_ids = [m["id"] for m in client.get("/api/models").json()["data"]["models"]]
        assert entry["id"] not in legacy_ids
    finally:
        client.__exit__(None, None, None)


def test_last_model_and_last_provider_are_protected(tmp_path):
    client, tm = _client(tmp_path)
    try:
        assert (
            client.delete("/api/providers/default/models/default").status_code == 409
        )
        assert client.delete("/api/providers/default").status_code == 409
    finally:
        client.__exit__(None, None, None)


# ── task model switching (both payload shapes) ──
def test_task_model_switch_accepts_entry_id_and_provider_pair(tmp_path):
    client, tm = _client(tmp_path)
    try:
        _create_provider(client)
        entry = client.post(
            "/api/providers/bailian/models", json={"model": "qwen-max"}
        ).json()["data"]
        task_id = client.post("/api/tasks", json={"input": "hi"}).json()["data"]["task_id"]
        _wait_parked(tm, task_id)

        switched = client.put(f"/api/tasks/{task_id}/model", json={"model": entry["id"]})
        assert switched.status_code == 200
        payload = switched.json()["data"]
        assert payload["model"] == entry["id"]
        assert payload["task"]["model"] == entry["id"]

        raw = client.put(
            f"/api/tasks/{task_id}/model",
            json={"provider": "bailian", "model": "qwen-raw"},
        )
        assert raw.status_code == 200
        raw_payload = raw.json()["data"]
        assert raw_payload["task"]["model"] == raw_payload["model"]
        catalog = client.get("/api/providers/bailian/models").json()["data"]["models"]
        created = next(m for m in catalog if m["id"] == raw_payload["model"])
        assert created == {
            "id": raw_payload["model"],
            "provider_id": "bailian",
            "model": "qwen-raw",
            "name": "qwen-raw",
            "source": "manual",
        }

        # The same pair reuses one entry instead of growing the catalog.
        again = client.put(
            f"/api/tasks/{task_id}/model",
            json={"provider": "bailian", "model": "qwen-raw"},
        )
        assert again.json()["data"]["model"] == raw_payload["model"]
        assert len(client.get("/api/providers/bailian/models").json()["data"]["models"]) == 2

        assert client.put(f"/api/tasks/{task_id}/model", json={"model": "nope"}).status_code == 409
        assert (
            client.put(
                f"/api/tasks/{task_id}/model", json={"provider": "nope", "model": "m"}
            ).status_code
            == 404
        )
        assert (
            client.put(
                f"/api/tasks/{task_id}/model", json={"provider": "bailian"}
            ).status_code
            == 422
        )
    finally:
        client.__exit__(None, None, None)


def test_anthropic_model_fails_loudly_until_the_adapter_lands(tmp_path, monkeypatch):
    monkeypatch.delenv(_TEST_ENV_NAME, raising=False)
    settings = make_settings(tmp_path, use_mock_llm=False)
    tm = make_manager(settings, None)
    tm.models.add_provider(
        id="claude",
        name="Claude",
        protocol=PROTOCOL_ANTHROPIC,
        base_url="https://api.anthropic.test/v1",
        api_key_env=_TEST_ENV_NAME,
    )
    entry = tm.models.add_model("claude", model="claude-3")
    task_id = tm.create_task("m", "hi", model=entry.id)
    with pytest.raises(RuntimeError, match="not supported yet"):
        tm.llm_for_task_id(task_id)


# ── discovery ──
def test_discover_previews_without_persisting_or_echoing_the_key(tmp_path, monkeypatch):
    monkeypatch.delenv(_TEST_ENV_NAME, raising=False)
    client, tm = _client(tmp_path)
    try:
        _create_provider(client, key=SENTINEL)
        client.post("/api/providers/bailian/models", json={"model": "qwen-max"})
        calls = []

        def fake_discover(provider, key, *, transport=None):
            calls.append((provider.id, key))
            return [
                {"model": "qwen-max", "name": "Qwen Max"},
                {"model": "qwen-plus", "name": "Qwen Plus"},
            ]

        monkeypatch.setattr(model_store_module, "discover_provider_models", fake_discover)
        response = client.post("/api/providers/bailian/models/discover")
        assert response.status_code == 200
        assert SENTINEL not in response.text
        assert response.json()["data"]["models"] == [
            {"model": "qwen-max", "name": "Qwen Max", "added": True},
            {"model": "qwen-plus", "name": "Qwen Plus", "added": False},
        ]
        assert calls == [("bailian", SENTINEL)]
        # Preview only: nothing was added to the catalog.
        catalog = client.get("/api/providers/bailian/models").json()["data"]["models"]
        assert [m["model"] for m in catalog] == ["qwen-max"]
        assert client.post("/api/providers/nope/models/discover").status_code == 404
    finally:
        client.__exit__(None, None, None)


def test_discover_anthropic_and_upstream_errors_are_readable(tmp_path, monkeypatch):
    monkeypatch.delenv(_TEST_ENV_NAME, raising=False)
    client, tm = _client(tmp_path)
    try:
        # The HTTP write face refuses anthropic (A2); a stored card can still
        # exist (hand-edited file / future channel), and discovery must refuse
        # it readably before any request goes out.
        tm.models.add_provider(
            id="claude",
            name="Claude",
            protocol=PROTOCOL_ANTHROPIC,
            base_url="https://api.anthropic.test/v1",
            api_key_env=_TEST_ENV_NAME,
            key=SENTINEL,
        )
        refused = client.post("/api/providers/claude/models/discover")
        assert refused.status_code == 400
        assert "协议暂不支持发现" in refused.json()["detail"]
        assert SENTINEL not in refused.text

        def leaky_discover(provider, key, *, transport=None):
            raise ProviderDiscoveryError(f"upstream rejected {SENTINEL}")

        monkeypatch.setattr(model_store_module, "discover_provider_models", leaky_discover)
        leaked = client.post("/api/providers/claude/models/discover")
        assert leaked.status_code == 400
        assert SENTINEL not in leaked.text
        assert "[redacted]" in leaked.json()["detail"]
    finally:
        client.__exit__(None, None, None)


def _provider(**overrides):
    body = {
        "id": "p",
        "name": "P",
        "protocol": "openai-compatible",
        "base_url": "https://api.test/v1",
    }
    body.update(overrides)
    return Provider(**body)


def test_discover_parses_the_openai_models_payload():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "m1"},
                    {"id": "m2", "name": "M2"},
                    {"id": "m1"},
                    "junk",
                ]
            },
        )

    models = discover_provider_models(
        _provider(), "sk-x", transport=httpx.MockTransport(handler)
    )

    assert models == [{"model": "m1", "name": "m1"}, {"model": "m2", "name": "M2"}]
    assert seen == {"url": "https://api.test/v1/models", "auth": "Bearer sk-x"}


def test_discover_without_a_key_sends_no_authorization_header():
    def handler(request):
        assert request.headers.get("authorization") is None
        return httpx.Response(200, json={"data": []})

    assert (
        discover_provider_models(_provider(), "", transport=httpx.MockTransport(handler)) == []
    )


@pytest.mark.parametrize(
    "status, fragment",
    [(401, "认证失败"), (403, "认证失败"), (500, "获取模型目录失败"), (302, "重定向")],
)
def test_discover_http_errors_are_readable_and_never_echo_the_key(status, fragment):
    def handler(request):
        return httpx.Response(status, headers={"location": "https://elsewhere.test/models"})

    with pytest.raises(ProviderDiscoveryError) as excinfo:
        discover_provider_models(
            _provider(), "sk-secret", transport=httpx.MockTransport(handler)
        )
    message = str(excinfo.value)
    assert fragment in message
    assert "sk-secret" not in message


def test_discover_timeout_and_network_errors_are_readable():
    def timeout_handler(request):
        raise httpx.TimeoutException("timed out")

    with pytest.raises(ProviderDiscoveryError, match="超时"):
        discover_provider_models(
            _provider(), "", transport=httpx.MockTransport(timeout_handler)
        )

    def connect_handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(ProviderDiscoveryError, match="无法连接"):
        discover_provider_models(
            _provider(), "", transport=httpx.MockTransport(connect_handler)
        )


def test_discover_rejects_oversized_non_json_and_wrong_shapes():
    def oversized_handler(request):
        return httpx.Response(200, content=b"{" + b" " * DISCOVER_MAX_BYTES)

    with pytest.raises(ProviderDiscoveryError, match="大小上限"):
        discover_provider_models(
            _provider(), "", transport=httpx.MockTransport(oversized_handler)
        )

    def non_json_handler(request):
        return httpx.Response(200, text="<html>nope</html>")

    with pytest.raises(ProviderDiscoveryError, match="JSON"):
        discover_provider_models(
            _provider(), "", transport=httpx.MockTransport(non_json_handler)
        )

    def wrong_shape_handler(request):
        return httpx.Response(200, json={"object": "list"})

    with pytest.raises(ProviderDiscoveryError, match="data 数组"):
        discover_provider_models(
            _provider(), "", transport=httpx.MockTransport(wrong_shape_handler)
        )


def test_discover_anthropic_protocol_is_refused_before_any_request():
    def handler(request):  # pragma: no cover - must never be called
        raise AssertionError("no request expected")

    with pytest.raises(ProviderDiscoveryError, match="协议暂不支持发现"):
        discover_provider_models(
            _provider(protocol=PROTOCOL_ANTHROPIC),
            "sk-x",
            transport=httpx.MockTransport(handler),
        )


# ── legacy /api/models projection keeps working ──
def test_legacy_models_projection_and_provider_scoped_key(tmp_path, monkeypatch):
    monkeypatch.delenv(_TEST_ENV_NAME, raising=False)
    client, tm = _client(tmp_path)
    try:
        _create_provider(client)
        first = client.post(
            "/api/providers/bailian/models", json={"model": "m1"}
        ).json()["data"]
        second = client.post(
            "/api/providers/bailian/models", json={"model": "m2"}
        ).json()["data"]

        listing = client.get("/api/models").json()["data"]
        projected = next(m for m in listing["models"] if m["id"] == first["id"])
        assert projected["provider_id"] == "bailian"
        assert projected["protocol"] == "openai-compatible"
        assert projected["base_url"] == "https://dashscope.test/v1"
        assert projected["model"] == "m1"
        assert projected["key_set"] is False

        # A key belongs to the provider, so both entries report it.
        saved = client.put(f"/api/models/{first['id']}/key", json={"key": SENTINEL})
        assert saved.status_code == 200
        assert SENTINEL not in saved.text
        entries = client.get("/api/models").json()["data"]["models"]
        assert all(m["key_set"] is True for m in entries if m["provider_id"] == "bailian")
        assert SENTINEL not in client.get("/api/models").text

        # Legacy delete drops one entry and keeps the provider for its sibling.
        assert client.delete(f"/api/models/{first['id']}").status_code == 200
        remaining = client.get("/api/providers/bailian/models").json()["data"]["models"]
        assert [m["id"] for m in remaining] == [second["id"]]
    finally:
        client.__exit__(None, None, None)


# ── A2 hard prerequisites: cache invalidation, total deadline, empty provider ──
class _CachedClient:
    model = "fake"

    def complete(self, messages, tools=None, **kwargs):
        return LLMResponse(content="done")


def test_provider_edits_and_key_rotation_invalidate_cached_clients(tmp_path, monkeypatch):
    monkeypatch.delenv(_TEST_ENV_NAME, raising=False)
    settings = make_settings(tmp_path, use_mock_llm=False)
    made = []

    def factory(profile, key):
        made.append((profile.base_url, key))
        return _CachedClient()

    tm = make_manager(settings, None, llm_factory=factory)
    tm.models.add_provider(
        id="p1", name="P1", base_url="https://old.test/v1", api_key_env=_TEST_ENV_NAME
    )
    entry = tm.models.add_model("p1", model="m1")
    task_id = tm.create_task("t", "hi", model=entry.id)
    _wait_parked(tm, task_id)
    tm.llm_for_task_id(task_id)  # cache a client built for the old address
    before = len(made)

    tm.update_provider("p1", base_url="https://new.test/v1")
    tm.llm_for_task_id(task_id)
    assert len(made) == before + 1
    assert made[-1] == ("https://new.test/v1", "")

    # A key rotation through the provider card must rebuild the client too.
    tm.update_provider("p1", key=SENTINEL)
    tm.llm_for_task_id(task_id)
    assert made[-1] == ("https://new.test/v1", SENTINEL)

    # Deleting the provider cannot leave a cached client behind for its entry.
    tm.delete_provider("p1")
    with pytest.raises(RuntimeError, match="no longer exists"):
        tm.llm_for_task_id(task_id)


def test_discover_has_a_total_wall_clock_deadline(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"data": [{"id": "m1"}]})

    # First call computes the deadline; the first body chunk is already past it.
    ticks = iter([0.0, DISCOVER_TIMEOUT_SEC + 1.0])
    monkeypatch.setattr(
        model_store_module,
        "_monotonic",
        lambda: next(ticks, DISCOVER_TIMEOUT_SEC + 2.0),
    )

    with pytest.raises(ProviderDiscoveryError, match="超时"):
        discover_provider_models(
            _provider(), "", transport=httpx.MockTransport(handler)
        )


def test_delete_model_from_an_empty_provider_is_model_not_found(tmp_path):
    """A provider with no entries is unreachable via the API while the
    last-model invariant holds; the store answers "model not found" rather
    than inventing a second empty-provider branch."""
    settings = make_settings(tmp_path)
    store = ModelStore(settings, home_dir=tmp_path / "home")
    store.add_provider(id="empty", name="Empty", base_url="https://empty.test/v1")

    with pytest.raises(KeyError):
        store.delete_model("empty", "anything")
