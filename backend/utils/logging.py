"""Centralised structured logging.

Logs are written both to stdout and to ``<data_dir>/logs/agent.log``.
API keys are never logged (callers must redact them before logging).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from ..config import get_settings

_CONFIGURED: dict[str, logging.Logger] = {}


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
