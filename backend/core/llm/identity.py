"""Client identity and OpenCode target detection (#351).

Every model request identifies this client with a fixed User-Agent. OpenCode
(Go / Zen / same-domain paths) also requires a stable per-conversation
``x-opencode-session``; the gateway answers a missing one with HTTP 400
``MissingSessionID``. Session ids are passed explicitly per call — never
inferred from trace scope.
"""

from __future__ import annotations

from typing import Dict
from urllib.parse import urlsplit

#: Sent on every OpenAI-compatible, Anthropic-native and discovery request.
QIANFAN_USER_AGENT = "qianfan/0.1.0"
OPENCODE_SESSION_HEADER = "x-opencode-session"
_OPENCODE_HOSTS = frozenset({"opencode.ai", "www.opencode.ai"})


def is_opencode_base_url(base_url: str) -> bool:
    """True when the configured host is OpenCode (Go, Zen or same-domain)."""
    try:
        host = (urlsplit(base_url or "").hostname or "").lower()
    except ValueError:
        return False
    return host in _OPENCODE_HOSTS


def opencode_session_headers(base_url: str, session_id: str) -> Dict[str, str]:
    """The session header, or nothing when the target/session does not call for it."""
    if not session_id or not is_opencode_base_url(base_url):
        return {}
    return {OPENCODE_SESSION_HEADER: session_id}
