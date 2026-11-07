"""Validated, environment-driven configuration (12-factor; no secrets in code).

Day-3 additions: voice-pipeline mode (echo|speech), speech provider selection
(mock|azure), Azure Speech settings, mock-recognizer tuning, speech timeouts
and the practice-API client settings. Day-2 settings unchanged.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_MOCK_STT_SCRIPT = (
    "I would like to book an appointment,|"
    "please respond with a test response,|"
    "I need to move my appointment,|"
    "I need a prescription repeat"
)


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
    CORS_ORIGINS: str = "*"
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

    # --- Day 3: voice pipeline mode --------------------------------------
    # echo   : Day-2 byte-for-byte echo path (protocol-test compatibility).
    # speech : full STT → FSM → response handler → TTS pipeline (default today).
    VOICE_PIPELINE: Literal["echo", "speech"] = "speech"

    # --- Day 3: speech providers -------------------------------------------
    STT_PROVIDER: Literal["mock", "azure"] = "mock"
    TTS_PROVIDER: Literal["mock", "azure"] = "mock"
    # Azure Speech (verified SDK + voice; see docs/architecture/day-3-implementation.md).
    # Azure selected without key+region → degraded readiness, sessions close 1011.
    AZURE_SPEECH_KEY: str | None = None
    AZURE_SPEECH_REGION: str | None = None
    AZURE_SPEECH_LANGUAGE: str = "en-GB"
    AZURE_SPEECH_VOICE: str = "en-GB-SoniaNeural"  # verified F0-eligible UK voice

    # Speech runtime timeouts (seconds; gateways, not silence detectors).
    SPEECH_CONNECTION_TIMEOUT: float = Field(default=10.0, gt=0)
    SPEECH_REQUEST_TIMEOUT: float = Field(default=15.0, gt=0)

    # --- Day 3: mock STT determinism --------------------------------------
    MOCK_STT_SCRIPT: str = _DEFAULT_MOCK_STT_SCRIPT  # lines separated by commas
    MOCK_STT_FINALIZE_MS: int = Field(default=250, ge=0, le=10_000)
    MOCK_STT_UTTERANCE_MS: int = Field(default=1000, ge=1, le=60_000)
    MOCK_STT_ERROR_AT_FRAME: int = Field(default=0, ge=0)  # 0 → no injected error

    # --- Day 3: practice backend (NestJS) ---------------------------------
    # Unset → practice persistence is disabled (client returns `disabled`
    # outcomes; the voice path never claims persistence).
    PRACTICE_API_BASE_URL: str | None = None
    PRACTICE_API_TIMEOUT: float = Field(default=5.0, gt=0)
    PRACTICE_API_MAX_ATTEMPTS: int = Field(default=2, ge=1, le=3)
    # Practice identity for submissions (seeded fictional practice id).
    PRACTICE_ID: str | None = None

    # --- Day 5: LLM conversation engine -------------------------------------
    LLM_PROVIDER: Literal["mock", "gemini"] = "mock"
    LLM_MODEL: str = "gemini-3.8-flash"
    LLM_API_KEY: str | None = None
    LLM_TIMEOUT_SECONDS: float = Field(default=15.0, gt=0)
    LLM_MAX_OUTPUT_TOKENS: int = Field(default=800, ge=1, le=4096)
    LLM_PROMPT_VERSION: str = "v1"

    @field_validator(
        "LLM_API_KEY",
        mode="before",
    )
    @classmethod
    def _no_blank_llm_key(cls, value: str | None) -> str | None:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def llm_provider_configured(self) -> bool:
        """False when a real provider is selected without credentials."""
        if self.LLM_PROVIDER == "gemini":
            return bool(self.LLM_API_KEY)
        return True

    # --- Day 6: knowledge base / RAG ----------------------------------------
    # Embedding provider: mock | gemini
    EMBEDDING_PROVIDER: Literal["mock", "gemini"] = "mock"
    GEMINI_EMBEDDING_MODEL: str = "gemini-embedding-2"
    GEMINI_EMBEDDING_DIMENSION: int = Field(default=768, ge=1, le=4096)
    GEMINI_EMBEDDING_API_KEY: str | None = None
    GEMINI_EMBEDDING_TIMEOUT_SECONDS: float = Field(default=15.0, gt=0)
    GEMINI_EMBEDDING_MAX_RETRIES: int = Field(default=2, ge=0, le=5)

    # Vector store: mock | qdrant
    VECTOR_STORE_PROVIDER: Literal["mock", "qdrant"] = "mock"
    # Qdrant connection mode: "local" (persistent file storage, no server) or
    # "remote" (HTTP Qdrant server at QDRANT_URL).
    QDRANT_MODE: Literal["local", "remote"] = "local"
    QDRANT_URL: str | None = None
    QDRANT_API_KEY: str | None = None
    QDRANT_LOCAL_PATH: str = "./data/qdrant"
    QDRANT_COLLECTION: str = "practice_knowledge"

    # Retrieval settings
    RETRIEVAL_TOP_K: int = Field(default=5, ge=1, le=20)
    RETRIEVAL_MIN_SCORE: float = Field(default=0.3, ge=0.0, le=1.0)
    RETRIEVAL_CHUNK_SIZE: int = Field(default=600, ge=100, le=2000)
    RETRIEVAL_CHUNK_OVERLAP: int = Field(default=80, ge=0, le=500)

    @field_validator(
        "GEMINI_EMBEDDING_API_KEY",
        mode="before",
    )
    @classmethod
    def _no_blank_embedding_key(cls, value: str | None) -> str | None:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def embedding_provider_configured(self) -> bool:
        """False when a real embedding provider is selected without credentials."""
        if self.EMBEDDING_PROVIDER == "gemini":
            return bool(self.GEMINI_EMBEDDING_API_KEY)
        return True

    @property
    def vector_store_configured(self) -> bool:
        """False when qdrant is selected without connection details."""
        if self.VECTOR_STORE_PROVIDER == "qdrant":
            if self.QDRANT_MODE == "local":
                return bool(self.QDRANT_LOCAL_PATH)
            return bool(self.QDRANT_URL)
        return True

    @property
    def rag_configured(self) -> bool:
        """True when embedding + vector store are both configured for real use."""
        if self.EMBEDDING_PROVIDER == "gemini" and not self.GEMINI_EMBEDDING_API_KEY:
            return False
        if self.VECTOR_STORE_PROVIDER == "qdrant":
            if self.QDRANT_MODE == "local":
                return bool(self.QDRANT_LOCAL_PATH)
            return bool(self.QDRANT_URL)
        return True

    @property
    def cors_origins_list(self) -> list[str]:
        v = self.CORS_ORIGINS.strip()
        if not v or v == "*":
            return ["*"]
        if v.startswith("[") and v.endswith("]"):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [str(x).strip() for x in parsed if str(x).strip()]
            except Exception:
                pass
        return [origin.strip() for origin in v.split(",") if origin.strip()]

    @property
    def mock_stt_script_lines(self) -> list[str]:
        lines = [line.strip() for line in self.MOCK_STT_SCRIPT.split(",")]
        lines = [line for line in lines if line]
        return lines or _DEFAULT_MOCK_STT_SCRIPT.split(",")

    @field_validator(
        "EXOTEL_API_KEY",
        "EXOTEL_API_TOKEN",
        "WS_AUTH_TOKEN",
        "AZURE_SPEECH_KEY",
        "AZURE_SPEECH_REGION",
        "PRACTICE_API_BASE_URL",
        "PRACTICE_ID",
        "QDRANT_LOCAL_PATH",
        "QDRANT_URL",
        mode="before",
    )
    @classmethod
    def _no_blank_strings(cls, value: str | None) -> str | None:
        # `.env` placeholders leave blank values; treat them as unset.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("VOICE_PIPELINE", "STT_PROVIDER", "TTS_PROVIDER", "EXOTEL_MODE", mode="before")
    @classmethod
    def _lower_choices(cls, value: str) -> str:
        if isinstance(value, str):
            return value.strip().lower()
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
    def speech_provider_configured(self) -> bool:
        """False when a provider is selected without the credentials it needs."""
        if self.STT_PROVIDER == "azure" or self.TTS_PROVIDER == "azure":
            return bool(self.AZURE_SPEECH_KEY and self.AZURE_SPEECH_REGION)
        return True

    @property
    def provider_readiness_ok(self) -> bool:
        """Live mode without credentials OR broken provider config blocks readiness."""
        if self.EXOTEL_MODE == "live":
            return self.exotel_live_credentials_configured
        return (
            self.speech_provider_configured
            and self.llm_provider_configured
            and self.embedding_provider_configured
            and self.vector_store_configured
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings access; tests call `get_settings.cache_clear()` after env changes."""
    return Settings()
