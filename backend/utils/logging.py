"""Centralised structured logging.

Logs are written both to stdout and to ``<data_dir>/logs/agent.log``.
API keys are never logged (callers must redact them before logging).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from ..config import get_settings

_CONFIGURED: dict[str, logging.Logger] = {}

#: D-47 (#271): the token query value is the only secret that could reach
#: uvicorn's access log (the logged path includes the query string). A literal
#: "token=" match is not enough: FastAPI URL-decodes the *parameter name*, so
#: `?%74oken=SEKRET` is a real token parameter while uvicorn logs the raw text.
#: The filter parses the query, decodes each key and masks the value when the
#: decoded key is `token` (case-insensitive). Other parameters, their encoding
#: and their order are preserved byte-for-byte; a bare `token` pair has no
#: value to leak and is left as logged.
#: Private marker on the logger so installation is idempotent by identity.
_ACCESS_REDACTION_ATTR = "_qianfan_access_log_redaction"


def _redact_query_pair(pair: str) -> str:
    """Mask one `key=value` query pair when the decoded key is `token`."""
    key, sep, _value = pair.partition("=")
    try:
        decoded_key = unquote(key).strip().lower()
    except Exception:  # pragma: no cover - unquote is total for str input
        decoded_key = key.strip().lower()
    if sep and decoded_key == "token":
        # Keep the key exactly as logged (it may itself be percent-encoded);
        # only the secret value changes. `?token` without "=" carries no value.
        return key + "=***"
    return pair


def _redact_access_string(text: str) -> str:
    """Mask token values inside one logged request path (query-aware)."""
    if "?" not in text:
        return text
    base, _, query = text.partition("?")
    if not query:
        return text
    redacted = "&".join(_redact_query_pair(pair) for pair in query.split("&"))
    return base + "?" + redacted


def _redact_access_arg(value: Any) -> Any:
    """Rewrite token query values inside one access-log arg (recursively)."""
    if isinstance(value, str):
        return _redact_access_string(value)
    if isinstance(value, tuple):
        return tuple(_redact_access_arg(item) for item in value)
    if isinstance(value, list):
        return [_redact_access_arg(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_access_arg(item) for key, item in value.items()}
    return value


class AccessLogRedactionFilter(logging.Filter):
    """D-47 (#271): mask `?token=...` in uvicorn access-log records.

    uvicorn formats access lines from `record.args` (client - "METHOD path
    HTTP/x" status), so the path with its query string is one of those args.
    The filter rewrites the args before any handler formats/emits the record;
    non-matching paths pass through unchanged.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = _redact_access_arg(record.args)
        return True


def install_access_log_redaction() -> AccessLogRedactionFilter:
    """Idempotently attach :class:`AccessLogRedactionFilter` to `uvicorn.access`.

    Installed from :mod:`backend.main` at import time, which covers both
    `uvicorn.run(...)` and `python -m uvicorn backend.main:app` (the --reload
    child imports the module in its own process). Identity (not just type) is
    checked, so multiple import paths cannot wrap the args twice.
    """
    logger = logging.getLogger("uvicorn.access")
    existing = getattr(logger, _ACCESS_REDACTION_ATTR, None)
    if isinstance(existing, AccessLogRedactionFilter):
        return existing
    redaction = AccessLogRedactionFilter()
    logger.addFilter(redaction)
    setattr(logger, _ACCESS_REDACTION_ATTR, redaction)
    return redaction


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger. Idempotent per ``name``."""
    if name in _CONFIGURED:
        return _CONFIGURED[name]

    settings = get_settings()
    logger = logging.getLogger(f"agent.{name}")
    if logger.handlers:
        _CONFIGURED[name] = logger
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    try:
        log_dir: Path = settings.data_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "agent.log", encoding="utf-8")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except Exception:  # pragma: no cover - logging must never crash the app
        pass

    logger.propagate = False
    _CONFIGURED[name] = logger
    return logger
