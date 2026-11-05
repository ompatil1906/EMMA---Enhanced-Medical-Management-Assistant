"""Validated, environment-driven configuration (12-factor; no secrets in code).

Only options actually used by the Day 2 implementation are declared. Run
day's scope deliberately has no STT/LLM/TTS/etc. keys.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),  # app-local unless run from repo root
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = Field(default="emma-voice-gateway", alias="APP_NAME", frozen=True)
    APP_ENV: Literal["development", "test", "production"] = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = Field(default=8000, validation_alias=AliasChoices("APP_PORT", "PORT"))
    CORS_ORIGINS: list[str] = Field(default_factory=lambda: ["*"])
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # --- Provider / simulation mode ---------------------------------------
    EXOTEL_MODE: Literal["simulation", "live"] = "simulation"
    # EXOTEL_MODE == "live" requires both below to accept connections.
    EXOTEL_API_KEY: str | None = None
    EXOTEL_API_TOKEN: str | None = None
    # Higher subdomain reserved until account shape is confirmed → deliberately absent.

    # --- Local simulator auth (X-WS-Token) --------------------------------
    # Unset → unauthenticated local development (readiness reports this).
    WS_AUTH_TOKEN: str | None = None

    # --- WS hardening (our own Day-2 policy, documented) -------------------
    MAX_WS_MESSAGE_BYTES: int = Field(default=262_144, ge=1024)  # entrant JSON cap (close 1009)
    MAX_CONSECUTIVE_ERRORS: int = Field(default=5, ge=1, le=50)  # malformed frames before 1003
    MAX_CONCURRENT_SESSIONS: int = Field(default=200, ge=1)  # overload guard (close 1013)
    # Exotel chunk guidance is 3,200–100,000 bytes decoded (verified defaults); the
    # minimum is NOT enforced inbound (logging only), the maximum is (tolerated error).
    EXOTEL_MAX_CHUNK_BYTES: int = Field(default=100_000, ge=1)

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            v_str = value.strip()
            if not v_str:
                return ["*"]
            if v_str.startswith("[") and v_str.endswith("]"):
                try:
                    return json.loads(v_str)
                except Exception:
                    pass
            return [origin.strip() for origin in v_str.split(",") if origin.strip()]
        if isinstance(value, list):
            return value
        return ["*"]

    @field_validator("EXOTEL_API_KEY", "EXOTEL_API_TOKEN", "WS_AUTH_TOKEN", mode="before")
    @classmethod
    def _no_blank_strings(cls, value: str | None) -> str | None:
        # `.env` placeholders leave blank values; treat them as unset.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _cross_validate(self) -> Settings:
        if self.MAX_WS_MESSAGE_BYTES < self.EXOTEL_MAX_CHUNK_BYTES * 2:
            raise ValueError(
                "MAX_WS_MESSAGE_BYTES must exceed EXOTEL_MAX_CHUNK_BYTES * 2 "
                "(base64 expansion + envelope overhead) or legitimate frames get rejected"
            )
        return self

    # -- derived (non-secret) introspection ---------------------------------
    @property
    def exotel_live_credentials_configured(self) -> bool:
        return bool(self.EXOTEL_API_KEY and self.EXOTEL_API_TOKEN)

    @property
    def ws_auth_required(self) -> bool:
        return self.WS_AUTH_TOKEN is not None or self.EXOTEL_MODE == "live"

    @property
    def provider_readiness_ok(self) -> bool:
        """Live mode without credentials blocks readiness (documented blocker path)."""
        if self.EXOTEL_MODE == "live":
            return self.exotel_live_credentials_configured
        return True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings access; tests call `get_settings.cache_clear()` after env changes."""
    return Settings()
