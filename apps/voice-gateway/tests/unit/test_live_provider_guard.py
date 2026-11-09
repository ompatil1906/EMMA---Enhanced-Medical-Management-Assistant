"""Day 18C: runtime guard wrapper tests (offline, controlled doubles).

Covers the wrappers the real runtime uses (`BudgetedSTT` / `BudgetedTTS`):

* reservation happens before the provider is contacted,
* refusals raise and never touch the provider (no network),
* Deepgram audio is capped *while streaming*, not only at reservation,
* cancellation and provider errors never refund a counted request,
* the exhausted STT session surfaces a fatal error so the turn ends.

A separate integration test (`tests/integration/test_live_guard_runtime.py`)
drives the real Deepgram adapter through the wrapper against a local server.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.domain.audio import AudioFormat
from app.domain.speech import (
    RecognitionError,
    RecognitionStarted,
    RecognitionStopped,
    SynthesisCompleted,
    SynthesisStarted,
)
from app.services.live_provider_budget import (
    LiveBudgetError,
    LiveProviderLedger,
    LiveProviderLimits,
)
from app.speech.base import SpeechProviderError
from app.speech.budget_guard import BudgetedSTT, BudgetedTTS

_FORMAT = AudioFormat(sample_rate=16000, channels=1)
_SECOND_OF_AUDIO = b"\x01\x00" * 16000  # 1 s of PCM16 mono @ 16 kHz


class FakeSTTProvider:
    """Controlled double: counts calls, never opens a socket."""

    provider_name = "fake-stt"

    def __init__(self, *, fail_start: bool = False) -> None:
        self.started = 0
        self.pushed = 0
        self.pushed_bytes = 0
        self.stopped = 0
        self.finished = 0
        self._fail_start = fail_start

    async def start(self, session_id: str) -> None:
        self.started += 1
        if self._fail_start:
            raise RuntimeError("provider refused the session")

    async def push_audio(self, session_id: str, audio: bytes) -> None:
        self.pushed += 1
        self.pushed_bytes += len(audio)

    def events(self, session_id: str) -> Any:
        async def _stream() -> Any:
            yield RecognitionStarted(session_id=session_id, provider=self.provider_name)
            yield RecognitionStopped(session_id=session_id, provider=self.provider_name)

        return _stream()

    async def stop(self, session_id: str, *, reason: str = "session_stop") -> None:
        self.stopped += 1

    async def finish(self, session_id: str, **kwargs: Any) -> None:
        self.finished += 1


class FakeTTSProvider:
    provider_name = "fake-tts"

    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.texts: list[str] = []
        self.cancelled: list[str] = []
        self._fail = fail

    async def synthesize(self, text: str, session_id: str, audio_format: Any) -> Any:
        self.calls += 1
        self.texts.append(text)
        if self._fail:
            raise RuntimeError("provider exploded")
        yield SynthesisStarted(
            session_id=session_id,
            provider=self.provider_name,
            utterance_id="u1",
            text_chars=len(text),
            audio_format=audio_format,
        )
        yield SynthesisCompleted(
            session_id=session_id,
            provider=self.provider_name,
            utterance_id="u1",
            total_bytes=3200,
            chunk_count=1,
        )

    async def cancel_turn(self, session_id: str, utterance_id: str) -> None:
        self.cancelled.append(utterance_id)


def _ledger(tmp_path: Path, **overrides: Any) -> LiveProviderLedger:
    return LiveProviderLedger(
        path=tmp_path / "ledger.json",
        limits=LiveProviderLimits(**overrides),
        run_id="guard-tests",
    )


async def _drain(iterator: Any) -> list[Any]:
    return [event async for event in iterator]


# --- STT -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_reserves_a_session_before_contacting_the_provider(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    inner = FakeSTTProvider()
    guarded = BudgetedSTT(inner, ledger, audio_format=_FORMAT)

    await guarded.start("s1")

    assert inner.started == 1
    assert ledger.snapshot()["usage"]["deepgram_sessions"] == 1
    assert guarded.provider_name.endswith("+budget-guarded")


@pytest.mark.asyncio
async def test_session_cap_refuses_without_touching_the_provider(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, max_deepgram_sessions=2)
    inner = FakeSTTProvider()

    await BudgetedSTT(inner, ledger, audio_format=_FORMAT).start("s1")
    await BudgetedSTT(inner, ledger, audio_format=_FORMAT).start("s2")
    assert inner.started == 2

    refused = FakeSTTProvider()
    with pytest.raises(LiveBudgetError, match="session cap"):
        await BudgetedSTT(refused, ledger, audio_format=_FORMAT).start("s3")

    assert refused.started == 0  # never contacted → no network, no spend
    assert ledger.snapshot()["usage"]["deepgram_sessions"] == 2


@pytest.mark.asyncio
async def test_audio_is_capped_while_streaming(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    inner = FakeSTTProvider()
    guarded = BudgetedSTT(inner, ledger, audio_format=_FORMAT)
    await guarded.start("s1")

    for _ in range(15):
        await guarded.push_audio("s1", _SECOND_OF_AUDIO)

    with pytest.raises(LiveBudgetError, match="audio cap"):
        await guarded.push_audio("s1", _SECOND_OF_AUDIO)

    assert inner.pushed == 15  # the 16th second never reached the provider
    assert inner.stopped == 1  # stream was torn down at the cap
    assert guarded.exhausted is True
    assert ledger.snapshot()["usage"]["deepgram_audio_seconds"] == 15.0


@pytest.mark.asyncio
async def test_audio_cap_is_exact_at_the_boundary(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    inner = FakeSTTProvider()
    guarded = BudgetedSTT(inner, ledger, audio_format=_FORMAT)
    await guarded.start("s1")

    await guarded.push_audio("s1", _SECOND_OF_AUDIO * 15)  # exactly at the cap
    with pytest.raises(LiveBudgetError):
        await guarded.push_audio("s1", b"\x00\x00")  # one extra sample

    assert inner.pushed == 1


@pytest.mark.asyncio
async def test_exhausted_session_still_yields_a_fatal_error_event(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    guarded = BudgetedSTT(FakeSTTProvider(), ledger, audio_format=_FORMAT)
    await guarded.start("s1")
    await guarded.push_audio("s1", _SECOND_OF_AUDIO * 15)
    with pytest.raises(LiveBudgetError):
        await guarded.push_audio("s1", _SECOND_OF_AUDIO)

    events = await _drain(guarded.events("s1"))

    assert isinstance(events[-1], RecognitionError)
    assert events[-1].fatal is True
    assert events[-1].code == "budget_exceeded"


@pytest.mark.asyncio
async def test_push_audio_after_exhaustion_is_a_noop(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    inner = FakeSTTProvider()
    guarded = BudgetedSTT(inner, ledger, audio_format=_FORMAT)
    await guarded.start("s1")
    for _ in range(15):
        await guarded.push_audio("s1", _SECOND_OF_AUDIO)
    with pytest.raises(LiveBudgetError):
        await guarded.push_audio("s1", _SECOND_OF_AUDIO)

    await guarded.push_audio("s1", _SECOND_OF_AUDIO)  # must not raise or forward
    assert inner.pushed == 15
    assert inner.pushed_bytes == 15 * len(_SECOND_OF_AUDIO)


@pytest.mark.asyncio
async def test_provider_failure_is_not_refunded(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    guarded = BudgetedSTT(FakeSTTProvider(fail_start=True), ledger, audio_format=_FORMAT)

    with pytest.raises(RuntimeError):
        await guarded.start("s1")

    # The attempt was made: keeping it counted is the conservative choice.
    assert ledger.snapshot()["usage"]["deepgram_sessions"] == 1


@pytest.mark.asyncio
async def test_stop_and_finish_delegate_to_the_inner_provider(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    inner = FakeSTTProvider()
    guarded = BudgetedSTT(inner, ledger, audio_format=_FORMAT)
    await guarded.start("s1")

    await guarded.finish("s1", grace_seconds=0.1)
    await guarded.stop("s1")

    assert inner.finished == 1
    assert inner.stopped == 1


@pytest.mark.asyncio
async def test_second_session_id_is_rejected(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    guarded = BudgetedSTT(FakeSTTProvider(), ledger, audio_format=_FORMAT)
    await guarded.start("s1")

    with pytest.raises(SpeechProviderError):
        await guarded.start("s2")


# --- TTS -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tts_reserves_one_request_per_synthesis(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    inner = FakeTTSProvider()
    guarded = BudgetedTTS(inner, ledger)

    await _drain(guarded.synthesize("Good morning.", "s1", _FORMAT))

    assert inner.calls == 1
    snapshot = ledger.snapshot()
    assert snapshot["usage"]["elevenlabs_requests"] == 1
    assert snapshot["usage"]["elevenlabs_chars"] == len("Good morning.")


@pytest.mark.asyncio
async def test_tts_request_cap_refuses_before_contacting_the_provider(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, max_elevenlabs_requests=2)
    inner = FakeTTSProvider()
    guarded = BudgetedTTS(inner, ledger)

    await _drain(guarded.synthesize("one", "s1", _FORMAT))
    await _drain(guarded.synthesize("two", "s1", _FORMAT))
    with pytest.raises(LiveBudgetError, match="request cap"):
        await _drain(guarded.synthesize("three", "s1", _FORMAT))

    assert inner.calls == 2
    assert "three" not in inner.texts


@pytest.mark.asyncio
async def test_tts_character_limit_refuses_before_contacting_the_provider(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    inner = FakeTTSProvider()
    guarded = BudgetedTTS(inner, ledger)

    with pytest.raises(LiveBudgetError, match="char cap"):
        await _drain(guarded.synthesize("x" * 301, "s1", _FORMAT))

    assert inner.calls == 0
    assert ledger.snapshot()["usage"]["elevenlabs_requests"] == 0


@pytest.mark.asyncio
async def test_tts_empty_text_does_not_consume_budget(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    inner = FakeTTSProvider()
    guarded = BudgetedTTS(inner, ledger)

    await _drain(guarded.synthesize("   ", "s1", _FORMAT))

    assert inner.calls == 1  # the provider still yields started/completed
    assert ledger.snapshot()["usage"]["elevenlabs_requests"] == 0


@pytest.mark.asyncio
async def test_tts_provider_exception_keeps_the_request_counted(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    guarded = BudgetedTTS(FakeTTSProvider(fail=True), ledger)

    with pytest.raises(RuntimeError):
        await _drain(guarded.synthesize("Good morning.", "s1", _FORMAT))

    assert ledger.snapshot()["usage"]["elevenlabs_requests"] == 1


@pytest.mark.asyncio
async def test_tts_cancellation_keeps_the_request_counted(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    inner = FakeTTSProvider()
    guarded = BudgetedTTS(inner, ledger)

    stream = guarded.synthesize("Good morning.", "s1", _FORMAT)
    async for _event in stream:
        await guarded.cancel_turn("s1", "u1")
        break

    assert inner.cancelled == ["u1"]
    assert ledger.snapshot()["usage"]["elevenlabs_requests"] == 1


@pytest.mark.asyncio
async def test_tts_repeated_calls_after_cancellation_still_count(tmp_path: Path) -> None:
    """Cancelling must not hand the caller a free extra request."""
    ledger = _ledger(tmp_path, max_elevenlabs_requests=2)
    inner = FakeTTSProvider()
    guarded = BudgetedTTS(inner, ledger)

    for _ in range(2):
        async for _event in guarded.synthesize("hi", "s1", _FORMAT):
            await guarded.cancel_turn("s1", "u1")
            break

    with pytest.raises(LiveBudgetError):
        await _drain(guarded.synthesize("hi", "s1", _FORMAT))
    assert inner.calls == 2


@pytest.mark.asyncio
async def test_guarded_wrappers_respect_a_restarted_ledger_count(tmp_path: Path) -> None:
    """A restarted worker re-reads the file and keeps the same budget."""
    first = BudgetedTTS(FakeTTSProvider(), _ledger(tmp_path, max_elevenlabs_requests=1))
    await _drain(first.synthesize("one", "s1", _FORMAT))

    # New wrapper, new ledger object, same file (worker restart).
    restarted_inner = FakeTTSProvider()
    restarted = BudgetedTTS(restarted_inner, _ledger(tmp_path, max_elevenlabs_requests=1))
    with pytest.raises(LiveBudgetError):
        await _drain(restarted.synthesize("two", "s1", _FORMAT))

    assert restarted_inner.calls == 0


@pytest.mark.asyncio
async def test_concurrent_tts_calls_cannot_exceed_the_cap(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, max_elevenlabs_requests=3)
    inner = FakeTTSProvider()
    guarded = BudgetedTTS(inner, ledger)

    async def call() -> bool:
        try:
            await _drain(guarded.synthesize("hi", "s1", _FORMAT))
            return True
        except LiveBudgetError:
            return False

    results = await asyncio.gather(*(call() for _ in range(12)))

    assert sum(results) == 3
    assert inner.calls == 3
