"""Deterministic mock STT provider (script-driven, no network, no audio decode).

Behaviour (all values recorded here are contract, tested by unit tests):

* `start` emits `RecognitionStarted`.
* Audio pushes accumulate *duration* (bytes / (2 bytes/sample) / sample_rate).
  Crossing `MOCK_STT_UTTERANCE_MS` of audio since the last finalized utterance
  schedules deterministic recognition: a `PartialTranscript` is emitted
  immediately and the matching `FinalTranscript` after `MOCK_STT_FINALIZE_MS`
  (simulated provider latency).
* Utterance text comes from the configured script lines, consumed in order and
  cycling when exhausted (multi-turn sessions stay deterministic).
* The transcript is NOT derived from the audio payload — the mock proves the
  pipeline/FSM/framing/rules, not phonetics (documented honestly).
* Empty payloads are accepted but never advance recognition.
* Optional fault injection: after the N-th audio push a `RecognitionError`
  (non-fatal) is emitted — exercises provider-error handling deterministically.
* `stop` cancels any pending finalize, emits `RecognitionStopped` and ends the
  event stream; `events()` completes only after that sentinel.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Final

from app.domain.speech import (
    FinalTranscript,
    PartialTranscript,
    RecognitionError,
    RecognitionStarted,
    RecognitionStopped,
    SttEvent,
)
from app.speech.base import SpeechProviderError

_STREAM_END: Final[object] = object()
_DEFAULT_CONFIDENCE: Final[float] = 0.99


class MockSpeechToTextProvider:
    """Script-driven STT implementing the SpeechToTextProvider contract."""

    def __init__(
        self,
        *,
        script: Sequence[str],
        finalize_ms: int,
        utterance_ms: int,
        sample_rate: int = 8000,
        error_at_frame: int = 0,
    ) -> None:
        if not script:
            raise ValueError("mock STT script must contain at least one line")
        self._script = tuple(script)
        self._finalize_s = finalize_ms / 1000.0
        self._utterance_ms = utterance_ms
        self._sample_rate = sample_rate
        self._error_at_frame = error_at_frame
        self._queue: asyncio.Queue[object] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session_id: str | None = None
        self._started = False
        self._stopped = False
        self._pending_handles: list[asyncio.TimerHandle] = []
        self._bytes_since_finalize = 0
        self._frames_pushed = 0
        self._script_index = 0
        self._utterance_sequence = 0

    @property
    def provider_name(self) -> str:
        return "mock-stt"

    # --- contract methods -----------------------------------------------------

    async def start(self, session_id: str) -> None:
        self._assert_session_free(session_id)
        self._loop = asyncio.get_running_loop()
        self._started = True
        self._queue.put_nowait(
            RecognitionStarted(session_id=session_id, provider=self.provider_name)
        )

    async def push_audio(self, session_id: str, audio: bytes) -> None:
        self._assert_session(session_id)
        if self._stopped:
            return
        self._frames_pushed += 1
        if self._error_at_frame and self._frames_pushed == self._error_at_frame:
            self._queue.put_nowait(
                RecognitionError(
                    session_id=session_id,
                    provider=self.provider_name,
                    code="mock_injected_error",
                    message="injected failure after push",
                    fatal=False,
                )
            )
            return
        if not audio:
            return
        self._bytes_since_finalize += len(audio)
        if self._duration_ms_since_finalize() >= self._utterance_ms:
            self._schedule_finalize(session_id)

    def _duration_ms_since_finalize(self) -> float:
        """Duration (ms) of audio accumulated since the last finalized utterance."""
        return (self._bytes_since_finalize / 2 / self._sample_rate) * 1000.0

    async def stop(self, session_id: str, *, reason: str = "session_stop") -> None:
        self._assert_session(session_id)
        if self._stopped:
            return
        self._stopped = True
        for handle in self._pending_handles:
            handle.cancel()
        self._pending_handles.clear()
        self._queue.put_nowait(
            RecognitionStopped(session_id=session_id, provider=self.provider_name, reason=reason)
        )
        self._queue.put_nowait(_STREAM_END)

    def events(self, session_id: str) -> AsyncIterator[SttEvent]:
        self._assert_session(session_id)

        async def _stream() -> AsyncIterator[SttEvent]:
            while True:
                item = await self._queue.get()
                if item is _STREAM_END:
                    break
                assert isinstance(item, SttEvent), f"unexpected queue item {item!r}"
                yield item  # type: ignore[misc]

        return _stream()

    # --- deterministic internals ----------------------------------------------

    def _schedule_finalize(self, session_id: str) -> None:
        """Emit a partial now and its final after the simulated finalize delay."""
        self._bytes_since_finalize = 0
        loop = self._loop or asyncio.get_running_loop()
        text, _index = self._next_script_line()
        sequence = self._utterance_sequence + 1
        self._utterance_sequence = sequence
        self._emit_partial(session_id, text, sequence)
        handle = loop.call_later(
            self._finalize_s, self._emit_final_sync, session_id, text, sequence
        )
        self._pending_handles.append(handle)

    def _emit_partial(self, session_id: str, text: str, sequence: int) -> None:
        self._queue.put_nowait(
            PartialTranscript(
                session_id=session_id,
                provider=self.provider_name,
                sequence=sequence,
                text=text,
                confidence=_DEFAULT_CONFIDENCE,
            )
        )

    def _emit_final_sync(self, session_id: str, text: str, sequence: int) -> None:
        if self._stopped:
            return
        self._queue.put_nowait(
            FinalTranscript(
                session_id=session_id,
                provider=self.provider_name,
                sequence=sequence,
                text=text,
                confidence=_DEFAULT_CONFIDENCE,
            )
        )

    def _next_script_line(self) -> tuple[str, int]:
        index = self._script_index % len(self._script)
        self._script_index += 1
        return self._script[index], index

    # --- session guards ---------------------------------------------------------

    def _assert_session_free(self, session_id: str) -> None:
        if self._session_id is not None and self._session_id != session_id:
            raise SpeechProviderError(f"provider already bound to session {self._session_id!r}")
        if self._session_id is None:
            self._session_id = session_id

    def _assert_session(self, session_id: str) -> None:
        if self._session_id is None:
            raise SpeechProviderError("provider session not started")
        if self._session_id != session_id:
            raise SpeechProviderError(f"session id mismatch (bound to {self._session_id!r})")
