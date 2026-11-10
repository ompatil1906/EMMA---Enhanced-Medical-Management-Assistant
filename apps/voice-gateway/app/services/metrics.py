"""Per-session voice metrics (compact JSON minutes, Langfuse-ready later).

Correlation-first design: every record carries session_id + call_sid +
practice_id and provider names/rates. Timings are monotonic deltas in
milliseconds. CONTENT POLICY (assignment §13): no transcript text, no audio
payloads, no secrets — counts/lengths only.

Day 20 adds the latency breakdown Pipecat reports per turn: time from the final
transcript to the first synthesised audio chunk, LLM time, TTS time, the
user-visible response latency, and interruption latency. Those numbers are what
`/api/v1/diagnostics` aggregates (p50/p95 over recent sessions).
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("emma.metrics")


class SessionMetrics:
    """Collects one session's speech-pipeline facts; emits ONE compact record."""

    def __init__(self, *, session_id: str, call_sid: str | None) -> None:
        self.session_id = session_id
        self.call_sid = call_sid
        self._t0 = time.monotonic()
        self.providers: dict[str, str] = {}
        self.sample_rate: int | None = None
        self.stt: dict[str, Any] = {
            "started_at_ms": None,
            "first_partial_ms": None,
            "first_final_ms": None,
            "partials": 0,
            "finals": 0,
            "empty_finals": 0,
            "low_confidence": 0,
        }
        self.processing: dict[str, Any] = {"started_at_ms": None, "turn_total_ms": []}
        self.tts: dict[str, Any] = {
            "started_at_ms": None,
            "first_chunk_ms": None,
            "completed_ms": None,
            "chunks": 0,
            "bytes": 0,
        }
        self.audio: dict[str, Any] = {
            "frames_in": 0,
            "bytes_in": 0,
            "conversions": 0,
            "conversion_notes": [],
            "bytes_out": 0,
            "outbound_chunks": 0,
        }
        self.providers_errors: list[str] = []
        self.state_transitions: int = 0
        self.practice_api: dict[str, Any] = {"outcomes": []}
        self.errors: list[str] = []
        # Day 20: turn assembly / interruption / latency breakdown.
        self.turns: dict[str, Any] = {
            "dropped": 0,
            "dropped_reasons": {},
            "held": 0,
            "interrupted": 0,
            "interrupted_speech": 0,
            "interruption_latency_ms": [],
        }
        self.latency: dict[str, Any] = {
            "final_to_processing_ms": [],
            "processing_to_first_audio_ms": [],
            "turn_total_ms": [],  # mirrored into processing.turn_total_ms
            "llm_ms": [],
            "tts_ms": [],
        }
        self.vad: dict[str, Any] = {
            "speech_starts": 0,
            "speech_ends": 0,
            "speech_ms": [],
            "early_barge_ins": 0,
            "barge_in_latency_ms": [],
        }
        self._final_at: float | None = None
        self._speech_started_at: float | None = None
        self._llm_started: float | None = None
        self._llm_ms: float | None = None
        self._tts_started: float | None = None
        self._first_audio_at: float | None = None

    # --- lifecycle stamps ---------------------------------------------------------

    def note_stt_started(self, provider: str, sample_rate: int) -> None:
        self.providers["stt"] = provider
        self.sample_rate = sample_rate
        self.stt["started_at_ms"] = self._elapsed_ms()

    def note_transcript(self, kind: str, *, confidence: float | None = None) -> None:
        now = self._elapsed_ms()
        if kind == "partial":
            if self.stt["first_partial_ms"] is None:
                self.stt["first_partial_ms"] = now
            self.stt["partials"] += 1
        elif kind == "final":
            if self.stt["first_final_ms"] is None:
                self.stt["first_final_ms"] = now
            self.stt["finals"] += 1
            if confidence is not None and confidence < 0.60:
                self.stt["low_confidence"] += 1

    def note_empty_final(self) -> None:
        self.stt["empty_finals"] += 1

    def note_final_received(self) -> None:
        """Stamp the moment a final transcript was admitted (turn clock origin)."""
        self._final_at = time.monotonic()

    def note_processing_started(self) -> None:
        if self.processing["started_at_ms"] is None:
            self.processing["started_at_ms"] = self._elapsed_ms()
        now = time.monotonic()
        self.processing["_turn_started"] = now
        if self._final_at is not None:
            self.latency["final_to_processing_ms"].append(round((now - self._final_at) * 1000.0, 1))

    def note_llm_started(self) -> None:
        self._llm_started = time.monotonic()

    def note_llm_completed(self) -> None:
        if self._llm_started is not None:
            self._llm_ms = round((time.monotonic() - self._llm_started) * 1000.0, 1)
            self.latency["llm_ms"].append(self._llm_ms)
            self._llm_started = None

    def note_turn_complete(self) -> None:
        turn_started = self.processing.get("_turn_started")
        if turn_started is not None:
            total = round((time.monotonic() - turn_started) * 1000.0, 1)
            self.processing["turn_total_ms"].append(total)
            self.latency["turn_total_ms"].append(total)
            self.processing.pop("_turn_started", None)
        self._final_at = None
        self._llm_started = None
        self._llm_ms = None
        self._tts_started = None
        self._first_audio_at = None

    # --- Day 20: VAD -------------------------------------------------------------

    def note_speech_started(self, *, energy_db: float | None = None) -> None:
        self.vad["speech_starts"] += 1
        self._speech_started_at = time.monotonic()

    def note_speech_ended(self, *, speech_ms: int = 0) -> None:
        self.vad["speech_ends"] += 1
        if speech_ms > 0:
            self.vad["speech_ms"].append(speech_ms)

    def note_early_barge_in(self) -> None:
        """Assistant playback cancelled from VAD (before the STT final)."""
        self.vad["early_barge_ins"] += 1
        if self._speech_started_at is not None:
            latency = (time.monotonic() - self._speech_started_at) * 1000.0
            self.vad["barge_in_latency_ms"].append(round(latency, 1))
            self._speech_started_at = None

    # --- Day 20: turn assembly / interruption ------------------------------------

    def note_turn_dropped(self, reason: str) -> None:
        self.turns["dropped"] += 1
        reasons = self.turns["dropped_reasons"]
        reasons[reason] = int(reasons.get(reason, 0)) + 1

    def note_turn_held(self, reason: str) -> None:
        self.turns["held"] += 1

    def note_interrupted_speech(self) -> None:
        self.turns["interrupted_speech"] += 1

    def note_interruption(self, latency_ms: float) -> None:
        self.turns["interrupted"] += 1
        self.turns["interruption_latency_ms"].append(round(latency_ms, 1))

    def note_tts_started(self, provider: str) -> None:
        self.providers["tts"] = provider
        self.tts["started_at_ms"] = self._elapsed_ms()
        self._tts_started = time.monotonic()

    def note_tts_chunk(self, payload_bytes: int) -> None:
        now = self._elapsed_ms()
        if self.tts["first_chunk_ms"] is None:
            self.tts["first_chunk_ms"] = now
        self.tts["chunks"] += 1
        self.tts["bytes"] += payload_bytes
        # First audible audio for this turn → user-visible latency stamps.
        if self._first_audio_at is None:
            self._first_audio_at = time.monotonic()
            if self._tts_started is not None:
                self.latency["tts_ms"].append(
                    round((self._first_audio_at - self._tts_started) * 1000.0, 1)
                )
            turn_started = self.processing.get("_turn_started")
            if turn_started is not None:
                self.latency["processing_to_first_audio_ms"].append(
                    round((self._first_audio_at - turn_started) * 1000.0, 1)
                )

    def note_tts_completed(self) -> None:
        self.tts["completed_ms"] = self._elapsed_ms()

    # --- audio ---------------------------------------------------------------------

    def note_audio_in(self, payload_bytes: int) -> None:
        self.audio["frames_in"] += 1
        self.audio["bytes_in"] += payload_bytes

    def note_conversion(self, source_rate: int, target_rate: int) -> None:
        self.audio["conversions"] += 1
        self.audio["conversion_notes"].append(f"{source_rate}->{target_rate}")

    def note_outbound_chunk(self, payload_bytes: int) -> None:
        self.audio["outbound_chunks"] += 1
        self.audio["bytes_out"] += payload_bytes

    # --- incidents ------------------------------------------------------------------

    def note_provider_error(self, provider: str, code: str, fatal: bool) -> None:
        entry = f"{provider}:{code}:{'fatal' if fatal else 'nonfatal'}"
        self.providers_errors.append(entry)

    def note_error(self, message: str) -> None:
        # messages are short typed phrases from pipeline internals, never payloads
        self.errors.append(message[:120])

    def note_transition(self) -> None:
        self.state_transitions += 1

    def note_practice_outcome(self, operation: str, outcome: str, details: str = "") -> None:
        self.practice_api["outcomes"].append(
            f"{operation}={outcome}" + (f"({details})" if details else "")
        )

    # --- emission ------------------------------------------------------------------

    def _elapsed_ms(self) -> float:
        return round((time.monotonic() - self._t0) * 1000.0, 1)

    def build_record(self) -> dict[str, Any]:
        """Compact JSON-ready record — the future Langfuse event payload."""
        processing = {k: v for k, v in self.processing.items() if k != "_turn_started"}
        return {
            "event": "voice_session_metrics",
            "session_id": self.session_id,
            "call_sid": self.call_sid,
            "providers": self.providers,
            "sample_rate": self.sample_rate,
            "stt": self.stt,
            "processing": processing,
            "tts": self.tts,
            "audio": self.audio,
            "provider_errors": self.providers_errors,
            "state_transitions": self.state_transitions,
            "practice_api": self.practice_api,
            "errors": self.errors,
            "turns": self.turns,
            "latency": self.latency,
        }

    def emit(self) -> dict[str, Any]:
        """Log the single compact metrics record (INFO)."""
        record = self.build_record()
        logger.info("session metrics", extra={"metrics_record": record})
        return record
