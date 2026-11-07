"""Provider-independent vector store contract (Day 6).

Defines the protocol and shared types that every vector store adapter (mock,
Qdrant, …) must implement. The knowledge-base and retrieval services import
only the protocol — never a vendor SDK directly.

Design rules:

* All vectors in one collection share the same model and dimension.
* Every document carries a practice_id for tenant isolation — every search is
  scoped to a single practice.
* Document lifecycle: active | superseded | archived | deleted. Only active
  documents may be retrieved.
* Stable, deterministic document and chunk IDs derived from content hashes —
  re-ingesting an identical document produces no duplicates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class VectorDocument:
    """One document chunk stored in the vector store."""

    chunk_id: str  # stable, deterministic: practice_id:document_id:version:chunk_index
    document_id: str  # stable: practice_id:source_filename:document_version
    practice_id: str  # tenant isolation — every search is scoped to one practice
    source_title: str  # human-readable document title
    source_filename: str  # original file name
    section: str | None  # heading/section within the document
    page_number: int | None  # PDF page number, if applicable
    chunk_index: int  # ordinal position within the document
    document_version: int  # ingestion version for lifecycle management
    content: str  # the chunk text
    vector: list[float]  # embedding vector
    metadata: dict[str, str | int | float | bool | None] = field(default_factory=dict)
    is_active: bool = True  # superseded/archived/deleted documents are not retrieved


@dataclass(frozen=True)
class VectorSearchResult:
    """One search result returned by the vector store."""

    chunk_id: str
    document_id: str
    practice_id: str
    source_title: str
    source_filename: str
    section: str | None
    page_number: int | None
    chunk_index: int
    content: str
    score: float  # cosine similarity (higher = more similar)
    metadata: dict[str, str | int | float | bool | None] = field(default_factory=dict)


class VectorStoreError(Exception):
    """Base class for vector store failures."""


class VectorStoreConfigurationError(VectorStoreError):
    """The configured provider cannot run with the supplied configuration."""


class VectorStoreConnectionError(VectorStoreError):
    """Connecting to the provider failed (or exceeded its timeout)."""


@runtime_checkable
class VectorStoreProvider(Protocol):
    """Vector store provider contract."""

    @property
    def provider_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    async def upsert(self, documents: list[VectorDocument]) -> int:
        """Upsert document chunks. Returns the number of chunks stored.

        Re-ingesting a chunk with an existing chunk_id replaces it — no
        duplicates are created.
        """
        ...

    async def search(
        self,
        *,
        practice_id: str,
        query_vector: list[float],
        top_k: int = 5,
        min_score: float = 0.0,
        document_version: int | None = None,
    ) -> list[VectorSearchResult]:
        """Search for similar chunks within one practice.

        Every search is scoped to a single practice — cross-practice leakage
        is impossible by construction.
        """
        ...

    async def deactivate_document(self, *, document_id: str, practice_id: str) -> int:
        """Mark all chunks of a document as superseded. Returns count deactivated."""
        ...

    async def delete_document(self, *, document_id: str, practice_id: str) -> int:
        """Permanently delete all chunks of a document. Returns count deleted."""
        ...

    async def count(self, *, practice_id: str, active_only: bool = True) -> int:
        """Return the number of stored chunks for a practice."""
        ...


__all__ = [
    "VectorStoreError",
    "VectorStoreConfigurationError",
    "VectorStoreConnectionError",
    "VectorStoreProvider",
    "VectorDocument",
    "VectorSearchResult",
]
