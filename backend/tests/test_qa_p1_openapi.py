"""QA independent boundary tests — P1 item 6 (OpenAPI tool wrapping).

Covers gaps in the engineer's suite:

* apiKey security injected into the *query* string (engineer tested header only);
* 5xx responses also map to ``success=False``;
* missing ``operationId`` -> deterministic ``{method}_{path}`` name;
* request body passed as JSON;
* a malformed operation is skipped without aborting the others;
* conflict resolution keeps the first-registered tool (built-in wins);
* generated tools actually land in a TaskManager's tool list.
"""

from __future__ import annotations

import json

import pytest

import backend.core.tools.openapi_tool as ot
from backend.core.tools.openapi_tool import OpenAPITool, build_tools_from_spec, load_openapi_spec
from backend.core.llm.client import MockLLMClient
from backend.tests.conftest import make_manager, make_settings


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self._text = text

    def json(self):
        if self._payload is not None:
            return self._payload
        raise ValueError("no json")

    @property
    def text(self):
        return self._text


class _FakeHttpxClient:
    captured: dict = {}

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def request(self, method, url, **kw):
        _FakeHttpxClient.captured = {"method": method, "url": url, **kw}
        return _FakeResponse(200, {"ok": True})


def _make_tool(**overrides) -> OpenAPITool:
    base = dict(
        name="getPet",
        description="Get a pet",
        args_schema={"type": "object", "properties": {}, "required": []},
        method="post",
        path="/pets",
        parameters=[
            {"name": "petId", "in": "path", "required": True, "schema": {"type": "string"}},
            {"name": "verbose", "in": "query", "schema": {"type": "boolean"}},
            {"name": "X-Trace", "in": "header", "schema": {"type": "string"}},
        ],
        security=[],
        security_schemes={},
        server_url="https://api.example.com/v1",
    )
    base.update(overrides)
    return OpenAPITool(**base)


def test_api_key_query_injection(monkeypatch):
    monkeypatch.setattr(ot.httpx, "Client", _FakeHttpxClient)
    tool = _make_tool(
        security=[{"KeyAuth": []}],
        security_schemes={"KeyAuth": {"type": "apiKey", "in": "query", "name": "api_key"}},
        api_key="q123",
    )
    res = tool.run(petId="x", verbose=True)
    assert res.success is True
    cap = _FakeHttpxClient.captured
    assert cap["params"]["api_key"] == "q123"
    assert cap["params"]["verbose"] is True


def test_5xx_is_failure(monkeypatch):
    class _Err5(_FakeHttpxClient):
        def request(self, method, url, **kw):
            _FakeHttpxClient.captured = {"method": method, "url": url, **kw}
            return _FakeResponse(503, {"error": "unavailable"})

    monkeypatch.setattr(ot.httpx, "Client", _Err5)
    tool = _make_tool()
    res = tool.run(petId="x")
    assert res.success is False
    assert res.data["status_code"] == 503
    assert "503" in (res.error or "")


def test_operation_id_missing_generates_method_path(tmp_path):
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "t", "version": "1"},
        "paths": {
            "/pets/{petId}": {"get": {"responses": {"200": {}}}},
            "/root": {"get": {"responses": {"200": {}}}},
        },
    }
    p = tmp_path / "noopid.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    tools = build_tools_from_spec(load_openapi_spec(str(p)))
    names = {t.name for t in tools}
    # Default name = normalized "{method}_{path}": '/' '{' '}' become '_'.
    assert "get__pets__petId" in names
    assert "get__root" in names


def test_request_body_injected_as_json(monkeypatch):
    monkeypatch.setattr(ot.httpx, "Client", _FakeHttpxClient)
    tool = _make_tool(
        method="post",
        path="/pets",
        parameters=[
            {"name": "body", "in": "body", "required": True, "schema": {"type": "object"}}
        ],
    )
    res = tool.run(body={"name": "rex", "age": 3})
    assert res.success is True
    cap = _FakeHttpxClient.captured
    assert cap["method"] == "POST"
    assert cap["json"] == {"name": "rex", "age": 3}


def test_malformed_operation_skipped_others_survive(tmp_path):
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "t", "version": "1"},
        "paths": {
            "/good": {"get": {"operationId": "goodOp", "responses": {"200": {}}}},
            # Not a dict -> build_tools_from_spec must skip, not abort.
            "/bad": "oops",
        },
    }
    p = tmp_path / "partial.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    tools = build_tools_from_spec(load_openapi_spec(str(p)))
    assert {t.name for t in tools} == {"goodOp"}


def test_build_tools_raises_without_paths():
    from backend.core.tools.openapi_tool import OpenAPISpecError

    with pytest.raises(OpenAPISpecError):
        build_tools_from_spec({"openapi": "3.0.0"})


# ── TaskManager integration: registration + conflict semantics ──
_SPEC_YAML = """
openapi: 3.0.0
info: {title: t, version: "1"}
paths:
  /qa:
    get:
      operationId: web_search
      summary: conflicts with the built-in web_search
      responses: {"200": {description: ok}}
    post:
      operationId: qa_unique_op
      summary: A unique QA operation
      responses: {"200": {description: ok}}
"""


def test_openapi_tools_registered_in_manager(tmp_path, event_bus):
    spec_file = tmp_path / "qa_spec.yaml"
    spec_file.write_text(_SPEC_YAML, encoding="utf-8")
    settings = make_settings(
        tmp_path,
        openapi_enabled=True,
        openapi_spec_path=str(spec_file),
    )
    tm = make_manager(settings, MockLLMClient(plan=["p"], final_answer="done"), event_bus=event_bus)
    names = {t.name for t in tm._tools}
    assert "qa_unique_op" in names
    tool = next(t for t in tm._tools if t.name == "qa_unique_op")
    assert isinstance(tool, OpenAPITool)


def test_openapi_conflict_keeps_first_registered(tmp_path, event_bus):
    spec_file = tmp_path / "conflict.yaml"
    spec_file.write_text(_SPEC_YAML, encoding="utf-8")
    settings = make_settings(
        tmp_path,
        openapi_enabled=True,
        openapi_spec_path=str(spec_file),
    )
    tm = make_manager(settings, MockLLMClient(plan=["p"], final_answer="done"), event_bus=event_bus)
    web = next(t for t in tm._tools if t.name == "web_search")
    # The built-in tool wins; the generated OpenAPITool is discarded.
    assert not isinstance(web, OpenAPITool), "OpenAPI tool overwrote the first-registered built-in"
