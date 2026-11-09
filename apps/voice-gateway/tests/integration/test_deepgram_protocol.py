"""Day 18: Deepgram streaming protocol tests against a LOCAL WebSocket server.

These tests never reach Deepgram: a real `websockets` server runs in-process on
127.0.0.1 and asserts the exact wire contract the adapter speaks. That is the
strongest verification possible without spending credits, and it is what the
Day 12 unit tests could not do (they mocked `websockets.connect` and therefore
only proved the adapter agreed with itself).

Contract under test (Deepgram public streaming docs):

* Flux models (`flux-general-en`) → `wss://api.deepgram.com/v2/listen` with
  `encoding=linear16&sample_rate=<rate>&channels=1`, `TurnInfo` messages
  (`event=Update|EagerEndOfTurn|EndOfTurn`), and `{"type":"CloseStream"}` to
  flush.
* Any other model → `wss://api.deepgram.com/v1/listen` with `language`,
  `interim_results` and `endpointing`, and classic `Results` messages.

Run: `.venv/bin/python -m pytest tests/integration/test_deepgram_protocol.py -q`
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import pytest
import websockets

from app.domain.audio import AudioFormat
from app.domain.speech import (
    FinalTranscript,
    PartialTranscript,
    RecognitionError,
    RecognitionStarted,
    RecognitionStopped,
    SttEvent,
)
from app.speech.base import ProviderConfigurationError, ProviderConnectionError
from app.speech.deepgram_stt import DeepgramFluxSTTProvider, is_flux_model

_FAKE_KEY = "test-key-not-real"


@dataclass
class RecordedConnection:
    """What the fake Deepgram server observed."""

    path: str = ""
    query: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    audio_bytes: int = 0
    close_stream_received: bool = False
    messages: list[str] = field(default_factory=list)


class FakeDeepgramServer:
    """Local WebSocket server that speaks one of the two Deepgram schemas."""

    def __init__(self, *, schema: str, transcript: str = "hello world") -> None:
        self._schema = schema  # "flux" | "classic"
        self._transcript = transcript
        self.recorded: list[RecordedConnection] = []
        self._server: Any = None
        self._port: int | None = None

    @property
    def url(self) -> str:
        assert self._port is not None
        path = "/v2/listen" if self._schema == "flux" else "/v1/listen"
        return f"ws://127.0.0.1:{self._port}{path}"

    async def __aenter__(self) -> FakeDeepgramServer:
        self._server = await websockets.serve(self._handler, "127.0.0.1", 0)
        self._port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_: Any) -> None:
        self._server.close()
        await self._server.wait_closed()

    async def _handler(self, ws: Any, path: str | None = None) -> None:
        request_path = getattr(getattr(ws, "request", None), "path", path or "")
        full = request_path or ""
        path_only, _, query = full.partition("?")
        recorded = RecordedConnection(
            path=path_only,
            query=dict(item.split("=", 1) for item in query.split("&") if "=" in item),
            headers={k.lower(): str(v) for k, v in dict(ws.request.headers).items()},
        )
        self.recorded.append(recorded)

        if self._schema == "flux":
            await ws.send(json.dumps({"type": "Connected", "request_id": "test"}))
            await ws.send(json.dumps({"type": "Configure", "thresholds": {}}))

        try:
            async for message in ws:
                if isinstance(message, (bytes, bytearray)):
                    recorded.audio_bytes += len(message)
                    # Emit one in-progress hypothesis per audio frame, then a
                    # committed end-of-turn once enough audio has arrived.
                    if self._schema == "flux":
                        await ws.send(
                            json.dumps(
                                {
                                    "type": "TurnInfo",
                                    "event": "Update",
                                    "transcript": "hello",
                                    "end_of_turn_confidence": 0.4,
                                }
                            )
                        )
                        if recorded.audio_bytes >= 320:
                            await ws.send(
                                json.dumps(
                                    {
                                        "type": "TurnInfo",
                                        "event": "EagerEndOfTurn",
                                        "transcript": "hello wor",
                                        "end_of_turn_confidence": 0.6,
                                    }
                                )
                            )
                            await ws.send(
                                json.dumps(
                                    {
                                        "type": "TurnInfo",
                                        "event": "EndOfTurn",
                                        "transcript": self._transcript,
                                        "end_of_turn_confidence": 0.98,
                                    }
                                )
                            )
                    else:
                        await ws.send(
                            json.dumps(
                                {
                                    "type": "Results",
                                    "is_final": False,
                                    "channel": {
                                        "alternatives": [{"transcript": "hello", "confidence": 0.5}]
                                    },
                                }
                            )
                        )
                        if recorded.audio_bytes >= 320:
                            await ws.send(
                                json.dumps(
                                    {
                                        "type": "Results",
                                        "is_final": True,
                                        "channel": {
                                            "alternatives": [
                                                {
                                                    "transcript": self._transcript,
                                                    "confidence": 0.97,
                                                }
                                            ]
                                        },
                                    }
                                )
                            )
                else:
                    decoded = json.loads(message)
                    recorded.messages.append(str(decoded))
                    if decoded.get("type") == "CloseStream":
                        recorded.close_stream_received = True
                        break
        except websockets.exceptions.ConnectionClosed:
            pass


async def _collect(provider: DeepgramFluxSTTProvider, session: str) -> tuple[list[SttEvent], Any]:
    events: list[SttEvent] = []

    async def run() -> None:
        async for event in provider.events(session):
            events.append(event)

    task = asyncio.create_task(run())
    return events, task


def _provider(url: str, *, model: str, rate: int = 16000) -> DeepgramFluxSTTProvider:
    return DeepgramFluxSTTProvider(
        api_key=_FAKE_KEY,
        model=model,
        language="en-GB",
        audio_format=AudioFormat(sample_rate=rate, channels=1),
        connection_timeout=5.0,
        request_timeout=5.0,
        endpoint=url,
    )


def _pcm(frames: int = 4) -> bytes:
    # 20 ms of PCM16 mono at 16 kHz = 320 samples = 640 bytes per frame.
    return b"\x01\x00" * 320 * frames


@pytest.mark.asyncio
async def test_flux_model_uses_v2_endpoint_and_turn_schema() -> None:
    """flux-* must use /v2/listen with TurnInfo events (not /v1 + Results)."""
    assert is_flux_model("flux-general-en")

    async with FakeDeepgramServer(schema="flux", transcript="hello world") as server:
        provider = _provider(server.url, model="flux-general-en")
        session = "sess-flux"
        events, task = await _collect(provider, session)

        await provider.start(session)
        await provider.push_audio(session, _pcm())
        # CloseStream flushes the pending turn, then the server closes.
        await provider.finish(session, grace_seconds=2.0)
        await provider.stop(session)
        await asyncio.wait_for(task, timeout=2.0)
        events_list = list(events)

    assert len(server.recorded) == 1
    recorded = server.recorded[0]
    assert recorded.path == "/v2/listen"
    assert recorded.query["model"] == "flux-general-en"
    assert recorded.query["encoding"] == "linear16"
    assert recorded.query["sample_rate"] == "16000"
    assert recorded.query["channels"] == "1"
    # Classic-only parameters must not be sent to Flux.
    assert "interim_results" not in recorded.query
    assert "endpointing" not in recorded.query
    assert recorded.headers["authorization"] == f"Token {_FAKE_KEY}"
    assert recorded.audio_bytes == len(_pcm())
    assert recorded.close_stream_received is True

    assert isinstance(events_list[0], RecognitionStarted)
    partials = [e for e in events_list if isinstance(e, PartialTranscript)]
    finals = [e for e in events_list if isinstance(e, FinalTranscript)]
    assert [p.text for p in partials] == ["hello", "hello wor"]
    assert [f.text for f in finals] == ["hello world"]
    assert finals[0].confidence == pytest.approx(0.98)
    assert finals[0].provider_metadata["deepgram"]["event"] == "EndOfTurn"
    # Sequence numbers increase monotonically across partial/final events.
    sequences = [e.sequence for e in events_list if hasattr(e, "sequence")]
    assert sequences == sorted(sequences)
    assert isinstance(events_list[-1], RecognitionStopped)


@pytest.mark.asyncio
async def test_classic_model_uses_v1_endpoint_and_results_schema() -> None:
    """Non-Flux models keep the classic /v1/listen contract."""
    async with FakeDeepgramServer(schema="classic", transcript="book an appointment") as server:
        provider = _provider(server.url, model="nova-3")
        session = "sess-classic"
        events, task = await _collect(provider, session)

        await provider.start(session)
        await provider.push_audio(session, _pcm())
        await asyncio.sleep(0.25)
        await provider.stop(session)
        await asyncio.wait_for(task, timeout=2.0)
        events_list = list(events)

    recorded = server.recorded[0]
    assert recorded.path == "/v1/listen"
    assert recorded.query["model"] == "nova-3"
    assert recorded.query["language"] == "en-GB"
    assert recorded.query["interim_results"] == "true"
    assert recorded.query["endpointing"] == "300"

    finals = [e for e in events_list if isinstance(e, FinalTranscript)]
    partials = [e for e in events_list if isinstance(e, PartialTranscript)]
    assert [f.text for f in finals] == ["book an appointment"]
    assert partials and partials[0].text == "hello"


@pytest.mark.asyncio
async def test_flux_ignores_unknown_and_empty_messages() -> None:
    """Metadata/empty transcripts must never produce events or break the stream."""
    provider = _provider("ws://127.0.0.1:1/v2/listen", model="flux-general-en")
    provider._session_id = "sess-quiet"
    provider._loop = asyncio.get_running_loop()
    provider._started = True

    provider._handle_message(json.dumps({"type": "Metadata", "request_id": "x"}))
    provider._handle_message(json.dumps({"type": "TurnInfo", "event": "Update", "transcript": ""}))
    provider._handle_message("not json")
    provider._handle_message(json.dumps({"type": "TurnInfo", "event": "StartOfTurn"}))
    await asyncio.sleep(0.05)
    # Nothing queued: the stream must still be empty (no fabricated transcripts).
    assert provider._queue.empty()


@pytest.mark.asyncio
async def test_flux_fatal_error_tears_down_and_surfaces_error() -> None:
    provider = _provider("ws://127.0.0.1:1/v2/listen", model="flux-general-en")
    provider._session_id = "sess-fatal"
    provider._loop = asyncio.get_running_loop()
    provider._started = True

    provider._handle_message(json.dumps({"type": "FatalError", "message": "invalid credentials"}))
    await asyncio.sleep(0.05)  # _emit_soon defers to the loop
    first = provider._queue.get_nowait()
    second = provider._queue.get_nowait()

    assert isinstance(first, RecognitionError)
    assert first.fatal is True
    assert first.code == "fatal_error"
    assert "invalid credentials" in first.message
    # The stream is terminated, never continued silently.
    assert provider._stopped is True
    assert second is not None and not isinstance(second, SttEvent)


@pytest.mark.asyncio
async def test_non_fatal_error_keeps_stream_open() -> None:
    provider = _provider("ws://127.0.0.1:1/v2/listen", model="flux-general-en")
    provider._session_id = "sess-error"
    provider._loop = asyncio.get_running_loop()
    provider._started = True

    provider._handle_message(json.dumps({"type": "Error", "description": "temporary hiccup"}))
    await asyncio.sleep(0.05)  # _emit_soon defers to the loop
    event = provider._queue.get_nowait()

    assert isinstance(event, RecognitionError)
    assert event.fatal is False
    assert provider._stopped is False


@pytest.mark.asyncio
async def test_connection_failure_raises_provider_connection_error() -> None:
    """A refused connection must raise; it must never fall back to mock."""
    provider = DeepgramFluxSTTProvider(
        api_key=_FAKE_KEY,
        model="flux-general-en",
        language="en-GB",
        audio_format=AudioFormat(sample_rate=16000, channels=1),
        connection_timeout=1.0,
        request_timeout=1.0,
        endpoint="ws://127.0.0.1:1/v2/listen",  # nothing listening
    )
    with pytest.raises(ProviderConnectionError):
        await provider.start("sess-refused")


def test_missing_api_key_refuses_to_construct() -> None:
    with pytest.raises(ProviderConfigurationError):
        DeepgramFluxSTTProvider(
            api_key="  ",
            model="flux-general-en",
            language="en-GB",
            audio_format=AudioFormat(sample_rate=16000, channels=1),
            connection_timeout=1.0,
            request_timeout=1.0,
        )


@pytest.mark.asyncio
async def test_push_audio_before_start_is_ignored() -> None:
    provider = _provider("ws://127.0.0.1:1/v2/listen", model="flux-general-en")
    provider._session_id = "sess-idle"
    await provider.push_audio("sess-idle", b"\x00\x00" * 10)  # must not raise
    assert provider._queue.empty()
