"""Provider-independent speech provider interfaces (Day-3 contract).

Design rules from the assignment:

* Speech providers are created PER SESSION by the central provider factory
  (`app.speech.factory`) with the already-negotiated audio format — Azure
  fixes its stream/synthesis format at object construction, so the format
  must be known before the provider object exists.
* One provider instance drives exactly one session (`_session_id_guard`).
* Providers never log and never route audio around the gateway.
* Azure runtime failures are surfaced as narrow exceptions; there is NEVER a
  silent mock fallback (explicit configuration only).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from app.domain.audio import AudioFormat
from app.domain.speech import SttEvent, TtsEvent


class SpeechProviderError(Exception):
    """Base class for speech-provider failures."""


class ProviderConfigurationError(SpeechProviderError):
    """The configured provider cannot run with the supplied configuration."""


class ProviderConnectionError(SpeechProviderError):
    """Connecting/starting the provider stream failed (or exceeded its timeout)."""


class ProviderSynthesisError(SpeechProviderError):
    """A synthesis request failed (empty/timeout/cancelled/provider error)."""


@runtime_checkable
class SpeechToTextProvider(Protocol):
    """Streaming STT contract; implemented by mock + Azure adapters."""

    @property
    def provider_name(self) -> str: ...

    async def start(self, session_id: str) -> None:
        """Open the recognition stream (connection-timeout bounded)."""
        ...

    async def push_audio(self, session_id: str, audio: bytes) -> None:
        """Push one decoded PCM16-mono chunk in the negotiated format."""
        ...

    def events(self, session_id: str) -> AsyncIterator[SttEvent]:
        """Yield normalized speech events until the stream ends (sentinel)."""
        ...

    async def stop(self, session_id: str, *, reason: str = "session_stop") -> None:
        """Close the stream cleanly (request-timeout bounded, idempotent)."""
        ...


@runtime_checkable
class TextToSpeechProvider(Protocol):
    """Synthesis contract; implemented by mock + Azure adapters."""

    @property
    def provider_name(self) -> str: ...

    def synthesize(
        self, text: str, session_id: str, audio_format: AudioFormat
    ) -> AsyncIterator[TtsEvent]:
        """Yield SynthesisStarted? chunks..., then SynthesisCompleted last.

        Raises ProviderSynthesisError on failure (pipeline decides recovery
        vs teardown). Empty text yields only the completed event (0 bytes).
        """
        ...

    async def cancel_turn(self, session_id: str, utterance_id: str) -> None:
        """Best-effort drop of an in-flight synthesis (barge-in support hook)."""
        ...
