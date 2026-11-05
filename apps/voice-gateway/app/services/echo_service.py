"""Day 2 echo service — the provider-independent audio path.

    AudioReceived ────────────────────────────────────────────► AudioToSend

Deliberately provider-agnostic: it consumes domain events only, so a future
Twilio/Plivo/browser adapter or a full pipeline can replace its internals
without touching transport code.

Day 3 wiring plan (do NOT implement today):
    AudioReceived ─► [VAD]        endpointing + barge-in gating (is caller done / interrupting?)
                ─► [STT]          Azure Speech F0 streaming speech-to-text
                ─► [LLM brain]    Gemini Flash + fallback adapters, guarded prompts
                ─► [TTS]          Azure Speech F0 synthesis → AudioToSend stream
Between VAD end-of-speech and the first TTS frame sits the p50 ≤ 1.5 s latency
budget tracked in architecture ADR docs.

This echo implementation never blocks the event loop (single cheap dict copy) —
the async signature exists so the future pipeline can await STT/TTS hops.
"""

from __future__ import annotations

from app.domain.events import AudioReceived, AudioToSend


class EchoService:
    """Frame-in → frame-out echo. Byte-for-byte; no transcode, no resampling."""

    async def process(self, audio: AudioReceived) -> AudioToSend:
        # Day 3 confirmed: VAD hooks here FIRST (decide whether the caller has
        # finished speaking or interrupted), then the frame is forwarded to STT.
        # NOTE: if STT/TTS become codec-sensitive, the negotiated
        # audio_format travels with every frame so resampling decisions stay local
        # to this service — transport and adapter never resample silently.
        return AudioToSend(
            session_id=audio.session_id,
            sequence=audio.sequence,
            payload=audio.payload,
            audio_format=audio.audio_format,
            request_sequence=audio.sequence,
        )
