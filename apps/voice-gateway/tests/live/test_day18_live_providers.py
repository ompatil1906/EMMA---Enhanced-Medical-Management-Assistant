"""Day 18: OPT-IN live provider smoke tests (Deepgram STT, ElevenLabs TTS).

These are the only tests in the repository that spend money, so they are
double-gated and hard-capped:

1. `EMMA_RUN_LIVE_TESTS=1` — live tests are off by default.
2. `EMMA_LIVE_CREDENTIALS_ROTATED=1` — the operator attests that the
   previously chat-exposed provider keys were rotated *and* that the new keys
   are the ones in this `.env`. Without that attestation the tests skip: a
   compromised key must never be used just to make a checklist green.
3. `EMMA_LIVE_PROVIDER_SPEND_APPROVED=1` — the operator has reviewed provider
   balances/pricing and authorises the bounded paid run (Day 18D gate). The
   runtime guard separately honours the same two flags.

Budget (Day 18E): every request here goes through the **same persistent
`LiveProviderLedger`** the running gateway/agent uses — same `EMMA_LIVE_RUN_ID`,
same ledger file, same `BudgetedSTT`/`BudgetedTTS` wrappers. There is no
separate in-process budget any more, so this module and the browser scenario
share one set of counters and cannot jointly exceed 3 Deepgram sessions /
3 ElevenLabs requests.

This module consumes at most **1 Deepgram session** (~4 s of audio) and **1
ElevenLabs request** (79 characters). The browser scenario consumes 1 Deepgram
session and 2 ElevenLabs requests (greeting + answer), so a full Day 18E run
uses 2/3 sessions and 3/3 requests.

Run (from `apps/voice-gateway`):

    EMMA_RUN_LIVE_TESTS=1 EMMA_LIVE_CREDENTIALS_ROTATED=1 \
    EMMA_LIVE_PROVIDER_SPEND_APPROVED=1 \
      .venv/bin/python -m pytest tests/live/test_day18_live_providers.py -q -s

Nothing here prints a credential, an Authorization header, or a full response
body. Only counts, durations, formats and transcripts are reported.
"""

from __future__ import annotations

import asyncio
import time
import wave
from array import array
from pathlib import Path

import pytest

from app.core.config import Settings
from app.domain.audio import AudioFormat
from app.domain.speech import (
    FinalTranscript,
    PartialTranscript,
    RecognitionError,
    SttEvent,
    SynthesisChunk,
    SynthesisCompleted,
)
from app.services.live_provider_budget import LiveProviderLedger, build_runtime_ledger
from app.speech.budget_guard import BudgetedSTT, BudgetedTTS
from app.speech.deepgram_stt import DeepgramFluxSTTProvider
from app.speech.elevenlabs_tts import ElevenLabsFlashTTSProvider

pytestmark = pytest.mark.live

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "speech_gp_appointment.wav"
REFERENCE_TRANSCRIPT = "Hello, I would like to book a routine GP appointment."
TTS_TEXT = "Good morning. Thank you for calling your GP practice. How may I help you today?"


# --- gates -------------------------------------------------------------------


def _require_live_env() -> Settings:
    """Gate on the same `Settings` the runtime uses (one source of truth)."""
    settings = Settings()
    if not settings.EMMA_RUN_LIVE_TESTS:
        pytest.skip("live tests disabled (set EMMA_RUN_LIVE_TESTS=1 to run)")
    if settings.live_attestation_problem is not None:
        # Rotation + spend approval are user acknowledgements, never evidence.
        pytest.skip(settings.live_attestation_problem)
    return settings


@pytest.fixture(scope="module")
def ledger() -> LiveProviderLedger:
    """The SAME persistent ledger the gateway/agent use (no test-only budget)."""
    settings = _require_live_env()
    ledger = build_runtime_ledger(settings)
    ledger.ensure_ready()  # fail closed on corrupt/unavailable ledger
    print(f"shared ledger      : {ledger.path.name} (run {ledger.snapshot()['run_id']})")
    return ledger


@pytest.fixture(scope="module")
def pcm_fixture() -> tuple[bytes, float, int]:
    """Load the locally synthesised phrase as (pcm16 bytes, seconds, rate)."""
    if not FIXTURE.exists():
        pytest.skip(
            f"speech fixture missing: {FIXTURE.name}. Generate it with "
            "`.venv/bin/python -m tools.make_speech_fixture --out "
            f"tests/fixtures/{FIXTURE.name}`"
        )
    with wave.open(str(FIXTURE), "rb") as handle:
        assert handle.getnchannels() == 1, "fixture must be mono"
        assert handle.getsampwidth() == 2, "fixture must be PCM16"
        rate = handle.getframerate()
        frames = handle.getnframes()
        raw = handle.readframes(frames)
    seconds = frames / rate
    assert seconds <= 15.0, "fixture must fit the 15 s live-session cap"
    # Sanity: refuse to present silence as speech.
    samples = array("h")
    samples.frombytes(raw)
    peak = max((abs(s) for s in samples), default=0)
    assert peak > 0.01 * 32768, "fixture is silent — refusing to label it speech"
    return raw, seconds, rate


