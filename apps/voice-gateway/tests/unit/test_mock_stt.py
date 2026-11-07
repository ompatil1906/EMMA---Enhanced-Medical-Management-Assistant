"""Mock STT provider unit tests (deterministic script recognition; §14 STT tests)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.domain.speech import (
    FinalTranscript,
    PartialTranscript,
    RecognitionError,
    RecognitionStarted,
    RecognitionStopped,
)
from app.speech.base import SpeechProviderError
from app.speech.mock_stt import MockSpeechToTextProvider

SCRIPT = ("book an appointment please", "this is a test response", "cancel my appointment")


def provider(
    *, finalize_ms: int = 10, utterance_ms: int = 50, error_at_frame: int = 0
) -> MockSpeechToTextProvider:
    return MockSpeechToTextProvider(
        script=SCRIPT,
        finalize_ms=finalize_ms,
        utterance_ms=utterance_ms,
        sample_rate=8000,  # 1,600 bytes == 100 ms; 400 bytes == 25 ms
        error_at_frame=error_at_frame,
    )


async def next_event(stream: Any, timeout: float = 2.0) -> Any:
    return await asyncio.wait_for(stream.__anext__(), timeout)


class TestLifecycle:
    async def test_start_emits_recognition_started(self) -> None:
        subject = provider()
        await subject.start("sess-1")
        stream = subject.events("sess-1")
        event = await next_event(stream)
        assert isinstance(event, RecognitionStarted)
        assert event.provider == "mock-stt"
        await stream.aclose()
        await subject.stop("sess-1")

    async def test_below_threshold_frames_accepted_without_events(self) -> None:
        subject = provider(utterance_ms=100)
        await subject.start("sess-1")
        await subject.push_audio("sess-1", b"\x00" * 400)  # 25 ms < 100 ms
        stream = subject.events("sess-1")
        assert isinstance(await next_event(stream), RecognitionStarted)
        with pytest.raises(asyncio.TimeoutError):
            await next_event(stream, 0.08)  # no finalize scheduled
        await subject.stop("sess-1")

    async def test_empty_audio_accepted_no_events(self) -> None:
        subject = provider(utterance_ms=1)
        await subject.start("sess-1")
        await subject.push_audio("sess-1", b"")
        await subject.push_audio("sess-1", b"")
        stream = subject.events("sess-1")
        assert isinstance(await next_event(stream), RecognitionStarted)
        with pytest.raises(asyncio.TimeoutError):
            await next_event(stream, 0.08)  # empty frames never advance recognition
        await subject.stop("sess-1")

    async def test_session_guards(self) -> None:
        subject = provider()
        with pytest.raises(SpeechProviderError):
            await subject.push_audio("sess-1", b"\x00\x00")  # not started
        with pytest.raises(SpeechProviderError):
            list(subject.events("sess-1"))
        await subject.start("sess-1")
        with pytest.raises(SpeechProviderError):
            await subject.start("sess-other")  # one instance drives one session
        with pytest.raises(SpeechProviderError):
            await subject.push_audio("sess-other", b"\x00\x00")
        await subject.stop("sess-1")


class TestRecognition:
    async def test_partial_then_final_for_scripted_utterance(self) -> None:
        subject = provider(finalize_ms=15, utterance_ms=50)
        await subject.start("sess-1")
        await subject.push_audio("sess-1", b"\x00" * 1600)  # exactly 100 ms ≥ threshold
        stream = subject.events("sess-1")
        assert isinstance(await next_event(stream), RecognitionStarted)  # start marker
        partial = await next_event(stream)
        assert isinstance(partial, PartialTranscript)
        assert partial.sequence == 1
        assert partial.text == "book an appointment please"
        final = await next_event(stream, 0.5)
        assert isinstance(final, FinalTranscript)
        assert final.text == "book an appointment please"
        assert final.confidence is not None and final.confidence > 0.9
        await subject.stop("sess-1")

    async def test_script_consumed_in_order_and_cycles(self) -> None:
        subject = provider(finalize_ms=5, utterance_ms=50)
        await subject.start("sess-1")
        stream = subject.events("sess-1")
        await next_event(stream)  # recognition started
        finals: list[str] = []
        sequences: list[int] = []
        for _index in range(4):  # 3 scripted lines + 1 wraparound
            await subject.push_audio("sess-1", b"\x00" * 1600)
            first = await next_event(stream, 0.5)
            second = await next_event(stream, 0.5)
            assert isinstance(first, PartialTranscript) and isinstance(second, FinalTranscript)
            assert first.text == second.text
            finals.append(second.text)
            sequences.append(second.sequence)
        assert finals == [
            "book an appointment please",
            "this is a test response",
            "cancel my appointment",
            "book an appointment please",  # script wraps (documented determinism)
        ]
        assert sequences == [1, 2, 3, 4]  # one increment per finalized utterance
        await subject.stop("sess-1")

    async def test_injected_provider_error_tolerated(self) -> None:
        subject = provider(error_at_frame=2)  # error after the 2nd push
        await subject.start("sess-1")
        stream = subject.events("sess-1")
        await next_event(stream)  # started
        await subject.push_audio("sess-1", b"\x00" * 400)  # frame 1 (no finalize: 25<50ms)
        await subject.push_audio("sess-1", b"\x00" * 400)  # frame 2 → injected error
        event = await next_event(stream, 0.2)
        assert isinstance(event, RecognitionError)
        assert event.fatal is False
        assert event.code == "mock_injected_error"
        await subject.stop("sess-1")
        stopped_or_end = await next_event(stream, 0.2)  # stop always eventually ends stream
        assert isinstance(stopped_or_end, RecognitionStopped)
        with pytest.raises(StopAsyncIteration):
            await next_event(stream, 0.2)


class TestStop:
    async def test_stop_cancels_pending_finalize(self) -> None:
        subject = provider(finalize_ms=300)  # long finalize window
        await subject.start("sess-1")
        stream = subject.events("sess-1")
        await next_event(stream)  # started
        await subject.push_audio("sess-1", b"\x00" * 1600)  # partial now; final in 300 ms
        partial = await next_event(stream, 0.2)
        assert isinstance(partial, PartialTranscript)
        await subject.stop("sess-1", reason="call_abort")
        stopped = await next_event(stream, 0.2)
        assert isinstance(stopped, RecognitionStopped)
        assert stopped.reason == "call_abort"
        with pytest.raises(StopAsyncIteration):
            await next_event(stream, 0.5)  # the pending final was cancelled

    async def test_stop_is_idempotent_and_ends_stream_once(self) -> None:
        subject = provider()
        await subject.start("sess-1")
        stream = subject.events("sess-1")
        await next_event(stream)  # started
        await subject.stop("sess-1")
        await subject.stop("sess-1")
        stopped = await next_event(stream, 0.2)  # exactly one stop event
        assert isinstance(stopped, RecognitionStopped)
        with pytest.raises(StopAsyncIteration):
            await next_event(stream, 0.2)  # stream terminated by the sentinel
        with pytest.raises(StopAsyncIteration):
            await next_event(stream, 0.2)  # and stays terminated
