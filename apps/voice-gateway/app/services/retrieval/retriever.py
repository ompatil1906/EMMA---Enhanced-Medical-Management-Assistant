"""Retrieval service (Day 6).

Orchestrates: embed query → vector store search → return practice-scoped results.

Every search is scoped to a single practice_id — cross-practice leakage is
impossible by construction.
"""

from __future__ import annotations

import time

from app.domain.retrieval import RetrievalQuery, RetrievalResult, RetrievedChunk
from app.integrations.embeddings.base import EmbeddingProvider, EmbeddingRequest
from app.integrations.vector_store.base import VectorStoreProvider


class Retriever:
    """Practice-scoped retrieval service."""

    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStoreProvider,
        default_top_k: int = 5,
        default_min_score: float = 0.3,
    ) -> None:
        self._embedding_provider = embedding_provider
        self._vector_store = vector_store
        self._default_top_k = default_top_k
        self._default_min_score = default_min_score

    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        """Retrieve chunks relevant to the query within one practice.

        Every search is scoped to query.practice_id — cross-practice leakage
        is impossible by construction.
        """
        t0 = time.monotonic()

        # 1. Embed the query
        embed_request = EmbeddingRequest(
            text=query.query_text,
            model=self._embedding_provider.model_name,
            dimension=self._embedding_provider.dimension,
        )
        embedding_response = await self._embedding_provider.embed(embed_request)
        query_vector = embedding_response.vector

        # 2. Search the vector store (practice-scoped)
        search_results = await self._vector_store.search(
            practice_id=query.practice_id,
            query_vector=query_vector,
            top_k=query.top_k or self._default_top_k,
            min_score=query.min_score if query.min_score is not None else self._default_min_score,
            document_version=query.document_version,
        )

        # 3. Convert to domain results
        chunks = [
            RetrievedChunk(
                chunk_id=r.chunk_id,
                document_id=r.document_id,
                practice_id=r.practice_id,
                source_title=r.source_title,
                source_filename=r.source_filename,
                section=r.section,
                page_number=r.page_number,
                chunk_index=r.chunk_index,
                content=r.content,
                score=r.score,
                metadata=dict(r.metadata),
            )
            for r in search_results
        ]

        retrieval_latency_ms = (time.monotonic() - t0) * 1000.0

        return RetrievalResult(
            query_text=query.query_text,
            practice_id=query.practice_id,
            chunks=chunks,
            total_retrieved=len(chunks),
            retrieval_latency_ms=retrieval_latency_ms,
        )


__all__ = ["Retriever"]
