"""Day 18: ElevenLabs TTS protocol tests against a LOCAL HTTP server.

No request ever reaches ElevenLabs. A real uvicorn server binds 127.0.0.1 and
serves the documented streaming endpoint, so these tests verify the actual wire
contract: path, query, auth header, JSON body, chunked PCM streaming, error
mapping, cancellation, timeouts and cleanup.

Contract under test (ElevenLabs public docs):

    POST /v1/text-to-speech/{voice_id}/stream?output_format=pcm_16000
    headers: xi-api-key, content-type: application/json
    body:    {"text": ..., "model_id": ..., "voice_settings": {...}}
    response: chunked raw PCM16LE mono at the requested rate

Run: `.venv/bin/python -m pytest tests/integration/test_elevenlabs_protocol.py -q`
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.domain.audio import AudioFormat
from app.domain.speech import SynthesisChunk, SynthesisCompleted, SynthesisStarted
from app.speech.base import ProviderConfigurationError, ProviderSynthesisError
from app.speech.elevenlabs_tts import ElevenLabsFlashTTSProvider, pcm_output_format

_FAKE_KEY = "test-key-not-real"
_VOICE_ID = "testVoiceId0000000001"
_MODEL = "eleven_flash_v2_5"


class FakeElevenLabsServer:
    """Local server that records requests and replays a scripted response."""

    def __init__(
        self,
        *,
        status: int = 200,
        chunks: tuple[bytes, ...] = (b"\x01\x00" * 800,),
        chunk_delay: float = 0.0,
        stall_seconds: float = 0.0,
    ) -> None:
        self.status = status
        self.chunks = chunks
        self.chunk_delay = chunk_delay
        self.stall_seconds = stall_seconds
        self.requests: list[dict[str, Any]] = []
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None
        self._port: int | None = None
        self._app = self._build_app()

    @property
    def base_url(self) -> str:
        assert self._port is not None
        return f"http://127.0.0.1:{self._port}/v1/text-to-speech"

    def _build_app(self) -> FastAPI:
        app = FastAPI()

        @app.post("/v1/text-to-speech/{voice_id}/stream")
        async def synth(voice_id: str, request: Request) -> Any:
            body = await request.json()
            self.requests.append(
                {
                    "path": request.url.path,
                    "query": dict(request.query_params),
                    "headers": {k.lower(): v for k, v in request.headers.items()},
                    "body": body,
                    "voice_id": voice_id,
                }
            )
            if self.status != 200:
                return JSONResponse(
                    status_code=self.status,
                    content={"detail": {"status": "error", "message": "scripted failure"}},
                )

            async def stream() -> Any:
                if self.stall_seconds:
                    await asyncio.sleep(self.stall_seconds)
                for chunk in self.chunks:
                    yield chunk
                    if self.chunk_delay:
                        await asyncio.sleep(self.chunk_delay)

            return StreamingResponse(stream(), media_type="application/octet-stream")

        return app

    async def __aenter__(self) -> FakeElevenLabsServer:
        config = uvicorn.Config(self._app, host="127.0.0.1", port=0, log_level="error")
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())
        deadline = asyncio.get_running_loop().time() + 10
        while not self._server.started:
            if asyncio.get_running_loop().time() > deadline:
                raise RuntimeError("local test server did not start")
            await asyncio.sleep(0.01)
        self._port = self._server.servers[0].sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_: Any) -> None:
        assert self._server is not None
        self._server.should_exit = True
        if self._task is not None:
            await asyncio.wait_for(self._task, timeout=10)


def _provider(
    base_url: str,
    *,
    rate: int = 16000,
    request_timeout: float = 5.0,
) -> ElevenLabsFlashTTSProvider:
    return ElevenLabsFlashTTSProvider(
        api_key=_FAKE_KEY,
        voice_id=_VOICE_ID,
        model=_MODEL,
        audio_format=AudioFormat(sample_rate=rate, channels=1),
        request_timeout=request_timeout,
        endpoint=base_url,
    )


async def _drain(provider: ElevenLabsFlashTTSProvider, text: str) -> list[Any]:
    events: list[Any] = []
    async for event in provider.synthesize(text, "sess-tts", provider._format):
        events.append(event)
    return events


@pytest.mark.asyncio
async def test_streams_documented_request_and_pcm_chunks() -> None:
    chunks = (b"\x11\x00" * 400, b"\x22\x00" * 400, b"\x33\x00" * 400)
    async with FakeElevenLabsServer(chunks=chunks) as server:
        provider = _provider(server.base_url)
        events = await _drain(provider, "Thank you for calling your GP practice.")

    assert len(server.requests) == 1
    request = server.requests[0]
    assert request["path"] == f"/v1/text-to-speech/{_VOICE_ID}/stream"
    assert request["voice_id"] == _VOICE_ID
    assert request["query"] == {"output_format": "pcm_16000"}
    assert request["headers"]["xi-api-key"] == _FAKE_KEY
    assert request["headers"]["content-type"].startswith("application/json")
    assert request["body"]["text"] == "Thank you for calling your GP practice."
    assert request["body"]["model_id"] == _MODEL
    assert "voice_settings" in request["body"]

    assert isinstance(events[0], SynthesisStarted)
    audio = b"".join(e.payload for e in events if isinstance(e, SynthesisChunk))
    assert audio == b"".join(chunks)
    completed = events[-1]
    assert isinstance(completed, SynthesisCompleted)
    assert completed.total_bytes == sum(len(c) for c in chunks)
    assert completed.chunk_count == len(chunks)
    assert completed.cancelled is False


@pytest.mark.asyncio
async def test_authentication_failure_surfaces_and_does_not_retry() -> None:
    async with FakeElevenLabsServer(status=401) as server:
        provider = _provider(server.base_url)
        with pytest.raises(ProviderSynthesisError) as excinfo:
            await _drain(provider, "Short utterance.")

    assert "authentication failed" in str(excinfo.value)
    assert "ELEVENLABS_API_KEY" in str(excinfo.value)
    # Exactly one attempt: no retry storm against a rejected credential.
    assert len(server.requests) == 1


@pytest.mark.asyncio
async def test_payment_required_is_reported_as_a_credit_problem() -> None:
    async with FakeElevenLabsServer(status=402) as server:
        provider = _provider(server.base_url)
        with pytest.raises(ProviderSynthesisError) as excinfo:
            await _drain(provider, "Short utterance.")
    assert "payment required" in str(excinfo.value)
    assert len(server.requests) == 1


@pytest.mark.asyncio
async def test_unknown_voice_id_is_reported_with_the_configured_id() -> None:
    async with FakeElevenLabsServer(status=404) as server:
        provider = _provider(server.base_url)
        with pytest.raises(ProviderSynthesisError) as excinfo:
            await _drain(provider, "Short utterance.")
    message = str(excinfo.value)
    assert "ELEVENLABS_VOICE_ID" in message
    assert _VOICE_ID in message  # the identifier is not secret


@pytest.mark.asyncio
async def test_empty_audio_response_is_a_failure_not_a_silent_success() -> None:
    async with FakeElevenLabsServer(chunks=()) as server:
        provider = _provider(server.base_url)
        with pytest.raises(ProviderSynthesisError) as excinfo:
            await _drain(provider, "Short utterance.")
    assert "empty audio response" in str(excinfo.value)


@pytest.mark.asyncio
async def test_timeout_raises_provider_synthesis_error() -> None:
    async with FakeElevenLabsServer(stall_seconds=1.5) as server:
        provider = _provider(server.base_url, request_timeout=0.25)
        with pytest.raises(ProviderSynthesisError) as excinfo:
            await _drain(provider, "Short utterance.")
    assert "transport failure" in str(excinfo.value)


@pytest.mark.asyncio
async def test_cancellation_stops_streaming_and_marks_completed() -> None:
    """Barge-in hook: cancelling mid-stream yields a `cancelled` completion."""
    chunks = (b"\x01\x00" * 400,) * 8
    async with FakeElevenLabsServer(chunks=chunks, chunk_delay=0.05) as server:
        provider = _provider(server.base_url)
        collected: list[Any] = []
        utterance_id: str | None = None
        async for event in provider.synthesize(
            "Longer utterance to interrupt.", "sess-cancel", provider._format
        ):
            collected.append(event)
            if isinstance(event, SynthesisStarted):
                utterance_id = event.utterance_id
            if isinstance(event, SynthesisChunk) and utterance_id is not None:
                await provider.cancel_turn("sess-cancel", utterance_id)

    completed = collected[-1]
    assert isinstance(completed, SynthesisCompleted)
    assert completed.cancelled is True
    assert completed.total_bytes <= sum(len(c) for c in chunks)


def test_unsupported_sample_rate_is_refused() -> None:
    """The pipeline never resamples silently, so a bad rate must fail fast.

    `AudioFormat` itself only accepts 8/16/24 kHz, and ElevenLabs supports all
    three, so this guard is defensive: it keeps the mapping honest if a future
    pipeline rate has no matching PCM output format.
    """
    with pytest.raises(ProviderConfigurationError):
        pcm_output_format(11025)
    with pytest.raises(ProviderConfigurationError):
        pcm_output_format(44101)


@pytest.mark.parametrize(
    "rate,expected", [(8000, "pcm_8000"), (16000, "pcm_16000"), (24000, "pcm_24000")]
)
def test_pcm_output_format_maps_negotiated_rate(rate: int, expected: str) -> None:
    assert pcm_output_format(rate) == expected


def test_missing_credentials_refuse_to_construct() -> None:
    with pytest.raises(ProviderConfigurationError):
        ElevenLabsFlashTTSProvider(
            api_key=" ",
            voice_id=_VOICE_ID,
            model=_MODEL,
            audio_format=AudioFormat(sample_rate=16000, channels=1),
            request_timeout=5.0,
        )
    with pytest.raises(ProviderConfigurationError):
        ElevenLabsFlashTTSProvider(
            api_key=_FAKE_KEY,
            voice_id="",
            model=_MODEL,
            audio_format=AudioFormat(sample_rate=16000, channels=1),
            request_timeout=5.0,
        )


@pytest.mark.asyncio
async def test_empty_text_yields_no_request() -> None:
    async with FakeElevenLabsServer() as server:
        provider = _provider(server.base_url)
        events = await _drain(provider, "   ")
    assert len(server.requests) == 0
    assert isinstance(events[0], SynthesisStarted)
    assert isinstance(events[1], SynthesisCompleted)
    assert events[1].total_bytes == 0


@pytest.mark.asyncio
async def test_api_key_never_appears_in_events() -> None:
    """Events are logged/forwarded, so the key must never leak into them."""
    async with FakeElevenLabsServer() as server:
        provider = _provider(server.base_url)
        events = await _drain(provider, "Short utterance.")
    assert provider.provider_name == f"elevenlabs-flash:{_MODEL}"
    assert _FAKE_KEY not in provider.provider_name
    serialised = json.dumps([json.loads(e.model_dump_json()) for e in events], default=str)
    assert _FAKE_KEY not in serialised
    assert _VOICE_ID not in serialised  # voice id is not secret, but not needed in events
