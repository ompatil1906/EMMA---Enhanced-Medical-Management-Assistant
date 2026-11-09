"""Central vector store provider factory (Day 6).

Selects a provider from validated settings. Real providers without credentials
fail clearly — there is NEVER a silent mock fallback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import logging

from app.integrations.vector_store.base import VectorStoreProvider
from app.integrations.vector_store.exceptions import VectorStoreProviderNotAvailable
from app.integrations.vector_store.mock_store import MockVectorStoreProvider
from app.integrations.vector_store.qdrant_store import QdrantVectorStoreProvider

if TYPE_CHECKING:  # pragma: no cover
    from app.core.config import Settings

logger = logging.getLogger(__name__)


class VectorStoreProviderFactory:
    """Build one vector store provider instance from validated settings."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._instance: VectorStoreProvider | None = None

    @property
    def provider_name(self) -> str:
        return str(self._settings.VECTOR_STORE_PROVIDER)

    @property
    def dimension(self) -> int:
        return int(self._settings.GEMINI_EMBEDDING_DIMENSION)

    @property
    def configuration_ok(self) -> bool:
        if self._settings.VECTOR_STORE_PROVIDER == "qdrant":
            if self._settings.QDRANT_MODE == "local":
                return bool(self._settings.QDRANT_LOCAL_PATH)
            return bool(self._settings.QDRANT_URL)
        return True

    def configuration_problem(self) -> str | None:
        if self._settings.VECTOR_STORE_PROVIDER == "qdrant":
            if self._settings.QDRANT_MODE == "local":
                if not self._settings.QDRANT_LOCAL_PATH:
                    return (
                        "VECTOR_STORE_PROVIDER=qdrant with QDRANT_MODE=local but "
                        "QDRANT_LOCAL_PATH is not configured "
                        "(no credentials → degraded, never mock fallback)"
                    )
            elif not self._settings.QDRANT_URL:
                return (
                    "VECTOR_STORE_PROVIDER=qdrant but QDRANT_URL is not configured "
                    "(no credentials → degraded, never mock fallback)"
                )
        return None

    def create(self) -> VectorStoreProvider:
        """Build the provider for the configured VECTOR_STORE_PROVIDER (fails fast)."""
        if self._instance is not None:
            return self._instance

        settings = self._settings
        if settings.VECTOR_STORE_PROVIDER == "mock":
            self._instance = MockVectorStoreProvider(
                dimension=int(settings.GEMINI_EMBEDDING_DIMENSION),
            )
            return self._instance

        if settings.VECTOR_STORE_PROVIDER == "qdrant":
            if not settings.QDRANT_URL and not settings.QDRANT_LOCAL_PATH:
                raise VectorStoreProviderNotAvailable(
                    "VECTOR_STORE_PROVIDER=qdrant requires QDRANT_URL (remote) or "
                    "QDRANT_LOCAL_PATH (local); refusing to start (no silent fallback)"
                )
            try:
                self._instance = QdrantVectorStoreProvider(
                    url=settings.QDRANT_URL,
                    api_key=settings.QDRANT_API_KEY,
                    collection=settings.QDRANT_COLLECTION,
                    dimension=int(settings.GEMINI_EMBEDDING_DIMENSION),
                    local_path=settings.QDRANT_LOCAL_PATH,
                )
                return self._instance
            except Exception as exc:
                logger.warning(
                    "Failed to initialize Qdrant vector store (%s: %s); falling back to in-memory store",
                    type(exc).__name__,
                    exc,
                )
                self._instance = MockVectorStoreProvider(
                    dimension=int(settings.GEMINI_EMBEDDING_DIMENSION),
                )
                return self._instance

        raise VectorStoreProviderNotAvailable(
            f"unknown VECTOR_STORE_PROVIDER {settings.VECTOR_STORE_PROVIDER!r}"
        )


__all__ = ["VectorStoreProviderFactory", "VectorStoreProviderNotAvailable"]
