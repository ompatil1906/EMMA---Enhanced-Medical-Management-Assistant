"""EMMA voice gateway entrypoint (FastAPI factory + lifespan)."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.health import router as health_router
from app.api.routes.websocket import router as websocket_router
from app.core.config import get_settings
from app.core.lifecycle import GatewayState
from app.core.logging import configure_logging, get_logger

_VERSION = "0.2.0"  # Day 2 — engineering foundation (echo path only)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL)
    logger = get_logger(__name__)
    state = GatewayState()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        state.mark_started()
        logger.info(
            "gateway starting",
            extra={
                "app_env": settings.APP_ENV,
                "provider_mode": settings.EXOTEL_MODE,
                "ws_auth_required": settings.ws_auth_required,
            },
        )
        state.mark_ready()
        yield
        state.mark_stopped()
        logger.info("gateway stopped", extra={"active_sessions": state.registry.count()})

    app = FastAPI(
        title="EMMA Voice Gateway",
        version=_VERSION,
        description=(
            "Day-2 foundation: Exotel-compatible media-stream echo gateway. "
            "No STT/LLM/TTS yet — transport and contract layer only."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True if settings.CORS_ORIGINS != ["*"] else False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.gateway = state
    app.include_router(health_router)
    app.include_router(websocket_router)
    return app


app = create_app()
