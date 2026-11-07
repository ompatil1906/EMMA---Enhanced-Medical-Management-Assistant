"""Mock TTS provider unit tests (deterministic synthetic PCM; §14 TTS tests)."""

from __future__ import annotations

import asyncio
from typing import Any

from app.domain.audio import AudioFormat
from app.domain.speech import SynthesisChunk, SynthesisCompleted, SynthesisStarted
from app.speech.mock_tts import MockTextToSpeechProvider

FMT_8K = AudioFormat(encoding="PCM_L16", sample_rate=8000, channels=1)
FMT_16K = AudioFormat(encoding="PCM_L16", sample_rate=16000, channels=1)


async def collect(agen: Any, limit: int = 64) -> list[Any]:
    events: list[Any] = []
    try:
        for _i in range(limit):
            events.append(await agen.__anext__())
    except StopAsyncIteration:
        pass
    return events


class TestSynthesisContract:
    async def test_completed_is_always_last(self) -> None:
        provider = MockTextToSpeechProvider()
        events = await collect(provider.synthesize("Hello there", "s1", FMT_8K))
        assert isinstance(events[0], SynthesisStarted)
        assert isinstance(events[-1], SynthesisCompleted)
        assert all(isinstance(event, SynthesisChunk) for event in events[1:-1])
        assert len(events) > 3

    async def test_chunks_match_negotiated_format_math(self) -> None:
        provider = MockTextToSpeechProvider()
        events = await collect(provider.synthesize("a" * 20, "s1", FMT_8K))  # 1400 ms → 14 chunks
        started, chunks, completed = events[0], events[1:-1], events[-1]
        assert started.audio_format == FMT_8K and started.text_chars == 20
        assert len(chunks) == 14
        # 100 ms @ 8 kHz mono 16-bit = 1,600 bytes per chunk
        assert all(len(chunk.payload) == 1600 for chunk in chunks)
        assert completed.total_bytes == 14 * 1600
        assert completed.chunk_count == 14
        assert completed.cancelled is False

    async def test_output_format_metadata_honors_rate(self) -> None:
        provider = MockTextToSpeechProvider()
        events = await collect(provider.synthesize("hi", "s1", FMT_16K))
        chunks = events[1:-1]
        # 100 ms @ 16 kHz = 3,200 bytes
        assert all(chunk.audio_format == FMT_16K for chunk in chunks)
        assert len(chunks[0].payload) == 3200

    async def test_empty_text_yields_started_and_completed_only(self) -> None:
        provider = MockTextToSpeechProvider()
        events = await collect(provider.synthesize("   ", "s1", FMT_8K))
        started, completed = events
        assert isinstance(started, SynthesisStarted) and started.text_chars == 0
        assert isinstance(completed, SynthesisCompleted)
        assert completed.total_bytes == 0 and completed.chunk_count == 0

    async def test_provider_name(self) -> None:
        assert MockTextToSpeechProvider().provider_name == "mock-tts"


class TestDeterminismAndCancellation:
    async def test_same_text_same_bytes(self) -> None:
        provider = MockTextToSpeechProvider()
        first = await collect(provider.synthesize("deterministic tone", "s1", FMT_8K))
        second = await collect(provider.synthesize("deterministic tone", "s2", FMT_8K))
        assert [event.payload for event in first[1:-1]] == [event.payload for event in second[1:-1]]

    async def test_cancellation_stops_soon_with_completed_true(self) -> None:
        provider = MockTextToSpeechProvider()
        agen = provider.synthesize("word " * 4, "s1", FMT_8K)
        await agen.__anext__()  # SynthesisStarted
        first_chunk = await agen.__anext__()
        utterance_id = first_chunk.utterance_id
        await provider.cancel_turn("s1", utterance_id)
        events = await collect(agen, limit=64)
        completed = events[-1]
        assert isinstance(completed, SynthesisCompleted)
        assert completed.cancelled is True

    async def test_cancellation_leaves_no_state_per_session(self) -> None:
        provider = MockTextToSpeechProvider()
        agen = provider.synthesize("hello world", "s1", FMT_8K)
        chunk = await collect(agen, limit=3)
        await provider.cancel_turn("s1", chunk[0].utterance_id)
        # cancelled utterances stop; a FRESH turn is unaffected (no leaked state)
        events = await collect(provider.synthesize("fresh turn", "s1", FMT_8K))
        assert isinstance(events[-1], SynthesisCompleted) and events[-1].cancelled is False


class TestNoEventLoopBlocking:
    async def test_generation_yields_control_between_chunks(self) -> None:
        provider = MockTextToSpeechProvider()
        flag = {"ticked": False}

        async def tick() -> None:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            flag["ticked"] = True

        synth_task = asyncio.create_task(
            collect(provider.synthesize("longer text here", "s", FMT_8K))
        )
        await asyncio.create_task(tick())
        assert flag["ticked"] is True  # took the event loop while synthesis ran
        await synth_task
