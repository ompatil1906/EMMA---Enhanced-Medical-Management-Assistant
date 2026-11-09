"""ElevenLabs Flash streaming TTS adapter (Day 12; protocol corrected Day 18).

Uses ElevenLabs' documented **HTTP streaming** endpoint:

    POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream
         ?output_format=pcm_{rate}

with `xi-api-key` auth and a JSON body (`text`, `model_id`, `voice_settings`).
The response is chunked raw audio which we request as **PCM16 little-endian
mono** (`pcm_8000` / `pcm_16000` / `pcm_22050` / `pcm_24000` / `pcm_44100`) —
exactly the format the EMMA pipeline and the LiveKit publisher already use, so
no decode step is needed.

Why this changed (Day 18 finding): the Day 12 implementation opened
``wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream/input`` and
expected ``{"type": "audio", "audio_base64": ...}`` messages. That path is not
an ElevenLabs endpoint and the frame names did not match the documented
streaming-input WebSocket schema, so it could not have worked. The HTTP
streaming endpoint is stable, needs no new dependency (`httpx` is already a
runtime dependency), streams incrementally for first-chunk latency, and lets us
request the pipeline's exact PCM format.

Unchanged design rules (assignment §8):

* Model comes from `ELEVENLABS_MODEL` only (no model fallback).
* Voice comes from `ELEVENLABS_VOICE_ID` only — never fabricated or substituted.
* Streaming synthesis; sentence chunking is the provider's responsibility.
* Cancellation via `cancel_turn()` (best-effort, cooperative).
* Session cleanup: the HTTP response is closed and the client released.
* No credentials → the adapter refuses to construct (no silent mock fallback).
  No naturalness claims are made anywhere without a listening evaluation.

Verification status: covered by offline protocol tests against a local HTTP
server (`tests/integration/test_elevenlabs_protocol.py`). **Not** verified
against the live ElevenLabs API in this environment — no live request has been
made (`docs/LIVE_PROVIDER_ACCEPTANCE.md`).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Final

import httpx

from app.domain.audio import AudioFormat
from app.domain.speech import (
    SynthesisChunk,
    SynthesisCompleted,
    SynthesisStarted,
    TtsEvent,
)
from app.speech.base import (
    ProviderConfigurationError,
    ProviderSynthesisError,
    SpeechProviderError,
)

if TYPE_CHECKING:  # pragma: no cover - type hints only; runtime import is lazy
    pass

logger = logging.getLogger(__name__)

_STREAM_END: Final[object] = object()

_ELEVENLABS_BASE = "https://api.elevenlabs.io/v1/text-to-speech"

# ElevenLabs PCM output formats are keyed by sample rate; anything else would
# require a decode/resample step the pipeline does not perform silently.
_PCM_RATES = {8000, 16000, 22050, 24000, 44100}


def pcm_output_format(sample_rate: int) -> str:
    """Map a negotiated sample rate to an ElevenLabs `output_format` value."""
    if sample_rate not in _PCM_RATES:
        raise ProviderConfigurationError(
            f"elevenlabs TTS has no PCM output for {sample_rate} Hz "
            f"(supported: {sorted(_PCM_RATES)}) — refusing to transcode silently"
        )
    return f"pcm_{sample_rate}"


class ElevenLabsFlashTTSProvider:
    """Per-session synthesizer; `synthesize` yields chunks then completion."""

    def __init__(
        self,
        *,
        api_key: str,
        voice_id: str,
        model: str,
        audio_format: AudioFormat,
        request_timeout: float,
        endpoint: str | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ProviderConfigurationError(
                "elevenlabs TTS requires ELEVENLABS_API_KEY; refusing to start"
            )
        if not voice_id or not voice_id.strip():
            # A blank/whitespace voice id would otherwise become a 404 at the
            # first synthesis request instead of failing at configuration time.
            raise ProviderConfigurationError(
                "elevenlabs TTS requires ELEVENLABS_VOICE_ID; refusing to start"
            )
        self._api_key = api_key
        self._voice_id = voice_id
        self._model = model
        self._format = audio_format
        self._request_timeout = request_timeout
        self._endpoint = endpoint or _ELEVENLABS_BASE
        self._session_id: str | None = None
        self._cancelled: set[str] = set()
        self._active_response: httpx.Response | None = None
        # Validate the format at construction: a provider that cannot produce
        # the negotiated format must fail fast, not mid-turn.
        self._output_format = pcm_output_format(audio_format.sample_rate)

    @property
    def provider_name(self) -> str:
        return f"elevenlabs-flash:{self._model}"

    @property
    def output_format(self) -> str:
        """The ElevenLabs `output_format` this provider requests (non-secret)."""
        return self._output_format

    # --- contract ------------------------------------------------------------

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

        yield started

        # Only `output_format` is sent: the older `optimize_streaming_latency`
        # parameter is deprecated upstream, and sending deprecated parameters is
        # how a request gets rejected on an account we cannot test against here.
        url = f"{self._endpoint}/{self._voice_id}/stream?output_format={self._output_format}"
        headers = {
            "xi-api-key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "application/octet-stream",
        }
        body: dict[str, Any] = {
            "text": text,
            "model_id": self._model,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.8},
        }
        timeout = httpx.Timeout(self._request_timeout, connect=min(5.0, self._request_timeout))

        emitted_bytes = 0
        emitted_chunks = 0
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", url, headers=headers, json=body) as response:
                    self._active_response = response
                    if response.status_code != 200:
                        detail = await _read_error(response)
                        raise ProviderSynthesisError(
                            _describe_status(response.status_code, detail, self._voice_id)
                        )
                    chunk_size = max(1024, int(audio_format.sample_rate / 50) * 2)  # ~20 ms
                    async for chunk in response.aiter_bytes(chunk_size=chunk_size):
                        if utterance_id in self._cancelled:
                            break
                        if not chunk:
                            continue
                        emitted_bytes += len(chunk)
                        emitted_chunks += 1
                        yield SynthesisChunk(
                            session_id=session_id,
                            provider=self.provider_name,
                            utterance_id=utterance_id,
                            payload=chunk,
                            audio_format=audio_format,
                        )
        except ProviderSynthesisError:
            raise
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # Closing an in-flight response is how `cancel_turn` interrupts a
            # barge-in, and httpx reports that close as a transport error. That
            # is an expected cancellation, not a provider failure.
            if utterance_id in self._cancelled:
                pass
            else:
                raise ProviderSynthesisError(
                    f"elevenlabs TTS transport failure ({type(exc).__name__}): {exc}"
                ) from exc
        finally:
            self._active_response = None

        cancelled = utterance_id in self._cancelled
        if emitted_bytes == 0 and not cancelled:
            # A 200 with no audio would otherwise look like a silent success.
            raise ProviderSynthesisError("elevenlabs TTS returned an empty audio response")

        yield SynthesisCompleted(
            session_id=session_id,
            provider=self.provider_name,
            utterance_id=utterance_id,
            total_bytes=emitted_bytes,
            chunk_count=emitted_chunks,
            cancelled=cancelled,
        )

    async def cancel_turn(self, session_id: str, utterance_id: str) -> None:
        """Best-effort drop of an in-flight synthesis (barge-in support hook).

        Marks the utterance cancelled and closes the streaming response, which
        breaks the `aiter_bytes` loop so no further audio is yielded.
        """
        self._cancelled.add(utterance_id)
        response = self._active_response
        if response is not None:
            try:
                await response.aclose()
            except Exception:  # noqa: BLE001 — best-effort cancellation
                pass


def _describe_status(status_code: int, detail: str, voice_id: str) -> str:
    """Turn an HTTP status into an operator-facing, non-secret message."""
    if status_code in (401, 403):
        return (
            f"elevenlabs TTS authentication failed ({status_code}) — check "
            f"ELEVENLABS_API_KEY (detail: {detail})"
        )
    if status_code == 402:
        return (
            f"elevenlabs TTS refused the request: payment required ({status_code}) — "
            f"check account credits (detail: {detail})"
        )
    if status_code == 404:
        return (
            f"elevenlabs TTS voice not found ({status_code}) — check "
            f"ELEVENLABS_VOICE_ID={voice_id!r} (detail: {detail})"
        )
    if status_code == 422:
        return (
            f"elevenlabs TTS rejected the request ({status_code}) — check the model "
            f"and voice settings (detail: {detail})"
        )
    if status_code == 429:
        return (
            f"elevenlabs TTS rate limited ({status_code}) — no retry performed (detail: {detail})"
        )
    return f"elevenlabs TTS request failed ({status_code}): {detail}"


async def _read_error(response: httpx.Response) -> str:
    """Read a bounded, non-secret error body for the operator message."""
    try:
        raw = await response.aread()
    except Exception:  # noqa: BLE001 — error body is best-effort
        return "no detail"
    text = raw.decode("utf-8", errors="replace").strip().replace("\n", " ")
    if not text:
        return "no detail"
    return text[:300]


__all__ = ["ElevenLabsFlashTTSProvider", "pcm_output_format"]
