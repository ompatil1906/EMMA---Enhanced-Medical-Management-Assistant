"""Exotel AgentStream adapter: verified JSON envelopes ⇄ internal domain events.

This is the ONLY module allowed to know Exotel's message shapes. A future
Twilio/Plivo/browser adapter implements the same three operations for its own
protocol so the echo service never changes.
"""

from __future__ import annotations

import base64
import json
import logging

from app.domain.audio import DEFAULT_ENCODING, DEFAULT_SAMPLE_RATE, AudioFormat
from app.domain.events import AudioReceived, AudioToSend, CallStarted
from app.integrations.exotel import codec
from app.integrations.exotel.schemas import (
    ExotelConnected,
    ExotelDtmf,
    ExotelMarkIn,
    ExotelMedia,
    ExotelStart,
    ExotelStop,
    parse_inbound_envelope,
)

logger = logging.getLogger(__name__)

# Outbound `media` envelopes are exactly 3 top-level fields per the verified docs:
# {event, stream_sid, media{payload}}. `stream_sid` is omitted when the provider
# has not supplied one yet (deviation is logged by the route layer).
_OUTBOUND_MEDIA_EVENT: dict[str, str] = {"event": "media"}


def build_outbound_media_envelope(audio: AudioToSend, *, stream_sid: str | None = None) -> str:
    """Serialize an AudioToSend frame into a verified-shape outbound `media` envelope."""
    envelope: dict[str, object] = dict(_OUTBOUND_MEDIA_EVENT)
    if stream_sid is not None:
        envelope["stream_sid"] = stream_sid
    envelope["media"] = {"payload": codec.encode_media_payload(audio.payload)}
    return json.dumps(envelope, separators=(",", ":"))


def build_auth_token(user: str, secret: str) -> str:
    """Base64 token body of the Basic auth header (verified Exotel AgentStream scheme)."""
    return base64.b64encode(f"{user}:{secret}".encode()).decode("ascii")


def build_auth_header(user: str, secret: str) -> str:
    """Complete header value: `Basic <token>` (as verified for AgentStream WSS)."""
    return f"Basic {build_auth_token(user, secret)}"


class ExotelAdapter:
    """Translate Exotel inbound envelopes into domain events for one connection."""

    def parse_inbound(
        self, raw_text: str
    ) -> ExotelConnected | ExotelStart | ExotelMedia | ExotelDtmf | ExotelMarkIn | ExotelStop:
        """Parse any inbound provider frame (raises ExotelProtocolError on garbage)."""
        return parse_inbound_envelope(raw_text)

    def handle_start(self, start: ExotelStart) -> CallStarted:
        """Convert a `start` event, negotiating audio format strictly."""
        audio_format = self._negotiate_audio_format(start)
        return CallStarted(
            session_id=start.stream_sid or "unknown-stream",  # route layer adopts concrete id
            provider="exotel",
            stream_sid=start.stream_sid,
            call_sid=start.call_sid,
            account_sid=start.account_sid,
            from_number=start.from_number,
            to_number=start.to_number,
            custom_parameters=start.custom_parameters or {},
            audio_format=audio_format,
            raw_envelope=start.model_dump(by_alias=True, exclude_none=True),
        )

    def handle_media(
        self,
        media: ExotelMedia,
        *,
        session_id: str,
        audio_format: AudioFormat,
        sequence: int,
        max_chunk_bytes: int,
    ) -> AudioReceived:
        """Convert a `media` event into a decoded AudioReceived frame."""
        encoded = media.chunk or ""
        payload = codec.decode_media_payload(encoded, max_bytes=max_chunk_bytes)
        return AudioReceived(
            session_id=session_id,
            sequence=sequence,
            payload=payload,
            audio_format=audio_format,
            provider_chunk_id=media.timestamp,  # timestamp doubles as chunk identifier
            provider_timestamp=media.timestamp,
        )

    def _negotiate_audio_format(self, start: ExotelStart) -> AudioFormat:
        """Strict negotiation: unsupported encoding/sample rate raises → route closes 1008."""
        declared = start.media_format
        if declared is None:
            # No negotiation in `start`; the route keeps its query-parameter/default format.
            return None
        negotiated = AudioFormat(
            encoding=declared.encoding or DEFAULT_ENCODING,
            sample_rate=declared.sample_rate or DEFAULT_SAMPLE_RATE,
            channels=1,
        )
        logger.debug(
            "exotel media_format negotiated", extra={"media_format": negotiated.model_dump()}
        )
        return negotiated