def _normalise(text: str) -> list[str]:
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    return [token for token in cleaned.split() if token]


def _token_overlap(reference: str, hypothesis: str) -> float:
    ref, hyp = set(_normalise(reference)), set(_normalise(hypothesis))
    if not ref:
        return 0.0
    return len(ref & hyp) / len(ref)


async def _drain_until_final(
    provider: DeepgramFluxSTTProvider,
    session: str,
    events: list[SttEvent],
    *,
    timeout: float,
) -> FinalTranscript | None:
    """Collect events until a final transcript or `timeout`."""

    async def run() -> FinalTranscript | None:
        async for event in provider.events(session):
            events.append(event)
            if isinstance(event, RecognitionError) and event.fatal:
                return None
            if isinstance(event, FinalTranscript):
                return event
        return None

    try:
        return await asyncio.wait_for(run(), timeout=timeout)
    except TimeoutError:
        return None


# --- Phase A: Deepgram STT ----------------------------------------------------


@pytest.mark.asyncio
async def test_deepgram_transcribes_known_phrase(
    ledger: LiveProviderLedger, pcm_fixture: tuple[bytes, float, int]
) -> None:
    """Phase A: real Deepgram streaming session against a known phrase."""
    settings = _require_live_env()
    if settings.STT_PROVIDER != "deepgram" or not settings.DEEPGRAM_API_KEY:
        pytest.skip("STT_PROVIDER=deepgram with DEEPGRAM_API_KEY is required")

    raw, seconds, rate = pcm_fixture

    audio_format = AudioFormat(sample_rate=rate, channels=1)
    # Same wrappers the runtime uses: the session is reserved in the shared
    # ledger before the WebSocket opens, and per-session audio is metered.
    provider = BudgetedSTT(
        DeepgramFluxSTTProvider(
            api_key=settings.DEEPGRAM_API_KEY,
            model=settings.DEEPGRAM_MODEL,
            language=settings.AZURE_SPEECH_LANGUAGE,
            audio_format=audio_format,
            connection_timeout=settings.SPEECH_CONNECTION_TIMEOUT,
            request_timeout=settings.SPEECH_REQUEST_TIMEOUT,
        ),
        ledger,
        audio_format=audio_format,
    )
    session = "day18-phase-a"
    events: list[SttEvent] = []

    connect_started = time.perf_counter()
    await provider.start(session)
    connect_ms = (time.perf_counter() - connect_started) * 1000

    collector = asyncio.create_task(
        _drain_until_final(provider, session, events, timeout=settings.SPEECH_REQUEST_TIMEOUT + 10)
    )

    # Stream 20 ms frames, paced close to real time so the provider's own
    # turn detection sees a realistic stream.
    frame_bytes = int(rate * 0.02) * 2
    send_started = time.perf_counter()
    first_partial_ms: float | None = None
    for offset in range(0, len(raw), frame_bytes):
        await provider.push_audio(session, raw[offset : offset + frame_bytes])
        if first_partial_ms is None and any(isinstance(e, PartialTranscript) for e in events):
            first_partial_ms = (time.perf_counter() - send_started) * 1000
        await asyncio.sleep(0.02)
    send_ms = (time.perf_counter() - send_started) * 1000  # paced 1x audio time

    await provider.finish(session, grace_seconds=3.0)
    final = await collector
    final_ms = (time.perf_counter() - send_started) * 1000
    await provider.stop(session)

    assert final is not None, f"no final transcript (events: {[type(e).__name__ for e in events]})"
    for event in events:
        assert not (isinstance(event, RecognitionError) and event.fatal), event.message

    partials = [e.text for e in events if isinstance(e, PartialTranscript)]
    overlap = _token_overlap(REFERENCE_TRANSCRIPT, final.text)

    print("\n--- Deepgram (Phase A) ---")  # noqa: T201 - report output
    print(f"model              : {settings.DEEPGRAM_MODEL}")  # noqa: T201
    print(f"audio              : {seconds:.2f}s @ {rate} Hz mono")  # noqa: T201
    print(f"connect            : {connect_ms:.0f} ms")  # noqa: T201
    print(f"first partial      : {first_partial_ms or -1:.0f} ms after first frame")  # noqa: T201
    print(f"frames sent in     : {send_ms:.0f} ms")  # noqa: T201
    print(f"final (from frames): {final_ms:.0f} ms")  # noqa: T201
    print(f"partial count      : {len(partials)}")  # noqa: T201
    print(f"reference          : {REFERENCE_TRANSCRIPT}")  # noqa: T201
    print(f"transcript         : {final.text}")  # noqa: T201
    print(f"token overlap      : {overlap:.2f}")  # noqa: T201
    usage = ledger.snapshot()
    print(f"usage              : {usage['usage']}")  # noqa: T201
    print(f"remaining          : {usage['remaining']}")  # noqa: T201
    assert usage["usage"]["deepgram_sessions"] >= 1  # ledger recorded the session

    # Recognition must be real: the phrase's content words must appear. This is
    # deliberately a token-overlap floor (not exact-match) because
    # capitalisation/punctuation/vendor formatting legitimately differ.
    assert overlap >= 0.6, f"transcript too far from reference: {final.text!r}"
    assert "appointment" in _normalise(final.text)


