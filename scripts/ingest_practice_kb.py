#!/usr/bin/env python3
"""Ingest practice knowledge base documents into the vector store (Day 6).

Usage:

    uv run python scripts/ingest_practice_kb.py \
        --practice-id demo-practice-001 \
        --path apps/voice-gateway/data/knowledge_base/demo-practice/

All operations are practice-scoped. Documents are identified by a deterministic
ID derived from practice_id, source filename, and document version. Re-ingesting
an identical document produces no duplicates. Re-ingesting a changed document
produces a new version and deactivates the old one.

Synthetic data only — no real patient or practice data.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.config import get_settings
from app.integrations.embeddings.provider_factory import EmbeddingProviderFactory
from app.integrations.vector_store.provider_factory import (
    VectorStoreProviderFactory,
)
from app.services.knowledge_base.document_parser import DocumentParser
from app.services.knowledge_base.ingestion_service import IngestionService


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest practice knowledge base documents into the vector store"
    )
    parser.add_argument(
        "--practice-id",
        required=True,
        help="Practice ID for tenant isolation (e.g. demo-practice-001)",
    )
    parser.add_argument(
        "--path",
        required=True,
        help="Path to the knowledge base directory containing .md/.txt/.pdf files",
    )
    parser.add_argument(
        "--document-version",
        type=int,
        default=1,
        help="Document version number (default: 1)",
    )
    parser.add_argument(
        "--target-chunk-size",
        type=int,
        default=600,
        help="Target chunk size in characters (default: 600)",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=80,
        help="Overlap between consecutive chunks in characters (default: 80)",
    )
    args = parser.parse_args()

    settings = get_settings()

    # Build providers
    embedding_factory = EmbeddingProviderFactory(settings)
    vector_store_factory = VectorStoreProviderFactory(settings)

    if not embedding_factory.configuration_ok:
        print(f"ERROR: {embedding_factory.configuration_problem()}")
        sys.exit(1)

    if not vector_store_factory.configuration_ok:
        print(f"ERROR: {vector_store_factory.configuration_problem()}")
        sys.exit(1)

    embedding_provider = embedding_factory.create()
    vector_store = vector_store_factory.create()
    doc_parser = DocumentParser()

    service = IngestionService(
        parser=doc_parser,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )

    print(f"Ingesting knowledge base for practice: {args.practice_id}")
    print(f"Source directory: {args.path}")
    print(f"Document version: {args.document_version}")
    print(f"Embedding provider: {embedding_provider.provider_name}")
    print(f"Vector store: {vector_store.provider_name}")
    print()

    report = await service.ingest_directory(
        practice_id=args.practice_id,
        directory_path=args.path,
        document_version=args.document_version,
        target_chunk_size=args.target_chunk_size,
        chunk_overlap=args.chunk_overlap,
    )

    print("Ingestion Report:")
    print(f"  Documents discovered: {report.documents_discovered}")
    print(f"  Documents parsed:     {report.documents_parsed}")
    print(f"  Chunks created:       {report.chunks_created}")
    print(f"  Embeddings generated: {report.embeddings_generated}")
    print(f"  Vectors inserted:     {report.vectors_inserted}")
    print(f"  Duplicates skipped:   {report.duplicates_skipped}")
    print(f"  Failures:             {len(report.failures)}")
    print(f"  Ingestion duration:   {report.ingestion_duration_ms:.1f} ms")

    if report.failures:
        print("\nFailures:")
        for failure in report.failures:
            print(f"  - {failure}")
        sys.exit(1)

    print("\nIngestion complete.")


if __name__ == "__main__":
    asyncio.run(main())
