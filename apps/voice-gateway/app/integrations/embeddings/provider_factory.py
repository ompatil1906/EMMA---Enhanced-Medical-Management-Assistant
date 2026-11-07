"""Central embedding provider factory (Day 6).

Selects a provider from validated settings. Real providers without credentials
fail clearly — there is NEVER a silent mock fallback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.integrations.embeddings.base import EmbeddingProvider
from app.integrations.embeddings.exceptions import EmbeddingProviderNotAvailable
from app.integrations.embeddings.gemini_provider import GeminiEmbeddingProvider
from app.integrations.embeddings.mock_provider import MockEmbeddingProvider

if TYPE_CHECKING:  # pragma: no cover
    from app.core.config import Settings


class EmbeddingProviderFactory:
    """Build one embedding provider instance from validated settings."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def provider_name(self) -> str:
        return str(self._settings.EMBEDDING_PROVIDER)

    @property
    def dimension(self) -> int:
        return int(self._settings.GEMINI_EMBEDDING_DIMENSION)

    @property
    def configuration_ok(self) -> bool:
        if self._settings.EMBEDDING_PROVIDER == "gemini":
            return bool(self._settings.GEMINI_EMBEDDING_API_KEY)
        return True

    def configuration_problem(self) -> str | None:
        if self._settings.EMBEDDING_PROVIDER == "gemini" and not (
            self._settings.GEMINI_EMBEDDING_API_KEY
        ):
            return (
                "EMBEDDING_PROVIDER=gemini but GEMINI_EMBEDDING_API_KEY is not "
                "configured (no credentials → degraded, never mock fallback)"
            )
        return None

    def create(self) -> EmbeddingProvider:
        """Build the provider for the configured EMBEDDING_PROVIDER (fails fast)."""
        settings = self._settings
        if settings.EMBEDDING_PROVIDER == "mock":
            return MockEmbeddingProvider(
                dimension=int(settings.GEMINI_EMBEDDING_DIMENSION),
            )
        if settings.EMBEDDING_PROVIDER == "gemini":
            if not settings.GEMINI_EMBEDDING_API_KEY:
                raise EmbeddingProviderNotAvailable(
                    "EMBEDDING_PROVIDER=gemini requires GEMINI_EMBEDDING_API_KEY; "
                    "refusing to start (no silent fallback)"
                )
            return GeminiEmbeddingProvider(
                api_key=settings.GEMINI_EMBEDDING_API_KEY,
                model=settings.GEMINI_EMBEDDING_MODEL,
                dimension=int(settings.GEMINI_EMBEDDING_DIMENSION),
                timeout_seconds=settings.GEMINI_EMBEDDING_TIMEOUT_SECONDS,
                max_retries=settings.GEMINI_EMBEDDING_MAX_RETRIES,
            )
        raise EmbeddingProviderNotAvailable(
            f"unknown EMBEDDING_PROVIDER {settings.EMBEDDING_PROVIDER!r}"
        )


__all__ = ["EmbeddingProviderFactory", "EmbeddingProviderNotAvailable"]
