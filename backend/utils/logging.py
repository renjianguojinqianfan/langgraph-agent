"""Centralised structured logging.

Logs are written both to stdout and to ``<data_dir>/logs/agent.log``.
API keys are never logged (callers must redact them before logging).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional, TextIO

from ..config import get_settings

_CONFIGURED: dict[str, logging.Logger] = {}

# Console-stream indirection (Issue #90). ``get_logger`` used to bind each
# handler to ``sys.stdout`` at first use, so loggers created *after* an entry
# point had already swept the then-existing handlers kept writing to stdout —
# which broke headless's ``--output json`` contract (the result object is the
# only thing on stdout). Resolution goes through this variable instead: flip
# it once via :func:`set_console_stream` and every later-created logger honors
# it. ``None`` = default (stdout, the server path).
_console_stream: Optional[TextIO] = None


def set_console_stream(stream: Optional[TextIO]) -> None:
    """Repoint loggers created from now on at ``stream`` (``None`` restores stdout)."""
    global _console_stream
    _console_stream = stream


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

    stream = logging.StreamHandler(_console_stream if _console_stream is not None else sys.stdout)
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
