"""Validated, environment-driven configuration (12-factor; no secrets in code).

Day-3 additions: voice-pipeline mode (echo|speech), speech provider selection
(mock|azure), Azure Speech settings, mock-recognizer tuning, speech timeouts
and the practice-API client settings. Day-2 settings unchanged.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Literal

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

    # --- Day 12: browser voice transport ----------------------------------
    # websocket : existing FastAPI WebSocket /ws/exotel (Azure + Gemini path).
    # livekit   : LiveKit WebRTC + LiveKit Agents (Deepgram + OpenAI + ElevenLabs).
    VOICE_TRANSPORT: Literal["websocket", "livekit"] = "websocket"

    # --- Day 3: speech providers -------------------------------------------
    STT_PROVIDER: Literal["mock", "azure", "deepgram"] = "mock"
    TTS_PROVIDER: Literal["mock", "azure", "elevenlabs"] = "mock"
    # Azure Speech (verified SDK + voice; see docs/architecture/day-3-implementation.md).
    # Azure selected without key+region → degraded readiness, sessions close 1011.
    AZURE_SPEECH_KEY: str | None = None
    AZURE_SPEECH_REGION: str | None = None
    AZURE_SPEECH_LANGUAGE: str = "en-GB"
    AZURE_SPEECH_VOICE: str = "en-GB-SoniaNeural"  # verified F0-eligible UK voice

    # Deepgram Flux (Day 12 primary STT; see docs/runbooks/deepgram-flux-setup.md).
    DEEPGRAM_API_KEY: str | None = None
    DEEPGRAM_MODEL: str = "flux-general-en"

    # ElevenLabs TTS (Day 12 primary TTS; see docs/runbooks/elevenlabs-setup.md).
    ELEVENLABS_API_KEY: str | None = None
    ELEVENLABS_MODEL: str = "eleven_flash_v2_5"
    ELEVENLABS_VOICE_ID: str | None = None

    # Speech runtime timeouts (seconds; gateways, not silence detectors).
    SPEECH_CONNECTION_TIMEOUT: float = Field(default=10.0, gt=0)
    SPEECH_REQUEST_TIMEOUT: float = Field(default=15.0, gt=0)

    # --- Day 18C: live-provider runtime budget guard -------------------------
    # Application-side request/usage guard around the REAL Deepgram and
    # ElevenLabs adapters (never a provider-enforced spending limit). The
    # ceilings are hard-coded maxima: configuration can lower them, never raise
    # them, so a typo cannot buy more spend. Guard is enabled explicitly, or
    # automatically for any opt-in live test run.
    EMMA_LIVE_GUARD_ENABLED: bool = False
    EMMA_RUN_LIVE_TESTS: bool = False
    # Day 18D: the two USER attestations. They are acknowledgements, not
    # evidence: the code cannot verify that a provider key was rotated or that
    # pricing was reviewed, only that the operator said so. Both are required
    # before a guard-armed run may construct a live provider.
    EMMA_LIVE_CREDENTIALS_ROTATED: bool = False
    EMMA_LIVE_PROVIDER_SPEND_APPROVED: bool = False
    EMMA_LIVE_RUN_ID: str = "day18-live"
    EMMA_LIVE_LEDGER_PATH: str | None = None
    EMMA_LIVE_MAX_DEEPGRAM_SESSIONS: int = Field(default=3, ge=1, le=3)
    EMMA_LIVE_MAX_DEEPGRAM_AUDIO_SECONDS: float = Field(default=15.0, gt=0, le=15.0)
    EMMA_LIVE_MAX_ELEVENLABS_REQUESTS: int = Field(default=3, ge=1, le=3)
    EMMA_LIVE_MAX_ELEVENLABS_CHARS: int = Field(default=300, ge=1, le=300)

    # --- Day 12: LiveKit (WebRTC voice runtime) -----------------------------
    # See docs/runbooks/livekit-setup.md. Credentials never reach the browser;
    # tokens are minted server-side with short expiry.
    LIVEKIT_URL: str | None = None
    LIVEKIT_API_KEY: str | None = None
    LIVEKIT_API_SECRET: str | None = None

    # --- Day 5: LLM conversation engine -------------------------------------
    LLM_PROVIDER: Literal["mock", "gemini", "openai"] = "mock"
    # GPT-6 Luna only (assignment §1 — no GPT-6.1 Terra, no expensive fallback).
    OPENAI_MODEL: str = "gpt-6-luna"
    OPENAI_API_KEY: str | None = None
    LLM_MODEL: str = "gemini-3.8-flash"
    LLM_API_KEY: str | None = None
    LLM_TIMEOUT_SECONDS: float = Field(default=15.0, gt=0)
    LLM_MAX_OUTPUT_TOKENS: int = Field(default=800, ge=1, le=4096)
    LLM_PROMPT_VERSION: str = "v1"

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

    # --- Day 19: everyday conversation -------------------------------------
    # Everyday small talk is always available (deterministic templates). World
    # knowledge answers additionally require an LLM provider; disabling this flag
    # removes that path entirely (templates + practice RAG only).
    GENERAL_CHAT_ENABLED: bool = True
    GENERAL_CHAT_MAX_SENTENCES: int = Field(default=2, ge=1, le=3)

    # --- Day 19: call summary + external task platforms ---------------------
    # Deterministic summary built from the conversation state at call end and
    # submitted to the practice backend (which mirrors it to Jira). Opt-out.
    CALL_SUMMARY_ENABLED: bool = True
    # deterministic | llm. Deterministic keeps the transcript server-side; the
    # LLM option is opt-in only (it sends an excerpt to the configured model).
    CALL_SUMMARY_PROVIDER: Literal["deterministic", "llm"] = "deterministic"
    # Include a short transcript excerpt in the call record/Jira issue.
    CALL_SUMMARY_INCLUDE_TRANSCRIPT: bool = False
    # Hard ceiling on how long the end-of-call summary may delay teardown.
    CALL_SUMMARY_TIMEOUT: float = Field(default=2.0, gt=0, le=10.0)

    # --- Day 5: LLM conversation engine -------------------------------------
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
        if self.LLM_PROVIDER == "openai":
            return bool(self.OPENAI_API_KEY)
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
        "OPENAI_API_KEY",
        "DEEPGRAM_API_KEY",
        "ELEVENLABS_API_KEY",
        "ELEVENLABS_VOICE_ID",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        mode="before",
    )
    @classmethod
    def _no_blank_strings(cls, value: str | None) -> str | None:
        # `.env` placeholders leave blank values; treat them as unset. A
        # whitespace-only DEEPGRAM_MODEL / ELEVENLABS_VOICE_ID would otherwise
        # survive validation and fail later, mid-call, as a provider error.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "EMMA_LIVE_GUARD_ENABLED",
        "EMMA_RUN_LIVE_TESTS",
        "EMMA_LIVE_CREDENTIALS_ROTATED",
        "EMMA_LIVE_PROVIDER_SPEND_APPROVED",
        mode="before",
    )
    @classmethod
    def _blank_flag_is_false(cls, value: object) -> object:
        """`KEY=` in a `.env` placeholder means "off", never a validation crash.

        Day 18E hit this: exporting these flags as empty strings aborted gateway
        startup with an opaque pydantic bool error.
        """
        if isinstance(value, str) and not value.strip():
            return False
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
        # LiveKit credentials are required when VOICE_TRANSPORT=livekit.
        if self.VOICE_TRANSPORT == "livekit":
            if not self.LIVEKIT_URL:
                raise ValueError(
                    "VOICE_TRANSPORT=livekit requires LIVEKIT_URL; "
                    "refusing to start (no silent fallback)"
                )
            if not self.LIVEKIT_API_KEY or not self.LIVEKIT_API_SECRET:
                raise ValueError(
                    "VOICE_TRANSPORT=livekit requires LIVEKIT_API_KEY and "
                    "LIVEKIT_API_SECRET; refusing to start (no silent fallback)"
                )
            # Development credentials must not be used in production.
            if self.APP_ENV == "production":
                if self.LIVEKIT_API_KEY == "devkey":
                    raise ValueError(
                        "LIVEKIT_API_KEY=devkey is a development credential and "
                        "must not be used in production (APP_ENV=production)"
                    )
                if self.LIVEKIT_API_SECRET == "secret":
                    raise ValueError(
                        "LIVEKIT_API_SECRET=secret is a development credential and "
                        "must not be used in production (APP_ENV=production)"
                    )
        # Deepgram: the model identifier must be present, and production must
        # have a key (development stays allowed to select mock providers only).
        if self.STT_PROVIDER == "deepgram":
            if not self.DEEPGRAM_MODEL or not self.DEEPGRAM_MODEL.strip():
                raise ValueError(
                    "STT_PROVIDER=deepgram requires DEEPGRAM_MODEL "
                    "(e.g. flux-general-en); refusing to start"
                )
            if self.APP_ENV == "production" and not self.DEEPGRAM_API_KEY:
                raise ValueError(
                    "STT_PROVIDER=deepgram requires DEEPGRAM_API_KEY in production; "
                    "refusing to start (no silent fallback)"
                )
        # ElevenLabs voice ID is required when TTS_PROVIDER=elevenlabs.
        if self.TTS_PROVIDER == "elevenlabs":
            if not self.ELEVENLABS_API_KEY:
                raise ValueError(
                    "TTS_PROVIDER=elevenlabs requires ELEVENLABS_API_KEY; "
                    "refusing to start (no silent fallback)"
                )
            if not self.ELEVENLABS_VOICE_ID:
                raise ValueError(
                    "TTS_PROVIDER=elevenlabs requires ELEVENLABS_VOICE_ID; "
                    "refusing to start (no silent fallback)"
                )
            if self.APP_ENV == "production":
                if self.ELEVENLABS_API_KEY == "devkey":
                    raise ValueError(
                        "ELEVENLABS_API_KEY=devkey is a development credential "
                        "and must not be used in production (APP_ENV=production)"
                    )
                if self.ELEVENLABS_VOICE_ID == "dev-voice-id":
                    raise ValueError(
                        "ELEVENLABS_VOICE_ID=dev-voice-id is a development value "
                        "and must not be used in production (APP_ENV=production)"
                    )
        return self

    # -- derived (non-secret) introspection ---------------------------------
    @property
    def exotel_live_credentials_configured(self) -> bool:
        return bool(self.EXOTEL_API_KEY and self.EXOTEL_API_TOKEN)

    @property
    def ws_auth_required(self) -> bool:
        return self.WS_AUTH_TOKEN is not None or self.EXOTEL_MODE == "live"

    # -- Day 18C: live-provider budget guard ---------------------------------
    @property
    def live_guard_enabled(self) -> bool:
        """True when the runtime guard must wrap live speech providers.

        `EMMA_RUN_LIVE_TESTS=1` implies the guard: an opt-in live run must never
        be able to skip the spend limits by forgetting the explicit flag.
        """
        return bool(self.EMMA_LIVE_GUARD_ENABLED or self.EMMA_RUN_LIVE_TESTS)

    @property
    def live_attestations_ok(self) -> bool:
        """Both Day 18D user attestations are present."""
        return bool(self.EMMA_LIVE_CREDENTIALS_ROTATED and self.EMMA_LIVE_PROVIDER_SPEND_APPROVED)

    @property
    def live_attestation_problem(self) -> str | None:
        """Name(s) of the missing attestation(s) when a guarded live run would spend."""
        if not self.live_guard_enabled:
            return None
        missing = []
        if not self.EMMA_LIVE_CREDENTIALS_ROTATED:
            missing.append("EMMA_LIVE_CREDENTIALS_ROTATED")
        if not self.EMMA_LIVE_PROVIDER_SPEND_APPROVED:
            missing.append("EMMA_LIVE_PROVIDER_SPEND_APPROVED")
        if not missing:
            return None
        return (
            "live provider spend is not authorised: "
            + " and ".join(f"{name}=1" for name in missing)
            + " must be set by the operator (user attestation); refusing to "
            "construct a live provider"
        )

    @property
    def live_provider_limits(self) -> Any:
        """Limits object for the guard (imported lazily to avoid a cycle)."""
        from app.services.live_provider_budget import LiveProviderLimits

        return LiveProviderLimits(
            max_deepgram_sessions=self.EMMA_LIVE_MAX_DEEPGRAM_SESSIONS,
            max_deepgram_audio_seconds=self.EMMA_LIVE_MAX_DEEPGRAM_AUDIO_SECONDS,
            max_elevenlabs_requests=self.EMMA_LIVE_MAX_ELEVENLABS_REQUESTS,
            max_elevenlabs_chars=self.EMMA_LIVE_MAX_ELEVENLABS_CHARS,
        )

    @property
    def live_ledger_path(self) -> Any:
        """Ledger location for the runtime guard (non-secret filesystem path)."""
        from app.services.live_provider_budget import default_ledger_path

        if self.EMMA_LIVE_LEDGER_PATH:
            from pathlib import Path

            return Path(self.EMMA_LIVE_LEDGER_PATH)
        return default_ledger_path()

    def live_guard_status(self) -> dict[str, Any]:
        """Safe, secret-free guard status for `/health/ready` and logs."""
        from app.services.live_provider_budget import live_guard_status

        return live_guard_status(self)

    @property
    def livekit_provider_configured(self) -> bool:
        """False when LiveKit is selected without credentials."""
        if self.VOICE_TRANSPORT == "livekit":
            return bool(self.LIVEKIT_API_KEY and self.LIVEKIT_API_SECRET)
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
            and self.livekit_provider_configured
        )

    @property
    def speech_provider_configured(self) -> bool:
        """False when a provider is selected without the credentials it needs."""
        if self.STT_PROVIDER == "azure" or self.TTS_PROVIDER == "azure":
            return bool(self.AZURE_SPEECH_KEY and self.AZURE_SPEECH_REGION)
        if self.STT_PROVIDER == "deepgram":
            return bool(self.DEEPGRAM_API_KEY)
        if self.TTS_PROVIDER == "elevenlabs":
            return bool(self.ELEVENLABS_API_KEY and self.ELEVENLABS_VOICE_ID)
        return True

    # --- Day 12: OpenAI budget guard ---------------------------------------
    # Strict application-level cap for GPT-6 Luna usage (assignment §2).
    # Total remaining balance: $2.50. These are application-side controls only;
    # they are NOT a replacement for provider billing controls.
    OPENAI_DAILY_BUDGET_USD: float = Field(default=2.50, gt=0)
    OPENAI_DEV_BUDGET_USD: float = Field(default=1.00, gt=0)
    # Estimated OpenAI prices (placeholders; replaced by provider usage response
    # when available). Never presented as authoritative pricing.
    OPENAI_INPUT_USD_PER_1K_TOKENS: float = Field(default=0.005, gt=0)
    OPENAI_OUTPUT_USD_PER_1K_TOKENS: float = Field(default=0.015, gt=0)
    OPENAI_CACHED_USD_PER_1K_TOKENS: float = Field(default=0.00125, gt=0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings access; tests call `get_settings.cache_clear()` after env changes."""
    return Settings()
