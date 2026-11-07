"""Vector store integration package (Day 6).

Provider-independent vector store interface with mock and Qdrant adapters.
The knowledge-base and retrieval services import only the protocol — never a
vendor SDK directly.
"""

from app.integrations.vector_store.base import (
    VectorDocument,
    VectorSearchResult,
    VectorStoreConfigurationError,
    VectorStoreConnectionError,
    VectorStoreError,
    VectorStoreProvider,
)
from app.integrations.vector_store.exceptions import VectorStoreProviderNotAvailable
from app.integrations.vector_store.mock_store import MockVectorStoreProvider
from app.integrations.vector_store.provider_factory import VectorStoreProviderFactory
from app.integrations.vector_store.qdrant_store import QdrantVectorStoreProvider

__all__ = [
    "VectorStoreError",
    "VectorStoreConfigurationError",
    "VectorStoreConnectionError",
    "VectorStoreProvider",
    "VectorStoreProviderNotAvailable",
    "VectorDocument",
    "VectorSearchResult",
    "MockVectorStoreProvider",
    "QdrantVectorStoreProvider",
    "VectorStoreProviderFactory",
]
