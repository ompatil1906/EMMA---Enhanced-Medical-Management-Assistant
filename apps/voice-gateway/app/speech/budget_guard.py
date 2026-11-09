"""Day 18C: runtime spend guard wrappers for live speech providers.

These wrappers are the enforcement point used by the **real runtime** — the
LiveKit agent and the WebSocket pipeline both obtain providers from
`SpeechProviderFactory`, which wraps the live adapters in these classes whenever
the live guard is enabled. They delegate to the existing Deepgram / ElevenLabs
adapters unchanged (no protocol logic here).

Guarantees:

* the budget is reserved **before** the provider is contacted, so a rejected
  call never reaches the network;
* a rejected call is not counted (the ledger commit is skipped on refusal);
* an accepted call is never refunded — not on cancellation, not on provider
  error — because the provider may already have billed it;
* per-session Deepgram audio is enforced while streaming, so extra frames after
  the reservation cannot push a session past the 15 s cap;
* every wrapper surfaces a clear `ProviderConfigurationError`/`LiveBudgetError`
  instead of silently degrading to a mock provider.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from app.domain.audio import AudioFormat
from app.domain.speech import RecognitionError, SttEvent, TtsEvent
from app.services.live_provider_budget import LiveBudgetError, LiveProviderLedger
from app.speech.base import SpeechProviderError

logger = logging.getLogger(__name__)

_GUARD_SUFFIX = "+budget-guarded"


class BudgetedSTT:
    """STT decorator that reserves a session and meters streamed audio."""

    def __init__(
        self,
        provider: Any,
        ledger: LiveProviderLedger,
        *,
        audio_format: AudioFormat,
    ) -> None:
        self._inner = provider
        self._ledger = ledger
        self._sample_rate = int(audio_format.sample_rate) or 16000
        self._session_id: str | None = None
        self._session_seconds = 0.0
        self._exhausted = False

    # --- introspection (used by tests, health and the report) ----------------

    @property
    def provider_name(self) -> str:
        return f"{self._inner.provider_name}{_GUARD_SUFFIX}"

    @property
    def inner(self) -> Any:
        return self._inner

    @property
    def session_seconds(self) -> float:
        return self._session_seconds

    @property
    def exhausted(self) -> bool:
        return self._exhausted

    # --- contract -------------------------------------------------------------

    async def start(self, session_id: str) -> None:
        if self._session_id is not None and self._session_id != session_id:
            raise SpeechProviderError(f"provider already bound to session {self._session_id!r}")
        if self._session_id == session_id:
            return

        # Reserve FIRST: the attempt is counted before any provider activity.
        try:
            self._ledger.reserve_stt_session()
        except LiveBudgetError as exc:
            logger.warning("live STT session refused by budget guard: %s", exc)
            raise
        self._session_id = session_id
        self._session_seconds = 0.0
        try:
            await self._inner.start(session_id)
        except Exception:
            # The provider was already contacted (or the attempt was made):
            # the reservation stands. Never refund here.
            raise

    async def push_audio(self, session_id: str, audio: bytes) -> None:
        if self._exhausted or self._session_id != session_id:
            return
        seconds = len(audio) / 2 / self._sample_rate if audio else 0.0
        limit = self._ledger.limits.max_deepgram_audio_seconds
        if self._session_seconds + seconds > limit:
            # Streaming duration enforcement: refuse the frame and tear the
            # provider down so no further audio can reach it.
            self._exhausted = True
            logger.warning(
                "live STT audio cap reached (%.2fs of %.2fs); closing provider stream",
                self._session_seconds,
                limit,
            )
            try:
                await self._inner.stop(session_id, reason="budget_exceeded")
            except Exception:  # noqa: BLE001 — teardown must not mask the refusal
                logger.debug("budget-refused STT teardown failed", exc_info=True)
            raise LiveBudgetError(
                f"deepgram audio cap reached for session {session_id!r}: "
                f"{self._session_seconds + seconds:.2f}s > {limit:.2f}s"
            )

        self._session_seconds += seconds
        self._ledger.add_stt_audio(seconds)
        await self._inner.push_audio(session_id, audio)

    def events(self, session_id: str) -> AsyncIterator[SttEvent]:
        async def _guarded() -> AsyncIterator[SttEvent]:
            async for event in self._inner.events(session_id):
                yield event
            if self._exhausted:
                # Surface the refusal as a fatal provider error so the pipeline
                # tears the turn down instead of waiting for text that will
                # never arrive.
                yield RecognitionError(
                    session_id=session_id,
                    provider=self.provider_name,
                    code="budget_exceeded",
                    message="live STT audio budget exhausted; stream closed",
                    fatal=True,
                )

        return _guarded()

    async def stop(self, session_id: str, *, reason: str = "session_stop") -> None:
        await self._inner.stop(session_id, reason=reason)

    async def finish(self, session_id: str, **kwargs: Any) -> None:
        """Delegate the optional one-shot flush capability when present."""
        finish = getattr(self._inner, "finish", None)
        if finish is not None:
            await finish(session_id, **kwargs)


class BudgetedTTS:
    """TTS decorator that reserves one request per non-empty synthesis call."""

    def __init__(self, provider: Any, ledger: LiveProviderLedger) -> None:
        self._inner = provider
        self._ledger = ledger

    # --- introspection --------------------------------------------------------

    @property
    def provider_name(self) -> str:
        return f"{self._inner.provider_name}{_GUARD_SUFFIX}"

    @property
    def inner(self) -> Any:
        return self._inner

    # --- contract -------------------------------------------------------------

    def synthesize(
        self, text: str, session_id: str, audio_format: AudioFormat
    ) -> AsyncIterator[TtsEvent]:
        async def _guarded() -> AsyncIterator[TtsEvent]:
            # Empty text is a no-op turn that never contacts the provider, so it
            # must not consume budget.
            if text.strip():
                try:
                    self._ledger.reserve_tts_request(text)
                except LiveBudgetError as exc:
                    logger.warning("live TTS request refused by budget guard: %s", exc)
                    raise
            async for event in self._inner.synthesize(text, session_id, audio_format):
                yield event

        return _guarded()

    async def cancel_turn(self, session_id: str, utterance_id: str) -> None:
        await self._inner.cancel_turn(session_id, utterance_id)


__all__ = ["BudgetedSTT", "BudgetedTTS"]
