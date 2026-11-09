"""Day 12 unit tests for the ElevenLabs TTS adapter (updated Day 18).

Day 18 replaced the guessed WebSocket protocol with ElevenLabs' documented HTTP
streaming endpoint, so these unit tests now assert the real request shape
(path, `output_format`, auth header, JSON body) and the streaming event
contract using `httpx.MockTransport` — still offline, still zero credits.

Wire-level behaviour against a real local HTTP server (chunked streaming,
cancellation, timeouts, status mapping) is covered by
`tests/integration/test_elevenlabs_protocol.py`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.domain.audio import AudioFormat
from app.domain.speech import SynthesisChunk, SynthesisCompleted, SynthesisStarted
from app.speech.base import (
    ProviderConfigurationError,
    ProviderSynthesisError,
    SpeechProviderError,
)
from app.speech.elevenlabs_tts import ElevenLabsFlashTTSProvider, pcm_output_format

_API_KEY = "test-key-not-real"
_VOICE_ID = "test-voice-id-not-real"
_MODEL = "eleven_flash_v2_5"
_FORMAT = AudioFormat(sample_rate=16000, channels=1)


def _make_settings() -> Settings:
    return Settings(
        ELEVENLABS_API_KEY=_API_KEY,
        ELEVENLABS_VOICE_ID=_VOICE_ID,
        ELEVENLABS_MODEL=_MODEL,
        SPEECH_REQUEST_TIMEOUT=15.0,
    )


def _make_provider(settings: Settings | None = None) -> ElevenLabsFlashTTSProvider:
    settings = settings or _make_settings()
    return ElevenLabsFlashTTSProvider(
        api_key=settings.ELEVENLABS_API_KEY or "",
        voice_id=settings.ELEVENLABS_VOICE_ID or "",
        model=settings.ELEVENLABS_MODEL,
        audio_format=_FORMAT,
        request_timeout=settings.SPEECH_REQUEST_TIMEOUT,
    )


def _install_transport(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """Route the adapter's httpx client through an in-process mock transport."""
    real_client = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


async def _collect(provider: ElevenLabsFlashTTSProvider, text: str) -> list[Any]:
    events: list[Any] = []
    async for event in provider.synthesize(text, "test-session", _FORMAT):
        events.append(event)
    return events


@pytest.mark.asyncio
async def test_requires_credentials_and_voice_id() -> None:
    """No API key or voice ID → ProviderConfigurationError (no silent fallback)."""
    with pytest.raises(ProviderConfigurationError):
        ElevenLabsFlashTTSProvider(
            api_key="",
            voice_id=_VOICE_ID,
            model=_MODEL,
            audio_format=_FORMAT,
            request_timeout=15.0,
        )
    with pytest.raises(ProviderConfigurationError):
        ElevenLabsFlashTTSProvider(
            api_key=_API_KEY,
            voice_id="  ",
            model=_MODEL,
            audio_format=_FORMAT,
            request_timeout=15.0,
        )


@pytest.mark.asyncio
async def test_synthesize_streams_chunks_then_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    audio = b"\x01\x00" * 1600
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200, content=audio, headers={"content-type": "application/octet-stream"}
        )

    _install_transport(monkeypatch, handler)
    provider = _make_provider()
    events = await _collect(provider, "Hello, this is a test.")

    assert captured["url"].startswith("https://api.elevenlabs.io/v1/text-to-speech/")
    assert f"/{_VOICE_ID}/stream?" in captured["url"]
    assert "output_format=pcm_16000" in captured["url"]
    assert captured["headers"]["xi-api-key"] == _API_KEY
    assert captured["body"]["text"] == "Hello, this is a test."
    assert captured["body"]["model_id"] == _MODEL
    assert captured["body"]["voice_settings"]

    assert isinstance(events[0], SynthesisStarted)
    assert events[0].provider == f"elevenlabs-flash:{_MODEL}"
    chunks = [e for e in events if isinstance(e, SynthesisChunk)]
    assert chunks and b"".join(c.payload for c in chunks) == audio
    completed = events[-1]
    assert isinstance(completed, SynthesisCompleted)
    assert completed.total_bytes == len(audio)
    assert completed.chunk_count == len(chunks)
    assert completed.cancelled is False


@pytest.mark.asyncio
async def test_empty_text_yields_no_network_request(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        calls.append(str(request.url))
        return httpx.Response(200, content=b"")

    _install_transport(monkeypatch, handler)
    provider = _make_provider()
    events = await _collect(provider, "   ")

    assert calls == []
    assert len(events) == 2
    assert isinstance(events[0], SynthesisStarted)
    assert isinstance(events[1], SynthesisCompleted)
    assert events[1].total_bytes == 0
    assert events[1].chunk_count == 0
    assert events[1].cancelled is False


@pytest.mark.asyncio
async def test_authentication_failure_maps_to_synthesis_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_transport(
        monkeypatch,
        lambda request: httpx.Response(401, json={"detail": "invalid api key"}),
    )
    provider = _make_provider()
    with pytest.raises(ProviderSynthesisError) as excinfo:
        await _collect(provider, "Hello.")
    assert "authentication failed" in str(excinfo.value)
    assert _API_KEY not in str(excinfo.value)


@pytest.mark.asyncio
async def test_empty_audio_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_transport(monkeypatch, lambda request: httpx.Response(200, content=b""))
    provider = _make_provider()
    with pytest.raises(ProviderSynthesisError) as excinfo:
        await _collect(provider, "Hello.")
    assert "empty audio response" in str(excinfo.value)


@pytest.mark.asyncio
async def test_cancel_turn_marks_completion_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    audio = b"\x02\x00" * 4000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=audio)

    _install_transport(monkeypatch, handler)
    provider = _make_provider()

    events: list[Any] = []
    utterance_id: str | None = None
    async for event in provider.synthesize("Interrupt me.", "test-session", _FORMAT):
        events.append(event)
        if isinstance(event, SynthesisStarted):
            utterance_id = event.utterance_id
        if isinstance(event, SynthesisChunk) and utterance_id is not None:
            await provider.cancel_turn("test-session", utterance_id)

    completed = events[-1]
    assert isinstance(completed, SynthesisCompleted)
    assert completed.cancelled is True


@pytest.mark.asyncio
async def test_second_session_id_is_rejected() -> None:
    provider = _make_provider()
    async for _ in provider.synthesize("", "session-a", _FORMAT):
        pass
    with pytest.raises(SpeechProviderError):
        async for _ in provider.synthesize("Hello.", "session-b", _FORMAT):
            pass


def test_provider_name_and_output_format_are_non_secret() -> None:
    provider = _make_provider()
    assert provider.provider_name == f"elevenlabs-flash:{_MODEL}"
    assert provider.output_format == "pcm_16000"
    assert _API_KEY not in provider.provider_name


def test_pcm_output_format_covers_pipeline_rates() -> None:
    assert pcm_output_format(8000) == "pcm_8000"
    assert pcm_output_format(16000) == "pcm_16000"
    assert pcm_output_format(24000) == "pcm_24000"
    with pytest.raises(ProviderConfigurationError):
        pcm_output_format(11025)


def test_settings_include_elevenlabs_fields() -> None:
    settings = _make_settings()
    assert settings.ELEVENLABS_API_KEY == _API_KEY
    assert settings.ELEVENLABS_VOICE_ID == _VOICE_ID
    assert settings.ELEVENLABS_MODEL == _MODEL
