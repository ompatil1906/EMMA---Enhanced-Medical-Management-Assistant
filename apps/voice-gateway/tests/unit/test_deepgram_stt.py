"""Day 12 Deepgram STT unit tests (updated Day 18).

Fast, offline coverage of the adapter's parsing/mapping layer:

* Flux `TurnInfo` messages → partial (`Update`, `EagerEndOfTurn`) and final
  (`EndOfTurn`) transcripts.
* Classic `Results` messages (non-Flux models) → partial/final.
* Error mapping, provider name, model→endpoint selection.
* No silent fallback / credential guards.

Day 18 finding: a Flux model must use `/v2/listen` with the turn schema, and
these tests now encode exactly that. Full wire-level verification (real local
WebSocket server: URL, query, headers, binary frames, CloseStream, teardown) is
in `tests/integration/test_deepgram_protocol.py`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.core.config import Settings
from app.domain.audio import AudioFormat
from app.domain.speech import FinalTranscript, PartialTranscript, RecognitionError
from app.speech.base import ProviderConfigurationError
from app.speech.deepgram_stt import DeepgramFluxSTTProvider


def _make_settings() -> Settings:
    return Settings(
        DEEPGRAM_API_KEY="test-key-not-real",
        DEEPGRAM_MODEL="flux-general-en",
        AZURE_SPEECH_LANGUAGE="en-GB",
        SPEECH_CONNECTION_TIMEOUT=10.0,
        SPEECH_REQUEST_TIMEOUT=15.0,
    )


def _make_provider(settings: Settings | None = None) -> DeepgramFluxSTTProvider:
    settings = settings or _make_settings()
    return DeepgramFluxSTTProvider(
        api_key=settings.DEEPGRAM_API_KEY or "",
        model=settings.DEEPGRAM_MODEL,
        language=settings.AZURE_SPEECH_LANGUAGE,
        audio_format=AudioFormat(sample_rate=16000, channels=1),
        connection_timeout=settings.SPEECH_CONNECTION_TIMEOUT,
        request_timeout=settings.SPEECH_REQUEST_TIMEOUT,
    )


def _results_message(transcript: str, is_final: bool, confidence: float | None = 0.95) -> str:
    return json.dumps(
        {
            "type": "Results",
            "channel": {
                "alternatives": [
                    {
                        "transcript": transcript,
                        "confidence": confidence,
                    }
                ]
            },
            "is_final": is_final,
        }
    )


def _turn_info(event: str, transcript: str, confidence: float | None = 0.9) -> str:
    payload: dict[str, Any] = {"type": "TurnInfo", "event": event, "transcript": transcript}
    if confidence is not None:
        payload["end_of_turn_confidence"] = confidence
    return json.dumps(payload)


def _error_message(message: str = "deepgram error") -> str:
    return json.dumps({"type": "Error", "error": message})


@pytest.mark.asyncio
async def test_deepgram_provider_requires_credentials() -> None:
    """No API key → ProviderConfigurationError (no silent fallback)."""
    settings = _make_settings()
    settings.DEEPGRAM_API_KEY = None
    with pytest.raises(ProviderConfigurationError):
        DeepgramFluxSTTProvider(
            api_key="",
            model=settings.DEEPGRAM_MODEL,
            language=settings.AZURE_SPEECH_LANGUAGE,
            audio_format=AudioFormat(sample_rate=16000, channels=1),
            connection_timeout=10.0,
            request_timeout=15.0,
        )


@pytest.mark.asyncio
async def test_flux_turn_info_emits_partial_and_final() -> None:
    """Flux: Update/EagerEndOfTurn are partials, only EndOfTurn is final."""
    provider = _make_provider()  # flux-general-en
    events: list[Any] = []

    provider._session_id = "test-session"
    provider._loop = asyncio.get_running_loop()
    provider._started = True

    provider._handle_message(_turn_info("Update", "i would like"))
    provider._handle_message(_turn_info("EagerEndOfTurn", "i would like to book"))
    provider._handle_message(_turn_info("EndOfTurn", "I would like to book.", 0.97))

    async def collect() -> None:
        async for event in provider.events("test-session"):
            events.append(event)

    collect_task = asyncio.create_task(collect())
    await asyncio.sleep(0.2)
    collect_task.cancel()

    partials = [e for e in events if isinstance(e, PartialTranscript)]
    finals = [e for e in events if isinstance(e, FinalTranscript)]
    assert [p.text for p in partials] == ["i would like", "i would like to book"]
    assert [f.text for f in finals] == ["I would like to book."]
    assert finals[0].confidence == pytest.approx(0.97)
    assert finals[0].provider_metadata["deepgram"]["event"] == "EndOfTurn"


@pytest.mark.asyncio
async def test_classic_results_emit_partial_and_final() -> None:
    provider = DeepgramFluxSTTProvider(
        api_key="test-key-not-real",
        model="nova-3",
        language="en-GB",
        audio_format=AudioFormat(sample_rate=16000, channels=1),
        connection_timeout=10.0,
        request_timeout=15.0,
    )
    events: list[Any] = []

    async def collect() -> None:
        async for event in provider.events("test-session"):
            events.append(event)

    # Set up session state without connecting to a real WebSocket.
    provider._session_id = "test-session"
    provider._loop = asyncio.get_running_loop()
    provider._started = True

    # Emit a partial result.
    provider._handle_message(_results_message("i would like", is_final=False))
    # Emit a final result.
    provider._handle_message(_results_message("i would like to book", is_final=True))

    # Collect events with a short timeout.
    collect_task = asyncio.create_task(collect())
    await asyncio.sleep(0.2)
    collect_task.cancel()

    finals = [e for e in events if isinstance(e, FinalTranscript)]
    partials = [e for e in events if isinstance(e, PartialTranscript)]

    assert len(finals) == 1
    assert finals[0].text == "i would like to book"
    assert len(partials) == 1
    assert partials[0].text == "i would like"


@pytest.mark.asyncio
async def test_deepgram_error_message_emits_recognition_error() -> None:
    provider = _make_provider()
    events: list[Any] = []

    async def collect() -> None:
        async for event in provider.events("test-session"):
            events.append(event)

    provider._session_id = "test-session"
    provider._loop = asyncio.get_running_loop()
    provider._started = True

    provider._handle_message(_error_message("api key invalid"))

    collect_task = asyncio.create_task(collect())
    await asyncio.sleep(0.2)
    collect_task.cancel()

    errors = [e for e in events if isinstance(e, RecognitionError)]
    assert len(errors) == 1
    assert errors[0].code == "provider_error"
    assert "api key invalid" in errors[0].message


def test_deepgram_provider_name() -> None:
    provider = _make_provider()
    assert provider.provider_name == "deepgram-flux:flux-general-en"
    assert "test-key-not-real" not in provider.provider_name


def test_models_select_the_documented_endpoint() -> None:
    """Flux models use /v2/listen; classic models use /v1/listen."""
    flux = _make_provider()
    assert flux.is_flux is True
    flux_url = flux._build_url()
    assert flux_url.startswith("wss://api.deepgram.com/v2/listen?")
    assert "model=flux-general-en" in flux_url
    assert "encoding=linear16" in flux_url
    assert "sample_rate=16000" in flux_url
    assert "interim_results" not in flux_url
    assert "endpointing" not in flux_url

    classic = DeepgramFluxSTTProvider(
        api_key="test-key-not-real",
        model="nova-3",
        language="en-GB",
        audio_format=AudioFormat(sample_rate=16000, channels=1),
        connection_timeout=10.0,
        request_timeout=15.0,
    )
    classic_url = classic._build_url()
    assert classic_url.startswith("wss://api.deepgram.com/v1/listen?")
    assert "language=en-GB" in classic_url
    assert "interim_results=true" in classic_url
    assert "endpointing=300" in classic_url


@pytest.mark.asyncio
async def test_deepgram_push_audio_before_start_is_ignored() -> None:
    provider = _make_provider()
    # Should not raise — audio pushed before start is silently dropped.
    await provider.push_audio("test-session", b"\x00\x00" * 320)
    await provider.stop("test-session")


def test_settings_include_deepgram_fields() -> None:
    settings = _make_settings()
    assert settings.DEEPGRAM_API_KEY == "test-key-not-real"
    assert settings.DEEPGRAM_MODEL == "flux-general-en"
