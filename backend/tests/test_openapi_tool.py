"""P1 item 6 — OpenAPI tool generation tests (fully offline).

Covers YAML / JSON / URL spec loading, per-operation tool generation, invalid
spec errors, ``OpenAPITool.run`` HTTP calls (via a fake httpx client) with
path/query/header/body mapping, 4xx/5xx -> ``success=False``, and apiKey
security injection.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import backend.core.tools.openapi_tool as ot
from backend.core.tools.openapi_tool import (
    OpenAPISpecError,
    OpenAPITool,
    build_tools_from_spec,
    load_openapi_spec,
)

_YAML_SPEC = """
openapi: 3.0.0
info:
  title: Petstore
  version: "1.0"
servers:
  - url: https://api.example.com/v1
paths:
  /pets:
    get:
      operationId: listPets
      summary: List all pets
      parameters:
        - name: limit
          in: query
          schema:
            type: integer
            default: 20
      responses:
        "200":
          description: ok
    post:
      operationId: createPet
      summary: Create a pet
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                name:
                  type: string
      responses:
        "201":
          description: created
  /pets/{petId}:
    get:
      operationId: getPet
      summary: Get a pet
      parameters:
        - name: petId
          in: path
          required: true
          schema:
            type: string
      responses:
        "200":
          description: ok
"""


def test_load_yaml_and_build(tmp_path):
    p = tmp_path / "spec.yaml"
    p.write_text(_YAML_SPEC, encoding="utf-8")
    spec = load_openapi_spec(str(p))
    tools = build_tools_from_spec(spec)
    names = {t.name for t in tools}
    assert {"listPets", "createPet", "getPet"} <= names
    get_pet = next(t for t in tools if t.name == "getPet")
    assert "petId" in get_pet.args_schema["required"]
    create_pet = next(t for t in tools if t.name == "createPet")
    assert "body" in create_pet.args_schema["properties"]


def test_load_json_spec(tmp_path):
    spec_dict = {
        "openapi": "3.0.0",
        "info": {"title": "t", "version": "1"},
        "paths": {
            "/a": {"get": {"operationId": "getA", "responses": {"200": {}}}},
            "/b": {"post": {"operationId": "postB", "responses": {"201": {}}}},
        },
    }
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(spec_dict), encoding="utf-8")
    tools = build_tools_from_spec(load_openapi_spec(str(p)))
    assert {t.name for t in tools} == {"getA", "postB"}


def test_load_spec_from_url(monkeypatch, tmp_path):
    body = json.dumps(
        {
            "openapi": "3.0.0",
            "info": {"title": "t", "version": "1"},
            "paths": {"/x": {"get": {"operationId": "getX", "responses": {"200": {}}}}},
        }
    ).encode("utf-8")

    class _FakeResp:
        def __init__(self, data):
            self._data = data

        def read(self):
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(url, timeout=15):
        return _FakeResp(body)

    monkeypatch.setattr(ot.urllib.request, "urlopen", _fake_urlopen)
    spec = load_openapi_spec("https://example.com/openapi.json")
    assert "paths" in spec


def test_invalid_spec_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("::: not yaml [[[", encoding="utf-8")
    with pytest.raises(OpenAPISpecError):
        load_openapi_spec(str(bad))

    with pytest.raises(OpenAPISpecError):
        load_openapi_spec(str(tmp_path / "missing.yaml"))

    with pytest.raises(OpenAPISpecError):
        load_openapi_spec("")


def test_spec_missing_paths_raises():
    with pytest.raises(OpenAPISpecError):
        build_tools_from_spec({"openapi": "3.0.0"})


# ── run() call mapping ──
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
        method="get",
        path="/pets/{petId}",
        parameters=[
            {"name": "petId", "in": "path", "required": True, "schema": {"type": "string"}},
            {"name": "verbose", "in": "query", "schema": {"type": "boolean"}},
        ],
        security=[],
        security_schemes={},
        server_url="https://api.example.com/v1",
    )
    base.update(overrides)
    return OpenAPITool(**base)


def test_run_injects_path_and_query(monkeypatch):
    monkeypatch.setattr(ot.httpx, "Client", _FakeHttpxClient)
    tool = _make_tool()
    res = tool.run(petId="abc", verbose=True)
    assert res.success is True
    cap = _FakeHttpxClient.captured
    assert cap["method"] == "GET"
    assert cap["url"] == "https://api.example.com/v1/pets/abc"
    assert cap["params"] == {"verbose": True}


def test_run_returns_4xx_as_failure(monkeypatch):
    class _ErrClient(_FakeHttpxClient):
        def request(self, method, url, **kw):
            _FakeHttpxClient.captured = {"method": method, "url": url, **kw}
            return _FakeResponse(404, {"error": "not found"})

    monkeypatch.setattr(ot.httpx, "Client", _ErrClient)
    tool = _make_tool()
    res = tool.run(petId="x")
    assert res.success is False
    assert res.data["status_code"] == 404
    assert "404" in (res.error or "")


def test_run_missing_required_param_errors(monkeypatch):
    monkeypatch.setattr(ot.httpx, "Client", _FakeHttpxClient)
    tool = _make_tool()
    res = tool.run()  # petId is required
    assert res.success is False
    assert "petId" in res.error


def test_run_api_key_header(monkeypatch):
    monkeypatch.setattr(ot.httpx, "Client", _FakeHttpxClient)
    tool = _make_tool(
        security=[{"ApiKeyAuth": []}],
        security_schemes={"ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"}},
        api_key="k123",
    )
    res = tool.run(petId="x")
    assert res.success is True
    assert _FakeHttpxClient.captured["headers"].get("X-API-Key") == "k123"


def test_run_api_key_missing_errors(monkeypatch):
    monkeypatch.setattr(ot.httpx, "Client", _FakeHttpxClient)
    tool = _make_tool(
        security=[{"ApiKeyAuth": []}],
        security_schemes={"ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"}},
        api_key="",  # not configured
    )
    res = tool.run(petId="x")
    assert res.success is False
    assert "apiKey" in res.error


def test_global_headers_applied(monkeypatch):
    monkeypatch.setattr(ot.httpx, "Client", _FakeHttpxClient)
    tool = _make_tool(global_headers={"X-Tenant": "acme"})
    tool.run(petId="x")
    assert _FakeHttpxClient.captured["headers"].get("X-Tenant") == "acme"
