"""Per-connection session book-keeping (identifier adoption + counters).

Contains no protocol parsing — the Exotel adapter owns provider semantics.
"""

from __future__ import annotations

import time
import uuid
from typing import Literal

from app.domain.audio import DEFAULT_CHANNELS, DEFAULT_ENCODING, DEFAULT_SAMPLE_RATE, AudioFormat

SessionState = Literal["pending", "active", "ended"]


def new_pending_session_id() -> str:
    """Locally generated id until the provider supplies its stream id."""
    return f"pending-{uuid.uuid4()}"


class VoiceSession:
    """Stateful book-keeping object for one WebSocket session (transport-level data only)."""

    __slots__ = (
        "session_id",
        "provider",
        "stream_sid",
        "call_sid",
        "audio_format",
        "state",
        "started_at",
        "frames_received",
        "frames_sent",
        "bytes_received",
        "bytes_sent",
        "malformed_count",
    )

    def __init__(self, provider: str, session_id: str | None = None) -> None:
        self.session_id: str = session_id or new_pending_session_id()
        self.provider: str = provider
        self.stream_sid: str | None = None
        self.call_sid: str | None = None
        # Exotel default until a `start` negotiates otherwise (verified default 8 kHz).
        self.audio_format: AudioFormat = AudioFormat(
            encoding=DEFAULT_ENCODING, sample_rate=DEFAULT_SAMPLE_RATE, channels=DEFAULT_CHANNELS
        )
        self.state: SessionState = "pending"
        self.started_at: float = time.monotonic()
        self.frames_received: int = 0
        self.frames_sent: int = 0
        self.bytes_received: int = 0
        self.bytes_sent: int = 0
        self.malformed_count: int = 0

    def adopt_stream(self, *, stream_sid: str | None, call_sid: str | None) -> str:
        """Adopt provider ids; returns the previous session id (registry re-keys with it)."""
        previous_id = self.session_id
        if stream_sid:
            self.stream_sid = stream_sid
            self.session_id = stream_sid
        if call_sid:
            self.call_sid = call_sid
        self.state = "active"
        return previous_id

    @property
    def uptime_seconds(self) -> float:
        return time.monotonic() - self.started_at