# --- Phase B: ElevenLabs TTS --------------------------------------------------


@pytest.mark.asyncio
async def test_elevenlabs_synthesises_administrative_response(ledger: LiveProviderLedger) -> None:
    """Phase B: one real ElevenLabs request, 79 characters, configured voice."""
    settings = _require_live_env()
    if settings.TTS_PROVIDER != "elevenlabs" or not (
        settings.ELEVENLABS_API_KEY and settings.ELEVENLABS_VOICE_ID
    ):
        pytest.skip("TTS_PROVIDER=elevenlabs with key + voice id is required")

    assert len(TTS_TEXT) <= 300

    audio_format = AudioFormat(sample_rate=16000, channels=1)
    # Same wrapper as the runtime: one request reserved in the shared ledger
    # before the HTTP call, character-capped at 300.
    provider = BudgetedTTS(
        ElevenLabsFlashTTSProvider(
            api_key=settings.ELEVENLABS_API_KEY,
            voice_id=settings.ELEVENLABS_VOICE_ID,
            model=settings.ELEVENLABS_MODEL,
            audio_format=audio_format,
            request_timeout=settings.SPEECH_REQUEST_TIMEOUT,
        ),
        ledger,
    )

    started = time.perf_counter()
    first_chunk_ms: float | None = None
    payload = bytearray()
    completed: SynthesisCompleted | None = None
    async for event in provider.synthesize(TTS_TEXT, "day18-phase-b", audio_format):
        if isinstance(event, SynthesisChunk):
            if first_chunk_ms is None:
                first_chunk_ms = (time.perf_counter() - started) * 1000
            payload.extend(event.payload)
        elif isinstance(event, SynthesisCompleted):
            completed = event
    total_ms = (time.perf_counter() - started) * 1000

    assert completed is not None
    assert completed.total_bytes == len(payload) > 0
    assert len(payload) % 2 == 0, "PCM16 output must be whole samples"
    audio_seconds = len(payload) / 2 / audio_format.sample_rate
    assert 0.5 < audio_seconds < 20.0, f"implausible audio duration {audio_seconds:.2f}s"

    print("\n--- ElevenLabs (Phase B) ---")  # noqa: T201 - report output
    print(f"model              : {settings.ELEVENLABS_MODEL}")  # noqa: T201
    print(f"voice id           : {settings.ELEVENLABS_VOICE_ID} (not secret)")  # noqa: T201
    print(f"output format      : {provider.output_format} (PCM16 mono)")  # noqa: T201
    print(f"characters         : {len(TTS_TEXT)}")  # noqa: T201
    print(f"first chunk        : {first_chunk_ms or -1:.0f} ms")  # noqa: T201
    print(f"total              : {total_ms:.0f} ms")  # noqa: T201
    print(f"bytes              : {len(payload)}")  # noqa: T201
    print(f"audio duration     : {audio_seconds:.2f}s")  # noqa: T201
    usage = ledger.snapshot()
    print(f"usage              : {usage['usage']}")  # noqa: T201
    print(f"remaining          : {usage['remaining']}")  # noqa: T201
    assert usage["usage"]["elevenlabs_requests"] >= 1

    # The synthesized audio must not be digital silence (that would mean a
    # "successful" request that a caller would hear as a dead line).
    samples = array("h")
    samples.frombytes(bytes(payload))
    peak = max((abs(s) for s in samples), default=0) / 32768.0
    assert peak > 0.01, "synthesised audio is silent"
