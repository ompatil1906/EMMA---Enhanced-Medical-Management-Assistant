"""EMMA voice gateway entrypoint (FastAPI factory + lifespan)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.health import router as health_router
from app.api.routes.livekit_token import router as livekit_token_router
from app.api.routes.websocket import router as websocket_router
from app.core.config import get_settings
from app.core.lifecycle import GatewayState
from app.core.logging import configure_logging, get_logger
from app.integrations.embeddings.provider_factory import EmbeddingProviderFactory
from app.integrations.llm.provider_factory import LLMProviderFactory
from app.integrations.practice_api import PracticeApiClient
from app.integrations.vector_store.provider_factory import VectorStoreProviderFactory
from app.speech.factory import SpeechProviderFactory

_VERSION = "0.3.0"  # Day 3 — speech pipeline (STT/FSM/TTS) + practice-backend client


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL)
    logger = get_logger(__name__)
    state = GatewayState()
    # Day-3 construction (validated; misconfigurations degrade readiness,
    # bell rung in logs; sessions close 1011 rather than silent fallback).
    state.speech_factory = SpeechProviderFactory(settings)
    if settings.PRACTICE_API_BASE_URL:
        state.practice_client = PracticeApiClient(
            base_url=settings.PRACTICE_API_BASE_URL,
            timeout=settings.PRACTICE_API_TIMEOUT,
            max_attempts=settings.PRACTICE_API_MAX_ATTEMPTS,
        )
    # Day-6: embedding provider factory + vector store factory + LLM factory.
    state.embedding_factory = EmbeddingProviderFactory(settings)
    state.vector_store_factory = VectorStoreProviderFactory(settings)
    state.llm_factory = LLMProviderFactory(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        state.mark_started()
        speech_problem = (
            state.speech_factory.configuration_problem() if state.speech_factory else "missing"
        )
        embedding_problem = (
            state.embedding_factory.configuration_problem()
            if state.embedding_factory
            else "missing"
        )
        vector_store_problem = (
            state.vector_store_factory.configuration_problem()
            if state.vector_store_factory
            else "missing"
        )
        logger.info(
            "gateway starting",
            extra={
                "app_env": settings.APP_ENV,
                "provider_mode": settings.EXOTEL_MODE,
                "voice_pipeline": settings.VOICE_PIPELINE,
                "stt_provider": str(settings.STT_PROVIDER),
                "tts_provider": str(settings.TTS_PROVIDER),
                "llm_provider": str(settings.LLM_PROVIDER),
                "practice_api": bool(settings.PRACTICE_API_BASE_URL),
                "speech_ready": state.speech_factory is not None and speech_problem is None,
                "embedding_provider": settings.EMBEDDING_PROVIDER,
                "vector_store": settings.VECTOR_STORE_PROVIDER,
                "rag_configured": settings.rag_configured,
            },
        )
        if speech_problem and settings.VOICE_PIPELINE == "speech":
            logger.warning("speech configuration blocker: %s", speech_problem)
        if embedding_problem:
            logger.warning("embedding configuration blocker: %s", embedding_problem)
        if vector_store_problem:
            logger.warning("vector store configuration blocker: %s", vector_store_problem)
        state.mark_ready()
        yield
        state.mark_stopped()
        if state.practice_client is not None:
            await state.practice_client.aclose()
        if state.embedding_factory is not None:
            provider = state.embedding_factory.create()
            await provider.aclose()
        if state.llm_factory is not None:
            provider = state.llm_factory.create()
            await provider.aclose()
        logger.info("gateway shutdown complete")

    app = FastAPI(
        title="EMMA Voice Gateway",
        version=_VERSION,
        description="Exotel AgentStream WebSocket gateway — Day 3 speech pipeline.",
        lifespan=lifespan,
    )
    app.state.gateway = state

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(websocket_router)
    app.include_router(livekit_token_router)
    return app


app = create_app()
