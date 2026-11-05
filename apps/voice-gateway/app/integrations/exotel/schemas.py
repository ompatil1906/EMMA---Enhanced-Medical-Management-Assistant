"""Exotel AgentStream WebSocket protocol schemas — verbatim from official docs.

Verified 2026-11-06 against:
  * https://developer.exotel.com/docs/agentstream/websocket-protocol
  * https://developer.exotel.com/docs/agentstream/stream-voicebot-applet
  * https://github.com/exotel/Agent-Stream/blob/main/docs/AGENTSTREAM_WSS_PROTOCOL.md
  * https://docs.exotel.com/exotel-agentstream/voicebot-applet

Field-name variance (per the official AgentStream guide): exact field names can
vary slightly between applet/Connect paths. These models therefore ignore unknown
extra fields and treat identifiers/metadata as optional with defaults — the
route layer logs deviations instead of crashing.

Do not add events or fields here without updating the protocol doc first.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from app.integrations.exotel.errors import ExotelProtocolError


class _ExotelEnvelope(BaseModel):
    """Base for all Exotel inbound JSON envelopes."""

    model_config = ConfigDict(
        extra="ignore",  # tolerate provider field drift (official tolerance guidance)
        validate_by_alias=True,
        validate_by_name=True,
    )

    event: str


class ExotelConnected(_ExotelEnvelope):
    """Exotel → bot: WebSocket accepted (verified event)."""

    event: Literal["connected"]


class ExotelMediaFormat(BaseModel):
    """Verified nested schema: start.media_format = {encoding, sample_rate, bit_rate}."""

    model_config = ConfigDict(validate_by_alias=True, validate_by_name=True, extra="ignore")

    encoding: str | None = None
    sample_rate: int | None = None
    bit_rate: int | None = None

    @field_validator("encoding", mode="before")
    @classmethod
    def _tolerate_encoding_variants(cls, value: Any) -> str:
        return str(value).strip().lower()

    @field_validator("sample_rate", mode="before")
    @classmethod
    def _coerce_sample_rate(cls, value: Any) -> int:
        return int(str(value))


class ExotelStart(_ExotelEnvelope):
    """Exotel → bot: stream metadata. Verified fields:
    stream_sid, call_sid, account_sid, from, to, custom_parameters, media_format.
    """

    event: Literal["start"]
    stream_sid: str | None = None
    call_sid: str | None = None
    account_sid: str | None = None
    from_number: str | None = Field(default=None, alias="from")
    to_number: str | None = Field(default=None, alias="to")
    custom_parameters: dict[str, Any] | None = None
    media_format: ExotelMediaFormat | None = None


class ExotelMedia(_ExotelEnvelope):
    """Exotel → bot: base64 PCM audio chunk. Verified fields: chunk, timestamp."""

    event: Literal["media"]
    chunk: str | None = None
    timestamp: str | None = None


class ExotelDtmf(_ExotelEnvelope):
    """Exotel → bot: DTMF digit (applet-dependent; tolerated, never logged with value)."""

    event: Literal["dtmf"]
    digit: str | None = None
    duration: int | None = None


class ExotelMarkIn(_ExotelEnvelope):
    """Exotel → bot: playback/sync marker (applet-dependent; tolerated)."""

    event: Literal["mark"]
    name: str | None = None


class ExotelStop(_ExotelEnvelope):
    """Exotel → bot: stream ending. Verified: call_sid, account_sid, reason {stopped|callended}."""

    event: Literal["stop"]
    call_sid: str | None = None
    account_sid: str | None = None
    reason: str | None = None


class ExotelUnknownEvent(BaseModel):
    """Well-formed frame whose `event` is outside the verified set.

    OUR tolerance policy (not an Exotel definition): log and continue — unknown
    provider event types must never crash a live call session.
    """

    model_config = ConfigDict(extra="ignore")

    event: str


_KNOWN_INBOUND_EVENTS: frozenset[str] = frozenset(
    {"connected", "start", "media", "dtmf", "mark", "stop"}
)

ExotelInboundEvent = Annotated[
    ExotelConnected | ExotelStart | ExotelMedia | ExotelDtmf | ExotelMarkIn | ExotelStop,
    Field(discriminator="event"),
]

ExotelInbound = ExotelConnected | ExotelStart | ExotelMedia | ExotelDtmf | ExotelMarkIn | ExotelStop

_INBOUND_ADAPTER = TypeAdapter(ExotelInboundEvent)


def parse_inbound_envelope(json_text: str) -> ExotelInbound | ExotelUnknownEvent:
    """Parse a raw WebSocket text frame into a typed Exotel inbound event.

    Raises:
        ExotelProtocolError: malformed JSON, missing `event`, or a *known* event
            whose fields fail the verified shape. Unknown-but-well-formed events
            return `ExotelUnknownEvent` (tolerated downstream, not an error).
    """
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ExotelProtocolError(f"malformed JSON: {exc.msg}") from exc

    if not isinstance(payload, dict):
        raise ExotelProtocolError("frame is not a JSON object")

    event_name = payload.get("event")
    if not isinstance(event_name, str) or not event_name:
        raise ExotelProtocolError("missing required field 'event'")

    if event_name not in _KNOWN_INBOUND_EVENTS:
        return ExotelUnknownEvent(event=event_name)

    try:
        return _INBOUND_ADAPTER.validate_python(payload)
    except ValidationError as exc:
        raise ExotelProtocolError(
            f"event {event_name!r} payload failed validation: {exc.errors()[0]['msg']}"
        ) from exc
