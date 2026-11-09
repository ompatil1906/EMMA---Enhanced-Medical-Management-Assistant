"""Day 12: LiveKit token minter and speech factory tests (offline).

Tests configuration and token minting without consuming API credits:

* Speech factory recognizes deepgram/elevenlabs provider options.
* Configuration problems detected when credentials missing.
* LiveKit token minter requires credentials.
* No live WebSocket or API calls — all offline.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import Settings
from app.domain.audio import AudioFormat
from app.integrations.livekit import LiveKitTokenMinter
from app.integrations.llm.openai_provider import OpenAILLMProvider
from app.integrations.llm.provider_factory import LLMProviderFactory
from app.speech.base import ProviderConfigurationError
from app.speech.deepgram_stt import DeepgramFluxSTTProvider
from app.speech.elevenlabs_tts import ElevenLabsFlashTTSProvider
from app.speech.factory import SpeechProviderFactory


def _make_settings(monkeypatch: pytest.MonkeyPatch | None = None, **overrides: Any) -> Settings:
    data = {
        "OPENAI_API_KEY": "test-key-not-real",
        "OPENAI_MODEL": "gpt-6-luna",
        "DEEPGRAM_API_KEY": "test-key-not-real",
        "DEEPGRAM_MODEL": "flux-general-en",
        "ELEVENLABS_API_KEY": "test-key-not-real",
        "ELEVENLABS_VOICE_ID": "test-voice-not-real",
        "ELEVENLABS_MODEL": "eleven_flash_v2_5",
        "LIVEKIT_API_KEY": "test-key-not-real",
        "LIVEKIT_API_SECRET": "test-secret-not-real",
        "AZURE_SPEECH_KEY": "test-key-not-real",
        "AZURE_SPEECH_REGION": "eastasia",
        "LLM_API_KEY": "test-key-not-real",
        "GEMINI_EMBEDDING_API_KEY": "test-key-not-real",
        "VOICE_TRANSPORT": "websocket",
    }
    data.update(overrides)

    if monkeypatch is not None:
        for key, value in overrides.items():
            env_key = key.upper()
            if value is None:
                monkeypatch.setenv(env_key, "")
            else:
                monkeypatch.setenv(env_key, str(value))

    return Settings(**data)


def test_speech_factory_deepgram_stt() -> None:
    settings = _make_settings(STT_PROVIDER="deepgram")
    factory = SpeechProviderFactory(settings)
    assert factory.stt_provider_name == "deepgram"
    assert factory.configuration_ok
    stt = factory.create_stt(AudioFormat(sample_rate=16000, channels=1))
    assert isinstance(stt, DeepgramFluxSTTProvider)
    assert stt.provider_name == "deepgram-flux:flux-general-en"


def test_speech_factory_elevenlabs_tts() -> None:
    settings = _make_settings(TTS_PROVIDER="elevenlabs")
    factory = SpeechProviderFactory(settings)
    assert factory.tts_provider_name == "elevenlabs"
    assert factory.configuration_ok
    tts = factory.create_tts(AudioFormat(sample_rate=16000, channels=1))
    assert isinstance(tts, ElevenLabsFlashTTSProvider)
    assert tts.provider_name == "elevenlabs-flash:eleven_flash_v2_5"


def test_speech_factory_deepgram_missing_key_raises() -> None:
    settings = _make_settings(STT_PROVIDER="deepgram", DEEPGRAM_API_KEY=None)
    factory = SpeechProviderFactory(settings)
    assert not factory.configuration_ok
    with pytest.raises(ProviderConfigurationError):
        factory.create_stt(AudioFormat(sample_rate=16000, channels=1))


def test_speech_factory_elevenlabs_missing_voice_id_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """ElevenLabs TTS requires ELEVENLABS_VOICE_ID — fail fast at settings level."""
    with pytest.raises(ValueError, match="ELEVENLABS_VOICE_ID"):
        _make_settings(monkeypatch, TTS_PROVIDER="elevenlabs", ELEVENLABS_VOICE_ID=None)


def test_llm_factory_openai_with_budget_guard() -> None:
    settings = _make_settings(LLM_PROVIDER="openai")
    factory = LLMProviderFactory(settings)
    assert factory.provider_name == "openai"
    assert factory.configuration_ok
    assert factory.budget_guard is not None
    llm = factory.create()
    assert isinstance(llm, OpenAILLMProvider)
    assert llm.provider_name == "openai:gpt-6-luna"


def test_llm_factory_openai_missing_key_raises() -> None:
    settings = _make_settings(LLM_PROVIDER="openai", OPENAI_API_KEY=None)
    factory = LLMProviderFactory(settings)
    assert not factory.configuration_ok
    from app.integrations.llm.exceptions import LLMProviderNotAvailable

    with pytest.raises(LLMProviderNotAvailable):
        factory.create()


def test_livekit_token_minter_requires_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _make_settings(monkeypatch, LIVEKIT_API_KEY=None, LIVEKIT_API_SECRET=None)
    with pytest.raises(ProviderConfigurationError):
        LiveKitTokenMinter(settings=settings)


def test_budget_guard_integrated_with_llm_factory() -> None:
    settings = _make_settings(LLM_PROVIDER="openai")
    factory = LLMProviderFactory(settings)
    guard = factory.budget_guard
    assert guard is not None
    snap = guard.snapshot()
    assert snap.request_count == 0
    assert snap.total_usd == 0.0
    assert snap.can_spend is True
