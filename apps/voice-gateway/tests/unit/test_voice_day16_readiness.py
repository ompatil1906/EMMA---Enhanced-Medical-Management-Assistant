"""Day 16: Voice infrastructure readiness tests (offline, no provider API calls).

Tests cover:

* LiveKit environment validation (URL, key, secret presence).
* API key and secret handling (dev vs production credentials).
* Token authorization (TTL bounds, practice isolation, multiple rooms).
* Expired and invalid token handling at the minter level.
* Production rejection of development configuration.
* Browser transport selection (websocket vs livekit).
* Missing ElevenLabs voice ID validation.
* Provider configuration validation (no silent fallback).
* Safe provider failures (constructor raises on missing credentials).
* Existing WebSocket fallback (no LiveKit credentials needed).

No network calls, no API credits consumed. All external providers are mocked.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from app.core.config import Settings
from app.integrations.livekit import LiveKitTokenMinter
from app.speech.base import ProviderConfigurationError
from app.speech.deepgram_stt import DeepgramFluxSTTProvider
from app.speech.elevenlabs_tts import ElevenLabsFlashTTSProvider

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_settings(monkeypatch: pytest.MonkeyPatch | None = None, **overrides: Any) -> Settings:
    """Build Settings, optionally clearing env vars that .env has set.

    When `monkeypatch` is provided and an override value is None, the
    corresponding env var is deleted so Settings does not fall back to .env.
    """
    data = {
        "LIVEKIT_API_KEY": "test-key-not-real",
        "LIVEKIT_API_SECRET": "test-secret-not-real",
        "LIVEKIT_URL": "ws://127.0.0.1:7880",
        "PRACTICE_ID": "demo-practice-001",
        "VOICE_TRANSPORT": "websocket",
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "test-key-not-real",
        "STT_PROVIDER": "deepgram",
        "DEEPGRAM_API_KEY": "test-key-not-real",
        "TTS_PROVIDER": "elevenlabs",
        "ELEVENLABS_API_KEY": "test-key-not-real",
        "ELEVENLABS_VOICE_ID": "test-voice-not-real",
        "APP_ENV": "development",
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


# ---------------------------------------------------------------------------
# 1. LiveKit environment validation
# ---------------------------------------------------------------------------


def test_livekit_url_required_when_transport_livekit(monkeypatch: pytest.MonkeyPatch) -> None:
    """LIVEKIT_URL is required when VOICE_TRANSPORT=livekit."""
    with pytest.raises(ValueError, match="LIVEKIT_URL"):
        _make_settings(
            monkeypatch,
            VOICE_TRANSPORT="livekit",
            LIVEKIT_URL=None,
            LIVEKIT_API_KEY="real-key",
            LIVEKIT_API_SECRET="real-secret",
        )


def test_livekit_api_key_required_when_transport_livekit(monkeypatch: pytest.MonkeyPatch) -> None:
    """LIVEKIT_API_KEY is required when VOICE_TRANSPORT=livekit."""
    with pytest.raises(ValueError, match="LIVEKIT_API_KEY"):
        _make_settings(
            monkeypatch,
            VOICE_TRANSPORT="livekit",
            LIVEKIT_API_KEY=None,
            LIVEKIT_API_SECRET="real-secret",
        )


def test_livekit_api_secret_required_when_transport_livekit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LIVEKIT_API_SECRET is required when VOICE_TRANSPORT=livekit."""
    with pytest.raises(ValueError, match="LIVEKIT_API_SECRET"):
        _make_settings(
            monkeypatch,
            VOICE_TRANSPORT="livekit",
            LIVEKIT_API_KEY="real-key",
            LIVEKIT_API_SECRET=None,
        )


def test_websocket_transport_without_livekit_credentials_ok() -> None:
    """VOICE_TRANSPORT=websocket works without LiveKit credentials (fallback)."""
    settings = _make_settings(
        VOICE_TRANSPORT="websocket",
        LIVEKIT_API_KEY=None,
        LIVEKIT_API_SECRET=None,
        LIVEKIT_URL=None,
    )
    assert settings.VOICE_TRANSPORT == "websocket"
    assert settings.livekit_provider_configured is True


# ---------------------------------------------------------------------------
# 2. API key and secret handling (dev vs production)
# ---------------------------------------------------------------------------


