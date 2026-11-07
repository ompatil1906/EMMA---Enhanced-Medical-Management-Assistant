"""Speech-domain event model tests (frozen, normalized, provider-independent)."""

from __future__ import annotations

import pytest

from app.domain.audio import AudioFormat
from app.domain.speech import (
    FinalTranscript,
    PartialTranscript,
    RecognitionError,
    RecognitionStarted,
    RecognitionStopped,
    SynthesisChunk,
    SynthesisCompleted,
    SynthesisStarted,
)

FMT = AudioFormat(encoding="PCM_L16", sample_rate=8000, channels=1)


@pytest.mark.parametrize(
    "event",
    [
        RecognitionStarted(session_id="s", provider="p"),
        PartialTranscript(session_id="s", provider="p", sequence=1, text="hi"),
        FinalTranscript(session_id="s", provider="p", sequence=1, text="hi", confidence=0.9),
        RecognitionStopped(session_id="s", provider="p", reason="session_stop"),
        RecognitionError(session_id="s", provider="p", code="x", message="boom", fatal=True),
        SynthesisStarted(
            session_id="s", provider="p", utterance_id="u", text_chars=4, audio_format=FMT
        ),
        SynthesisChunk(
            session_id="s", provider="p", utterance_id="u", payload=b"\x00\x01", audio_format=FMT
        ),
        SynthesisCompleted(
            session_id="s", provider="p", utterance_id="u", total_bytes=2, chunk_count=1
        ),
    ],
)
def test_events_are_frozen_with_timestamp(event) -> None:
    with pytest.raises((TypeError, ValueError)):
        event.timestamp = 0.0  # frozen
    assert event.timestamp > 0


def test_transcript_events_carry_sequence_and_confidence() -> None:
    partial = PartialTranscript(session_id="s", provider="p", sequence=2, text="partial text")
    final = FinalTranscript(session_id="s", provider="p", sequence=2, text="final text")
    assert partial.confidence is None
    assert partial.provider_metadata == {}
    assert final.sequence == 2


def test_confidence_is_optional_provider_metadata_only_when_needed() -> None:
    event = FinalTranscript(
        session_id="s",
        provider="azure",
        sequence=1,
        text="words",
        confidence=0.83,
        provider_metadata={"offset_ticks": 1000, "duration_ticks": 2000},
    )
    assert event.confidence == 0.83
    assert event.provider_metadata["offset_ticks"] == 1000


def test_recognition_error_fatal_flag_is_part_of_event() -> None:
    fatal = RecognitionError(session_id="s", provider="p", code="c", message="m", fatal=True)
    assert fatal.fatal is True
