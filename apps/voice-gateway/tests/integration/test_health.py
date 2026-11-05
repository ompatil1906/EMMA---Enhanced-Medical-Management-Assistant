"""Health endpoint integration tests (real uvicorn, real HTTP)."""

import httpx
import pytest


async def test_liveness_reports_alive(server_url: dict[str, str]) -> None:
    async with httpx.AsyncClient() as http_client:
        response = await http_client.get(f"{server_url['http']}/health/live")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "alive"
    assert isinstance(body["uptime_seconds"], (int, float))


async def test_readiness_reports_structured_checks(server_url: dict[str, str]) -> None:
    async with httpx.AsyncClient() as http_client:
        response = await http_client.get(f"{server_url['http']}/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    checks = body["checks"]
    assert checks["lifespan_complete"] is True
    assert checks["provider_mode"] == "simulation"
    assert checks["provider_credentials_configured"] is False  # not needed in simulation
    assert checks["active_sessions"] == 0
    assert checks["ws_auth_required"] is False
    assert checks["max_concurrent_sessions"] >= 1


@pytest.mark.parametrize("server_url", [{"EXOTEL_MODE": "live"}], indirect=True)
async def test_live_mode_without_credentials_is_degraded(server_url: dict[str, str]) -> None:
    async with httpx.AsyncClient() as http_client:
        response = await http_client.get(f"{server_url['http']}/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["provider_mode"] == "live"
    assert body["checks"]["provider_credentials_configured"] is False


@pytest.mark.parametrize(
    "server_url",
    [{"EXOTEL_MODE": "live", "EXOTEL_API_KEY": "key", "EXOTEL_API_TOKEN": "token"}],
    indirect=True,
)
async def test_live_mode_with_credentials_is_ready(server_url: dict[str, str]) -> None:
    async with httpx.AsyncClient() as http_client:
        response = await http_client.get(f"{server_url['http']}/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["provider_credentials_configured"] is True
