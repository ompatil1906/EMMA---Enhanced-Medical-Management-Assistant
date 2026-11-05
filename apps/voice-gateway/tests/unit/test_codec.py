"""Base64 audio codec contract tests."""

import pytest

from app.integrations.exotel.codec import decode_media_payload, encode_media_payload
from app.integrations.exotel.errors import ExotelProtocolError


class TestRoundtrip:
    def test_roundtrip_preserves_bytes(self) -> None:
        payload = bytes(range(256)) + b"\x00\xff"
        assert decode_media_payload(encode_media_payload(payload)) == payload

    def test_empty_payload_roundtrip(self) -> None:
        assert encode_media_payload(b"") == ""
        assert decode_media_payload("") == b""


class TestDecodeErrors:
    def test_non_ascii_rejected(self) -> None:
        with pytest.raises(ExotelProtocolError, match="non-ASCII"):
            decode_media_payload("ümlaut")

    def test_invalid_base64_rejected(self) -> None:
        with pytest.raises(ExotelProtocolError, match="not valid base64"):
            decode_media_payload("!!!not-base64!!!")

    def test_whitespace_is_invalid(self) -> None:
        # validate=True means even benign padding/newlines are rejected — strict is intentional.
        with pytest.raises(ExotelProtocolError):
            decode_media_payload("AA==\n")

    def test_max_bytes_enforced(self) -> None:
        with pytest.raises(ExotelProtocolError, match="exceeds limit"):
            decode_media_payload(encode_media_payload(b"x" * 11), max_bytes=10)

    def test_at_limit_accepted(self) -> None:
        payload = b"x" * 10
        assert decode_media_payload(encode_media_payload(payload), max_bytes=10) == payload

    def test_empty_payload_bypasses_limit(self) -> None:
        assert decode_media_payload("", max_bytes=0) == b""


class TestEncode:
    def test_no_newlines_in_output(self) -> None:
        # ws.send_text frames cannot contain unencoded newlines
        encoded = encode_media_payload(bytes(range(256)) * 100)
        assert "\n" not in encoded and "\r" not in encoded
