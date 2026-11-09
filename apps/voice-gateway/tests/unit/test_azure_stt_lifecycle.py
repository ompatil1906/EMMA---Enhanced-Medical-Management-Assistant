"""Azure STT adapter lifecycle tests with a fake SDK (no network).

These tests protect the two live-verified fixes:

1. The recognizer must receive `AudioConfig(stream=push_stream)`, not the raw
   `PushAudioInputStream`. Passing the stream directly constructs but never
   delivers audio.
2. Closing the push stream during graceful teardown produces
   `CancellationReason.EndOfStream`, which is normal and must not be surfaced
   as a fatal provider error.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.audio import AudioFormat
from app.domain.speech import FinalTranscript, RecognitionError, RecognitionStopped
from app.speech.azure_stt import AzureSpeechToTextProvider


class _FakeHandle:
    def get(self) -> None:
        return None


class _FakeConnector:
    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def connect(self, handler: Any) -> None:
        self.handlers.append(handler)

    def fire(self, event_args: Any) -> None:
        for handler in self.handlers:
            handler(event_args)


class _FakePushStream:
    instances: list[_FakePushStream] = []

    def __init__(self, *, stream_format: Any = None) -> None:
        self.stream_format = stream_format
        self.written: list[bytes] = []
        self.closed = False
        _FakePushStream.instances.append(self)

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def close(self) -> None:
        self.closed = True


class _FakeAudioConfig:
    def __init__(self, *, stream: Any = None) -> None:
        self.stream = stream


class _FakeAudioStreamFormat:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FakeSpeechConfig:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.speech_recognition_language: str | None = None


class _FakeRecognizer:
    def __init__(self, *, speech_config: Any, audio_config: Any) -> None:
        self.speech_config = speech_config
        self.audio_config = audio_config
        self.recognizing = _FakeConnector()
        self.recognized = _FakeConnector()
        self.canceled = _FakeConnector()
        self.started = 0
        self.stopped = 0

    def start_continuous_recognition_async(self) -> _FakeHandle:
        self.started += 1
        return _FakeHandle()

    def stop_continuous_recognition_async(self) -> _FakeHandle:
        self.stopped += 1
        return _FakeHandle()


class _FakeResultReason:
    RecognizedSpeech = "RecognizedSpeech"
    NoMatch = "NoMatch"


class _FakeCancellationReason:
    EndOfStream = "EndOfStream"
    Error = "Error"


class _FakeDetails:
    def __init__(self, reason: str, error_details: str = "") -> None:
        self.reason = reason
        self.error_details = error_details


class _FakeResult:
    def __init__(
        self,
        *,
        text: str = "",
        reason: str = "RecognizedSpeech",
        cancellation: _FakeDetails | None = None,
    ) -> None:
        self.text = text
        self.reason = reason
        self.cancellation_details = cancellation
        self.offset = 0
        self.duration = 0
        self.json = "{}"


class _FakeEvent:
    def __init__(self, result: _FakeResult) -> None:
        self.result = result


class _FakeSdk:
    class audio:  # noqa: N801 - mirrors SDK namespace
        AudioStreamFormat = _FakeAudioStreamFormat
        PushAudioInputStream = _FakePushStream
        AudioConfig = _FakeAudioConfig

    SpeechConfig = _FakeSpeechConfig
    SpeechRecognizer = _FakeRecognizer
    ResultReason = _FakeResultReason
    CancellationReason = _FakeCancellationReason


def _provider() -> AzureSpeechToTextProvider:
    provider = AzureSpeechToTextProvider(
        speech_key="test-key-not-real",
        speech_region="test-region",
        language="en-GB",
        audio_format=AudioFormat(sample_rate=16000, channels=1),
        connection_timeout=1.0,
        stop_timeout=1.0,
    )
    provider._import_sdk = lambda: _FakeSdk  # type: ignore[method-assign]
    return provider


@pytest.mark.asyncio
async def test_recognizer_receives_audio_config_wrapper() -> None:
    provider = _provider()
    await provider.start("session-1")
    try:
        recognizer = provider._recognizer
        assert isinstance(recognizer, _FakeRecognizer)
        assert isinstance(recognizer.audio_config, _FakeAudioConfig)
        assert recognizer.audio_config.stream is provider._push_stream
        # The raw stream must not be passed directly as audio_config.
        assert recognizer.audio_config is not provider._push_stream
    finally:
        await provider.stop("session-1")


@pytest.mark.asyncio
async def test_final_transcript_is_delivered_once() -> None:
    provider = _provider()
    await provider.start("session-2")
    recognizer = provider._recognizer
    assert isinstance(recognizer, _FakeRecognizer)

    recognizer.recognized.fire(_FakeEvent(_FakeResult(text="hello there")))
    await provider.stop("session-2")

    events = [event async for event in provider.events("session-2")]
    finals = [event for event in events if isinstance(event, FinalTranscript)]
    assert [event.text for event in finals] == ["hello there"]
    assert isinstance(events[-1], RecognitionStopped)


@pytest.mark.asyncio
async def test_end_of_stream_is_not_a_fatal_error() -> None:
    provider = _provider()
    await provider.start("session-3")
    recognizer = provider._recognizer
    assert isinstance(recognizer, _FakeRecognizer)

    recognizer.canceled.fire(
        _FakeEvent(_FakeResult(cancellation=_FakeDetails(_FakeCancellationReason.EndOfStream)))
    )
    await provider.stop("session-3")

    events = [event async for event in provider.events("session-3")]
    assert not [event for event in events if isinstance(event, RecognitionError)]
    assert isinstance(events[-1], RecognitionStopped)


@pytest.mark.asyncio
async def test_real_cancellation_is_fatal() -> None:
    provider = _provider()
    await provider.start("session-4")
    recognizer = provider._recognizer
    assert isinstance(recognizer, _FakeRecognizer)

    recognizer.canceled.fire(
        _FakeEvent(
            _FakeResult(
                cancellation=_FakeDetails(_FakeCancellationReason.Error, "boom"),
            )
        )
    )

    events = [event async for event in provider.events("session-4")]
    errors = [event for event in events if isinstance(event, RecognitionError)]
    assert len(errors) == 1
    assert errors[0].fatal is True
    assert "boom" in errors[0].message


@pytest.mark.asyncio
async def test_stop_closes_stream_before_stopping_recognition() -> None:
    provider = _provider()
    await provider.start("session-5")
    stream = provider._push_stream
    recognizer = provider._recognizer
    assert isinstance(stream, _FakePushStream)
    assert isinstance(recognizer, _FakeRecognizer)

    await provider.stop("session-5")

    assert stream.closed is True
    assert recognizer.stopped == 1


@pytest.mark.asyncio
async def test_push_audio_is_ignored_after_stop() -> None:
    provider = _provider()
    await provider.start("session-6")
    stream = provider._push_stream
    assert isinstance(stream, _FakePushStream)

    await provider.stop("session-6")
    before = list(stream.written)
    await provider.push_audio("session-6", b"\x00\x00")
    assert stream.written == before