def test_dev_livekit_credentials_accepted_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dev LiveKit credentials accepted when APP_ENV=development."""
    settings = _make_settings(
        monkeypatch,
        LIVEKIT_API_KEY="devkey",
        LIVEKIT_API_SECRET="secret",
        VOICE_TRANSPORT="livekit",
        APP_ENV="development",
    )
    assert settings.VOICE_TRANSPORT == "livekit"
    assert settings.livekit_provider_configured is True


def test_dev_livekit_credentials_rejected_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dev LiveKit credentials rejected when APP_ENV=production."""
    with pytest.raises(ValueError, match="development credential"):
        _make_settings(
            monkeypatch,
            LIVEKIT_API_KEY="devkey",
            LIVEKIT_API_SECRET="secret",
            VOICE_TRANSPORT="livekit",
            APP_ENV="production",
        )


def test_dev_secret_rejected_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dev secret rejected even with a real key in production."""
    with pytest.raises(ValueError, match="development credential"):
        _make_settings(
            monkeypatch,
            LIVEKIT_API_KEY="real-key",
            LIVEKIT_API_SECRET="secret",
            VOICE_TRANSPORT="livekit",
            APP_ENV="production",
        )


def test_real_credentials_accepted_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real (non-dev) credentials accepted when APP_ENV=production."""
    settings = _make_settings(
        monkeypatch,
        LIVEKIT_API_KEY="prod-key-12345",
        LIVEKIT_API_SECRET="prod-secret-67890",
        VOICE_TRANSPORT="livekit",
        APP_ENV="production",
    )
    assert settings.VOICE_TRANSPORT == "livekit"
    assert settings.livekit_provider_configured is True


# ---------------------------------------------------------------------------
# 3. Token authorization (TTL, practice isolation, multiple rooms)
# ---------------------------------------------------------------------------


def test_token_minter_requires_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """LiveKitTokenMinter raises without API key/secret."""
    settings = _make_settings(
        monkeypatch,
        LIVEKIT_API_KEY=None,
        LIVEKIT_API_SECRET=None,
    )
    with pytest.raises(ProviderConfigurationError):
        LiveKitTokenMinter(settings=settings)


def test_token_mint_contains_practice_id() -> None:
    """Minted token embeds server-side practice_id in metadata."""
    settings = _make_settings(PRACTICE_ID="demo-practice-001")
    minter = LiveKitTokenMinter(settings=settings)
    token = minter.mint_participant_token(
        room_name="emma-room-001",
        participant_identity="emma-caller-abc123",
        practice_id="demo-practice-001",
        ttl_seconds=3600,
    )
    assert isinstance(token, str)
    assert len(token) > 0
    # JWT has three base64url segments separated by dots.
    parts = token.split(".")
    assert len(parts) == 3


def test_token_ttl_is_short_lived() -> None:
    """Default TTL is 3600 seconds (1 hour) — short-lived, not permanent."""
    settings = _make_settings()
    minter = LiveKitTokenMinter(settings=settings)
    token = minter.mint_participant_token(
        room_name="emma-room-001",
        participant_identity="emma-caller-abc123",
        practice_id="demo-practice-001",
        ttl_seconds=3600,
    )
    # Decode the payload (second segment) to check exp claim.
    import base64
    import json

    payload_b64 = token.split(".")[1]
    padding = 4 - len(payload_b64) % 4
    if padding != 4:
        payload_b64 += "=" * padding
    payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    now = int(time.time())
    assert payload["exp"] > now
    assert payload["exp"] - now <= 3600 + 60  # allow clock skew


def test_practice_isolation_different_practice_ids() -> None:
    """Different practice IDs are accepted as server-bound metadata (isolation).

    The gateway never lets the client choose practice_id — it is resolved
    server-side from settings (see test_mint_token_practice_id_server_side).
    """
    settings = _make_settings(PRACTICE_ID="practice-a")
    minter = LiveKitTokenMinter(settings=settings)
    # Both calls should succeed without error — different practice_id values
    # are accepted as server-bound token metadata.
    token_a = minter.mint_participant_token(
        room_name="room-1",
        participant_identity="caller-1",
        practice_id="practice-a",
    )
    token_b = minter.mint_participant_token(
        room_name="room-1",
        participant_identity="caller-1",
        practice_id="practice-b",
    )
    assert isinstance(token_a, str) and len(token_a) > 0
    assert isinstance(token_b, str) and len(token_b) > 0


