"""Provider-independent speech-domain events (SttEvent / TtsEvent).

Day 3 extension of `app.domain.events`: normalized speech events consumed and
produced by pipeline services. Provider-specific shapes (Azure SDK callback
objects, etc.) are translated in `app/speech/<provider>.py` — routes and
services never see provider types (same rule as the Exotel adapter).

Events are frozen facts; the pipeline stamps metrics when it observes them.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import Field

from app.domain.audio import AudioFormat
from app.domain.events import GatewayEvent


def _now() -> float:
    return time.time()


class SpeechEventBase(GatewayEvent):
    """Common speech-event header: ids + provider + wall-clock timestamp."""

    session_id: str
    provider: str
    timestamp: float = Field(default_factory=_now)


# --- Streaming speech-to-text ----------------------------------------------


class RecognitionStarted(SpeechEventBase):
    """The recognition stream is live and accepting audio."""


class PartialTranscript(SpeechEventBase):
    """Interim recognition result — subject to change, NOT final user intent."""

    sequence: int  # 1-based utterance ordinal within the session
    text: str
    confidence: float | None = None  # provider confidence when available
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class FinalTranscript(SpeechEventBase):
    """End-of-utterance recognition result — the only kind treated as intent."""

    sequence: int
    text: str
    confidence: float | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class RecognitionStopped(SpeechEventBase):
    """Recognition stream ended cleanly (stop requested or provider close)."""

    reason: str = "session_stop"  # session_stop | provider_closed


class RecognitionError(SpeechEventBase):
    """Recognition failure. `fatal` sessions must be torn down, never retried silently."""

    code: str = "recognition_error"
    message: str = ""
    fatal: bool = False


SttEvent = (
    RecognitionStarted | PartialTranscript | FinalTranscript | RecognitionStopped | RecognitionError
)


# --- Text-to-synthesis -------------------------------------------------------


class SynthesisStarted(SpeechEventBase):
    """A synthesis request was accepted (`utterance_id` correlates its chunks)."""

    utterance_id: str
    text_chars: int
    audio_format: AudioFormat


class SynthesisChunk(SpeechEventBase):
    """One incremental synthesized-audio chunk (provider streaming, if supported)."""

    utterance_id: str
    payload: bytes
    audio_format: AudioFormat


class SynthesisCompleted(SpeechEventBase):
    """Synthesis finished. Emitted last (after chunks); `cancelled` when aborted."""

    utterance_id: str
    total_bytes: int
    chunk_count: int
    cancelled: bool = False


TtsEvent = SynthesisStarted | SynthesisChunk | SynthesisCompleted
