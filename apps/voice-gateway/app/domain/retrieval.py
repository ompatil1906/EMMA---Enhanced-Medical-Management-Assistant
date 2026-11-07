"""Retrieval domain models (Day 6).

Represents a retrieval request and its results, including citation metadata
for grounded answer generation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RetrievalQuery:
    """One retrieval request."""

    query_text: str  # the caller's final transcript utterance
    practice_id: str  # tenant isolation — from server-side session context
    top_k: int = 5
    min_score: float = 0.3
    document_version: int | None = None


@dataclass(frozen=True)
class RetrievedChunk:
    """One retrieved chunk with relevance score."""

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


@dataclass(frozen=True)
class RetrievalResult:
    """Result of one retrieval operation."""

    query_text: str
    practice_id: str
    chunks: list[RetrievedChunk]
    total_retrieved: int
    retrieval_latency_ms: float

    @property
    def has_results(self) -> bool:
        return len(self.chunks) > 0

    @property
    def top_score(self) -> float | None:
        if not self.chunks:
            return None
        return self.chunks[0].score


__all__ = [
    "RetrievalQuery",
    "RetrievedChunk",
    "RetrievalResult",
]
