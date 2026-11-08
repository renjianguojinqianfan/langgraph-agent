"""Basic token authentication (P1 item 5).

A small, dependency-free token service built on the standard library:

* :class:`TokenIssuer` — issues HMAC-signed tokens
  ``{expires_ts}.{nonce}.{sig}`` and registers them in an in-memory store so
  verification is (signature + registration + expiry) based;
* :class:`TokenIssuer` is **fail-closed**: an empty or placeholder secret
  raises at construction instead of silently signing with a public value;
* :func:`verify_token` — a FastAPI dependency that reads
  ``Authorization: Bearer <token>`` (REST). A ``?token=<token>`` query
  parameter is accepted **only** on the two browser-native GETs (SSE
  ``/events`` and file ``/files/download``, where EventSource / ``<a href>``
  cannot set headers). When ``auth_enabled=False`` it returns ``"local"``
  immediately — **zero regression** for the local demo.

Design decisions:

* dependency injection (``Depends``), **not** a global middleware — protected
  endpoints opt in explicitly and ``/auth/token`` + ``/health`` stay public;
* ``sse.py`` is untouched — the check happens in the route layer before the
  streaming response is created.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from typing import TYPE_CHECKING, Dict, Optional, Tuple

from fastapi import Header, HTTPException, Query, Request

from ..config import PLACEHOLDER_AUTH_TOKEN

if TYPE_CHECKING:  # Settings is annotation-only; no runtime import needed here
    from ..config import Settings

#: localStorage key the frontend uses to persist the token.
TOKEN_STORAGE_KEY = "lga_auth_token"


class TokenIssuer:
    """Issue and verify HMAC-signed, server-registered bearer tokens.

    Fail-closed (D-45 / #271): an empty or placeholder secret raises instead of
    falling back to the publicly documented placeholder. The startup guard
    (:func:`ensure_startup_auth_configured`) is still the entrypoint check; this
    constructor is the last line of defence so a future caller cannot silently
    recreate the #254 bug.
    """

    def __init__(self, secret: str, ttl_sec: int = 86400) -> None:
        cleaned = str(secret or "").strip()
        if not cleaned or cleaned.lower() == PLACEHOLDER_AUTH_TOKEN.lower():
            raise ValueError(
                "TokenIssuer requires a non-empty, non-placeholder auth secret; "
                "set a strong auth_token before enabling auth."
            )
        self._secret = cleaned.encode("utf-8")
        self._ttl_sec = max(1, int(ttl_sec))
        self._store: Dict[str, float] = {}  # token -> expires_at
        self._lock = threading.Lock()

    def _sign(self, payload: str) -> str:
        return hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def issue(self) -> Tuple[str, float]:
        """Issue a new token. Returns ``(token, expires_at_ts)``."""
        expires_at = time.time() + self._ttl_sec
        nonce = secrets.token_urlsafe(16)
        payload = f"{int(expires_at)}.{nonce}"
        token = f"{payload}.{self._sign(payload)}"
        with self._lock:
            self._store[token] = expires_at
        return token, expires_at

    def verify(self, token: str) -> bool:
        """Return True when ``token`` has a valid signature and is registered
        and not expired."""
        if not token:
            return False
        parts = token.split(".")
        if len(parts) != 3:
            return False
        payload = f"{parts[0]}.{parts[1]}"
        if not hmac.compare_digest(self._sign(payload), parts[2]):
            return False
        try:
            expires_at = float(parts[0])
        except ValueError:
            return False
        with self._lock:
            stored = self._store.get(token)
            if stored is None:
                return False
        if time.time() > expires_at or time.time() > stored:
            return False
        return True

    def revoke_all(self) -> int:
        """Clear the token store (used by tests). Returns number removed."""
        with self._lock:
            n = len(self._store)
            self._store.clear()
        return n


#: D-46 (#271): `?token=` is only for browser-native GETs that cannot set
#: an Authorization header — EventSource (SSE) and ``<a href>`` navigation
#: (download). Anchored full-path matches: ``/api/tasks/<id>/events-extra`` must
#: NOT match. Paths include the ``/api`` router prefix.
_QUERY_TOKEN_PATHS = (
    re.compile(r"/api/tasks/[^/]+/events"),
    re.compile(r"/api/tasks/[^/]+/files/download"),
)


def query_token_allowed(method: str, path: str) -> bool:
    """True only for the two whitelisted browser-native GET endpoints."""
    return method.upper() == "GET" and any(
        pattern.fullmatch(path) for pattern in _QUERY_TOKEN_PATHS
    )


def verify_token(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None),
) -> str:
    """FastAPI dependency guarding protected endpoints.

    * ``auth_enabled=False`` -> return ``"local"`` (passthrough, zero regress);
    * otherwise: an ``Authorization: Bearer`` header is the REST contract and
      always wins; ``?token=`` is read **only** when no Authorization header is
      present and the request is a whitelisted browser-native GET (SSE /
      download, D-46). Validation goes through ``app.state.auth``
      (:class:`TokenIssuer`); any failure raises ``401``.
    """
    settings = getattr(request.app.state, "settings", None)
    if settings is None or not settings.auth_enabled:
        # Known boundary (kept as-is, #271): a missing app.state.settings is
        # only possible for a hand-assembled app without the lifespan, and it
        # fails open exactly like auth_enabled=False. The real lifespan always
        # sets it before any request is served.
        return "local"
    issuer: Optional[TokenIssuer] = getattr(request.app.state, "auth", None)
    if issuer is None:
        raise HTTPException(status_code=401, detail="auth not configured")
    raw: Optional[str] = None
    if authorization is not None:
        # A present-but-malformed header does not fall back to ?token=: the
        # REST face is header-only, so a bad header is a 401, not a downgrade.
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer":
            raw = value.strip()
    elif token is not None and query_token_allowed(request.method, request.url.path):
        raw = token.strip()
    if not raw or not issuer.verify(raw):
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return raw


def ensure_startup_auth_configured(settings: "Settings") -> None:
    """Refuse to start when auth is enabled but no usable secret is configured.

    ``AUTH_TOKEN=`` used to slip past the ``== "changeme"`` check; the issuer
    then fell back to the publicly documented placeholder secret and
    ``/auth/token`` accepted an empty password. That made the documented
    "enable auth before exposing the port" mitigation silently do nothing
    (#254 AC1). The default ``auth_enabled=false`` local-demo mode is untouched.

    This is an **entrypoint** guard: ``backend.main`` calls it at import time;
    any new process entrypoint that builds the app must call it too. Case and
    surrounding whitespace are ignored when comparing the placeholder.
    """
    if not settings.auth_enabled:
        return
    token = settings.auth_token.strip()
    if not token or token.lower() == PLACEHOLDER_AUTH_TOKEN.lower():
        raise RuntimeError(
            "auth_enabled=true but auth_token is empty or still the placeholder "
            f"'{PLACEHOLDER_AUTH_TOKEN}'. Set a strong auth_token via environment "
            "variable before enabling auth."
        )


def auth_state_message(settings: "Settings") -> str:
    """One startup line stating whether the local API is authenticated."""
    if settings.auth_enabled:
        return "auth enabled: a valid token is required for REST and SSE"
    return (
        "auth disabled: the local API is unauthenticated (local-demo default); "
        "set auth_enabled=true and a strong auth_token before exposing the port"
    )
