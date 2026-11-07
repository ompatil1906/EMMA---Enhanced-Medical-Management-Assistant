"""Embedding provider integration package (Day 6).

Provider-independent embedding interface with mock and Gemini adapters.
The knowledge-base and retrieval services import only the protocol — never a
vendor SDK directly.
"""

from app.integrations.embeddings.base import (
    EmbeddingConfigurationError,
    EmbeddingConnectionError,
    EmbeddingError,
    EmbeddingProvider,
    EmbeddingRequest,
    EmbeddingResponse,
)
from app.integrations.embeddings.exceptions import (
    EmbeddingIntegrationError,
    EmbeddingProviderNotAvailable,
    EmbeddingTimeoutError,
)
from app.integrations.embeddings.gemini_provider import GeminiEmbeddingProvider
from app.integrations.embeddings.mock_provider import MockEmbeddingProvider
from app.integrations.embeddings.provider_factory import EmbeddingProviderFactory

__all__ = [
    "EmbeddingError",
    "EmbeddingConfigurationError",
    "EmbeddingConnectionError",
    "EmbeddingIntegrationError",
    "EmbeddingProviderNotAvailable",
    "EmbeddingTimeoutError",
    "EmbeddingProvider",
    "EmbeddingRequest",
    "EmbeddingResponse",
    "GeminiEmbeddingProvider",
    "MockEmbeddingProvider",
    "EmbeddingProviderFactory",
]
