"""Health probes — structured for future monitoring/dashboards.

`/health/live`  → process is up (transport-level truth).
`/health/ready` → able to accept WebSocket sessions (lifespan complete, provider
mode's prerequisites met). Simulation mode never needs provider credentials;
live mode deliberately stays degraded until `EXOTEL_API_KEY`/`EXOTEL_API_TOKEN`
are configured — this is where the documented onboarding blocker surfaces.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.lifecycle import GatewayState

router = APIRouter(tags=["health"])


def _state(request: Request) -> GatewayState:
    state: GatewayState | None = getattr(request.app.state, "gateway", None)
    if state is None:
        raise RuntimeError("gateway state missing — lifespan did not initialise app.state.gateway")
    return state


@router.get("/health/live")
async def liveness(request: Request) -> JSONResponse:
    state = _state(request)
    body = {
        "status": "alive" if state.started_at is not None else "starting",
        "uptime_seconds": state.uptime_seconds,
    }
    return JSONResponse(content=body, status_code=200 if state.started_at else 503)


@router.get("/health/ready")
async def readiness(request: Request) -> JSONResponse:
    state, settings = _state(request), get_settings()
    checks = {
        "lifespan_complete": state.ready,
        "provider_mode": settings.EXOTEL_MODE,
        "provider_credentials_configured": settings.exotel_live_credentials_configured,
        "ws_auth_required": settings.ws_auth_required,
        "active_sessions": state.registry.count(),
        "max_concurrent_sessions": settings.MAX_CONCURRENT_SESSIONS,
    }
    ready = state.ready and settings.provider_readiness_ok
    body = {"status": "ready" if ready else "degraded", "checks": checks}
    return JSONResponse(content=body, status_code=200 if ready else 503)
