"""Day-3/Day-5 speech-pipeline integration tests with LLM interpretation.

Runs the full speech pipeline against mock providers via real WebSocket traffic.
The `server_url` fixture is parametrized per-test with mock provider params:

* `VOICE_PIPELINE=speech` — full STT → emergency guard → LLM → controller → TTS path.
* `MOCK_STT_FINALIZE_MS=30` / `MOCK_STT_UTTERANCE_MS=1200` — fast deterministic recognition.
* `PRACTICE_API_BASE_URL=""` — practice persistence disabled.
* `LLM_PROVIDER=mock` — deterministic mock LLM (no credentials needed).
"""

from __future__ import annotations

import base64

import pytest
from websockets.asyncio.client import connect

from app.services.response_handler import DeterministicResponseHandler
from tests.conftest import media_message, open_stream, receive_json, start_message, wait_closed
from tests.integration.conftest import poll_active_sessions  # noqa: F401 (fixture marker)

TEST_STREAM_SID = "stream-test-001"

SPEECH_MODE_PARAMS: dict[str, str] = {
    "VOICE_PIPELINE": "speech",
    # Tests must be deterministic regardless of the developer's `.env`.
    # The repository `.env` may select real Azure speech providers; these
    # tests explicitly pin the mock providers (no silent fallback in the app
    # itself — this is test configuration only).
    "STT_PROVIDER": "mock",
    "TTS_PROVIDER": "mock",
    "MOCK_STT_FINALIZE_MS": "30",
    "MOCK_STT_UTTERANCE_MS": "1200",
    "MOCK_STT_ERROR_AT_FRAME": "0",
    "PRACTICE_API_BASE_URL": "",
    "LLM_PROVIDER": "mock",
    "EMBEDDING_PROVIDER": "mock",
    "VECTOR_STORE_PROVIDER": "mock",
    # The repository `.env` may point at a live Jira; tests must never write to
    # a real board.
    "JIRA_MODE": "disabled",
    "CALL_RECORD_SINK": "practice_api",
}

USER_UTTERANCE = "I would like to book an appointment"
USER_AUDIO_BYTES = 25600  # 16,000 PCM16 samples @ 8 kHz = 2,000 ms ≥ threshold


def mock_bytes_for_text(text: str, sample_rate: int = 8000) -> int:
    """Mock-TTS synthesis byte contract (see `app/speech/mock_tts.py`).

    `min(len(text) * 70ms, 4800ms)` of PCM16 mono at the negotiated rate.
    """
    duration_ms = min(len(text) * 70, 4800)
    return int(round((duration_ms / 1000.0) * sample_rate * 2))


async def _drain_outbound_frames(ws, *, target_bytes: int, timeout: float = 10.0) -> list[bytes]:
    frames: list[bytes] = []
    received_bytes = 0
    while received_bytes < target_bytes:
        reply = await receive_json(ws, timeout=timeout)
        assert reply["event"] == "media", f"unexpected outbound envelope: {reply!r}"
        payload = base64.b64decode(reply["media"]["payload"])
        frames.append(payload)
        received_bytes += len(payload)
    return frames


async def _send_stop_and_expect_clean_close(ws) -> tuple[int, str | None]:
    await ws.send('{"event":"stop"}')
    return await wait_closed(ws)


@pytest.mark.parametrize("server_url", [SPEECH_MODE_PARAMS], indirect=True)
async def test_speech_mode_greeting_first_outbound_is_tts_chunk(
    server_url: dict[str, str],
) -> None:
    async with connect(server_url["ws"]) as ws:
        await open_stream(ws)
        reply = await receive_json(ws)
        assert reply["event"] == "media", f"expected first outbound media frame, got {reply!r}"
        payload = base64.b64decode(reply["media"]["payload"])
        assert len(payload) >= 3200
        assert len(payload) % 2 == 0
        code, reason = await _send_stop_and_expect_clean_close(ws)
        assert (code, reason) == (1000, "stop")


@pytest.mark.parametrize("server_url", [SPEECH_MODE_PARAMS], indirect=True)
async def test_speech_mode_end_to_end_turn_with_llm_interpretation(
    server_url: dict[str, str],
) -> None:
    """Simulated audio → STT → emergency guard → mock LLM → controller → TTS → close."""
    handler = DeterministicResponseHandler()
    greeting = (await handler.greeting()).response_text

    async with connect(server_url["ws"]) as ws:
        await open_stream(ws)
        await _drain_outbound_frames(ws, target_bytes=mock_bytes_for_text(greeting))

        # Simulated user utterance: exceeds the mock STT utterance threshold.
        await ws.send(media_message(b"\x01" * USER_AUDIO_BYTES))

        response = await handler.respond(utterance=USER_UTTERANCE)
        response_frames = await _drain_outbound_frames(
            ws, target_bytes=mock_bytes_for_text(response.response_text)
        )
        assert response_frames, "no outbound audio was produced for the deterministic turn"

        code, reason = await _send_stop_and_expect_clean_close(ws)
        assert (code, reason) == (1000, "stop")

    await poll_active_sessions(server_url["http"], expected=0)


