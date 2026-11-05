"""Integration-test harness: REAL uvicorn on an ephemeral port + the `websockets` client.

Starlette's TestClient WebSocket behaviour around server-initiated close codes is
unreliable in starlette 0.162 (AttributeError instead of WebSocketDisconnect),
while production traffic flows through uvicorn. These tests therefore run a real
uvicorn server in-process on 127.0.0.1:<ephemeral> and speak to it with the same
`websockets` client the simulator uses — the closest analogue to real Exotel
traffic, including true RFC 6455 close codes.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from app.main import create_app


@pytest.fixture()
async def server_url(monkeypatch, request) -> AsyncIterator[dict[str, str]]:
    """Real uvicorn serving the gateway; fully torn down after each test.

    Tests can inject environment overrides before the app binds:

        @pytest.mark.parametrize("server_url", [{"MAX_WS_MESSAGE_BYTES": "1024"}],
                                 indirect=True)
    """
    import uvicorn

    for key, value in getattr(request, "param", {}).items():
        monkeypatch.setenv(key, value)
    config = uvicorn.Config(
        app=create_app(),
        host="127.0.0.1",
        port=0,
        lifespan="on",
        log_level="warning",
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.05)
        if not server.started:
            raise RuntimeError("uvicorn server did not start within 10s")
        port = server.servers[0].sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        yield {"http": base, "ws": f"ws://127.0.0.1:{port}/ws/exotel"}
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)


async def poll_active_sessions(http_base: str, expected: int, *, tries: int = 20) -> None:
    """Wait (bounded) for session cleanup to surface in /health/ready — avoids races."""
    import httpx

    async with httpx.AsyncClient() as http_client:
        for _ in range(tries):
            response = await http_client.get(f"{http_base}/health/ready")
            if response.json()["checks"]["active_sessions"] == expected:
                return
            await asyncio.sleep(0.05)
    pytest.fail(f"active_sessions never reached {expected} after {tries} attempts")
