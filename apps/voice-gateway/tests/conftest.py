"""Root pytest fixtures & Exotel protocol helpers shared by unit + integration tests.

Every integration test runs against a REAL uvicorn server (see
tests/integration/conftest.py) because starlette's TestClient WebSocket close
handling is unreliable in starlette 0.162. All audio here is synthetic and
deterministic — see tests/fixtures/README.md.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Iterator

import pytest
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

from app.core.config import get_settings

TEST_STREAM_SID = "stream-test-001"
TEST_CALL_SID = "call-test-001"


@pytest.fixture(autouse=True)
def fresh_settings_cache() -> Iterator[None]:
    """Isolate env-driven Settings across tests (`get_settings` is lru_cache'd)."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def audio_frame(payload: bytes | None = None) -> bytes:
    """Deterministic synthetic audio (default 1,280 B = 80 ms @ 8 kHz mono 16-bit)."""
    if payload is not None:
        return payload
    return bytes((i * 7 + j) % 256 for i in range(20) for j in range(64))


def media_message(payload: bytes, timestamp: str = "12345") -> str:
    """Verified inbound `media` envelope (base64 chunk)."""
    return json.dumps(
        {
            "event": "media",
            "chunk": base64.b64encode(payload).decode("ascii"),
            "timestamp": timestamp,
        }
    )


def start_message(stream_sid: str | None = TEST_STREAM_SID, sample_rate: int = 8000) -> str:
    """Verified `start` envelope (stream metadata + media_format)."""
    body: dict[str, object] = {
        "event": "start",
        "call_sid": TEST_CALL_SID,
        "account_sid": "acct-test",
        "from": "+441234000001",
        "to": "+441234000999",
        "custom_parameters": {"practice": "test-practice"},
        "media_format": {"encoding": "PCM", "sample_rate": sample_rate, "bit_rate": 128000},
    }
    if stream_sid is not None:
        body["stream_sid"] = stream_sid
    return json.dumps(body)


async def open_stream(
    ws: ClientConnection, stream_sid: str | None = TEST_STREAM_SID, sample_rate: int = 8000
) -> None:
    """Drive a connection to the active state (connected + start; server emits nothing)."""
    await ws.send(json.dumps({"event": "connected"}))
    await ws.send(start_message(stream_sid, sample_rate))


async def receive_json(ws: ClientConnection, timeout: float = 5.0) -> dict[str, object]:
    return json.loads(await asyncio.wait_for(ws.recv(), timeout))


async def receive_payload(ws: ClientConnection, timeout: float = 5.0) -> bytes:
    """Read one echoed `media` envelope and return the decoded PCM payload."""
    reply = await receive_json(ws, timeout)
    assert reply["event"] == "media", f"unexpected envelope: {reply!r}"
    return base64.b64decode(reply["media"]["payload"])


async def wait_closed(
    ws: ClientConnection, *, timeout: float = 5.0
) -> tuple[int | None, str | None]:
    """Drain until the peer closes; assert a real close frame and return (code, reason)."""
    while True:
        try:
            await asyncio.wait_for(ws.recv(), timeout)
        except TimeoutError:  # asyncio.wait_for timeout
            raise AssertionError("socket never closed") from None
        except ConnectionClosed as exc:
            if exc.rcvd is None:
                raise AssertionError(f"peer vanished without a close frame: {exc}") from exc
            return exc.rcvd.code, exc.rcvd.reason
