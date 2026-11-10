"""Audio-level voice activity detection (Day 20).

Pipecat runs a VAD processor before STT so the pipeline knows *when* the caller
started and stopped speaking, independently of provider transcripts. EMMA only
had transcript-driven turn ends, which means barge-in could not begin until the
STT emitted a final — often hundreds of milliseconds after the caller started
talking.

This module adds the missing signal as a small, dependency-free, deterministic
component:

* energy-based VAD over the already-decoded PCM16 mono frames (no model, no
  network, no new package — Silero/torch would add ~1 GB of runtime for a
  binary speech/non-speech decision);
* **adaptive noise floor** (EMA while silent) so a noisy room raises the bar
  instead of firing constantly;
* **hysteresis** (a higher start threshold than end threshold) plus a minimum
  speech duration and an end-silence window, which is what stops a cough or a
  door slam from cancelling a reply;
* **echo guard** — while the assistant is speaking the start threshold is
  raised, because a browser's acoustic echo cancellation is never perfect and
  EMMA must not interrupt itself.

The VAD never decides intent, never calls a provider and never speaks. It emits
one event per transition; the pipeline decides what to do with it (metrics,
early barge-in).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

_SILENCE_FLOOR_DB = -90.0  # arithmetic floor for log(0)


class VadState(StrEnum):
    """Binary speech state (no "maybe" state on purpose)."""

    SILENCE = "silence"
    SPEECH = "speech"


@dataclass(frozen=True, slots=True)
class VadConfig:
    """Tunables. All thresholds are dBFS (0 dB = full scale)."""

    # Start is stricter than end (hysteresis): speech must be clearly speech to
    # begin, and only clearly-quiet audio ends it.
    start_energy_db: float = -35.0
    end_energy_db: float = -45.0
    # Consecutive voiced audio required before announcing speech (kills clicks).
    min_speech_ms: int = 180
    # Quiet audio required before announcing the end of speech.
    end_silence_ms: int = 450
    # Extra margin required while the assistant is speaking (echo guard).
    echo_guard_db: float = 6.0
    # Adaptive noise floor: EMA of silent frames. 0 disables adaptation.
    noise_floor_alpha: float = 0.02
    # Never let the adaptive floor rise above this (keeps quiet callers audible).
    max_noise_floor_db: float = -50.0
    # Ignore absurdly small payloads (partial frames from the transport).
    min_frame_bytes: int = 320  # 10 ms @ 16 kHz mono PCM16


@dataclass(frozen=True, slots=True)
class VadEvent:
    """One VAD transition (emitted once per state change)."""

    kind: Literal["speech_started", "speech_ended"]
    state: VadState
    at_ms: int
    energy_db: float
    # Duration of the speech segment that just ended (0 for speech_started).
    speech_ms: int = 0
    # Why the transition fired (for logs/metrics; never spoken).
    reason: str = ""


def pcm16_rms(payload: bytes) -> float:
    """Root-mean-square of little-endian PCM16 mono samples."""
    if len(payload) < 2:
        return 0.0
    sample_count = len(payload) // 2
    total = 0
    # `array` is much faster than struct.unpack for hot paths.
    import sys
    from array import array

    samples = array("h")
    samples.frombytes(payload[: sample_count * 2])
    if sys.byteorder != "little":  # pragma: no cover - x86/ARM are little-endian
        samples.byteswap()
    for sample in samples:
        total += sample * sample
    return math.sqrt(total / sample_count)


def pcm16_dbfs(payload: bytes) -> float:
    """Frame loudness in dBFS (silence floor -90 dB)."""
    if not payload:
        return _SILENCE_FLOOR_DB
    rms = pcm16_rms(payload)
    if rms <= 1.0:
        return _SILENCE_FLOOR_DB
    return max(_SILENCE_FLOOR_DB, 20.0 * math.log10(rms / 32768.0))


def config_noise_ceiling(config: VadConfig) -> float:
    """Initial adaptive floor: the configured ceiling, not the first frame."""
    return float(config.max_noise_floor_db)


class EnergyVad:
    """Stateful energy VAD for one session (call `reset()` between calls)."""

    def __init__(self, config: VadConfig | None = None) -> None:
        self._config = config or VadConfig()
        self._state = VadState.SILENCE
        # Start at the ceiling, never at the first frame: a call that opens with
        # speech would otherwise calibrate the floor to speech and then mask it.
        self._noise_floor_db: float | None = config_noise_ceiling(self._config)
        self._voiced_ms = 0
        self._silent_ms = 0
        self._segment_started_ms: int | None = None
        self._last_at_ms: int | None = None
        self.speech_starts = 0
        self.speech_ends = 0
        self.frames = 0

    @property
    def config(self) -> VadConfig:
        return self._config

    @property
    def state(self) -> VadState:
        return self._state

    @property
    def noise_floor_db(self) -> float | None:
        return self._noise_floor_db

    def reset(self) -> None:
        self._state = VadState.SILENCE
        self._noise_floor_db = config_noise_ceiling(self._config)
        self._voiced_ms = 0
        self._silent_ms = 0
        self._segment_started_ms = None
        self._last_at_ms = None

    # --- frame processing ---------------------------------------------------

    def process(
        self,
        payload: bytes,
        *,
        sample_rate: int,
        now_ms: int,
        assistant_speaking: bool = False,
    ) -> VadEvent | None:
        """Consume one PCM16 frame; return a transition event or None.

        `now_ms` is injected (monotonic) so the component is deterministic in
        tests. Frame duration is derived from the payload size, so variable
        chunk sizes from different transports are handled correctly.
        """
        cfg = self._config
        if len(payload) < cfg.min_frame_bytes or sample_rate <= 0:
            return None

        self.frames += 1
        frame_ms = int(round((len(payload) // 2) / sample_rate * 1000.0))
        if frame_ms <= 0:
            return None

        energy_db = pcm16_dbfs(payload)
        if self._noise_floor_db is None:
            self._noise_floor_db = cfg.max_noise_floor_db

        start_threshold = cfg.start_energy_db
        if assistant_speaking:
            start_threshold += cfg.echo_guard_db
        if self._noise_floor_db is not None and cfg.noise_floor_alpha > 0:
            start_threshold = max(start_threshold, self._noise_floor_db + 9.0)

        voiced = energy_db >= start_threshold
        quiet = energy_db <= cfg.end_energy_db

        if self._state is VadState.SILENCE:
            # Adapt the noise floor only while nobody is talking.
            if cfg.noise_floor_alpha > 0 and not assistant_speaking:
                target = min(energy_db, cfg.max_noise_floor_db)
                self._noise_floor_db = (
                    1 - cfg.noise_floor_alpha
                ) * self._noise_floor_db + cfg.noise_floor_alpha * target

            self._voiced_ms = self._voiced_ms + frame_ms if voiced else 0
            self._last_at_ms = now_ms
            if voiced and self._voiced_ms >= cfg.min_speech_ms:
                self._state = VadState.SPEECH
                self._silent_ms = 0
                self.speech_starts += 1
                # The segment started when the *first* voiced frame arrived.
                self._segment_started_ms = now_ms - self._voiced_ms
                return VadEvent(
                    kind="speech_started",
                    state=VadState.SPEECH,
                    at_ms=now_ms,
                    energy_db=round(energy_db, 1),
                    reason="energy_above_threshold",
                )
            return None

        # SPEECH
        self._silent_ms = self._silent_ms + frame_ms if quiet else 0
        self._last_at_ms = now_ms
        if self._silent_ms >= cfg.end_silence_ms:
            speech_ms = 0
            if self._segment_started_ms is not None:
                speech_ms = max(0, now_ms - self._silent_ms - self._segment_started_ms)
            self._state = VadState.SILENCE
            self._voiced_ms = 0
            self._segment_started_ms = None
            self.speech_ends += 1
            return VadEvent(
                kind="speech_ended",
                state=VadState.SILENCE,
                at_ms=now_ms,
                energy_db=round(energy_db, 1),
                speech_ms=speech_ms,
                reason="end_silence_reached",
            )
        return None


__all__ = [
    "EnergyVad",
    "VadConfig",
    "VadEvent",
    "VadState",
    "config_noise_ceiling",
    "pcm16_dbfs",
    "pcm16_rms",
]
