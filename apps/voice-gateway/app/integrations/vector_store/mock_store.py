"""Deterministic mock vector store provider (Day 6).

In-memory, zero network, zero randomness. Used for local development and the
regression suite — never a substitute for real vector-store testing.

Determinism rules:
* Same chunk_id → same stored vector.
* Cosine similarity computed from stored unit-normalized vectors.
* Every search is scoped to a single practice_id — cross-practice leakage is
  impossible by construction.
* Document lifecycle: active | superseded | archived | deleted.
"""

from __future__ import annotations

import math

from app.integrations.vector_store.base import (
    VectorDocument,
    VectorSearchResult,
)


class MockVectorStoreProvider:
    """Deterministic in-memory mock vector store implementing VectorStoreProvider.

    Vectors are stored in a dict keyed by chunk_id. Cosine similarity is
    computed against stored (unit-normalized) vectors.
    """

    def __init__(self, *, dimension: int = 768) -> None:
        self.dimension = dimension
        self._chunks: dict[str, VectorDocument] = {}
        self._call_count = 0

    @property
    def provider_name(self) -> str:
        return f"mock-vector-store:{self.dimension}"

    async def upsert(self, documents: list[VectorDocument]) -> int:
        """Upsert document chunks. Re-ingesting an existing chunk_id replaces it."""
        stored = 0
        for doc in documents:
            if doc.vector is None or len(doc.vector) != self.dimension:
                continue
            self._chunks[doc.chunk_id] = doc
            stored += 1
        self._call_count += 1
        return stored

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

        Cosine similarity between query_vector and stored vectors. Only active
        documents matching practice_id are considered.
        """
        self._call_count += 1

        if len(query_vector) != self.dimension:
            return []

        query_norm = math.sqrt(sum(v * v for v in query_vector)) or 1.0
        query_normalized = [v / query_norm for v in query_vector]

        results: list[tuple[float, VectorDocument]] = []
        for doc in self._chunks.values():
            if not doc.is_active:
                continue
            if doc.practice_id != practice_id:
                continue
            if document_version is not None and doc.document_version != document_version:
                continue
            if not doc.vector or len(doc.vector) != self.dimension:
                continue

            doc_vector = doc.vector or []
            doc_norm = math.sqrt(sum(v * v for v in doc_vector)) or 1.0
            doc_normalized = [v / doc_norm for v in doc_vector]
            score = sum(q * d for q, d in zip(query_normalized, doc_normalized, strict=False))
            # Clamp to [0, 1] for cosine similarity
            score = max(0.0, min(1.0, score))
            if score >= min_score:
                results.append((score, doc))

        # Sort by score descending, then by chunk_id for determinism
        results.sort(key=lambda x: (-x[0], x[1].chunk_id))

        return [
            VectorSearchResult(
                chunk_id=doc.chunk_id,
                document_id=doc.document_id,
                practice_id=doc.practice_id,
                source_title=doc.source_title,
                source_filename=doc.source_filename,
                section=doc.section,
                page_number=doc.page_number,
                chunk_index=doc.chunk_index,
                content=doc.content,
                score=score,
                metadata=dict(doc.metadata),
            )
            for score, doc in results[:top_k]
        ]

    async def deactivate_document(self, *, document_id: str, practice_id: str) -> int:
        """Mark all chunks of a document as superseded."""
        count = 0
        for doc in self._chunks.values():
            if doc.document_id == document_id and doc.practice_id == practice_id:
                # VectorDocument is frozen — we need to replace it
                updated = VectorDocument(
                    chunk_id=doc.chunk_id,
                    document_id=doc.document_id,
                    practice_id=doc.practice_id,
                    source_title=doc.source_title,
                    source_filename=doc.source_filename,
                    section=doc.section,
                    page_number=doc.page_number,
                    chunk_index=doc.chunk_index,
                    document_version=doc.document_version,
                    content=doc.content,
                    vector=doc.vector,
                    metadata=dict(doc.metadata),
                    is_active=False,
                )
                self._chunks[doc.chunk_id] = updated
                count += 1
        return count

    async def delete_document(self, *, document_id: str, practice_id: str) -> int:
        """Permanently delete all chunks of a document."""
        to_delete = [
            cid
            for cid, doc in self._chunks.items()
            if doc.document_id == document_id and doc.practice_id == practice_id
        ]
        for cid in to_delete:
            del self._chunks[cid]
        return len(to_delete)

    async def count(self, *, practice_id: str, active_only: bool = True) -> int:
        """Return the number of stored chunks for a practice."""
        count = 0
        for doc in self._chunks.values():
            if doc.practice_id != practice_id:
                continue
            if active_only and not doc.is_active:
                continue
            count += 1
        return count


__all__ = ["MockVectorStoreProvider"]
