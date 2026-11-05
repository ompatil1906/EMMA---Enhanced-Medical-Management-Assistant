"""Structured logging + redaction contract tests."""

import json
import logging

import pytest

from app.core.logging import JsonFormatter, is_sensitive_key, redact


def _record(**extras: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test.logger",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    for key, value in extras.items():
        setattr(record, key, value)
    return record


class TestRedaction:
    @pytest.mark.parametrize(
        "key", ["api_token", "WS_AUTH_TOKEN", "exotel_api_token", "client_secret", "auth_key"]
    )
    def test_sensitive_keys_detected(self, key: str) -> None:
        assert is_sensitive_key(key) is True

    @pytest.mark.parametrize("key", ["session_id", "frames_in", "media_format"])
    def test_technical_keys_not_redacted(self, key: str) -> None:
        assert is_sensitive_key(key) is False

    def test_redact_masks_value(self) -> None:
        assert redact("abcdef123456") == "*********456"
        assert redact("abc") == "***"
        assert redact(None) == "redacted[null]"


class TestJsonFormatter:
    def test_shape_and_context(self) -> None:
        formatted = json.loads(JsonFormatter().format(_record(session_id="s1", frames_in=3)))
        assert formatted["msg"] == "hello"
        assert formatted["level"] == "WARNING"
        assert formatted["ctx"] == {"session_id": "s1", "frames_in": 3}

    def test_sensitive_values_are_never_emitted_raw(self) -> None:
        formatted = json.loads(
            JsonFormatter().format(_record(auth_header="Basic REAL-SECRET", note="public"))
        )
        assert "REAL-SECRET" not in json.dumps(formatted)
        assert formatted["ctx"]["auth_header"].startswith("*")
        assert formatted["ctx"]["note"] == "public"

    def test_boolean_flags_are_not_string_redacted(self) -> None:
        formatted = json.loads(JsonFormatter().format(_record(ws_auth_required=False, frames_in=3)))
        assert formatted["ctx"]["ws_auth_required"] is False
        assert formatted["ctx"]["frames_in"] == 3
