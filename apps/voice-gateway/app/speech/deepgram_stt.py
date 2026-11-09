"""Deepgram streaming STT adapter (Day 12; protocol corrected Day 18).

Two documented Deepgram streaming contracts are supported, chosen by the
configured model — never by a fallback:

* **Flux** (`DEEPGRAM_MODEL` starts with ``flux``), e.g. ``flux-general-en``:
  ``wss://api.deepgram.com/v2/listen``. Server messages are ``TurnInfo``
  objects with ``event`` in ``StartOfTurn`` / ``Update`` / ``EagerEndOfTurn`` /
  ``EndOfTurn`` / ``TurnResumed`` and a ``transcript`` field.
* **Classic streaming** (any other model, e.g. ``nova-3``):
  ``wss://api.deepgram.com/v1/listen`` with ``Results`` messages containing
  ``channel.alternatives[0].transcript`` and ``is_final``.

Why this matters (Day 18 finding): the Day 12 implementation sent
``model=flux-general-en`` to the *classic* ``/v1/listen`` endpoint with
``interim_results``/``endpointing`` parameters. Flux is served from ``/v2/listen``
with a different message schema, so the two halves could not work together.
The model identifier is unchanged — only the endpoint/schema pairing was fixed.

Design rules preserved from Day 12:

* Model comes from `DEEPGRAM_MODEL` only — no alternative-model fallback.
* Partial and final transcripts; Flux provides native end-of-turn detection.
* Duplicate-final protection: every final has a monotonically increasing
  sequence (the pipeline also rejects duplicates).
* Session cleanup: the WebSocket is closed on `stop()`; resources released.
* Errors surface as `RecognitionError`; fatal errors tear the session down and
  never silently fall back to another provider or to mock.

Audio: raw PCM16 little-endian mono at the negotiated sample rate
(`encoding=linear16`), pushed as binary WebSocket frames.

Verification status: covered by offline protocol tests against a local
WebSocket server (`tests/integration/test_deepgram_protocol.py`). **Not**
verified against the live Deepgram API in this environment — no live call has
been made (`docs/LIVE_PROVIDER_ACCEPTANCE.md`).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Final

try:
    import websockets  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - websockets is a runtime dependency
    websockets = None  # type: ignore[assignment]

from app.domain.speech import (
    FinalTranscript,
    PartialTranscript,
    RecognitionError,
    RecognitionStarted,
    RecognitionStopped,
    SttEvent,
)
from app.speech.base import (
    ProviderConfigurationError,
    ProviderConnectionError,
    SpeechProviderError,
)

if TYPE_CHECKING:  # pragma: no cover - type hints only; runtime import is lazy
    from app.domain.audio import AudioFormat

logger = logging.getLogger(__name__)

_STREAM_END: Final[object] = object()

# Flux (turn-based) and classic streaming live on different paths with
# different message schemas; see the module docstring.
_FLUX_ENDPOINT = "wss://api.deepgram.com/v2/listen"
_CLASSIC_ENDPOINT = "wss://api.deepgram.com/v1/listen"


def is_flux_model(model: str) -> bool:
    """True when the configured model is a Flux conversational model."""
    return model.strip().lower().startswith("flux")


class DeepgramFluxSTTProvider:
    """One recognition stream per session (Deepgram streaming WebSocket)."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        language: str,
        audio_format: AudioFormat,
        connection_timeout: float,
        request_timeout: float,
        endpoint: str | None = None,
    ) -> None:
        if websockets is None:
            raise ProviderConfigurationError(
                "deepgram STT requires the `websockets` package; not installed"
            )
        if not api_key or not api_key.strip():
            raise ProviderConfigurationError(
                "deepgram STT requires DEEPGRAM_API_KEY; refusing to start"
            )
        self._api_key = api_key
        self._model = model
        self._language = language
        self._format = audio_format
        self._connection_timeout = connection_timeout
        self._request_timeout = request_timeout
        self._endpoint = endpoint or (_FLUX_ENDPOINT if is_flux_model(model) else _CLASSIC_ENDPOINT)
        self._queue: asyncio.Queue[object] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session_id: str | None = None
        self._started = False
        self._stopped = False
        self._closing = False
        self._sequence = 0
        self._ws: Any = None
        self._receiver_task: asyncio.Task[None] | None = None

    @property
    def provider_name(self) -> str:
        return f"deepgram-flux:{self._model}"

    @property
    def is_flux(self) -> bool:
        """Whether this stream uses the turn-based Flux schema."""
        return is_flux_model(self._model)

    # --- contract ------------------------------------------------------------

    async def start(self, session_id: str) -> None:
        if self._session_id is not None and self._session_id != session_id:
            raise SpeechProviderError(f"provider already bound to session {self._session_id!r}")
        if self._started:
            return
        self._loop = asyncio.get_running_loop()
        self._session_id = session_id

        try:
            await self._connect_websocket()
        except Exception as exc:  # noqa: BLE001 — narrow re-raise below
            await self._release()
            if isinstance(exc, SpeechProviderError):
                raise
            raise ProviderConnectionError(f"deepgram STT connection failed: {exc}") from exc

        self._started = True
        self._queue.put_nowait(
            RecognitionStarted(session_id=session_id, provider=self.provider_name)
        )

    async def push_audio(self, session_id: str, audio: bytes) -> None:
        if self._session_id != session_id or not self._started or self._stopped:
            return
        if not audio or self._ws is None:
            return
        # Deepgram expects raw PCM16 over the WebSocket binary frames.
        try:
            await self._ws.send(audio)
        except Exception as exc:  # noqa: BLE001 — send failure tears session
            self._stopped = True
            self._emit_soon(
                RecognitionError(
                    session_id=session_id,
                    provider=self.provider_name,
                    code="send_failed",
                    message=str(exc),
                    fatal=True,
                )
            )
            self._queue.put_nowait(_STREAM_END)

    async def finish(self, session_id: str, *, grace_seconds: float = 2.5) -> None:
        """Ask Deepgram to flush the in-flight turn, then wait for closure.

        Live smoke tests call this after the last audio frame so a final
        transcript is not thrown away by an immediate `stop()`. The pipeline
        itself never needs it: Flux ends turns from its own detector.
        """
        if self._session_id != session_id or self._ws is None or self._stopped:
            return
        try:
            await self._ws.send(json.dumps({"type": "CloseStream"}))
        except Exception:  # noqa: BLE001 — stream already gone
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + grace_seconds
        while loop.time() < deadline:
            task = self._receiver_task
            if task is None or task.done():
                return
            await asyncio.sleep(0.05)

    async def stop(self, session_id: str, *, reason: str = "session_stop") -> None:
        if self._session_id != session_id or self._stopped:
            return
        self._closing = True
        self._stopped = True
        if self._receiver_task is not None:
            self._receiver_task.cancel()
            try:
                await self._receiver_task
            except asyncio.CancelledError:
                pass
            self._receiver_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001 — best-effort close
                pass
            self._ws = None
        await self._release()
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

    # --- WebSocket plumbing --------------------------------------------------

    def _build_url(self) -> str:
        """Compose the streaming URL for the configured model family."""
        base = f"encoding=linear16&sample_rate={self._format.sample_rate}&channels=1"
        if self.is_flux:
            # Flux performs its own turn detection; classic streaming-only
            # parameters (interim_results/endpointing) are rejected here.
            return f"{self._endpoint}?model={self._model}&{base}"
        return (
            f"{self._endpoint}?model={self._model}&language={self._language}"
            f"&{base}&interim_results=true&endpointing=300"
        )

    async def _connect_websocket(self) -> None:
        assert websockets is not None
        headers = {"Authorization": f"Token {self._api_key}"}

        self._ws = await asyncio.wait_for(
            websockets.connect(self._build_url(), additional_headers=headers),
            timeout=self._connection_timeout,
        )

        # Receiver task: reads messages from Deepgram and emits events.
        self._receiver_task = asyncio.create_task(self._receive_loop())

    async def _receive_loop(self) -> None:
        assert self._ws is not None
        try:
            async for message in self._ws:
                if self._stopped:
                    break
                self._handle_message(message)
        except Exception as exc:  # noqa: BLE001 — connection loss
            if not self._stopped and not self._closing:
                self._emit_soon(
                    RecognitionError(
                        session_id=self._session_id or "",
                        provider=self.provider_name,
                        code="connection_lost",
                        message=str(exc),
                        fatal=True,
                    )
                )
                self._queue.put_nowait(_STREAM_END)

    def _handle_message(self, raw: Any) -> None:
        try:
            msg = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode())
        except (ValueError, TypeError):
            return
        if not isinstance(msg, dict):
            return

        msg_type = msg.get("type")
        if msg_type == "TurnInfo":
            self._handle_turn_info(msg)
        elif msg_type == "Results":
            self._handle_classic_results(msg)
        elif msg_type in {"Error", "FatalError"}:
            self._handle_error(msg, fatal=msg_type == "FatalError")
        # "Connected" / "Configure" / "Metadata" and unknown types are ignored:
        # they carry no transcript and must never break the stream.

    def _handle_turn_info(self, msg: dict[str, Any]) -> None:
        """Map Flux turn events onto partial/final transcripts.

        ``Update`` is an in-progress hypothesis, ``EagerEndOfTurn`` is a
        *tentative* end of turn whose transcript may still be revised, and
        ``EndOfTurn`` is the committed transcript. Only ``EndOfTurn`` becomes a
        FinalTranscript — treating an eager end as final would risk acting on a
        transcript the provider may still correct.
        """
        event = msg.get("event")
        transcript = str(msg.get("transcript") or "").strip()
        if not transcript:
            return
        sequence = self._sequence_increment()
        if event == "EndOfTurn":
            self._emit_soon(
                FinalTranscript(
                    session_id=self._session_id or "",
                    provider=self.provider_name,
                    sequence=sequence,
                    text=transcript,
                    confidence=_confidence(msg.get("end_of_turn_confidence")),
                    provider_metadata={"deepgram": msg},
                )
            )
        elif event in {"Update", "EagerEndOfTurn"}:
            self._emit_soon(
                PartialTranscript(
                    session_id=self._session_id or "",
                    provider=self.provider_name,
                    sequence=sequence,
                    text=transcript,
                    confidence=_confidence(msg.get("end_of_turn_confidence")),
                    provider_metadata={"deepgram": msg},
                )
            )

    def _handle_classic_results(self, msg: dict[str, Any]) -> None:
        channel = msg.get("channel", {})
        alternatives = channel.get("alternatives", []) if isinstance(channel, dict) else []
        if not alternatives:
            return
        alt = alternatives[0]
        transcript = str(alt.get("transcript") or "").strip()
        if not transcript:
            return
        sequence = self._sequence_increment()
        event_class = FinalTranscript if msg.get("is_final", False) else PartialTranscript
        self._emit_soon(
            event_class(
                session_id=self._session_id or "",
                provider=self.provider_name,
                sequence=sequence,
                text=transcript,
                confidence=_confidence(alt.get("confidence")),
                provider_metadata={"deepgram": msg},
            )
        )

    def _handle_error(self, msg: dict[str, Any], *, fatal: bool) -> None:
        detail = (
            msg.get("error") or msg.get("message") or msg.get("description") or "deepgram error"
        )
        error = RecognitionError(
            session_id=self._session_id or "",
            provider=self.provider_name,
            code="fatal_error" if fatal else "provider_error",
            message=str(detail),
            fatal=fatal,
        )
        if not fatal:
            self._emit_soon(error)
            return
        # Fatal: the error must be observable BEFORE the stream ends, otherwise
        # teardown would silently swallow it. Both items are queued from the
        # same loop-tick callback chain (FIFO), so consumers see error → end.
        self._stopped = True
        loop = self._loop
        if loop is None:
            self._queue.put_nowait(error)
            self._queue.put_nowait(_STREAM_END)
            return
        loop.call_soon_threadsafe(self._queue.put_nowait, error)
        loop.call_soon_threadsafe(self._queue.put_nowait, _STREAM_END)

    def _emit_soon(self, event: SttEvent) -> None:
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self._queue.put_nowait, event)

    async def _release(self) -> None:
        self._ws = None

    def _sequence_increment(self) -> int:
        self._sequence += 1
        return self._sequence


def _confidence(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["DeepgramFluxSTTProvider", "is_flux_model"]
