"""Day 18C: the runtime factory must never hand out an unguarded live provider.

These tests pin the integration contract:

* guard disabled (default, mock development) → unchanged behaviour, no ledger,
* guard enabled → Deepgram/ElevenLabs are always wrapped, no opt-out,
* guard enabled with an unusable or corrupt ledger → fail closed (raise), never
  an unguarded provider and never a mock fallback,
* mock providers keep working and never touch the ledger,
* the actual LiveKit agent construction path (`build_speech_factory`) yields
  guarded providers,
* the hard ceilings cannot be raised by configuration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.domain.audio import AudioFormat
from app.integrations.livekit.agent import build_speech_factory
from app.services.live_provider_budget import LiveProviderLedger
from app.speech.base import ProviderConfigurationError
from app.speech.budget_guard import BudgetedSTT, BudgetedTTS
from app.speech.deepgram_stt import DeepgramFluxSTTProvider
from app.speech.elevenlabs_tts import ElevenLabsFlashTTSProvider
from app.speech.factory import SpeechProviderFactory
from app.speech.mock_stt import MockSpeechToTextProvider
from app.speech.mock_tts import MockTextToSpeechProvider

_FORMAT = AudioFormat(sample_rate=16000, channels=1)


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    base: dict[str, object] = {
        "STT_PROVIDER": "deepgram",
        "TTS_PROVIDER": "elevenlabs",
        "LLM_PROVIDER": "mock",
        "DEEPGRAM_API_KEY": "test-key-not-real",
        "DEEPGRAM_MODEL": "flux-general-en",
        "ELEVENLABS_API_KEY": "test-key-not-real",
        "ELEVENLABS_VOICE_ID": "testVoiceId0000000001",
        "ELEVENLABS_MODEL": "eleven_flash_v2_5",
        "EMMA_LIVE_GUARD_ENABLED": True,
        "EMMA_LIVE_CREDENTIALS_ROTATED": True,
        "EMMA_LIVE_PROVIDER_SPEND_APPROVED": True,
        "EMMA_LIVE_LEDGER_PATH": str(tmp_path / "ledger.json"),
        "EMMA_LIVE_RUN_ID": "factory-tests",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# --- disabled --------------------------------------------------------------------


def test_guard_disabled_returns_plain_live_providers(tmp_path: Path) -> None:
    factory = SpeechProviderFactory(_settings(tmp_path, EMMA_LIVE_GUARD_ENABLED=False))

    stt = factory.create_stt(_FORMAT)
    tts = factory.create_tts(_FORMAT)

    assert isinstance(stt, DeepgramFluxSTTProvider)
    assert isinstance(tts, ElevenLabsFlashTTSProvider)
    assert not isinstance(stt, BudgetedSTT)
    assert not (tmp_path / "ledger.json").exists()  # no ledger written
    assert factory.live_guard_ready is True


# --- enabled ---------------------------------------------------------------------


def test_guard_enabled_wraps_both_live_providers(tmp_path: Path) -> None:
    factory = SpeechProviderFactory(_settings(tmp_path))

    stt = factory.create_stt(_FORMAT)
    tts = factory.create_tts(_FORMAT)

    assert isinstance(stt, BudgetedSTT)
    assert isinstance(stt.inner, DeepgramFluxSTTProvider)
    assert isinstance(tts, BudgetedTTS)
    assert isinstance(tts.inner, ElevenLabsFlashTTSProvider)
    assert stt.provider_name.endswith("+budget-guarded")
    assert tts.provider_name.endswith("+budget-guarded")
    assert factory.live_guard_ready is True
    assert factory.configuration_ok is True


def test_opt_in_live_test_flag_implies_the_guard(tmp_path: Path) -> None:
    settings = _settings(tmp_path, EMMA_LIVE_GUARD_ENABLED=False, EMMA_RUN_LIVE_TESTS=True)
    factory = SpeechProviderFactory(settings)

    assert settings.live_guard_enabled is True
    assert isinstance(factory.create_stt(_FORMAT), BudgetedSTT)
    assert isinstance(factory.create_tts(_FORMAT), BudgetedTTS)


def test_mock_providers_are_untouched_and_consume_no_budget(tmp_path: Path) -> None:
    factory = SpeechProviderFactory(_settings(tmp_path, STT_PROVIDER="mock", TTS_PROVIDER="mock"))

    assert isinstance(factory.create_stt(_FORMAT), MockSpeechToTextProvider)
    assert isinstance(factory.create_tts(_FORMAT), MockTextToSpeechProvider)
    assert not (tmp_path / "ledger.json").exists()


# --- fail closed ------------------------------------------------------------------


def _corrupt_ledger(path: Path) -> None:
    path.write_text('{"version": 1, "run_id": "factory-tests", "deepgram":')  # truncated


def test_corrupt_ledger_refuses_to_build_live_providers(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _corrupt_ledger(tmp_path / "ledger.json")

    factory = SpeechProviderFactory(settings)  # construction succeeds, marked unusable
    assert factory.live_guard_ready is False
    assert factory.configuration_ok is False
    assert "ledger" in (factory.configuration_problem() or "").lower()

    with pytest.raises(ProviderConfigurationError, match="budget guard"):
        factory.create_stt(_FORMAT)
    with pytest.raises(ProviderConfigurationError, match="budget guard"):
        factory.create_tts(_FORMAT)


def test_unwritable_ledger_location_refuses_to_build_live_providers(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    factory = SpeechProviderFactory(
        _settings(tmp_path, EMMA_LIVE_LEDGER_PATH=str(blocker / "l.json"))
    )

    assert factory.live_guard_ready is False
    with pytest.raises(ProviderConfigurationError):
        factory.create_stt(_FORMAT)


def test_no_silent_mock_fallback_when_the_guard_is_unusable(tmp_path: Path) -> None:
    _corrupt_ledger(tmp_path / "ledger.json")
    factory = SpeechProviderFactory(_settings(tmp_path))

    with pytest.raises(ProviderConfigurationError):
        factory.create_stt(_FORMAT)  # must raise, never return MockSpeechToTextProvider


# --- agent construction path --------------------------------------------------------


def test_livekit_agent_builds_guarded_providers(tmp_path: Path) -> None:
    """`build_speech_factory` is exactly what the agent entrypoint uses."""
    factory = build_speech_factory(_settings(tmp_path))

    assert factory.live_guard_enabled is True
    stt = factory.create_stt(_FORMAT)
    tts = factory.create_tts(_FORMAT)

    assert isinstance(stt, BudgetedSTT)
    assert isinstance(tts, BudgetedTTS)
    # The real adapters are beneath the wrappers (no replacement, only wrapping).
    assert type(stt.inner).__name__ == "DeepgramFluxSTTProvider"
    assert type(tts.inner).__name__ == "ElevenLabsFlashTTSProvider"


def test_agent_construction_shares_one_ledger(tmp_path: Path) -> None:
    factory = build_speech_factory(_settings(tmp_path))
    factory.create_stt(_FORMAT)

    ledger = LiveProviderLedger(
        path=tmp_path / "ledger.json",
        limits=factory._settings.live_provider_limits,  # noqa: SLF001 - same settings object
        run_id="factory-tests",
    )
    assert ledger.snapshot()["usage"]["deepgram_sessions"] >= 0  # readable + valid


# --- status surface ------------------------------------------------------------------


def test_guard_status_is_secret_free_and_reports_capacity(tmp_path: Path) -> None:
    factory = SpeechProviderFactory(_settings(tmp_path))
    status = factory.live_guard_status()

    assert status["enabled"] is True
    assert status["attestation"] == {
        "credentials_rotated": True,
        "spend_approved": True,
        "ok": True,
    }
    assert status["ledger_available"] is True
    assert status["configuration_ready"] is True
    assert status["remaining"]["deepgram_sessions"] == 3
    assert status["limits"]["max_elevenlabs_chars"] == 300
    assert status["run_id"] == "factory-tests"
    serialised = json.dumps(status)
    assert "test-key-not-real" not in serialised
    assert "testVoiceId0000000001" not in serialised


def test_guard_status_fails_closed_for_corrupt_ledger(tmp_path: Path) -> None:
    _corrupt_ledger(tmp_path / "ledger.json")
    status = SpeechProviderFactory(_settings(tmp_path)).live_guard_status()

    assert status["enabled"] is True
    assert status["ledger_available"] is False
    assert status["configuration_ready"] is False
    assert "Corrupt" in str(status["error"])


def test_guard_status_disabled_shape(tmp_path: Path) -> None:
    status = SpeechProviderFactory(
        _settings(tmp_path, EMMA_LIVE_GUARD_ENABLED=False)
    ).live_guard_status()
    assert status["enabled"] is False
    assert status["configuration_ready"] is True
    assert status["usage"] is None


# --- configuration cannot raise the ceilings ------------------------------------------


@pytest.mark.parametrize(
    "override",
    [
        {"EMMA_LIVE_MAX_DEEPGRAM_SESSIONS": 4},
        {"EMMA_LIVE_MAX_DEEPGRAM_AUDIO_SECONDS": 16},
        {"EMMA_LIVE_MAX_ELEVENLABS_REQUESTS": 4},
        {"EMMA_LIVE_MAX_ELEVENLABS_CHARS": 301},
    ],
)
def test_guard_limits_cannot_be_raised(tmp_path: Path, override: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        _settings(tmp_path, **override)


def test_guard_limits_can_be_lowered(tmp_path: Path) -> None:
    settings = _settings(tmp_path, EMMA_LIVE_MAX_DEEPGRAM_SESSIONS=1)
    limits = settings.live_provider_limits
    assert limits.max_deepgram_sessions == 1
    assert limits.max_elevenlabs_requests == 3  # others keep the defaults


# --- Day 18D: user attestation gate ---------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"EMMA_LIVE_CREDENTIALS_ROTATED": False, "EMMA_LIVE_PROVIDER_SPEND_APPROVED": True},
        {"EMMA_LIVE_CREDENTIALS_ROTATED": True, "EMMA_LIVE_PROVIDER_SPEND_APPROVED": False},
        {"EMMA_LIVE_CREDENTIALS_ROTATED": False, "EMMA_LIVE_PROVIDER_SPEND_APPROVED": False},
    ],
    ids=["rotation-missing", "spend-missing", "both-missing"],
)
def test_guard_armed_without_attestation_refuses_live_providers(
    tmp_path: Path, overrides: dict[str, bool]
) -> None:
    """A guard-armed run may not spend until the operator attested both."""
    factory = SpeechProviderFactory(_settings(tmp_path, **overrides))

    with pytest.raises(ProviderConfigurationError, match="not authorised"):
        factory.create_stt(_FORMAT)
    with pytest.raises(ProviderConfigurationError, match="not authorised"):
        factory.create_tts(_FORMAT)

    assert factory.configuration_ok is False
    assert "not authorised" in (factory.configuration_problem() or "")
    status = factory.live_guard_status()
    assert status["attestation"]["ok"] is False
    assert status["configuration_ready"] is False


def test_missing_attestation_does_not_affect_mock_providers(tmp_path: Path) -> None:
    """Mock development keeps working without the paid-run attestations."""
    factory = SpeechProviderFactory(
        _settings(
            tmp_path,
            STT_PROVIDER="mock",
            TTS_PROVIDER="mock",
            EMMA_LIVE_CREDENTIALS_ROTATED=False,
            EMMA_LIVE_PROVIDER_SPEND_APPROVED=False,
        )
    )

    assert isinstance(factory.create_stt(_FORMAT), MockSpeechToTextProvider)
    assert isinstance(factory.create_tts(_FORMAT), MockTextToSpeechProvider)
    assert factory.configuration_ok is True


def test_attestation_status_is_reported_even_when_guard_disabled(tmp_path: Path) -> None:
    status = SpeechProviderFactory(
        _settings(
            tmp_path,
            EMMA_LIVE_GUARD_ENABLED=False,
            EMMA_LIVE_CREDENTIALS_ROTATED=False,
            EMMA_LIVE_PROVIDER_SPEND_APPROVED=False,
        )
    ).live_guard_status()

    assert status["enabled"] is False
    assert status["attestation"]["ok"] is False
    assert status["configuration_ready"] is True  # nothing to protect when off


# --- Day 18E: blank flag placeholders must not crash startup --------------------------


def test_blank_flag_values_mean_false_instead_of_a_validation_error(tmp_path: Path) -> None:
    """`KEY=` in .env is "off" — not an opaque pydantic bool crash at boot."""
    settings = Settings(
        STT_PROVIDER="mock",
        TTS_PROVIDER="mock",
        LLM_PROVIDER="mock",
        EMMA_LIVE_GUARD_ENABLED="",
        EMMA_RUN_LIVE_TESTS="",
        EMMA_LIVE_CREDENTIALS_ROTATED="",
        EMMA_LIVE_PROVIDER_SPEND_APPROVED="",
    )

    assert settings.live_guard_enabled is False
    assert settings.live_attestations_ok is False
    assert settings.live_attestation_problem is None


def test_explicit_true_flags_still_parse(tmp_path: Path) -> None:
    settings = Settings(
        STT_PROVIDER="mock",
        TTS_PROVIDER="mock",
        LLM_PROVIDER="mock",
        EMMA_LIVE_GUARD_ENABLED="1",
        EMMA_LIVE_CREDENTIALS_ROTATED="1",
        EMMA_LIVE_PROVIDER_SPEND_APPROVED="true",
    )

    assert settings.live_guard_enabled is True
    assert settings.live_attestations_ok is True
