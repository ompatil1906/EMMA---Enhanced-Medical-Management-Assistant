"""Knowledge-base domain models (Day 6).

Represents practice-specific administrative documents and their chunks.
All data is synthetic — no real patient or practice data.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class KnowledgeDocument:
    """One ingested practice document."""

    document_id: str  # stable: practice_id:source_filename:document_version
    practice_id: str  # tenant isolation
    source_title: str  # human-readable document title
    source_filename: str  # original file name
    document_version: int  # ingestion version for lifecycle management
    document_status: str  # active | superseded | archived | deleted
    content_sha256: str  # content hash for dedup
    effective_from: str | None  # ISO timestamp, if applicable
    effective_to: str | None  # ISO timestamp, if applicable
    ingested_at: str  # ISO timestamp
    source_type: str  # e.g. "practice_document"


@dataclass(frozen=True)
class KnowledgeChunk:
    """One chunk of a knowledge document, ready for embedding and storage."""

    chunk_id: str  # stable: practice_id:document_id:version:chunk_index
    document_id: str
    practice_id: str
    source_title: str
    source_filename: str
    section: str | None
    page_number: int | None
    chunk_index: int
    document_version: int
    content: str  # the chunk text
    metadata: dict[str, str | int | float | bool | None] = field(default_factory=dict)


@dataclass
class IngestionReport:
    """Summary of one ingestion run."""

    documents_discovered: int = 0
    documents_parsed: int = 0
    chunks_created: int = 0
    embeddings_generated: int = 0
    vectors_inserted: int = 0
    duplicates_skipped: int = 0
    failures: list[str] = field(default_factory=list)
    ingestion_duration_ms: float = 0.0

    @property
    def success(self) -> bool:
        return len(self.failures) == 0


__all__ = [
    "KnowledgeDocument",
    "KnowledgeChunk",
    "IngestionReport",
]
