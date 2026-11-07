"""Central speech-provider factory (assignment §5 — provider selection lives here).

Rules:
* Selection comes from validated settings (`STT_PROVIDER` / `TTS_PROVIDER`).
* Azure selected without key/region raises `ProviderConfigurationError` —
  readiness is degraded and sessions close 1011; there is NEVER a silent
  mock fallback (an explicit fallback switch would be a documented,
  observable setting; none exists today).
* Providers are per-session: each session's negotiated audio format is baked
  into the provider object (Azure cannot change stream formats mid-stream).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from app.speech.base import ProviderConfigurationError, SpeechProviderError

if TYPE_CHECKING:  # pragma: no cover - type hints only; runtime import is lazy
    from app.core.config import Settings
    from app.domain.audio import AudioFormat
    from app.speech.azure_stt import AzureSpeechToTextProvider
    from app.speech.azure_tts import AzureTextToSpeechProvider
    from app.speech.mock_stt import MockSpeechToTextProvider
    from app.speech.mock_tts import MockTextToSpeechProvider


SttProviderName = Literal["mock", "azure"]
TtsProviderName = Literal["mock", "azure"]


class SpeechProviderFactory:
    """Build per-session provider instances from validated settings."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # --- configuration introspection --------------------------------------------

    @property
    def configuration_ok(self) -> bool:
        """True when every selected provider can actually run (readiness signal)."""
        return self.configuration_problem() is None

    def configuration_problem(self) -> str | None:
        """Human-readable blocker for logs/health/degraded readiness."""
        if self.stt_provider_name == "azure" and not (
            self._settings.AZURE_SPEECH_KEY and self._settings.AZURE_SPEECH_REGION
        ):
            return (
                "STT_PROVIDER=azure but AZURE_SPEECH_KEY/AZURE_SPEECH_REGION are not "
                "configured (no credentials → degraded, never mock fallback)"
            )
        if self.tts_provider_name == "azure" and not (
            self._settings.AZURE_SPEECH_KEY and self._settings.AZURE_SPEECH_REGION
        ):
            return (
                "TTS_PROVIDER=azure but AZURE_SPEECH_KEY/AZURE_SPEECH_REGION are not "
                "configured (no credentials → degraded, never mock fallback)"
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
    ) -> AzureSpeechToTextProvider | MockSpeechToTextProvider:
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
        raise ProviderConfigurationError(f"unknown STT_PROVIDER {settings.STT_PROVIDER!r}")

    def create_tts(
        self, audio_format: AudioFormat
    ) -> AzureTextToSpeechProvider | MockTextToSpeechProvider:
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
        raise ProviderConfigurationError(f"unknown TTS_PROVIDER {settings.TTS_PROVIDER!r}")

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
]
