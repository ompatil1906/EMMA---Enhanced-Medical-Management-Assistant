"""Settings validation tests (environment-driven config contract)."""

import pytest
from pydantic import ValidationError

from app.core.config import get_settings


class TestDefaults:
    def test_day2_defaults_are_simulation_development(self) -> None:
        settings = get_settings()
        assert settings.APP_ENV == "development"
        assert settings.EXOTEL_MODE == "simulation"
        assert settings.LOG_LEVEL == "INFO"
        assert settings.ws_auth_required is False
        assert settings.provider_readiness_ok is True
        assert settings.exotel_live_credentials_configured is False

    def test_hardening_limits_have_sane_defaults(self) -> None:
        settings = get_settings()
        assert settings.MAX_WS_MESSAGE_BYTES >= settings.EXOTEL_MAX_CHUNK_BYTES * 2
        assert settings.MAX_CONSECUTIVE_ERRORS >= 1
        assert settings.MAX_CONCURRENT_SESSIONS >= 1


class TestEnvironmentOverrides:
    def test_live_mode_without_credentials_is_not_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EXOTEL_MODE", "live")
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.provider_readiness_ok is False
        assert settings.ws_auth_required is True

    def test_live_mode_with_credentials_is_ready(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXOTEL_MODE", "live")
        monkeypatch.setenv("EXOTEL_API_KEY", "key")
        monkeypatch.setenv("EXOTEL_API_TOKEN", "token")
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.provider_readiness_ok is True
        assert settings.exotel_live_credentials_configured is True

    def test_numeric_environment_overrides_apply(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXOTEL_MAX_CHUNK_BYTES", "1024")  # keep cross-check valid
        monkeypatch.setenv("MAX_WS_MESSAGE_BYTES", "4096")
        monkeypatch.setenv("MAX_CONSECUTIVE_ERRORS", "2")
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.MAX_WS_MESSAGE_BYTES == 4096
        assert settings.MAX_CONSECUTIVE_ERRORS == 2


class TestValidation:
    @pytest.mark.parametrize(
        ("key", "value"),
        [("EXOTEL_MODE", "banana"), ("LOG_LEVEL", "trace"), ("MAX_CONSECUTIVE_ERRORS", "0")],
    )
    def test_invalid_values_rejected(
        self, monkeypatch: pytest.MonkeyPatch, key: str, value: str
    ) -> None:
        monkeypatch.setenv(key, value)
        get_settings.cache_clear()
        with pytest.raises(ValidationError):
            get_settings()

    def test_message_size_must_respect_chunk_cross_check(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EXOTEL_MAX_CHUNK_BYTES", "100000")
        monkeypatch.setenv("MAX_WS_MESSAGE_BYTES", "1024")
        get_settings.cache_clear()
        with pytest.raises(ValidationError, match="MAX_WS_MESSAGE_BYTES"):
            get_settings()

    def test_blank_secret_behaves_as_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WS_AUTH_TOKEN", "   ")
        get_settings.cache_clear()
        assert get_settings().ws_auth_required is False
