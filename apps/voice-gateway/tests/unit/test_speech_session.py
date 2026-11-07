"""Per-call speech-session state unit tests (bounded history, format lock)."""

from __future__ import annotations

import pytest

from app.domain.audio import AudioFormat
from app.domain.fsm import TransitionReason, VoiceState
from app.domain.speech import FinalTranscript, PartialTranscript, RecognitionStarted
from app.domain.speech_session import (
    MediaFormatChangedError,
    ProviderState,
    SpeechSession,
    TranscriptRecord,
)

FMT_8K = AudioFormat(encoding="PCM_L16", sample_rate=8000, channels=1)
FMT_16K = AudioFormat(encoding="PCM_L16", sample_rate=16000, channels=1)


def make_session(fmt: AudioFormat = FMT_8K) -> SpeechSession:
    return SpeechSession(session_id="sess-1", call_sid="call-1", audio_format=fmt)


class TestFormatLock:
    def test_initial_format_from_construction(self) -> None:
        session = make_session()
        assert session.audio_format is FMT_8K

    def test_lock_same_format_ok(self) -> None:
        session = make_session()
        assert session.lock_audio_format(FMT_8K.model_copy()) == FMT_8K

    def test_lock_mismatched_format_raises_without_mutating(self) -> None:
        session = make_session()
        with pytest.raises(MediaFormatChangedError) as excinfo:
            session.lock_audio_format(FMT_16K)
        assert excinfo.value.locked == FMT_8K
        assert session.audio_format == FMT_8K  # unchanged (no silent resample)


class TestTranscriptHistory:
    def test_final_and_partial_recorded(self) -> None:
        session = make_session()
        session.record_transcript(
            PartialTranscript(session_id="s", provider="m", sequence=1, text="hello")
        )
        session.record_transcript(
            FinalTranscript(session_id="s", provider="m", sequence=1, text="hello there")
        )
        counts = session.transcript_counts
        assert counts == {"partials": 1, "finals": 1}
        kinds = [record.kind for record in session.transcripts]
        assert kinds == ["partial", "final"]

    def test_history_is_bounded_to_last_20(self) -> None:
        session = make_session()
        for index in range(25):
            session.record_transcript(
                FinalTranscript(
                    session_id="s", provider="m", sequence=index + 1, text=f"utterance {index}"
                )
            )
        assert len(session.transcripts) == 20
        assert session.transcripts[-1].sequence == 25
        assert session.transcripts[0].sequence == 6

    def test_transcript_record_fields(self) -> None:
        record = TranscriptRecord(kind="final", text="x", sequence=3, confidence=0.7)
        assert record.confidence == 0.7
        assert record.timestamp > 0
        assert "chars=1" in repr(record)  # length, never content


class TestPendingAndCleanup:
    def test_note_turn_completed_clears_pending(self) -> None:
        session = make_session()
        session.pending_utterance = FinalTranscript(
            session_id="s", provider="m", sequence=1, text="p"
        )
        session.pending_response = "response"
        session.note_turn_completed()
        assert session.pending_utterance is None
        assert session.pending_response is None
        assert session.turns_completed == 1

    def test_begin_cleanup_idempotent(self) -> None:
        session = make_session()
        session.begin_cleanup()
        session.begin_cleanup()
        assert session.cleanup_done

    def test_fsm_is_owned_by_session(self) -> None:
        session = make_session()
        session.fsm.transition(VoiceState.CONNECTING, TransitionReason.CALL_ACCEPTED)
        assert session.voice_state is VoiceState.CONNECTING


class TestProviderStates:
    def test_default_states_not_started(self) -> None:
        session = make_session()
        assert session.stt_state is ProviderState.NOT_STARTED
        assert session.tts_state is ProviderState.NOT_STARTED

    def test_snapshot_excludes_transcript_text(self) -> None:
        session = make_session()
        session.record_transcript(
            FinalTranscript(session_id="s", provider="m", sequence=1, text="sensitive words")
        )
        dump = session.snapshot()
        assert "sensitive words" not in str(dump)
        assert "transcript_history" in dump

    def test_unrelated_events_do_not_crash_snapshot(self) -> None:
        session = make_session()
        # RecognitionStarted isn't a transcript event; state simply untouched.
        event = RecognitionStarted(session_id="s", provider="m")
        assert event.provider == "m"
        assert session.transcript_counts == {"partials": 0, "finals": 0}
