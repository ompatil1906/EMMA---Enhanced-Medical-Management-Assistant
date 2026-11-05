"""Authentication integration tests — simulation (X-WS-Token) vs live (Basic) paths."""

import base64

import pytest
from websockets.asyncio.client import connect

from tests.conftest import audio_frame, media_message, open_stream, receive_payload, wait_closed

SIM_TOKEN = "test-token"


class TestSimulationModeToken:
    @pytest.mark.parametrize("server_url", [{"WS_AUTH_TOKEN": SIM_TOKEN}], indirect=True)
    async def test_valid_token_connects_and_echoes(self, server_url: dict[str, str]) -> None:
        async with connect(server_url["ws"], additional_headers={"X-WS-Token": SIM_TOKEN}) as ws:
            await open_stream(ws)
            await ws.send(media_message(audio_frame()))
            assert await receive_payload(ws) == audio_frame()
            await ws.send('{"event":"stop"}')
            await wait_closed(ws)

    @pytest.mark.parametrize("server_url", [{"WS_AUTH_TOKEN": SIM_TOKEN}], indirect=True)
    async def test_wrong_token_rejected_1008(self, server_url: dict[str, str]) -> None:
        async with connect(
            server_url["ws"], additional_headers={"X-WS-Token": "wrong-token"}
        ) as ws:
            code, reason = await wait_closed(ws)
            assert (code, reason) == (1008, "authentication failed")

    @pytest.mark.parametrize("server_url", [{"WS_AUTH_TOKEN": SIM_TOKEN}], indirect=True)
    async def test_missing_token_rejected_1008(self, server_url: dict[str, str]) -> None:
        async with connect(server_url["ws"]) as ws:
            code, reason = await wait_closed(ws)
            assert (code, reason) == (1008, "authentication failed")


class TestLiveModeBasicAuth:
    @pytest.mark.parametrize(
        "server_url",
        [{"EXOTEL_MODE": "live", "EXOTEL_API_KEY": "key1", "EXOTEL_API_TOKEN": "tok1"}],
        indirect=True,
    )
    async def test_valid_basic_header_connects_and_echoes(self, server_url: dict[str, str]) -> None:
        token = base64.b64encode(b"key1:tok1").decode("ascii")
        async with connect(
            server_url["ws"], additional_headers={"Authorization": f"Basic {token}"}
        ) as ws:
            await open_stream(ws)
            await ws.send(media_message(audio_frame()))
            assert await receive_payload(ws) == audio_frame()
            await ws.send('{"event":"stop"}')
            await wait_closed(ws)

    @pytest.mark.parametrize(
        "server_url",
        [{"EXOTEL_MODE": "live", "EXOTEL_API_KEY": "key1", "EXOTEL_API_TOKEN": "tok1"}],
        indirect=True,
    )
    async def test_wrong_credentials_rejected_1008(self, server_url: dict[str, str]) -> None:
        token = base64.b64encode(b"key1:WRONG").decode("ascii")
        async with connect(
            server_url["ws"], additional_headers={"Authorization": f"Basic {token}"}
        ) as ws:
            code, reason = await wait_closed(ws)
            assert (code, reason) == (1008, "authentication failed")

    @pytest.mark.parametrize(
        "server_url",
        [{"EXOTEL_MODE": "live", "EXOTEL_API_KEY": "key1", "EXOTEL_API_TOKEN": "tok1"}],
        indirect=True,
    )
    async def test_missing_header_rejected_1008(self, server_url: dict[str, str]) -> None:
        async with connect(server_url["ws"]) as ws:
            code, reason = await wait_closed(ws)
            assert (code, reason) == (1008, "authentication failed")

    @pytest.mark.parametrize(
        "server_url",
        [{"EXOTEL_MODE": "live", "EXOTEL_API_KEY": "key1", "EXOTEL_API_TOKEN": "tok1"}],
        indirect=True,
    )
    async def test_bearer_scheme_rejected_1008(self, server_url: dict[str, str]) -> None:
        async with connect(
            server_url["ws"], additional_headers={"Authorization": "Bearer whatever"}
        ) as ws:
            code, reason = await wait_closed(ws)
            assert (code, reason) == (1008, "authentication failed")