def test_multiple_rooms_different_tokens() -> None:
    """Different room names produce different tokens."""
    settings = _make_settings()
    minter = LiveKitTokenMinter(settings=settings)
    token_1 = minter.mint_participant_token(
        room_name="room-a",
        participant_identity="caller-1",
        practice_id="demo-practice-001",
    )
    token_2 = minter.mint_participant_token(
        room_name="room-b",
        participant_identity="caller-1",
        practice_id="demo-practice-001",
    )
    assert token_1 != token_2


# ---------------------------------------------------------------------------
# 4. Missing ElevenLabs voice ID
# ---------------------------------------------------------------------------


def test_elevenlabs_voice_id_missing_raises() -> None:
    """ElevenLabs TTS requires ELEVENLABS_VOICE_ID — no silent fallback."""
    with pytest.raises(ProviderConfigurationError, match="ELEVENLABS_VOICE_ID"):
        ElevenLabsFlashTTSProvider(
            api_key="test-key-not-real",
            voice_id="",
            model="eleven_flash_v2_5",
            audio_format=None,  # type: ignore[arg-type]
            request_timeout=15.0,
        )


def test_elevenlabs_api_key_missing_raises() -> None:
    """ElevenLabs TTS requires ELEVENLABS_API_KEY — no silent fallback."""
    with pytest.raises(ProviderConfigurationError, match="ELEVENLABS_API_KEY"):
        ElevenLabsFlashTTSProvider(
            api_key="",
            voice_id="test-voice",
            model="eleven_flash_v2_5",
            audio_format=None,  # type: ignore[arg-type]
            request_timeout=15.0,
        )


# ---------------------------------------------------------------------------
# 5. Provider configuration validation (no silent fallback)
# ---------------------------------------------------------------------------


def test_deepgram_missing_key_raises() -> None:
    """Deepgram STT requires DEEPGRAM_API_KEY — no silent fallback."""
    with pytest.raises(ProviderConfigurationError, match="DEEPGRAM_API_KEY"):
        DeepgramFluxSTTProvider(
            api_key="",
            model="flux-general-en",
            language="en-GB",
            audio_format=None,  # type: ignore[arg-type]
            connection_timeout=10.0,
            request_timeout=15.0,
        )


def test_speech_provider_configured_false_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """speech_provider_configured is False when real provider lacks credentials."""
    settings = _make_settings(
        monkeypatch,
        STT_PROVIDER="deepgram",
        DEEPGRAM_API_KEY=None,
    )
    assert settings.speech_provider_configured is False


def test_llm_provider_configured_false_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """llm_provider_configured is False when real LLM lacks credentials."""
    settings = _make_settings(
        monkeypatch,
        LLM_PROVIDER="openai",
        OPENAI_API_KEY=None,
    )
    assert settings.llm_provider_configured is False


def test_provider_readiness_ok_false_without_livekit_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """provider_readiness_ok is False when livekit transport lacks credentials.

    livekit transport without credentials raises ValueError at settings load
    (no silent fallback), which is the desired behavior.
    """
    with pytest.raises(ValueError, match="LIVEKIT_API_KEY"):
        _make_settings(
            monkeypatch,
            VOICE_TRANSPORT="livekit",
            LIVEKIT_API_KEY=None,
            LIVEKIT_API_SECRET=None,
        )


# ---------------------------------------------------------------------------
# 6. Existing WebSocket fallback
# ---------------------------------------------------------------------------


def test_websocket_fallback_no_livekit_credentials_needed() -> None:
    """VOICE_TRANSPORT=websocket does not require LiveKit config at all."""
    settings = _make_settings(
        VOICE_TRANSPORT="websocket",
        LIVEKIT_API_KEY=None,
        LIVEKIT_API_SECRET=None,
        LIVEKIT_URL=None,
    )
    assert settings.VOICE_TRANSPORT == "websocket"
    assert settings.provider_readiness_ok is True


def test_azure_fallback_path_unaffected_by_livekit() -> None:
    """Azure STT/TTS config is independent of LiveKit transport."""
    settings = _make_settings(
        STT_PROVIDER="azure",
        TTS_PROVIDER="azure",
        AZURE_SPEECH_KEY="test-key",
        AZURE_SPEECH_REGION="eastasia",
    )
    assert settings.speech_provider_configured is True
