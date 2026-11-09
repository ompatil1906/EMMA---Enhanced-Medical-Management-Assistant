"""Central speech-provider factory (assignment §5 — provider selection lives here).

Rules:
* Selection comes from validated settings (`STT_PROVIDER` / `TTS_PROVIDER`).
* Providers without credentials raise `ProviderConfigurationError` — readiness
  is degraded and sessions close 1011; there is NEVER a silent mock fallback.
* Providers are per-session: each session's negotiated audio format is baked
  into the provider object.

Day 12: adds Deepgram Flux (STT) and ElevenLabs Flash (TTS) options alongside
the existing Azure and mock providers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from app.services.live_provider_budget import (
    LiveBudgetError,
    LiveProviderLedger,
    build_runtime_ledger,
    live_guard_status,
)
from app.speech.base import ProviderConfigurationError, SpeechProviderError

if TYPE_CHECKING:  # pragma: no cover - type hints only; runtime import is lazy
    from app.core.config import Settings
    from app.domain.audio import AudioFormat
    from app.speech.azure_stt import AzureSpeechToTextProvider
    from app.speech.azure_tts import AzureTextToSpeechProvider
    from app.speech.deepgram_stt import DeepgramFluxSTTProvider
    from app.speech.elevenlabs_tts import ElevenLabsFlashTTSProvider
    from app.speech.mock_stt import MockSpeechToTextProvider
    from app.speech.mock_tts import MockTextToSpeechProvider

SttProviderName = Literal["mock", "azure", "deepgram"]
TtsProviderName = Literal["mock", "azure", "elevenlabs"]


class SpeechProviderFactory:
    """Build per-session provider instances from validated settings."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Day 18C: the runtime spend guard. Built once per factory so the
        # gateway and the LiveKit agent share one ledger-backed budget; a
        # failure here is reported (never silently ignored) and every live
        # provider construction fails closed while it persists.
        self._ledger: LiveProviderLedger | None = None
        self._ledger_error: str | None = None
        if settings.live_guard_enabled:
            try:
                self._ledger = build_runtime_ledger(settings)
                self._ledger.ensure_ready()
            except LiveBudgetError as exc:
                self._ledger = None
                self._ledger_error = f"{type(exc).__name__}: {exc}"

    # --- configuration introspection --------------------------------------------

    @property
    def configuration_ok(self) -> bool:
        """True when every selected provider can actually run (readiness signal)."""
        return self.configuration_problem() is None

    def configuration_problem(self) -> str | None:
        """Human-readable blocker for logs/health/degraded readiness."""
        live_selected = self.stt_provider_name == "deepgram" or (
            self.tts_provider_name == "elevenlabs"
        )
        if live_selected and self._settings.live_attestation_problem is not None:
            return str(self._settings.live_attestation_problem)
        if self._settings.live_guard_enabled and self._ledger is None and live_selected:
            return (
                "live provider budget guard is enabled but its ledger is "
                f"unavailable ({self._ledger_error}); refusing live provider "
                "construction (no unlimited spend)"
            )
        if self.stt_provider_name == "azure" and not (
            self._settings.AZURE_SPEECH_KEY and self._settings.AZURE_SPEECH_REGION
        ):
            return (
                "STT_PROVIDER=azure but AZURE_SPEECH_KEY/AZURE_SPEECH_REGION are not "
                "configured (no credentials → degraded, never mock fallback)"
            )
        if self.stt_provider_name == "deepgram" and not self._settings.DEEPGRAM_API_KEY:
            return (
                "STT_PROVIDER=deepgram but DEEPGRAM_API_KEY is not configured "
                "(no credentials → degraded, never mock fallback)"
            )
        if self.tts_provider_name == "azure" and not (
            self._settings.AZURE_SPEECH_KEY and self._settings.AZURE_SPEECH_REGION
        ):
            return (
                "TTS_PROVIDER=azure but AZURE_SPEECH_KEY/AZURE_SPEECH_REGION are not "
                "configured (no credentials → degraded, never mock fallback)"
            )
        if self.tts_provider_name == "elevenlabs" and not (
            self._settings.ELEVENLABS_API_KEY and self._settings.ELEVENLABS_VOICE_ID
        ):
            return (
                "TTS_PROVIDER=elevenlabs but ELEVENLABS_API_KEY/ELEVENLABS_VOICE_ID "
                "are not configured (no credentials → degraded, never mock fallback)"
            )
        return None

    @property
    def stt_provider_name(self) -> str:
        return str(self._settings.STT_PROVIDER)

    @property
    def tts_provider_name(self) -> str:
        return str(self._settings.TTS_PROVIDER)

    # --- per-session construction ------------------------------------------------

    def create_stt(
        self, audio_format: AudioFormat
    ) -> AzureSpeechToTextProvider | MockSpeechToTextProvider | DeepgramFluxSTTProvider:
        """Build the STT provider for one session (fails fast, never falls back)."""
        settings = self._settings
        if settings.STT_PROVIDER == "mock":
            from app.speech.mock_stt import MockSpeechToTextProvider

            return MockSpeechToTextProvider(
                script=settings.mock_stt_script_lines,
                finalize_ms=settings.MOCK_STT_FINALIZE_MS,
                utterance_ms=settings.MOCK_STT_UTTERANCE_MS,
                sample_rate=audio_format.sample_rate,
                error_at_frame=settings.MOCK_STT_ERROR_AT_FRAME,
            )
        if settings.STT_PROVIDER == "azure":
            self._require_azure_credentials("STT")
            from app.speech.azure_stt import AzureSpeechToTextProvider

            return AzureSpeechToTextProvider(
                speech_key=settings.AZURE_SPEECH_KEY,
                speech_region=settings.AZURE_SPEECH_REGION,
                language=settings.AZURE_SPEECH_LANGUAGE,
                audio_format=audio_format,
                connection_timeout=settings.SPEECH_CONNECTION_TIMEOUT,
                stop_timeout=settings.SPEECH_REQUEST_TIMEOUT,
            )
        if settings.STT_PROVIDER == "deepgram":
            if not settings.DEEPGRAM_API_KEY:
                raise ProviderConfigurationError(
                    "STT_PROVIDER=deepgram requires DEEPGRAM_API_KEY; "
                    "refusing to start (no silent fallback)"
                )
            from app.speech.budget_guard import BudgetedSTT
            from app.speech.deepgram_stt import DeepgramFluxSTTProvider

            provider = DeepgramFluxSTTProvider(
                api_key=settings.DEEPGRAM_API_KEY,
                model=settings.DEEPGRAM_MODEL,
                language=settings.AZURE_SPEECH_LANGUAGE,
                audio_format=audio_format,
                connection_timeout=settings.SPEECH_CONNECTION_TIMEOUT,
                request_timeout=settings.SPEECH_REQUEST_TIMEOUT,
            )
            if not settings.live_guard_enabled:
                return provider
            self._require_attestation("Deepgram STT")
            # Fail closed: `_require_ledger` raises rather than returning an
            # unguarded provider when the ledger is unusable.
            return BudgetedSTT(
                provider,
                self._require_ledger("Deepgram STT"),
                audio_format=audio_format,
            )
        raise ProviderConfigurationError(f"unknown STT_PROVIDER {settings.STT_PROVIDER!r}")

    def create_tts(
        self, audio_format: AudioFormat
    ) -> AzureTextToSpeechProvider | MockTextToSpeechProvider | ElevenLabsFlashTTSProvider:
        """Build the TTS provider for one session (fails fast, never falls back)."""
        settings = self._settings
        if settings.TTS_PROVIDER == "mock":
            from app.speech.mock_tts import MockTextToSpeechProvider

            return MockTextToSpeechProvider()
        if settings.TTS_PROVIDER == "azure":
            self._require_azure_credentials("TTS")
            from app.speech.azure_tts import AzureTextToSpeechProvider

            return AzureTextToSpeechProvider(
                speech_key=settings.AZURE_SPEECH_KEY,
                speech_region=settings.AZURE_SPEECH_REGION,
                voice=settings.AZURE_SPEECH_VOICE,
                language=settings.AZURE_SPEECH_LANGUAGE,
                audio_format=audio_format,
                request_timeout=settings.SPEECH_REQUEST_TIMEOUT,
            )
        if settings.TTS_PROVIDER == "elevenlabs":
            if not (settings.ELEVENLABS_API_KEY and settings.ELEVENLABS_VOICE_ID):
                raise ProviderConfigurationError(
                    "TTS_PROVIDER=elevenlabs requires ELEVENLABS_API_KEY and "
                    "ELEVENLABS_VOICE_ID; refusing to start (no silent fallback)"
                )
            from app.speech.budget_guard import BudgetedTTS
            from app.speech.elevenlabs_tts import ElevenLabsFlashTTSProvider

            provider = ElevenLabsFlashTTSProvider(
                api_key=settings.ELEVENLABS_API_KEY,
                voice_id=settings.ELEVENLABS_VOICE_ID,
                model=settings.ELEVENLABS_MODEL,
                audio_format=audio_format,
                request_timeout=settings.SPEECH_REQUEST_TIMEOUT,
            )
            if not settings.live_guard_enabled:
                return provider
            self._require_attestation("ElevenLabs TTS")
            return BudgetedTTS(provider, self._require_ledger("ElevenLabs TTS"))
        raise ProviderConfigurationError(f"unknown TTS_PROVIDER {settings.TTS_PROVIDER!r}")

    # --- live spend guard ----------------------------------------------------------

    @property
    def live_guard_enabled(self) -> bool:
        return bool(self._settings.live_guard_enabled)

    @property
    def live_guard_ready(self) -> bool:
        """True when the guard is disabled, or enabled with a usable ledger."""
        return (not self.live_guard_enabled) or self._ledger is not None

    def live_guard_status(self) -> dict[str, object]:
        """Secret-free guard status (also exposed on `/health/ready`)."""
        return live_guard_status(self._settings)

    def _require_attestation(self, provider_label: str) -> None:
        """Fail closed unless the operator attested rotation AND spend approval."""
        problem = self._settings.live_attestation_problem
        if problem is not None:
            raise ProviderConfigurationError(f"{provider_label}: {problem}")

    def _require_ledger(self, provider_label: str) -> LiveProviderLedger:
        """Fail closed: a live provider is never constructed without the guard."""
        if self._ledger is None:
            raise ProviderConfigurationError(
                f"{provider_label} requires the live budget guard, but the ledger "
                f"is unavailable ({self._ledger_error}); refusing to construct an "
                "unguarded live provider"
            )
        return self._ledger

    # --- guards -------------------------------------------------------------------

    def _require_azure_credentials(self, which: str) -> None:
        if not (self._settings.AZURE_SPEECH_KEY and self._settings.AZURE_SPEECH_REGION):
            raise ProviderConfigurationError(
                f"{which}_PROVIDER=azure requires AZURE_SPEECH_KEY and "
                "AZURE_SPEECH_REGION; refusing to start (no silent fallback)"
            )


__all__ = [
    "SpeechProviderFactory",
    "SpeechProviderError",
    "ProviderConfigurationError",
    "SttProviderName",
    "TtsProviderName",
]
