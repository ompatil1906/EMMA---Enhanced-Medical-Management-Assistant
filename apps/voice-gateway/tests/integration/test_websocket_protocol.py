"""WebSocket protocol-robustness integration tests (malformed frames, limits, negotiation)."""

import base64
import json

import pytest
from websockets.asyncio.client import connect

from tests.conftest import (
    audio_frame,
    media_message,
    open_stream,
    receive_json,
    receive_payload,
    start_message,
    wait_closed,
)


async def test_malformed_frames_tolerated_and_session_recovers(server_url: dict[str, str]) -> None:
    async with connect(server_url["ws"]) as ws:
        for _ in range(4):  # budget default is 5 — all tolerated
            await ws.send("this-is-not-json")
        await open_stream(ws)
        await ws.send(media_message(audio_frame()))
        assert await receive_payload(ws) == audio_frame()
        await ws.send('{"event":"stop"}')
        code, _ = await wait_closed(ws)
        assert code == 1000


@pytest.mark.parametrize("server_url", [{"MAX_CONSECUTIVE_ERRORS": "2"}], indirect=True)
async def test_malformed_frame_budget_exhausted_closes_1003(server_url: dict[str, str]) -> None:
    async with connect(server_url["ws"]) as ws:
        await ws.send("bad-frame-1")
        await ws.send("bad-frame-2")
        code, reason = await wait_closed(ws)
        assert (code, reason) == (1003, "protocol error tolerance exhausted")


@pytest.mark.parametrize(
    "server_url", [{"EXOTEL_MAX_CHUNK_BYTES": "512", "MAX_WS_MESSAGE_BYTES": "1024"}], indirect=True
)
async def test_oversized_message_immediately_rejected_1009(server_url: dict[str, str]) -> None:
    async with connect(server_url["ws"]) as ws:
        await open_stream(ws)  # start envelope (≈270 B) fits the 1024 B limit
        await ws.send(json.dumps({"event": "media", "chunk": "A" * 2000}))
        code, reason = await wait_closed(ws)
        assert (code, reason) == (1009, "message size limit exceeded")


async def test_unknown_and_extended_events_never_crash_session(server_url: dict[str, str]) -> None:
    async with connect(server_url["ws"]) as ws:
        await open_stream(ws)
        await ws.send('{"event":"symphony","anything":1}')  # unknown → tolerated
        await ws.send('{"event":"mark","name":"playback-1"}')  # known, applet-dependent
        await ws.send('{"event":"dtmf","digit":"5","duration":100}')
        await ws.send(media_message(audio_frame()))
        assert await receive_payload(ws) == audio_frame()
        await ws.send('{"event":"stop"}')
        code, _ = await wait_closed(ws)
        assert code == 1000


async def test_media_without_chunk_tolerated_with_empty_echo(server_url: dict[str, str]) -> None:
    async with connect(server_url["ws"]) as ws:
        await open_stream(ws)
        await ws.send('{"event":"media"}')
        assert await receive_payload(ws) == b""  # graceful echo of an empty chunk
        await ws.send(media_message(audio_frame()))
        assert await receive_payload(ws) == audio_frame()
        await ws.send('{"event":"stop"}')
        await wait_closed(ws)


async def test_invalid_base64_tolerated_then_recovery(server_url: dict[str, str]) -> None:
    async with connect(server_url["ws"]) as ws:
        await open_stream(ws)
        await ws.send(json.dumps({"event": "media", "chunk": "@@@not-base64@@@"}))
        await ws.send(media_message(audio_frame()))
        assert await receive_payload(ws) == audio_frame()
        await ws.send('{"event":"stop"}')
        code, _ = await wait_closed(ws)
        assert code == 1000


async def test_binary_frame_tolerated_then_recovery(server_url: dict[str, str]) -> None:
    async with connect(server_url["ws"]) as ws:
        await open_stream(ws)
        await ws.send(b"\x00\x01binary-frame")  # non-text frame → tolerated
        await ws.send(media_message(audio_frame()))
        assert await receive_payload(ws) == audio_frame()
        await ws.send('{"event":"stop"}')
        await wait_closed(ws)


async def test_start_without_stream_sid_still_hears_frames(server_url: dict[str, str]) -> None:
    async with connect(server_url["ws"]) as ws:
        await ws.send('{"event":"connected"}')
        await ws.send(start_message(stream_sid=None))
        await ws.send(media_message(audio_frame()))
        reply = await receive_json(ws)
        assert sorted(reply.keys()) == ["event", "media"]  # no stream_sid yet → omitted
        assert base64.b64decode(reply["media"]["payload"]) == audio_frame()
        await ws.send('{"event":"stop"}')
        code, _ = await wait_closed(ws)
        assert code == 1000


async def test_start_with_unsupported_sample_rate_rejected_1008(server_url: dict[str, str]) -> None:
    async with connect(server_url["ws"]) as ws:
        await ws.send('{"event":"connected"}')
        await ws.send(start_message(sample_rate=44100))
        code, reason = await wait_closed(ws)
        assert (code, reason) == (1008, "unsupported media format")


class TestQuerySampleRateNegotiation:
    async def test_sample_rate_query_param_accepted_and_first_frame_echoes(
        self, server_url: dict[str, str]
    ) -> None:
        async with connect(server_url["ws"] + "?sample-rate=16000") as ws:
            body = json.loads(start_message())
            del body["media_format"]  # query param supplies the rate instead
            await ws.send('{"event":"connected"}')
            await ws.send(json.dumps(body))
            await ws.send(media_message(audio_frame()))
            reply = await receive_json(ws)
            assert sorted(reply.keys()) == ["event", "media", "stream_sid"]
            assert base64.b64decode(reply["media"]["payload"]) == audio_frame()
            await ws.send('{"event":"stop"}')
            await wait_closed(ws)

    async def test_unsupported_sample_rate_query_param_rejected_1008(
        self, server_url: dict[str, str]
    ) -> None:
        async with connect(server_url["ws"] + "?sample-rate=44100") as ws:
            code, reason = await wait_closed(ws)
            assert (code, reason) == (1008, "unsupported sample rate")

    async def test_non_numeric_sample_rate_query_param_rejected_1008(
        self, server_url: dict[str, str]
    ) -> None:
        async with connect(server_url["ws"] + "?sample-rate=nonsense") as ws:
            code, reason = await wait_closed(ws)
            assert (code, reason) == (1008, "unsupported sample rate")

    async def test_start_media_format_overrides_query_param(
        self, server_url: dict[str, str]
    ) -> None:
        # start.media_format wins over the query fallback when both are present.
        async with connect(server_url["ws"] + "?sample-rate=16000") as ws:
            await open_stream(ws)  # start carries media_format 8000 → authoritative
            await ws.send(media_message(audio_frame()))
            assert await receive_payload(ws) == audio_frame()
            await ws.send('{"event":"stop"}')
            await wait_closed(ws)


async def test_stop_reason_entropy_does_not_change_close_code(server_url: dict[str, str]) -> None:
    async with connect(server_url["ws"]) as ws:
        await open_stream(ws)
        await ws.send('{"event":"stop","reason":"stopped"}')
        code, reason = await wait_closed(ws)
        assert (code, reason) == (1000, "stop")
