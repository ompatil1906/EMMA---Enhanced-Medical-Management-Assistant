"""Day 20: audio-level VAD (energy, hysteresis, adaptive floor, echo guard).

The VAD exists to make barge-in start when the caller *speaks*, not when the STT
finally transcribes them. These tests pin the three qualities that make it safe
to run on a live call:

* it fires on sustained speech, never on a single loud click;
* it does not end speech during a natural pause (hysteresis + end-silence);
* it is harder to trigger while EMMA herself is speaking (echo guard).
"""

from __future__ import annotations

import math

import pytest

from app.services.audio.vad import EnergyVad, VadConfig, VadState, pcm16_dbfs

SAMPLE_RATE = 16_000


def _frame(amplitude: int, *, ms: int = 20, sample_rate: int = SAMPLE_RATE) -> bytes:
    """PCM16 mono frame of a constant-amplitude square wave."""
    samples = int(sample_rate * ms / 1000)
    return int(amplitude).to_bytes(2, "little", signed=True) * samples


def _silence(ms: int = 20) -> bytes:
    return b"\x00\x00" * int(SAMPLE_RATE * ms / 1000)


def _speech(ms: int = 20) -> bytes:
    # ~3277 RMS ≈ -20 dBFS — clearly speech-level energy.
    return _frame(3277, ms=ms)


@pytest.mark.parametrize(
    ("amplitude", "expected_db"),
    [(0, -90.0), (327, -40.0), (3277, -20.0), (32767, 0.0)],
)
def test_dbfs_matches_the_expected_loudness(amplitude: int, expected_db: float) -> None:
    assert pcm16_dbfs(_frame(amplitude)) == pytest.approx(expected_db, abs=0.6)


def test_silence_has_no_event() -> None:
    vad = EnergyVad(VadConfig())
    for i in range(50):
        assert vad.process(_silence(), sample_rate=SAMPLE_RATE, now_ms=i * 20) is None
    assert vad.state is VadState.SILENCE


def test_short_click_does_not_start_speech() -> None:
    vad = EnergyVad(VadConfig(min_speech_ms=180))
    assert vad.process(_speech(), sample_rate=SAMPLE_RATE, now_ms=0) is None
    # 20 ms of loud audio is a click, not speech.
    assert vad.state is VadState.SILENCE


def test_sustained_speech_starts_exactly_once() -> None:
    vad = EnergyVad(VadConfig(min_speech_ms=180))
    events = []
    for i in range(30):
        event = vad.process(_speech(), sample_rate=SAMPLE_RATE, now_ms=i * 20)
        if event is not None:
            events.append(event)
    assert [e.kind for e in events] == ["speech_started"]
    assert events[0].state is VadState.SPEECH
    assert vad.speech_starts == 1


def test_speech_ends_only_after_the_end_silence_window() -> None:
    vad = EnergyVad(VadConfig(min_speech_ms=100, end_silence_ms=400))
    for i in range(20):
        vad.process(_speech(), sample_rate=SAMPLE_RATE, now_ms=i * 20)
    assert vad.state is VadState.SPEECH

    ended: list = []
    for i in range(20, 60):
        event = vad.process(_silence(), sample_rate=SAMPLE_RATE, now_ms=i * 20)
        if event is not None:
            ended.append(event)
    assert [e.kind for e in ended] == ["speech_ended"]
    assert ended[0].speech_ms > 0


def test_a_short_pause_does_not_end_the_turn() -> None:
    """Natural mid-sentence pauses must not split the turn (hysteresis)."""
    vad = EnergyVad(VadConfig(min_speech_ms=100, end_silence_ms=600))
    for i in range(15):
        vad.process(_speech(), sample_rate=SAMPLE_RATE, now_ms=i * 20)
    # 300 ms pause — shorter than the end-silence window.
    for i in range(15, 30):
        assert vad.process(_silence(), sample_rate=SAMPLE_RATE, now_ms=i * 20) is None
    assert vad.state is VadState.SPEECH


def test_echo_guard_requires_louder_input_while_the_assistant_speaks() -> None:
    quiet_speech = _frame(600)  # ≈ -34.7 dBFS: just above the plain start threshold
    plain = EnergyVad(VadConfig(min_speech_ms=60, noise_floor_alpha=0.0))
    guarded = EnergyVad(VadConfig(min_speech_ms=60, noise_floor_alpha=0.0))

    plain_events = [
        plain.process(quiet_speech, sample_rate=SAMPLE_RATE, now_ms=i * 20) for i in range(20)
    ]
    guarded_events = [
        guarded.process(
            quiet_speech, sample_rate=SAMPLE_RATE, now_ms=i * 20, assistant_speaking=True
        )
        for i in range(20)
    ]

    assert any(e is not None and e.kind == "speech_started" for e in plain_events)
    assert all(e is None for e in guarded_events), "echo guard must suppress borderline audio"


def test_adaptive_noise_floor_rises_in_a_noisy_room() -> None:
    vad = EnergyVad(VadConfig(noise_floor_alpha=0.1, max_noise_floor_db=-50.0))
    noise = _frame(200)  # ≈ -44 dBFS steady background noise
    for i in range(100):
        vad.process(noise, sample_rate=SAMPLE_RATE, now_ms=i * 20)
    assert vad.noise_floor_db is not None
    assert vad.noise_floor_db > -90.0


def test_reset_clears_the_state() -> None:
    vad = EnergyVad(VadConfig(min_speech_ms=60))
    for i in range(20):
        vad.process(_speech(), sample_rate=SAMPLE_RATE, now_ms=i * 20)
    assert vad.state is VadState.SPEECH
    vad.reset()
    assert vad.state is VadState.SILENCE
    # The adaptive floor returns to its configured ceiling (not "unset").
    assert vad.noise_floor_db == vad.config.max_noise_floor_db


def test_tiny_frames_are_ignored() -> None:
    vad = EnergyVad()
    assert vad.process(b"\x00\x00", sample_rate=SAMPLE_RATE, now_ms=0) is None
    assert vad.frames == 0


def test_vad_never_raises_on_garbage() -> None:
    vad = EnergyVad()
    # An odd number of bytes is still safe (last byte ignored).
    assert vad.process(b"\xff" * 641, sample_rate=SAMPLE_RATE, now_ms=0) is None
    assert math.isfinite(vad.noise_floor_db or 0.0)
