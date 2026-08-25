"""Basic token authentication (P1 item 5).

A small, dependency-free token service built on the standard library:

* :class:`TokenIssuer` — issues HMAC-signed tokens
  ``{expires_ts}.{nonce}.{sig}`` and registers them in an in-memory store so
  verification is (signature + registration + expiry) based;
* :func:`verify_token` — a FastAPI dependency that reads
  ``Authorization: Bearer <token>`` (REST) or ``?token=<token>`` (SSE, since
  ``EventSource`` cannot set headers). When ``auth_enabled=False`` it returns
  ``"local"`` immediately — **zero regression** for the local demo.

Design decisions:

* dependency injection (``Depends``), **not** a global middleware — protected
  endpoints opt in explicitly and ``/auth/token`` + ``/health`` stay public;
* ``sse.py`` is untouched — the check happens in the route layer before the
  streaming response is created.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from typing import Any, Dict, Optional, Tuple

from fastapi import Header, HTTPException, Query, Request

#: localStorage key the frontend uses to persist the token.
TOKEN_STORAGE_KEY = "lga_auth_token"


class TokenIssuer:
    """Issue and verify HMAC-signed, server-registered bearer tokens."""

    def __init__(self, secret: str, ttl_sec: int = 86400) -> None:
        self._secret = (secret or "changeme").encode("utf-8")
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


def verify_token(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None),
) -> str:
    """FastAPI dependency guarding protected endpoints.

    * ``auth_enabled=False`` -> return ``"local"`` (passthrough, zero regress);
    * otherwise read ``Authorization: Bearer`` or ``?token=`` and validate via
      ``app.state.auth`` (:class:`TokenIssuer`); failures raise ``401``.
    """
    settings = getattr(request.app.state, "settings", None)
    if settings is None or not settings.auth_enabled:
        return "local"
    issuer: Optional[TokenIssuer] = getattr(request.app.state, "auth", None)
    if issuer is None:
        raise HTTPException(status_code=401, detail="auth not configured")
    raw: Optional[str] = None
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
    elif token:
        raw = token.strip()
    if not raw or not issuer.verify(raw):
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return raw
