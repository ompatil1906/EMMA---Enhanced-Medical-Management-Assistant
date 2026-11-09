"""Day 13/14: LiveKit router endpoint tests (offline, no credentials).

Tests the `/api/voice/token` endpoint using a mocked `LiveKitTokenMinter`.
No network calls, no API credits consumed.

Day 14 additions:
* Local development configuration (dev credentials accepted in development mode)
* Production configuration validation (dev credentials rejected in production)
* VOICE_TRANSPORT=livekit without credentials = error
* VOICE_TRANSPORT=websocket without LiveKit credentials = OK (fallback)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import Settings


def _make_settings(monkeypatch: pytest.MonkeyPatch | None = None, **overrides: Any) -> Settings:
    """Build Settings, optionally clearing env vars that .env has set."""
    data = {
        "LIVEKIT_API_KEY": "test-key-not-real",
        "LIVEKIT_API_SECRET": "test-secret-not-real",
        "PRACTICE_ID": "demo-practice-001",
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


@pytest.fixture()
def patched_minter() -> Any:
    """Patch `LiveKitTokenMinter` so the token endpoint doesn't need real
    LiveKit credentials or the `livekit` package."""
    with patch("app.api.routes.livekit_token.LiveKitTokenMinter") as mock_minter_class:
        mock_minter = MagicMock()
        mock_minter.mint_participant_token.return_value = "mock-jwt-token-12345"
        mock_minter_class.return_value = mock_minter
        yield mock_minter


@pytest.mark.asyncio
async def test_mint_token_success(patched_minter: Any) -> None:
    """A valid room name returns a JWT token."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    client = TestClient(app)

    settings = _make_settings()
    with patch("app.api.routes.livekit_token.get_settings", return_value=settings):
        resp = client.post("/api/voice/token", json={"room_name": "emma-room-001"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["token"] == "mock-jwt-token-12345"
        assert body["room"] == "emma-room-001"
        assert body["practice_id"] == "demo-practice-001"


@pytest.mark.asyncio
async def test_mint_token_includes_livekit_url(patched_minter: Any) -> None:
    """Day 17: the token response carries the server-side LiveKit URL.

    livekit-client's `Room.connect(url, token)` requires the URL explicitly —
    the JWT does not carry it. The URL is publicly derived info (not a secret).
    """
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    client = TestClient(app)

    settings = _make_settings(LIVEKIT_URL="ws://127.0.0.1:7880")
    with patch("app.api.routes.livekit_token.get_settings", return_value=settings):
        resp = client.post("/api/voice/token", json={"room_name": "emma-voice"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["livekit_url"] == "ws://127.0.0.1:7880"


@pytest.mark.asyncio
async def test_mint_token_livekit_url_none_when_unset(patched_minter: Any) -> None:
    """Without LIVEKIT_URL configured, the field is null (browser errors clearly)."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    client = TestClient(app)

    settings = _make_settings(monkeypatch=None, LIVEKIT_URL=None)
    with patch("app.api.routes.livekit_token.get_settings", return_value=settings):
        resp = client.post("/api/voice/token", json={"room_name": "emma-voice"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["livekit_url"] is None


@pytest.mark.asyncio
async def test_mint_token_missing_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without LiveKit credentials, the endpoint returns 503."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    client = TestClient(app)

    settings = _make_settings(
        monkeypatch,
        LIVEKIT_API_KEY=None,
        LIVEKIT_API_SECRET=None,
    )
    with patch("app.api.routes.livekit_token.get_settings", return_value=settings):
        resp = client.post("/api/voice/token", json={"room_name": "emma-room-001"})
        assert resp.status_code == 503


@pytest.mark.asyncio
async def test_mint_token_invalid_room_name(patched_minter: Any) -> None:
    """An invalid room name is rejected with 400."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    client = TestClient(app)

    settings = _make_settings()
    with patch("app.api.routes.livekit_token.get_settings", return_value=settings):
        resp = client.post("/api/voice/token", json={"room_name": "../evil/../../etc/passwd"})
        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_mint_token_missing_room_name(patched_minter: Any) -> None:
    """A missing room name is rejected with 422."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    client = TestClient(app)

    settings = _make_settings()
    with patch("app.api.routes.livekit_token.get_settings", return_value=settings):
        resp = client.post("/api/voice/token", json={})
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_mint_token_practice_id_server_side(patched_minter: Any) -> None:
    """The practice ID comes from settings, never from the client request."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    client = TestClient(app)

    settings = _make_settings(PRACTICE_ID="demo-practice-001")
    with patch("app.api.routes.livekit_token.get_settings", return_value=settings):
        # Client tries to supply a different practice_id — should be ignored.
        resp = client.post(
            "/api/voice/token",
            json={"room_name": "emma-room-001", "practice_id": "evil-practice"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["practice_id"] == "demo-practice-001"
        # Verify the minter was called with the server-side practice id.
        call_kwargs = patched_minter.mint_participant_token.call_args.kwargs
        assert call_kwargs["practice_id"] == "demo-practice-001"


# ---------------------------------------------------------------------------
# Day 14: LiveKit configuration validation tests
# ---------------------------------------------------------------------------


def test_livekit_transport_websocket_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VOICE_TRANSPORT=websocket works without LiveKit credentials."""
    settings = _make_settings(
        monkeypatch,
        LIVEKIT_API_KEY=None,
        LIVEKIT_API_SECRET=None,
        VOICE_TRANSPORT="websocket",
    )
    assert settings.VOICE_TRANSPORT == "websocket"
    assert settings.livekit_provider_configured is True
    assert settings.provider_readiness_ok is True


def test_livekit_transport_livekit_with_credentials_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live transport with credentials works."""
    settings = _make_settings(
        monkeypatch,
        LLM_PROVIDER="openai",
        VOICE_TRANSPORT="livekit",
        LIVEKIT_URL="ws://127.0.0.1:7880",
    )
    assert settings.VOICE_TRANSPORT == "livekit"
    assert settings.livekit_provider_configured is True
    assert settings.provider_readiness_ok is True


def test_livekit_transport_livekit_with_dev_credentials_in_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev credentials are accepted in development mode."""
    settings = _make_settings(
        monkeypatch,
        LIVEKIT_API_KEY="devkey",
        LIVEKIT_API_SECRET="secret",
        VOICE_TRANSPORT="livekit",
        APP_ENV="development",
        LIVEKIT_URL="ws://127.0.0.1:7880",
    )
    assert settings.VOICE_TRANSPORT == "livekit"
    assert settings.livekit_provider_configured is True
    assert settings.provider_readiness_ok is True


def test_livekit_transport_livekit_with_dev_credentials_in_production_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev credentials are rejected in production mode."""
    with pytest.raises(ValueError, match="development credential"):
        _make_settings(
            monkeypatch,
            LIVEKIT_API_KEY="devkey",
            LIVEKIT_API_SECRET="secret",
            VOICE_TRANSPORT="livekit",
            APP_ENV="production",
            LIVEKIT_URL="ws://127.0.0.1:7880",
        )


def test_livekit_transport_livekit_with_dev_secret_in_production_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev secret is rejected in production mode."""
    with pytest.raises(ValueError, match="development credential"):
        _make_settings(
            monkeypatch,
            LIVEKIT_API_KEY="real-key",
            LIVEKIT_API_SECRET="secret",
            VOICE_TRANSPORT="livekit",
            APP_ENV="production",
            LIVEKIT_URL="ws://127.0.0.1:7880",
        )


def test_livekit_transport_livekit_with_real_credentials_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real credentials are accepted in production mode."""
    settings = _make_settings(
        monkeypatch,
        LIVEKIT_API_KEY="real-key-12345",
        LIVEKIT_API_SECRET="real-secret-67890",
        VOICE_TRANSPORT="livekit",
        APP_ENV="production",
        LIVEKIT_URL="ws://127.0.0.1:7880",
    )
    assert settings.VOICE_TRANSPORT == "livekit"
    assert settings.livekit_provider_configured is True
    assert settings.provider_readiness_ok is True


def test_livekit_url_required_when_voice_transport_livekit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LIVEKIT_URL is required when VOICE_TRANSPORT=livekit."""
    with pytest.raises(ValueError, match="LIVEKIT_URL"):
        _make_settings(
            monkeypatch,
            LLM_PROVIDER="openai",
            VOICE_TRANSPORT="livekit",
            LIVEKIT_API_KEY="real-key",
            LIVEKIT_API_SECRET="real-secret",
            LIVEKIT_URL=None,
        )


def test_elevenlabs_voice_id_required_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """ELEVENLABS_VOICE_ID is required when TTS_PROVIDER=elevenlabs in production."""
    with pytest.raises(ValueError, match="ELEVENLABS_VOICE_ID"):
        _make_settings(
            monkeypatch,
            LLM_PROVIDER="openai",
            ELEVENLABS_API_KEY="real-key",
            ELEVENLABS_VOICE_ID="",
            TTS_PROVIDER="elevenlabs",
            APP_ENV="production",
        )


def test_elevenlabs_voice_id_dev_value_rejected_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev voice ID is rejected in production mode."""
    with pytest.raises(ValueError, match="ELEVENLABS_VOICE_ID=dev-voice-id"):
        _make_settings(
            monkeypatch,
            ELEVENLABS_API_KEY="real-key",
            ELEVENLABS_VOICE_ID="dev-voice-id",
            TTS_PROVIDER="elevenlabs",
            APP_ENV="production",
        )


def test_elevenlabs_api_key_required_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """ELEVENLABS_API_KEY is required when TTS_PROVIDER=elevenlabs in production."""
    with pytest.raises(ValueError, match="ELEVENLABS_API_KEY"):
        _make_settings(
            monkeypatch,
            ELEVENLABS_API_KEY="",
            ELEVENLABS_VOICE_ID="real-voice-id",
            TTS_PROVIDER="elevenlabs",
            APP_ENV="production",
        )
