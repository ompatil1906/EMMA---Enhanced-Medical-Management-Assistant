"""Provider-independent domain events for the voice gateway.

Routes and services must consume/produce only these frozen Pydantic models.
Provider JSON shapes are translated in `app/integrations/<provider>/adapter.py`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.audio import AudioFormat


class GatewayEvent(BaseModel):
    """Base type: domain events are immutable facts about a session."""

    model_config = ConfigDict(frozen=True)


class CallStarted(GatewayEvent):
    """A stream/call context became available (provider `start` equivalent)."""

    session_id: str
    provider: str = "unknown"
    stream_sid: str | None = None
    call_sid: str | None = None
    account_sid: str | None = None
    from_number: str | None = None
    to_number: str | None = None
    # Arbitrary custom metadata. Never logged by default (may contain customer data).
    custom_parameters: dict[str, Any] = Field(default_factory=dict)
    audio_format: AudioFormat | None = None
    raw_envelope: dict[str, Any] | None = None  # untouched provider payload (adapter-internal)


class AudioReceived(GatewayEvent):
    """One decoded inbound audio frame."""

    session_id: str
    sequence: int
    payload: bytes
    audio_format: AudioFormat
    provider_chunk_id: str | None = None  # provider-native chunk identifier if present
    provider_timestamp: str | None = None  # provider-native timestamp string if present


class AudioToSend(GatewayEvent):
    """An audio frame the gateway must deliver back to the provider."""

    session_id: str
    sequence: int
    payload: bytes
    audio_format: AudioFormat
    request_sequence: int | None = None  # inbound sequence this frame answers (order invariant)


class CallEnded(GatewayEvent):
    """The provider signalled the end of the stream (provider `stop` equivalent)."""

    session_id: str
    reason: str | None = None


class ConnectionClosed(GatewayEvent):
    """The WebSocket connection terminated (normally or abruptly)."""

    session_id: str
    code: int | None = None
    reason: str | None = None
    abnormal: bool = True
