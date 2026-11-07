"""Azure Speech streaming STT adapter (azure-cognitiveservices-speech 1.53.0).

Verified SDK facts encoded here (Microsoft Learn, read 2026-11-06):
  * Streaming input: `audio.PushAudioInputStream` created with an explicit
    `audio.AudioStreamFormat(samples_per_second, bits_per_sample=16, channels=1)`
    — headerless linear PCM; the recognizer reads from the push stream as audio
    is written.
  * Continuous recognition: `SpeechRecognizer.start_continuous_recognition_async()`
    / `stop_continuous_recognition_async()`; event signals: `recognizing`
    (partial), `recognized` (per-utterance final), `session_started`,
    `session_stopped`, `canceled`.
  * Result objects expose `text`, `offset`, `duration`, `reason`, `json` — the
    Python SDK has NO top-level `confidence` property; confidence is read
    defensively from the detailed-result JSON (`Confidence` / `NBest[0]`)
    when present (None otherwise).
  * SDK callbacks run on the SDK's own thread → bridged onto the asyncio loop
    via `loop.call_soon_threadsafe`.
  * `start_*_async()` returns an awaitable-compatible handle resolved through
    `.get()`, which BLOCKS — executed off-loop via `asyncio.to_thread`.

No credentials → never reaches this module: the central factory refuses to
construct Azure providers without key+region and the app degrades readiness
(no silent mock fallback).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Final

from app.domain.speech import (
    FinalTranscript,
    PartialTranscript,
    RecognitionError,
    RecognitionStarted,
    RecognitionStopped,
    SttEvent,
)
from app.speech.base import ProviderConnectionError, SpeechProviderError

if TYPE_CHECKING:  # pragma: no cover - type hints only; runtime import is lazy
    from app.domain.audio import AudioFormat

logger = logging.getLogger(__name__)

_STREAM_END: Final[object] = object()


def _extract_confidence(result_json: str | None) -> float | None:
    """Defensive confidence extraction (SDK has no direct property; see above)."""
    if not result_json:
        return None
    try:
        payload = json.loads(result_json)
        if isinstance(payload, dict):
            top = payload.get("Confidence")
            if isinstance(top, (int, float)):
                return float(top)
            nbest = payload.get("NBest")
            if isinstance(nbest, list) and nbest:
                value = nbest[0].get("Confidence") if isinstance(nbest[0], dict) else None
                if isinstance(value, (int, float)):
                    return float(value)
    except (ValueError, TypeError):
        return None
    return None


class AzureSpeechToTextProvider:
    """One recognition stream per session (Azure F0 = 1 concurrent STT run)."""

    def __init__(
        self,
        *,
        speech_key: str,
        speech_region: str,
        language: str,
        audio_format: AudioFormat,
        connection_timeout: float,
        stop_timeout: float,
    ) -> None:
        self._speech_key = speech_key
        self._speech_region = speech_region
        self._language = language
        self._format = audio_format
        self._connection_timeout = connection_timeout
        self._stop_timeout = stop_timeout
        self._queue: asyncio.Queue[object] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session_id: str | None = None
        self._started = False
        self._stopped = False
        self._sequence = 0
        self._mk_lock = threading.Lock()  # guards `_sequence` (SDK-thread callbacks)
        self._sdk = None  # azure.cognitiveservices.speech (lazy)
        self._push_stream: Any | None = None
        self._recognizer: Any | None = None

    @property
    def provider_name(self) -> str:
        return f"azure-speech-stt:{self._language}"

    # --- contract ------------------------------------------------------------

    async def start(self, session_id: str) -> None:
        if self._session_id is not None and self._session_id != session_id:
            raise SpeechProviderError(f"provider already bound to session {self._session_id!r}")
        if self._started:
            return
        self._loop = asyncio.get_running_loop()
        self._session_id = session_id
        self._sdk = self._import_sdk()
        try:
            self._build_stream_and_recognizer()
            handle = self._recognizer.start_continuous_recognition_async()
            await asyncio.wait_for(asyncio.to_thread(handle.get), timeout=self._connection_timeout)
        except Exception as exc:  # noqa: BLE001 — narrow re-raise below
            await self._release_sdk_objects()
            if isinstance(exc, SpeechProviderError):
                raise
            raise ProviderConnectionError(f"azure STT start failed: {exc}") from exc
        self._started = True
        self._queue.put_nowait(
            RecognitionStarted(session_id=session_id, provider=self.provider_name)
        )

    async def push_audio(self, session_id: str, audio: bytes) -> None:
        if self._session_id != session_id or not self._started or self._stopped:
            return
        if not audio:
            return
        # SDK write is cheap/buffered; direct call documented non-blocking.
        self._push_stream.write(audio)

    async def stop(self, session_id: str, *, reason: str = "session_stop") -> None:
        if self._session_id != session_id or self._stopped:
            return
        self._stopped = True
        if self._started and self._recognizer is not None:
            try:
                handle = self._recognizer.stop_continuous_recognition_async()
                await asyncio.wait_for(asyncio.to_thread(handle.get), timeout=self._stop_timeout)
            except Exception as exc:  # noqa: BLE001 — teardown must not raise
                logger.warning(
                    "azure STT stop aborted (session still torn down)",
                    extra={"session_id": session_id, "detail": str(exc)},
                )
        await self._release_sdk_objects()
        self._queue.put_nowait(
            RecognitionStopped(session_id=session_id, provider=self.provider_name, reason=reason)
        )
        self._queue.put_nowait(_STREAM_END)

    def events(self, session_id: str) -> AsyncIterator[SttEvent]:
        if self._session_id != session_id:
            raise SpeechProviderError(f"session id mismatch (bound to {self._session_id!r})")

        async def _stream() -> AsyncIterator[SttEvent]:
            while True:
                item = await self._queue.get()
                if item is _STREAM_END:
                    break
                assert isinstance(item, SttEvent), f"unexpected queue item {item!r}"
                yield item  # type: ignore[misc]

        return _stream()

    # --- SDK plumbing ----------------------------------------------------------

    @staticmethod
    def _import_sdk() -> Any:
        from azure.cognitiveservices import speech as sdk  # type: ignore[import-untyped]

        return sdk

    def _build_stream_and_recognizer(self) -> None:
        sdk = self._sdk
        audio_cls = sdk.audio
        stream_format = audio_cls.AudioStreamFormat(
            samples_per_second=self._format.sample_rate,
            bits_per_sample=16,
            channels=1,
        )
        self._push_stream = audio_cls.PushAudioInputStream(stream_format=stream_format)

        speech_config = sdk.SpeechConfig(subscription=self._speech_key, region=self._speech_region)
        speech_config.speech_recognition_language = self._language

        self._recognizer = sdk.SpeechRecognizer(
            speech_config=speech_config, audio_config=self._push_stream
        )
        self._recognizer.recognizing.connect(self._on_recognizing)
        self._recognizer.recognized.connect(self._on_recognized)
        self._recognizer.canceled.connect(self._on_canceled)
        # session_started/session_stopped intentionally not consumed separately:
        # RecognitionStarted/Stopped are emitted by start()/stop() and are the
        # pipeline's provider-state source of truth.

    def _emit_soon(self, event: SttEvent) -> None:
        loop = self._loop
        if loop is None:  # disposed session; drop
            return
        loop.call_soon_threadsafe(self._queue.put_nowait, event)

    # Callbacks run on the SDK thread (verified SDK threading model).

    def _on_recognizing(self, event_args: Any) -> None:
        if self._stopped:
            return
        text = event_args.result.text
        if not text:
            return
        self._emit_soon(
            PartialTranscript(
                session_id=self._session_id or "",
                provider=self.provider_name,
                sequence=self._sequence_increment(),
                text=text,
                confidence=None,  # recognizing results carry no confidence
                provider_metadata=self._metadata_of(event_args),
            )
        )

    def _on_recognized(self, event_args: Any) -> None:
        if self._stopped:
            return
        result = event_args.result
        sdk = self._sdk
        reason = getattr(sdk, "ResultReason", None)
        only_speech = reason is not None and result.reason == reason.RecognizedSpeech
        if not only_speech:
            return  # NoMatch / silence handled by continuous recognition itself
        text = result.text
        if not text:
            return  # empty transcript handling: nothing to emit
        self._emit_soon(
            FinalTranscript(
                session_id=self._session_id or "",
                provider=self.provider_name,
                sequence=self._sequence_increment(),
                text=text,
                confidence=_extract_confidence(result.json),
                provider_metadata=self._metadata_of(event_args),
            )
        )

    def _on_canceled(self, event_args: Any) -> None:
        if self._stopped:
            return
        details = getattr(event_args.result, "cancellation_details", None)
        message = str(getattr(details, "error_details", "") or "recognition canceled")
        self._stopped = True  # provider closed the stream; no further events make sense
        self._emit_soon(
            RecognitionError(
                session_id=self._session_id or "",
                provider=self.provider_name,
                code="recognized_canceled",
                message=message,
                fatal=True,
            )
        )
        self._queue.put_nowait(_STREAM_END)

    def _metadata_of(self, event_args: Any) -> dict[str, Any]:
        result = event_args.result
        return {
            "offset_ticks": getattr(result, "offset", None),
            "duration_ticks": getattr(result, "duration", None),
        }

    def _sequence_increment(self) -> int:
        with self._mk_lock:
            self._sequence += 1
            return self._sequence

    async def _release_sdk_objects(self) -> None:
        stream = self._push_stream
        self._push_stream = None
        self._recognizer = None

        def _close_stream() -> None:
            close = getattr(stream, "close", None)
            if callable(close):
                close()

        for task in ((asyncio.to_thread(_close_stream) if stream is not None else None),):
            if task is not None:
                try:
                    await task
                except Exception:  # noqa: BLE001 — best-effort release
                    pass
