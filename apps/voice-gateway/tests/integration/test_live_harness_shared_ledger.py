"""Day 18E: the live harness and the runtime must share ONE ledger.

Day 18D found that `tests/live/test_day18_live_providers.py` still used an
in-process `LiveProviderBudget`. Day 18E routes it through
`build_runtime_ledger(settings)` + `BudgetedSTT`/`BudgetedTTS`, i.e. exactly the
counters the gateway/agent use.

These tests prove the sharing offline (local fakes only, no provider traffic):

* same ledger file and run id for the harness and the factory,
* a harness reservation is visible to the runtime factory view,
* combined reservations cannot exceed the caps from either side,
* concurrent interleaving cannot exceed them either,
* corrupt/unavailable ledgers fail closed for the harness too,
* missing attestations make the harness skip (never spend).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.domain.audio import AudioFormat
from app.domain.speech import RecognitionStarted, SynthesisCompleted, SynthesisStarted
from app.services.live_provider_budget import (
    LiveBudgetError,
    LiveLedgerCorrupt,
    LiveProviderLedger,
    build_runtime_ledger,
)
from app.speech.budget_guard import BudgetedSTT, BudgetedTTS
from app.speech.factory import SpeechProviderFactory

# The harness module's own gate is exercised directly (module import makes no
# provider calls — it only defines tests).
from tests.live.test_day18_live_providers import _require_live_env

_FORMAT = AudioFormat(sample_rate=16000, channels=1)
_PCM_1S = b"\x01\x00" * 16000


class _FakeSTT:
    provider_name = "fake-stt"

    def __init__(self) -> None:
        self.started = 0

    async def start(self, session_id: str) -> None:
        self.started += 1

    async def push_audio(self, session_id: str, audio: bytes) -> None:  # noqa: ARG002
        # Reject any audio that reaches the fake (proves refusals happen first).
        raise AssertionError("audio reached the provider despite a budget refusal")

    def events(self, session_id: str) -> Any:
        async def _stream() -> Any:
            yield RecognitionStarted(session_id=session_id, provider=self.provider_name)

        return _stream()

    async def stop(self, session_id: str, *, reason: str = "session_stop") -> None:  # noqa: ARG002
        return None


class _FakeTTS:
    provider_name = "fake-tts"

    def __init__(self) -> None:
        self.calls = 0

    async def synthesize(self, text: str, session_id: str, audio_format: Any) -> Any:
        self.calls += 1
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
            total_bytes=1600,
            chunk_count=1,
        )

    async def cancel_turn(self, session_id: str, utterance_id: str) -> None:  # noqa: ARG002
        return None


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        STT_PROVIDER="deepgram",
        TTS_PROVIDER="elevenlabs",
        LLM_PROVIDER="mock",
        DEEPGRAM_API_KEY="test-key-not-real",
        ELEVENLABS_API_KEY="test-key-not-real",
        ELEVENLABS_VOICE_ID="testVoiceId0000000001",
        EMMA_LIVE_GUARD_ENABLED=True,
        EMMA_LIVE_CREDENTIALS_ROTATED=True,
        EMMA_LIVE_PROVIDER_SPEND_APPROVED=True,
        EMMA_LIVE_RUN_ID="shared-ledger-tests",
        EMMA_LIVE_LEDGER_PATH=str(tmp_path / "ledger.json"),
    )


def _harness_ledger(settings: Settings) -> LiveProviderLedger:
    """Exactly what the live harness does (same function, same settings)."""
    return build_runtime_ledger(settings)


# --- identity ---------------------------------------------------------------------


def test_harness_and_factory_resolve_to_the_same_ledger(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    harness = _harness_ledger(settings)
    factory = SpeechProviderFactory(settings)

    status = factory.live_guard_status()
    assert status["ledger_available"] is True
    assert status["ledger_file"] == harness.path.name
    assert status["run_id"] == harness.snapshot()["run_id"] == "shared-ledger-tests"

    # The factory's own ledger object is the same file (not a private copy).
    factory_ledger = factory._ledger  # noqa: SLF001 - asserting identity is the point
    assert factory_ledger is not None
    assert factory_ledger.path.resolve() == harness.path.resolve()
    assert factory_ledger.lock_path.resolve() == harness.lock_path.resolve()


def test_default_ledger_path_is_the_single_shared_default(tmp_path: Path) -> None:
    """With no override both sides derive one default path (no file access)."""
    from app.services.live_provider_budget import default_ledger_path

    settings = Settings(
        STT_PROVIDER="deepgram",
        TTS_PROVIDER="elevenlabs",
        LLM_PROVIDER="mock",
        DEEPGRAM_API_KEY="test-key-not-real",
        ELEVENLABS_API_KEY="test-key-not-real",
        ELEVENLABS_VOICE_ID="testVoiceId0000000001",
        EMMA_LIVE_GUARD_ENABLED=True,
        EMMA_LIVE_CREDENTIALS_ROTATED=True,
        EMMA_LIVE_PROVIDER_SPEND_APPROVED=True,
        EMMA_LIVE_RUN_ID="default-path-test",
        EMMA_LIVE_LEDGER_PATH=None,
    )

    assert settings.live_ledger_path == default_ledger_path()
    assert settings.live_ledger_path.name == "emma-live-provider-ledger.json"
    assert _harness_ledger(settings).path == settings.live_ledger_path


# --- shared counters ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_harness_reservation_is_visible_to_the_runtime_factory(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    harness = _harness_ledger(settings)
    factory = SpeechProviderFactory(settings)

    # Harness-style reservation (wrapped adapter, as the live test does).
    await BudgetedSTT(_FakeSTT(), harness, audio_format=_FORMAT).start("harness-1")

    status = factory.live_guard_status()
    assert status["usage"]["deepgram_sessions"] == 1
    assert status["remaining"]["deepgram_sessions"] == 2


@pytest.mark.asyncio
async def test_combined_reservations_cannot_exceed_the_cap(tmp_path: Path) -> None:
    """Harness + runtime share the caps: 2 + 1 is the maximum in total."""
    settings = _settings(tmp_path)
    harness = _harness_ledger(settings)
    factory_ledger = SpeechProviderFactory(settings)._ledger  # noqa: SLF001
    assert factory_ledger is not None

    # Harness side: two sessions.
    await BudgetedSTT(_FakeSTT(), harness, audio_format=_FORMAT).start("h1")
    await BudgetedSTT(_FakeSTT(), harness, audio_format=_FORMAT).start("h2")

    # Runtime side: one more is allowed, the fourth is refused on both sides.
    runtime_provider = _FakeSTT()
    await BudgetedSTT(runtime_provider, factory_ledger, audio_format=_FORMAT).start("r1")

    refused_harness = _FakeSTT()
    with pytest.raises(LiveBudgetError):
        await BudgetedSTT(refused_harness, harness, audio_format=_FORMAT).start("h3")
    assert refused_harness.started == 0  # refused before any provider contact

    refused_runtime = _FakeSTT()
    with pytest.raises(LiveBudgetError):
        await BudgetedSTT(refused_runtime, factory_ledger, audio_format=_FORMAT).start("r2")
    assert refused_runtime.started == 0

    assert harness.snapshot()["usage"]["deepgram_sessions"] == 3
    assert factory_ledger.snapshot()["usage"]["deepgram_sessions"] == 3


@pytest.mark.asyncio
async def test_combined_tts_requests_share_one_counter(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    harness = _harness_ledger(settings)
    factory_ledger = SpeechProviderFactory(settings)._ledger  # noqa: SLF001
    assert factory_ledger is not None

    async def drain(provider: BudgetedTTS, text: str) -> None:
        async for _event in provider.synthesize(text, "s", _FORMAT):
            pass

    await drain(BudgetedTTS(_FakeTTS(), harness), "harness one")
    await drain(BudgetedTTS(_FakeTTS(), factory_ledger), "runtime one")
    await drain(BudgetedTTS(_FakeTTS(), harness), "harness two")

    late = _FakeTTS()
    with pytest.raises(LiveBudgetError):
        await drain(BudgetedTTS(late, factory_ledger), "runtime two")
    assert late.calls == 0

    usage = harness.snapshot()["usage"]
    assert usage["elevenlabs_requests"] == 3
    assert usage["elevenlabs_chars"] == len("harness one") + len("runtime one") + len("harness two")


def test_concurrent_harness_and_runtime_cannot_exceed_the_cap(tmp_path: Path) -> None:
    """Interleaved harness/runtime threads still respect the shared cap."""
    settings = _settings(tmp_path)
    harness = _harness_ledger(settings)
    factory_ledger = SpeechProviderFactory(settings)._ledger  # noqa: SLF001
    assert factory_ledger is not None

    ledgers = [harness, factory_ledger]

    def attempt(index: int) -> bool:
        try:
            ledgers[index % 2].reserve_stt_session()  # noqa: SLF001 - same call the wrapper makes
            return True
        except LiveBudgetError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, range(12)))

    assert sum(outcomes) == 3
    assert harness.snapshot()["usage"]["deepgram_sessions"] == 3


# --- fail closed --------------------------------------------------------------------


def test_corrupt_ledger_fails_closed_for_the_harness(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    (tmp_path / "ledger.json").write_text('{"version": 1, "run_id": "shared-ledger-tests",')

    with pytest.raises(LiveLedgerCorrupt):
        _harness_ledger(settings).ensure_ready()


@pytest.mark.asyncio
async def test_missing_attestation_makes_the_harness_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No attestation → pytest.skip (never a provider request)."""
    monkeypatch.setenv("EMMA_RUN_LIVE_TESTS", "1")
    monkeypatch.setenv("EMMA_LIVE_CREDENTIALS_ROTATED", "0")
    monkeypatch.setenv("EMMA_LIVE_PROVIDER_SPEND_APPROVED", "1")

    with pytest.raises(pytest.skip.Exception):
        _require_live_env()


def test_live_harness_module_has_no_in_process_budget(tmp_path: Path) -> None:
    """The old in-process accounting must not come back."""
    source = Path(__file__).resolve().parents[1] / "live" / "test_day18_live_providers.py"
    text = source.read_text(encoding="utf-8")

    assert "LiveProviderBudget(" not in text
    assert "check_deepgram_session" not in text
    assert "record_deepgram_session" not in text
    assert "check_elevenlabs_request" not in text
    assert "build_runtime_ledger(" in text
    assert "BudgetedSTT(" in text and "BudgetedTTS(" in text
