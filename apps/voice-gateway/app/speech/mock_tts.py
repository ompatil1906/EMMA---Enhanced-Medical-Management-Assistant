"""Deterministic mock TTS provider — synthetic sine-pulse PCM16, no network.

Contract implementation notes (tested by unit tests):

* Chunks are 100 ms of PCM16 mono at the negotiated sample rate; a non-empty
  input of N (stripped) chars maps to N × 70 ms of audio capped at 4.8 s —
  deterministic, recorded in this docstring.
* Output stream order: `SynthesisStarted` → `SynthesisChunk`… →
  `SynthesisCompleted` (completed is ALWAYS last; on cancellation the
  completed event carries `cancelled=True`).
* Empty/whitespace text yields only `SynthesisStarted` + `SynthesisCompleted`
  (`total_bytes=0`) — empty-input handling is exercised end to end.
* Payload is a deterministic 240 Hz carrier with a shaped envelope per chunk
  (tone, not speech; the mock proves streaming/framing/format, not phonetics —
  same honesty rule as the mock STT).
* Provider-internal cancellation (`cancel_turn`) interrupts generation at the
  next chunk boundary (documented, cooperative, no background task left).
"""

from __future__ import annotations

import asyncio
import math
import struct
import uuid
from collections.abc import AsyncIterator
from typing import Final

from app.domain.audio import AudioFormat
from app.domain.speech import SynthesisChunk, SynthesisCompleted, SynthesisStarted, TtsEvent

_MS_PER_CHAR: Final[int] = 70
_MAX_DURATION_MS: Final[int] = 4800
_CHUNK_MS: Final[int] = 100
_CARRIER_HZ: Final[float] = 240.0
_AMPLITUDE: Final[int] = 3640  # ~ -18 dBFS int16


class MockTextToSpeechProvider:
    """Synthetic-TTS implementing the TextToSpeechProvider contract."""

    def __init__(self) -> None:
        self._cancelled: set[str] = set()

    @property
    def provider_name(self) -> str:
        return "mock-tts"

    async def synthesize(
        self, text: str, session_id: str, audio_format: AudioFormat
    ) -> AsyncIterator[TtsEvent]:
        utterance_id = uuid.uuid4().hex
        stripped = text.strip()
        if not stripped:
            yield SynthesisStarted(
                session_id=session_id,
                provider=self.provider_name,
                utterance_id=utterance_id,
                text_chars=0,
                audio_format=audio_format,
            )
            yield SynthesisCompleted(
                session_id=session_id,
                provider=self.provider_name,
                utterance_id=utterance_id,
                total_bytes=0,
                chunk_count=0,
            )
            return

        duration_ms = min(len(stripped) * _MS_PER_CHAR, _MAX_DURATION_MS)
        chunk_count = max(1, math.ceil(duration_ms / _CHUNK_MS))
        yield SynthesisStarted(
            session_id=session_id,
            provider=self.provider_name,
            utterance_id=utterance_id,
            text_chars=len(stripped),
            audio_format=audio_format,
        )
        samples_per_chunk = max(1, round(audio_format.sample_rate * _CHUNK_MS / 1000))
        emitted_bytes = 0
        emitted_chunks = 0
        cancelled = utterance_id in self._cancelled
        for index in range(chunk_count):
            if utterance_id in self._cancelled:
                cancelled = True
                break
            payload = _tone_chunk(index, samples_per_chunk)
            emitted_bytes += len(payload)
            emitted_chunks += 1
            yield SynthesisChunk(
                session_id=session_id,
                provider=self.provider_name,
                utterance_id=utterance_id,
                payload=payload,
                audio_format=audio_format,
            )
            # The mock synthesizes faster than real-time; a scheduler quantum
            # lets the consuming pump interleave (keeps the generator cooperative).
            await asyncio.sleep(0)

        yield SynthesisCompleted(
            session_id=session_id,
            provider=self.provider_name,
            utterance_id=utterance_id,
            total_bytes=emitted_bytes,
            chunk_count=emitted_chunks,
            cancelled=cancelled,
        )

    async def cancel_turn(self, session_id: str, utterance_id: str) -> None:
        self._cancelled.add(utterance_id)


def _tone_chunk(index: int, samples: int) -> bytes:
    """Deterministic carrier chunk (stable across processes for given inputs)."""
    out = bytearray()
    total = samples * index  # absolute sample offset keeps the waveform continuous
    for i in range(samples):
        phase = 2.0 * math.pi * _CARRIER_HZ * ((total + i) / 8000.0)
        envelope = 0.6 + 0.4 * math.sin(2.0 * math.pi * ((total + i) % samples) / samples)
        value = int(_AMPLITUDE * envelope * math.sin(phase))
        out += struct.pack("<h", max(-32768, min(32767, value)))
    return bytes(out)
