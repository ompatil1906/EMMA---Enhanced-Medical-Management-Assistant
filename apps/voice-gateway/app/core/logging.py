"""Structured JSON logging with secret redaction.

Policy (assignment §9): log session/call lifecycle and technical metadata only —
never patient information, API keys, tokens, or full audio payloads. Extra keys
whose names look sensitive are automatically redacted.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any, Final

_SENSITIVE_KEY_FRAGMENTS: Final[frozenset[str]] = frozenset(
    {"token", "secret", "password", "credential", "authorization", "api_key", "auth_key", "auth_"}
)
_STANDARD_RECORD_ATTRS: Final[frozenset[str]] = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS)


def redact(value: str | None) -> str:
    """Mask a sensitive value, preserving only a length hint and a 3-char tail."""
    if value is None:
        return "redacted[null]"
    text = str(value)
    if len(text) <= 3:
        return "*" * len(text)
    return "*" * (len(text) - 3) + text[-3:]


class JsonFormatter(logging.Formatter):
    """One JSON object per log line; extras land under `ctx` (sensitive keys redacted)."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": round(time.time(), 3),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        ctx: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_ATTRS:
                continue
            if isinstance(value, str):
                ctx[key] = redact(value) if is_sensitive_key(key) else value
            else:
                ctx[key] = value  # ints/bools/dicts carry structure, not secrets
        if ctx:
            entry["ctx"] = ctx
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_logging(level: str) -> None:
    """Install the JSON handler on the root logger (uvicorn's own loggers stay as-is)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]  # replace, don't stack up on repeated configure() calls
    root.setLevel(level)
    # Third parties that spam INFO on every frame; keep our signal clean.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
