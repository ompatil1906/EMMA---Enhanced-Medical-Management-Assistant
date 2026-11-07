"""Azure Speech TTS adapter (azure-cognitiveservices-speech 1.53.0).

Verified SDK facts encoded here (Microsoft Learn, read 2026-11-06):
  * In-memory/streaming output: `SpeechSynthesizer(speech_config, audio_config=None)`
    prevents default speaker playback; audio arrives via the `synthesizing`
    event (`event_args.result.audio_data`, bytes per chunk) with the end
    signalled by `synthesis_completed` / `synthesis_canceled`.
  * Raw (headerless) PCM output at exactly the negotiated rates:
    `set_speech_synthesis_output_format(SpeechSynthesisOutputFormat.Raw
    {8,16,24}Khz16BitMonoPcm)` — no WAVE header, no unexpected container.
  * `speak_text_async(text)` returns a future resolved with `.get()` AFTER the
    synthesizing stream has been delivered; the Future's result exposes
    `audio_data`/`cancellation_details` for post-hoc verification.
  * One synthesis per synthesizer at a time (concurrent use → synthesis
    canceled); this adapter serializes turns with an asyncio lock and builds
    one synthesizer per session, disposed after each synthesis.
  * Callbacks fire on the SDK thread → bridged with `loop.call_soon_threadsafe`.

Cancellation: the consumer cancelling the generator, or `cancel_turn()`,
stops yielding and ignores late chunks; the server-side synthesis itself
completes (documented SDK behaviour — this capture pattern has no mid-flight
abort API on Day 3).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any, Final

from app.domain.audio import AudioFormat
from app.domain.speech import SynthesisChunk, SynthesisCompleted, SynthesisStarted, TtsEvent
from app.speech.base import ProviderSynthesisError, SpeechProviderError

logger = logging.getLogger(__name__)

_STREAM_END: Final[object] = object()

_RAW_OUTPUT_BY_RATE: Final[dict[int, str]] = {
    8000: "Raw8Khz16BitMonoPcm",
    16000: "Raw16Khz16BitMonoPcm",
    24000: "Raw24Khz16BitMonoPcm",
}


def raw_output_format_name(sample_rate: int) -> str:
    """Map a negotiated rate to the verified Azure raw-PCM output format name."""
    try:
        return _RAW_OUTPUT_BY_RATE[sample_rate]
    except KeyError as exc:
        raise ProviderSynthesisError(
            f"no verified Azure raw-PCM output format for {sample_rate} Hz"
        ) from exc


class AzureTextToSpeechProvider:
    """Per-session synthesizer; `synthesize` yields chunks then completion."""

    def __init__(
        self,
        *,
        speech_key: str,
        speech_region: str,
        voice: str,
        language: str,
        audio_format: AudioFormat,
        request_timeout: float,
    ) -> None:
        self._speech_key = speech_key
        self._speech_region = speech_region
        self._voice = voice
        self._language = language
        self._format = audio_format
        self._request_timeout = request_timeout
        self._session_id: str | None = None
        self._cancelled: set[str] = set()
        self._synthesis_lock = asyncio.Lock()
        self._sdk: Any | None = None

    @property
    def provider_name(self) -> str:
        return f"azure-speech-tts:{self._voice}"

    async def synthesize(
        self, text: str, session_id: str, audio_format: AudioFormat
    ) -> AsyncIterator[TtsEvent]:
        if self._session_id is None:
            self._session_id = session_id
        elif self._session_id != session_id:
            raise SpeechProviderError(f"provider bound to session {self._session_id!r}")

        utterance_id = uuid.uuid4().hex
        started = SynthesisStarted(
            session_id=session_id,
            provider=self.provider_name,
            utterance_id=utterance_id,
            text_chars=len(text.strip()),
            audio_format=audio_format,
        )

        if not text.strip():  # empty-input contract: still a well-formed turn
            completed = SynthesisCompleted(
                session_id=session_id,
                provider=self.provider_name,
                utterance_id=utterance_id,
                total_bytes=0,
                chunk_count=0,
            )
            yield started
            yield completed
            return

        synthesizer = self._build_synthesizer(audio_format)
        queue: asyncio.Queue[object] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        state: dict[str, Any] = {"error": None}

        def on_chunk(event_args: Any) -> None:
            audio = event_args.result.audio_data
            if audio:
                loop.call_soon_threadsafe(queue.put_nowait, audio)

        def on_completed(_event_args: Any) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, _STREAM_END)

        def on_canceled(event_args: Any) -> None:
            details = getattr(event_args.result, "cancellation_details", None)
            state["error"] = str(
                getattr(details, "error_details", "") or "synthesis canceled by provider"
            )
            loop.call_soon_threadsafe(queue.put_nowait, _STREAM_END)

        synthesizer.synthesizing.connect(on_chunk)
        synthesizer.synthesis_completed.connect(on_completed)
        synthesizer.synthesis_canceled.connect(on_canceled)

        yield started
        synthesizing = True
        emitted_bytes = 0
        emitted_chunks = 0
        async with self._synthesis_lock:
            speak_task: asyncio.Task[Any] | None = None
            try:
                speak_task = asyncio.create_task(
                    asyncio.to_thread(synthesizer.speak_text_async(text).get)
                )
                while synthesizing:
                    item = await asyncio.wait_for(queue.get(), timeout=self._request_timeout)
                    if item is _STREAM_END:
                        break
                    if utterance_id in self._cancelled:
                        break  # stop feeding; late chunks ignored (documented)
                    assert isinstance(item, bytes)
                    emitted_bytes += len(item)
                    emitted_chunks += 1
                    yield SynthesisChunk(
                        session_id=session_id,
                        provider=self.provider_name,
                        utterance_id=utterance_id,
                        payload=item,
                        audio_format=audio_format,
                    )
                if state["error"]:
                    raise ProviderSynthesisError(f"azure TTS cancelled: {state['error']}")
            except TimeoutError as exc:
                raise ProviderSynthesisError(
                    f"azure TTS stalled beyond request timeout ({self._request_timeout:.0f}s)"
                ) from exc
            except ProviderSynthesisError:
                raise
            except Exception as exc:  # noqa: BLE001 — surfaced with provider context
                raise ProviderSynthesisError(f"azure TTS failed: {exc}") from exc

            if utterance_id in self._cancelled:
                yield SynthesisCompleted(
                    session_id=session_id,
                    provider=self.provider_name,
                    utterance_id=utterance_id,
                    total_bytes=emitted_bytes,
                    chunk_count=emitted_chunks,
                    cancelled=True,
                )
                await self._dispose(synthesizer)
                return

            try:
                await asyncio.wait_for(asyncio.shield(speak_task), timeout=self._request_timeout)
            except TimeoutError as exc:
                raise ProviderSynthesisError("azure TTS failed to finalize") from exc

            completed = SynthesisCompleted(
                session_id=session_id,
                provider=self.provider_name,
                utterance_id=utterance_id,
                total_bytes=emitted_bytes,
                chunk_count=emitted_chunks,
                cancelled=False,
            )
            await self._dispose(synthesizer)
            yield completed

    async def cancel_turn(self, session_id: str, utterance_id: str) -> None:
        self._cancelled.add(utterance_id)

    # --- internals ------------------------------------------------------------------

    def _build_synthesizer(self, audio_format: AudioFormat) -> Any:
        sdk = self._import_sdk()
        self._sdk = sdk
        speech_config = sdk.SpeechConfig(subscription=self._speech_key, region=self._speech_region)
        speech_config.speech_synthesis_language = self._language
        speech_config.speech_synthesis_voice_name = self._voice
        speech_config.set_speech_synthesis_output_format(
            getattr(
                sdk.SpeechSynthesisOutputFormat,
                raw_output_format_name(audio_format.sample_rate),
            )
        )
        # audio_config=None → capture path (no default device playback; verified).
        return sdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)

    @staticmethod
    def _import_sdk() -> Any:
        from azure.cognitiveservices import speech as sdk  # type: ignore[import-untyped]

        return sdk

    @staticmethod
    async def _dispose(synthesizer: Any) -> None:
        try:
            await asyncio.to_thread(synthesizer.dispose)
        except Exception:  # noqa: BLE001 — disposal is best-effort
            pass
