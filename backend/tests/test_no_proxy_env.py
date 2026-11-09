"""NO_PROXY normalization before outbound clients are built (P5 finding).

httpx turns every NO_PROXY entry into ``URLPattern("all://*" + host)``. A
bracketed IPv6 loopback entry (``[::1]``) is not a URL host in that shape, so
``httpx.Client(...)`` raises ``InvalidURL: Invalid port: ':1]'`` while the LLM
client is being constructed — the backend dies during startup. NO_PROXY is a
host list, not a URL, so brackets are unnecessary; the runtime normalizes them
once before any client exists.

The tests pin both halves: the red case on the real httpx seam, and the green
path through the OpenAI-compatible client constructor.
"""

from __future__ import annotations

import os

import httpx
import pytest
from httpx._utils import URLPattern, get_environment_proxies

from backend.core.llm.openai_compat import OpenAICompatibleClient
from backend.runtime import build_runtime
from backend.tests.conftest import make_settings
from backend.utils.net import normalize_no_proxy


def _proxy_patterns_parse() -> None:
    for key in get_environment_proxies():
        URLPattern(key)


def test_bracketed_ipv6_entries_are_unwrapped():
    # A bracketed port cannot travel into httpx's host-only matcher: keeping
    # the digits would make "::1:8000" a different IPv6 address.
    env = {"NO_PROXY": "[::1], localhost ,[::1]:8000"}
    assert normalize_no_proxy(env) is True
    assert env["NO_PROXY"] == "::1,localhost,::1"


def test_both_cases_are_normalized_and_empty_entries_dropped():
    env = {"NO_PROXY": "localhost,, [::1] ", "no_proxy": "127.0.0.1, [::1]"}
    assert normalize_no_proxy(env) is True
    assert env["NO_PROXY"] == "localhost,::1"
    assert env["no_proxy"] == "127.0.0.1,::1"


def test_clean_values_report_no_change():
    env = {"NO_PROXY": "localhost,127.0.0.1,::1"}
    assert normalize_no_proxy(env) is False
    assert env["NO_PROXY"] == "localhost,127.0.0.1,::1"


def test_httpx_proxy_patterns_parse_after_normalization(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "[::1],localhost")
    with pytest.raises(httpx.InvalidURL):
        _proxy_patterns_parse()

    assert normalize_no_proxy() is True
    _proxy_patterns_parse()


def test_openai_client_constructs_after_normalization(monkeypatch):
    """The exact startup failure: OpenAI(...) builds its httpx client from env."""
    monkeypatch.setenv("NO_PROXY", "[::1]")
    with pytest.raises(httpx.InvalidURL):
        OpenAICompatibleClient(
            base_url="http://example.invalid/v1",
            api_key="test-key",
            model="test-model",
        )

    normalize_no_proxy()
    client = OpenAICompatibleClient(
        base_url="http://example.invalid/v1",
        api_key="test-key",
        model="test-model",
    )
    assert client.model == "test-model"


def test_build_runtime_normalizes_the_process_env(monkeypatch, tmp_path):
    """The web and REPL entries share this seam; deleting the call must fail."""
    monkeypatch.setenv("NO_PROXY", "[::1],localhost")
    build_runtime(make_settings(tmp_path))
    assert os.environ["NO_PROXY"] == "::1,localhost"
