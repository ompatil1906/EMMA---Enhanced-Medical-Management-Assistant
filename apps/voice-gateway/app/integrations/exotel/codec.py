"""Base64 PCM codec for the Exotel `media` event (verified transport rules).

Verified facts encoded here (2026-11-06, official docs — see protocol doc):
  * Audio is raw linear PCM, 16-bit signed little-endian, mono.
  * Payload rides base64-encoded inside `media.chunk`.
  * Provider-side chunk guidance: 3,200–100,000 bytes per chunk (defaults).
Max-size enforcement is a config knob (`EXOTEL_MAX_CHUNK_BYTES`) because the
constraint applies to Exotel's own writes; our inbound tolerance policy is our
own decision, not a documented provider rule.
"""

from __future__ import annotations

import base64
import binascii

from app.integrations.exotel.errors import ExotelProtocolError


def encode_media_payload(payload: bytes) -> str:
    """Encode raw PCM bytes for an outbound `media` envelope."""
    return base64.b64encode(payload).decode("ascii")


def decode_media_payload(encoded: str, *, max_bytes: int | None = None) -> bytes:
    """Decode a base64 audio chunk into raw PCM bytes.

    Raises:
        ExotelProtocolError: non-ASCII content, invalid base64, or a payload
            larger than `max_bytes` (when provided). Empty payloads return b"".
    """
    if encoded == "":
        return b""
    try:
        raw = encoded.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ExotelProtocolError("audio chunk contains non-ASCII characters") from exc
    try:
        decoded = base64.b64decode(raw, validate=True)
    except binascii.Error as exc:
        raise ExotelProtocolError("audio chunk is not valid base64") from exc
    if max_bytes is not None and len(decoded) > max_bytes:
        raise ExotelProtocolError(
            f"decoded audio chunk {len(decoded)} bytes exceeds limit {max_bytes} bytes"
        )
    return decoded
