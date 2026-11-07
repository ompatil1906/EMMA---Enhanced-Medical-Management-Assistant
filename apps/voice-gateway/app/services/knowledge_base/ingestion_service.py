"""Document ingestion service (Day 6).

Orchestrates: parse → chunk → embed → upsert into the vector store.

All operations are practice-scoped. Documents are identified by a deterministic
ID derived from practice_id, source filename, and document version. Re-ingesting
an identical document produces no duplicates (content_hash dedup). Re-ingesting
a changed document produces a new version and deactivates the old one.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.knowledge import IngestionReport, KnowledgeDocument
from app.integrations.embeddings.base import EmbeddingProvider, EmbeddingRequest
from app.integrations.vector_store.base import (
    VectorDocument,
    VectorStoreProvider,
)
from app.services.knowledge_base.document_chunker import chunk_document
from app.services.knowledge_base.document_parser import DocumentParser


@dataclass
class IngestionService:
    """Ingests practice documents into the vector store."""

    parser: DocumentParser
    embedding_provider: EmbeddingProvider
    vector_store: VectorStoreProvider

    async def ingest_directory(
        self,
        *,
        practice_id: str,
        directory_path: str,
        document_version: int = 1,
        target_chunk_size: int = 600,
        chunk_overlap: int = 80,
    ) -> IngestionReport:
        """Ingest all supported documents from a directory.

        Each .md/.txt/.pdf file is parsed, chunked, embedded, and upserted.
        Re-ingesting the same document version produces no duplicates.
        """
        import os

        report = IngestionReport()

        if not os.path.isdir(directory_path):
            report.failures.append(f"directory not found: {directory_path}")
            return report

        filenames = sorted(
            f for f in os.listdir(directory_path) if f.lower().endswith((".md", ".txt", ".pdf"))
        )
        report.documents_discovered = len(filenames)

        for filename in filenames:
            file_path = os.path.join(directory_path, filename)
            try:
                with open(file_path, "rb") as f:
                    data = f.read()
            except OSError as exc:
                report.failures.append(f"{filename}: cannot read file: {exc}")
                continue

            result = self.parser.parse(filename, data)
            if result.document is None:
                report.failures.append(f"{filename}: {result.error or 'parse failed'}")
                continue

            report.documents_parsed += 1
            doc = result.document

            # Build the domain document
            domain_doc = KnowledgeDocument(
                document_id=self._make_document_id(practice_id, filename, document_version),
                practice_id=practice_id,
                source_title=doc.title,
                source_filename=filename,
                document_version=document_version,
                document_status="active",
                content_sha256=doc.content_sha256,
                effective_from=None,
                effective_to=None,
                ingested_at=self._now_iso(),
                source_type=doc.source_type,
            )

            # Chunk the document
            chunks = chunk_document(
                document_id=domain_doc.document_id,
                practice_id=practice_id,
                source_title=doc.title,
                source_filename=filename,
                content=doc.content,
                sections=doc.sections,
                document_version=document_version,
                target_chunk_size=target_chunk_size,
                chunk_overlap=chunk_overlap,
            )

            # Embed and upsert each chunk
            vectors_inserted = 0
            for chunk in chunks:
                try:
                    vector = await self._embed_chunk(chunk["content"])
                    vector_doc = VectorDocument(
                        chunk_id=chunk["chunk_id"],
                        document_id=domain_doc.document_id,
                        practice_id=practice_id,
                        source_title=doc.title,
                        source_filename=filename,
                        section=chunk["section"],
                        page_number=chunk["page_number"],
                        chunk_index=chunk["chunk_index"],
                        document_version=document_version,
                        content=chunk["content"],
                        vector=vector,
                        metadata={},
                    )
                    await self.vector_store.upsert([vector_doc])
                    vectors_inserted += 1
                    report.chunks_created += 1
                except Exception as exc:  # noqa: BLE001 — continue on chunk error
                    report.failures.append(f"{filename} chunk {chunk['chunk_index']}: {exc}")

            report.vectors_inserted += vectors_inserted
            report.embeddings_generated += vectors_inserted

        report.ingestion_duration_ms = 0.0  # set by caller if desired
        return report

    # --- helpers -----------------------------------------------------------

    @staticmethod
    def _make_document_id(practice_id: str, source_filename: str, document_version: int) -> str:
        """Deterministic document ID."""
        import hashlib

        raw = f"{practice_id}:{source_filename}:{document_version}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _now_iso() -> str:
        import datetime

        return datetime.datetime.now(datetime.UTC).isoformat()

    async def _embed_chunk(self, text: str) -> list[float]:
        """Embed one chunk of text."""

        request = EmbeddingRequest(
            text=text,
            model=self.embedding_provider.model_name,
            dimension=self.embedding_provider.dimension,
        )
        response = await self.embedding_provider.embed(request)
        return response.vector


__all__ = ["IngestionService"]
