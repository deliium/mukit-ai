"""Configurable logger for the adaptive client.

Level follows ``LOG_LEVEL`` (default INFO). Records never include the bearer
token, the Authorization header, context values, mappings, or cue lists.
"""

from __future__ import annotations

import logging
import os

LOGGER_NAME = "mukit_adaptive"

_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
}


def get_logger() -> logging.Logger:
    """Return the package logger, applying ``LOG_LEVEL`` on each call."""
    logger = logging.getLogger(LOGGER_NAME)
    if not any(isinstance(handler, logging.StreamHandler) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    logger.propagate = True
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    logger.setLevel(_LEVELS.get(level_name, logging.INFO))
    return logger


def log_event(level: int, event: str, **fields: object) -> None:
    """Log ``event`` with sorted ``key=value`` fields. Drop nothing silently."""
    logger = get_logger()
    rendered = " ".join(f"{key}={fields[key]}" for key in sorted(fields))
    message = event if not rendered else f"{event} {rendered}"
    extra = {"event": event}
    for key, value in fields.items():
        if key not in _RESERVED:
            extra[key] = value
    logger.log(level, message, extra=extra)