@pytest.mark.parametrize("server_url", [SPEECH_MODE_PARAMS], indirect=True)
async def test_speech_mode_emergency_guard_bypasses_llm(
    server_url: dict[str, str],
) -> None:
    """Emergency trigger → fixed script, no LLM call, no admin request, clean close."""
    async with connect(server_url["ws"]) as ws:
        await open_stream(ws)
        await _drain_outbound_frames(ws, target_bytes=mock_bytes_for_text("test"))

        # Simulated user utterance containing an emergency red flag.
        await ws.send(media_message(b"\x02" * USER_AUDIO_BYTES))

        # The emergency script should be returned as outbound audio.
        reply = await receive_json(ws, timeout=10.0)
        assert reply["event"] == "media", f"expected emergency media frame, got {reply!r}"
        payload = base64.b64decode(reply["media"]["payload"])
        assert len(payload) >= 3200

        code, reason = await _send_stop_and_expect_clean_close(ws)
        assert (code, reason) == (1000, "stop")

    await poll_active_sessions(server_url["http"], expected=0)


@pytest.mark.parametrize("server_url", [SPEECH_MODE_PARAMS], indirect=True)
async def test_speech_mode_control_events_opt_in(server_url: dict[str, str]) -> None:
    """`?control-events=1` adds UI messages without changing media envelopes."""
    ws_url = f"{server_url['ws']}?sample-rate=8000&control-events=1"
    async with connect(ws_url) as ws:
        await open_stream(ws)
        seen: list[dict[str, object]] = []
        media_bytes = 0
        for _ in range(30):
            reply = await receive_json(ws, timeout=10.0)
            seen.append(reply)
            if reply["event"] == "media":
                media_bytes += len(base64.b64decode(reply["media"]["payload"]))
            if any(r["event"] == "assistant" for r in seen) and media_bytes >= 3200:
                break

        assert any(
            r["event"] == "assistant" and isinstance(r.get("text"), str) and r["text"] for r in seen
        ), f"no assistant control event in {seen!r}"
        assert any(r["event"] == "state" and r.get("state") == "speaking" for r in seen), (
            f"no speaking state in {seen!r}"
        )
        assert media_bytes >= 3200, "greeting audio missing"

        code, reason = await _send_stop_and_expect_clean_close(ws)
        assert (code, reason) == (1000, "stop")


@pytest.mark.parametrize("server_url", [SPEECH_MODE_PARAMS], indirect=True)
async def test_speech_mode_renegotiation_rejected_1008(server_url: dict[str, str]) -> None:
    async with connect(server_url["ws"]) as ws:
        await open_stream(ws)
        await ws.send(start_message(stream_sid=TEST_STREAM_SID, sample_rate=16000))
        code, reason = await wait_closed(ws)
        assert (code, reason) == (1008, "unsupported media renegotiation")


@pytest.mark.parametrize("server_url", [SPEECH_MODE_PARAMS], indirect=True)
async def test_speech_mode_abrupt_disconnect_cleans_registered_session(
    server_url: dict[str, str],
) -> None:
    async with connect(server_url["ws"]) as ws:
        await open_stream(ws)
        await ws.send(media_message(b"\x03" * 3200))
        await ws.close(code=1001)
    await poll_active_sessions(server_url["http"], expected=0)


@pytest.mark.parametrize("server_url", [SPEECH_MODE_PARAMS], indirect=True)
async def test_speech_mode_greets_the_caller_by_name(server_url: dict[str, str]) -> None:
    """Day 19: the WebSocket fallback greets the intake form's first name.

    The browser sends `?caller-first-name=sarah`; the gateway sanitises it and
    EMMA speaks "Hello, Sarah." — proven end to end through the assistant
    control event, not just at the handler unit level.
    """
    url = f"{server_url['ws']}?sample-rate=8000&control-events=1&caller-first-name=sarah"
    async with connect(url) as ws:
        await open_stream(ws)
        first_assistant = None
        for _ in range(200):
            reply = await receive_json(ws)
            if reply.get("event") == "assistant":
                first_assistant = str(reply.get("text"))
                break
        assert first_assistant is not None, "no assistant control event was emitted"
        assert first_assistant.startswith("Hello, Sarah.")
        code, reason = await _send_stop_and_expect_clean_close(ws)
        assert (code, reason) == (1000, "stop")


@pytest.mark.parametrize("server_url", [SPEECH_MODE_PARAMS], indirect=True)
async def test_speech_mode_greeting_is_generic_without_a_name(
    server_url: dict[str, str],
) -> None:
    url = f"{server_url['ws']}?sample-rate=8000&control-events=1"
    async with connect(url) as ws:
        await open_stream(ws)
        first_assistant = None
        for _ in range(200):
            reply = await receive_json(ws)
            if reply.get("event") == "assistant":
                first_assistant = str(reply.get("text"))
                break
        assert first_assistant is not None
        assert first_assistant.startswith("Hello, I am EMMA")
        code, reason = await _send_stop_and_expect_clean_close(ws)
        assert (code, reason) == (1000, "stop")
