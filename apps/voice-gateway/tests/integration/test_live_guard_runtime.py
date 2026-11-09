"""Day 18C: the guard wraps the REAL adapters without changing their protocol.

The Deepgram and ElevenLabs adapters are driven through `BudgetedSTT` /
`BudgetedTTS` against the local fake servers used by the Day 18 protocol tests.
This proves three things at once, with no external traffic:

* the wrapper does not alter the adapter's wire behaviour (framing, path,
  headers, streaming, final transcript / PCM bytes),
* usage is recorded in the persistent ledger,
* a refused reservation stops the request *before* the socket is opened — the
  fake server sees no connection and no HTTP request.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.domain.audio import AudioFormat
from app.domain.speech import FinalTranscript, SynthesisChunk, SynthesisCompleted
from app.services.live_provider_budget import (
    LiveBudgetError,
    LiveProviderLedger,
    LiveProviderLimits,
)
from app.speech.budget_guard import BudgetedSTT, BudgetedTTS
from app.speech.deepgram_stt import DeepgramFluxSTTProvider
from app.speech.elevenlabs_tts import ElevenLabsFlashTTSProvider
from tests.integration.test_deepgram_protocol import FakeDeepgramServer
from tests.integration.test_elevenlabs_protocol import FakeElevenLabsServer

_FAKE_KEY = "test-key-not-real"
_FORMAT = AudioFormat(sample_rate=16000, channels=1)
_PCM_FRAME = b"\x01\x00" * 320  # 20 ms of PCM16 mono @ 16 kHz
_TTS_TEXT = "Good morning. Thank you for calling your GP practice."


def _ledger(tmp_path: Path, **overrides: object) -> LiveProviderLedger:
    return LiveProviderLedger(
        path=tmp_path / "ledger.json",
        limits=LiveProviderLimits(**overrides),  # type: ignore[arg-type]
        run_id="runtime-tests",
    )


@pytest.mark.asyncio
async def test_guarded_deepgram_session_end_to_end(tmp_path: Path) -> None:
    async with FakeDeepgramServer(schema="flux", transcript="book a gp appointment") as server:
        ledger = _ledger(tmp_path)
        provider = BudgetedSTT(
            DeepgramFluxSTTProvider(
                api_key=_FAKE_KEY,
                model="flux-general-en",
                language="en-GB",
                audio_format=_FORMAT,
                connection_timeout=5.0,
                request_timeout=5.0,
                endpoint=server.url,
            ),
            ledger,
            audio_format=_FORMAT,
        )

        events: list[object] = []

        async def collect() -> None:
            async for event in provider.events("s1"):
                events.append(event)

        collector = asyncio.create_task(collect())
        await provider.start("s1")
        for _ in range(5):
            await provider.push_audio("s1", _PCM_FRAME)
        await provider.finish("s1", grace_seconds=2.0)
        await provider.stop("s1")
        await asyncio.wait_for(collector, timeout=5.0)

    finals = [e for e in events if isinstance(e, FinalTranscript)]
    # The fake server commits one end-of-turn per frame past its threshold, so
    # assert on content rather than on how many times it repeated.
    assert finals and all(f.text == "book a gp appointment" for f in finals)
    # The adapter's wire contract is unchanged by the wrapper.
    assert server.recorded[0].path == "/v2/listen"
    assert server.recorded[0].headers["authorization"] == f"Token {_FAKE_KEY}"
    assert server.recorded[0].audio_bytes == 5 * len(_PCM_FRAME)

    usage = ledger.snapshot()["usage"]
    assert usage["deepgram_sessions"] == 1
    assert usage["deepgram_audio_seconds"] == pytest.approx(0.1, abs=1e-6)


@pytest.mark.asyncio
async def test_refused_deepgram_session_never_opens_a_connection(tmp_path: Path) -> None:
    async with FakeDeepgramServer(schema="flux") as server:
        ledger = _ledger(tmp_path, max_deepgram_sessions=1)

        first = BudgetedSTT(
            DeepgramFluxSTTProvider(
                api_key=_FAKE_KEY,
                model="flux-general-en",
                language="en-GB",
                audio_format=_FORMAT,
                connection_timeout=5.0,
                request_timeout=5.0,
                endpoint=server.url,
            ),
            ledger,
            audio_format=_FORMAT,
        )
        await first.start("s1")
        await first.stop("s1")

        second = BudgetedSTT(
            DeepgramFluxSTTProvider(
                api_key=_FAKE_KEY,
                model="flux-general-en",
                language="en-GB",
                audio_format=_FORMAT,
                connection_timeout=5.0,
                request_timeout=5.0,
                endpoint=server.url,
            ),
            ledger,
            audio_format=_FORMAT,
        )
        with pytest.raises(LiveBudgetError):
            await second.start("s2")

        assert len(server.recorded) == 1  # only the allowed session connected
        assert ledger.snapshot()["usage"]["deepgram_sessions"] == 1


@pytest.mark.asyncio
async def test_guarded_elevenlabs_synthesis_end_to_end(tmp_path: Path) -> None:
    async with FakeElevenLabsServer(chunks=(b"\x11\x00" * 400, b"\x22\x00" * 400)) as server:
        ledger = _ledger(tmp_path)
        provider = BudgetedTTS(
            ElevenLabsFlashTTSProvider(
                api_key=_FAKE_KEY,
                voice_id="testVoiceId0000000001",
                model="eleven_flash_v2_5",
                audio_format=_FORMAT,
                request_timeout=5.0,
                endpoint=server.base_url,
            ),
            ledger,
        )

        events = [event async for event in provider.synthesize(_TTS_TEXT, "s1", _FORMAT)]

    audio = b"".join(e.payload for e in events if isinstance(e, SynthesisChunk))
    completed = events[-1]
    assert isinstance(completed, SynthesisCompleted)
    assert audio == b"\x11\x00" * 400 + b"\x22\x00" * 400
    assert completed.total_bytes == len(audio)

    assert len(server.requests) == 1
    assert server.requests[0]["query"] == {"output_format": "pcm_16000"}
    usage = ledger.snapshot()["usage"]
    assert usage["elevenlabs_requests"] == 1
    assert usage["elevenlabs_chars"] == len(_TTS_TEXT)


@pytest.mark.asyncio
async def test_refused_elevenlabs_request_never_reaches_http(tmp_path: Path) -> None:
    async with FakeElevenLabsServer() as server:
        ledger = _ledger(tmp_path, max_elevenlabs_requests=1)
        provider = BudgetedTTS(
            ElevenLabsFlashTTSProvider(
                api_key=_FAKE_KEY,
                voice_id="testVoiceId0000000001",
                model="eleven_flash_v2_5",
                audio_format=_FORMAT,
                request_timeout=5.0,
                endpoint=server.base_url,
            ),
            ledger,
        )

        async for _ in provider.synthesize("one", "s1", _FORMAT):
            pass
        with pytest.raises(LiveBudgetError):
            async for _ in provider.synthesize("two", "s1", _FORMAT):
                pass

        assert len(server.requests) == 1  # the refused request never hit HTTP
        assert ledger.snapshot()["usage"]["elevenlabs_requests"] == 1
