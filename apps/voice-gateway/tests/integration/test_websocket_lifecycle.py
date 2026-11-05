"""WebSocket session lifecycle integration tests (real uvicorn + real close codes)."""

import httpx
from websockets.asyncio.client import connect

from tests.conftest import (
    TEST_STREAM_SID,
    audio_frame,
    media_message,
    open_stream,
    receive_json,
    receive_payload,
    wait_closed,
)
from tests.integration.conftest import poll_active_sessions  # noqa: F401 (fixture import marker)


async def test_echo_roundtrip_and_normally_stopped(server_url: dict[str, str]) -> None:
    sent = audio_frame(b"round-trip-payload-0x2A")
    async with connect(server_url["ws"]) as ws:
        await open_stream(ws)
        await ws.send(media_message(sent))
        received = await receive_payload(ws)
        assert received == sent  # byte-for-byte echo is the Day-2 contract
        await ws.send('{"event":"stop","reason":"callended"}')
        code, reason = await wait_closed(ws)
        assert (code, reason) == (1000, "stop")


async def test_outbound_envelope_matches_verified_protocol_shape(
    server_url: dict[str, str],
) -> None:
    async with connect(server_url["ws"]) as ws:
        await open_stream(ws)
        await ws.send(media_message(audio_frame()))
        reply = await receive_json(ws)
        # Verified outbound contract: exactly {event, stream_sid, media{payload}}
        assert sorted(reply.keys()) == ["event", "media", "stream_sid"]
        assert reply["stream_sid"] == TEST_STREAM_SID
        assert sorted(reply["media"].keys()) == ["payload"]
        await ws.send('{"event":"stop"}')
        await wait_closed(ws)


async def test_multiple_frames_echoed_in_arrival_order(server_url: dict[str, str]) -> None:
    frames = [audio_frame(bytes([marker] * 32)) for marker in (1, 2, 3)]
    async with connect(server_url["ws"]) as ws:
        await open_stream(ws)
        for index, frame in enumerate(frames):
            await ws.send(media_message(frame, timestamp=str(1000 + index)))
            # strict ping-pong: server must answer each frame before the next is sent
            assert await receive_payload(ws) == frame
        await ws.send('{"event":"stop"}')
        await wait_closed(ws)


async def test_unexpected_client_disconnect_cleans_up_session(
    server_url: dict[str, str],
) -> None:
    async with connect(server_url["ws"]) as ws:
        await open_stream(ws)
        await ws.send(media_message(audio_frame()))
        assert await receive_payload(ws) == audio_frame()
        await ws.close(code=1001)  # "going away" — session ends without a stop event
    await poll_active_sessions(server_url["http"], expected=0)


async def test_concurrent_sessions_echo_independently(server_url: dict[str, str]) -> None:
    first_ws = await connect(server_url["ws"])
    second_ws = await connect(server_url["ws"])
    try:
        await open_stream(first_ws, stream_sid="stream-a")
        await open_stream(second_ws, stream_sid="stream-b")
        await first_ws.send(media_message(b"payload-for-a"))
        await second_ws.send(media_message(b"payload-for-b"))
        assert await receive_payload(first_ws) == b"payload-for-a"
        assert await receive_payload(second_ws) == b"payload-for-b"
        async with httpx.AsyncClient() as http_client:
            ready = (await http_client.get(f"{server_url['http']}/health/ready")).json()
        assert ready["checks"]["active_sessions"] == 2
    finally:
        await first_ws.close()
        await second_ws.close()
    await poll_active_sessions(server_url["http"], expected=0)
