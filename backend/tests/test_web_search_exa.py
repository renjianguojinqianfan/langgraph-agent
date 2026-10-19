"""S2: the Exa search provider (#175).

All HTTP is substituted; the real network is only used by the manual web
acceptance run (EXA_API_KEY in the environment).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx

from backend.config import Settings
from backend.core.tools.web_search import WebSearchTool

SENTINEL = "exa-sentinel-key-do-not-leak"


def _settings(**overrides):
    values = {"search_provider": "exa", "exa_api_key": SENTINEL}
    values.update(overrides)
    return Settings(_env_file=None, **values)


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("POST", "https://api.exa.ai/search"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self._payload


def test_exa_request_shape_and_result_mapping():
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeResponse(
            {
                "results": [
                    {
                        "title": "Agent news",
                        "url": "https://a.test",
                        "highlights": ["first highlight", "second highlight"],
                        "text": "full page text",
                    },
                    {"title": "Long text", "url": "https://b.test", "text": "x" * 2000},
                ]
            }
        )

    with patch("httpx.post", fake_post):
        res = WebSearchTool(_settings()).run(query="latest agents", max_results=3)

    assert res.success is True
    url, kwargs = calls[0]
    assert url == "https://api.exa.ai/search"
    assert kwargs["headers"]["x-api-key"] == SENTINEL
    body = kwargs["json"]
    assert body["query"] == "latest agents"
    assert body["numResults"] == 3
    assert body["type"] == "auto"
    assert body["contents"]["highlights"] is True
    assert body["contents"]["text"]["maxCharacters"] >= 500

    results = res.data["results"]
    assert results[0] == {
        "title": "Agent news",
        "snippet": "first highlight … second highlight",
        "url": "https://a.test",
    }
    assert results[1]["snippet"].startswith("x")
    assert len(results[1]["snippet"]) <= 500


def test_exa_without_key_returns_a_readable_result_and_never_calls_out():
    with patch("httpx.post") as post:
        res = WebSearchTool(_settings(exa_api_key="")).run(query="q")

    assert res.success is True
    assert "not set" in res.data["results"][0]["snippet"]
    assert res.data["results"][0]["url"] == ""
    post.assert_not_called()


def test_exa_http_error_is_returned_as_a_readable_result():
    with patch("httpx.post", return_value=_FakeResponse({}, status_code=401)):
        res = WebSearchTool(_settings()).run(query="q")

    assert res.success is True
    assert "Exa" in res.data["results"][0]["title"]
    assert "401" in res.data["results"][0]["snippet"]


def test_exa_timeout_is_returned_as_a_readable_result():
    def fake_post(*args, **kwargs):
        raise httpx.TimeoutException("timed out")

    with patch("httpx.post", fake_post):
        res = WebSearchTool(_settings()).run(query="q")

    assert res.success is True
    assert "timed out" in res.data["results"][0]["snippet"].lower()


def test_exa_key_never_appears_in_results_or_logs(caplog):
    with patch("httpx.post", return_value=_FakeResponse({}, status_code=500)):
        with caplog.at_level("WARNING"):
            res = WebSearchTool(_settings()).run(query="q")

    assert SENTINEL not in json.dumps(res.data, ensure_ascii=False)
    assert SENTINEL not in caplog.text


def test_default_provider_stays_duckduckgo(monkeypatch):
    monkeypatch.delenv("SEARCH_PROVIDER", raising=False)
    assert Settings(_env_file=None).search_provider == "duckduckgo"


def test_exa_key_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", SENTINEL)
    assert Settings(_env_file=None).exa_api_key == SENTINEL
