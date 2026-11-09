"""Day 18C: `/health/ready` must expose and enforce the live spend guard.

Real uvicorn, real HTTP, no provider traffic. Pins:

* guard disabled (default) → unchanged readiness payload,
* guard enabled with a usable ledger → capacity visible, still ready,
* guard enabled + live providers + unusable ledger → **503 degraded** (fail
  closed), with a secret-free error string,
* guard enabled but only mock providers → readiness unaffected (mock dev keeps
  working) while the status still reports the guard state.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from app.core.config import get_settings
from app.main import create_app

_GUARD_KEYS = {
    "enabled",
    "applicable",
    "attestation",
    "ledger_available",
    "ledger_file",
    "run_id",
    "usage",
    "limits",
    "remaining",
    "exhausted",
    "configuration_ready",
    "error",
}
_LIVE_PROVIDERS = {
    "STT_PROVIDER": "deepgram",
    "TTS_PROVIDER": "elevenlabs",
    "DEEPGRAM_API_KEY": "test-key-not-real",
    "ELEVENLABS_API_KEY": "test-key-not-real",
    "ELEVENLABS_VOICE_ID": "testVoiceId0000000001",
}


@asynccontextmanager
async def _running_gateway(monkeypatch: pytest.MonkeyPatch, **env: str) -> AsyncIterator[str]:
    """Start the gateway with explicit env (real uvicorn, ephemeral port)."""
    import uvicorn

    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    config = uvicorn.Config(
        app=create_app(), host="127.0.0.1", port=0, lifespan="on", log_level="warning"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.05)
        if not server.started:  # pragma: no cover - infra failure
            raise RuntimeError("gateway did not start")
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.gather(task, return_exceptions=True)
        get_settings.cache_clear()


async def _readiness(base: str) -> tuple[int, dict]:
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{base}/health/ready")
    return response.status_code, response.json()


async def test_guard_disabled_by_default(
    server_url: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    status_code, body = await _readiness(server_url["http"])

    assert status_code == 200
    guard = body["checks"]["live_provider_guard"]
    assert set(guard) == _GUARD_KEYS
    assert guard["enabled"] is False
    assert guard["applicable"] is False
    assert guard["configuration_ready"] is True
    assert guard["usage"] is None  # nothing to report when disabled


async def test_guard_enabled_but_mock_providers_stay_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async with _running_gateway(
        monkeypatch,
        EMMA_LIVE_GUARD_ENABLED="1",
        EMMA_LIVE_RUN_ID="health-mock",
        EMMA_LIVE_LEDGER_PATH=str(tmp_path / "ledger.json"),
    ) as base:
        status_code, body = await _readiness(base)

    guard = body["checks"]["live_provider_guard"]
    assert guard["enabled"] is True
    assert guard["applicable"] is False  # mock providers → guard irrelevant
    assert guard["configuration_ready"] is True
    assert status_code == 200


async def test_guard_enabled_with_live_providers_exposes_capacity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async with _running_gateway(
        monkeypatch,
        EMMA_LIVE_GUARD_ENABLED="1",
        EMMA_LIVE_RUN_ID="health-live",
        EMMA_LIVE_CREDENTIALS_ROTATED="1",
        EMMA_LIVE_PROVIDER_SPEND_APPROVED="1",
        EMMA_LIVE_LEDGER_PATH=str(tmp_path / "ledger.json"),
        **_LIVE_PROVIDERS,
    ) as base:
        status_code, body = await _readiness(base)

    guard = body["checks"]["live_provider_guard"]
    assert guard["enabled"] is True
    assert guard["applicable"] is True
    assert guard["ledger_available"] is True
    assert guard["configuration_ready"] is True
    assert guard["remaining"]["deepgram_sessions"] == 3
    assert guard["remaining"]["elevenlabs_chars"] == 300
    assert guard["limits"]["max_deepgram_audio_seconds"] == 15.0
    assert guard["run_id"] == "health-live"
    assert status_code == 200


async def test_unusable_ledger_degrades_readiness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Live providers + unusable ledger → readiness fails closed (503)."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")

    async with _running_gateway(
        monkeypatch,
        EMMA_LIVE_GUARD_ENABLED="1",
        EMMA_LIVE_RUN_ID="health-broken",
        EMMA_LIVE_CREDENTIALS_ROTATED="1",
        EMMA_LIVE_PROVIDER_SPEND_APPROVED="1",
        EMMA_LIVE_LEDGER_PATH=str(blocker / "ledger.json"),
        **_LIVE_PROVIDERS,
    ) as base:
        status_code, body = await _readiness(base)

    guard = body["checks"]["live_provider_guard"]
    assert guard["enabled"] is True
    assert guard["applicable"] is True
    assert guard["ledger_available"] is False
    assert guard["configuration_ready"] is False
    assert guard["error"]
    assert status_code == 503
    assert body["status"] == "degraded"
    # Secret-free: no key material or voice id in the diagnostic.
    assert "test-key-not-real" not in str(guard["error"])
    assert "testVoiceId0000000001" not in str(guard["error"])


async def test_corrupt_ledger_degrades_readiness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ledger = tmp_path / "ledger.json"
    ledger.write_text('{"version": 1, "run_id": "health-corrupt", "deepgram":')

    async with _running_gateway(
        monkeypatch,
        EMMA_LIVE_GUARD_ENABLED="1",
        EMMA_LIVE_RUN_ID="health-corrupt",
        EMMA_LIVE_CREDENTIALS_ROTATED="1",
        EMMA_LIVE_PROVIDER_SPEND_APPROVED="1",
        EMMA_LIVE_LEDGER_PATH=str(ledger),
        **_LIVE_PROVIDERS,
    ) as base:
        status_code, body = await _readiness(base)

    guard = body["checks"]["live_provider_guard"]
    assert guard["ledger_available"] is False
    assert "Corrupt" in str(guard["error"])
    assert status_code == 503


async def test_missing_attestation_degrades_readiness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Guard armed + live providers + no attestation → 503, before any spend."""
    async with _running_gateway(
        monkeypatch,
        EMMA_LIVE_GUARD_ENABLED="1",
        EMMA_LIVE_RUN_ID="health-unattested",
        EMMA_LIVE_LEDGER_PATH=str(tmp_path / "ledger.json"),
        **_LIVE_PROVIDERS,
    ) as base:
        status_code, body = await _readiness(base)

    guard = body["checks"]["live_provider_guard"]
    assert guard["attestation"] == {
        "credentials_rotated": False,
        "spend_approved": False,
        "ok": False,
    }
    assert guard["configuration_ready"] is False
    assert "not authorised" in str(guard["error"])
    assert status_code == 503
