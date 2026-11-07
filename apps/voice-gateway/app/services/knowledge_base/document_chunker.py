"""Structure-aware document chunker (Day 6).

Splits parsed document text into chunks suitable for embedding and retrieval.

Design rules:

* Split by headings and paragraphs where possible — never cut sentences
  arbitrarily.
* Preserve source title, section, and page number metadata.
* Store chunk index and original character boundaries.
* Use limited overlap to avoid losing context across boundaries.
* Configurable target chunk size and overlap.
* Do not combine content from different documents or practices into one chunk.
"""

from __future__ import annotations

import re


def chunk_document(
    *,
    document_id: str,
    practice_id: str,
    source_title: str,
    source_filename: str,
    content: str,
    sections: list[tuple[str, int | None, int, int]],
    document_version: int,
    target_chunk_size: int = 600,
    chunk_overlap: int = 80,
) -> list[dict]:
    """Split one document into chunks.

    Returns a list of chunk dicts with all metadata needed for embedding and
    storage. Chunks are structure-aware: they respect heading and paragraph
    boundaries and never split mid-sentence where avoidable.
    """
    if not content.strip():
        return []

    chunks: list[dict] = []

    # Build a map from character offset to (section_title, page_number).
    # Only store the start offset of each section; lookups use the nearest
    # preceding section.
    section_boundaries: list[tuple[int, str | None, int | None]] = []
    for section_title, page_number, start_char, _end_char in sections:
        section_boundaries.append((start_char, section_title, page_number))
    section_boundaries.sort()

    def section_at_offset(offset: int) -> tuple[str | None, int | None]:
        """Return the (section_title, page_number) active at character offset."""
        result: tuple[str | None, int | None] = (None, None)
        for start_char, section_title, page_number in section_boundaries:
            if start_char <= offset:
                result = (section_title, page_number)
            else:
                break
        return result

    # Split into paragraphs first
    paragraphs = _split_into_paragraphs(content)

    current_chunk_parts: list[str] = []
    current_chunk_size = 0
    current_chunk_start_offset = 0

    def flush_chunk() -> None:
        nonlocal current_chunk_parts, current_chunk_size, current_chunk_start_offset
        if not current_chunk_parts:
            return
        chunk_text = "\n\n".join(current_chunk_parts)
        chunk_index = len(chunks)

        section_title, page_number = section_at_offset(current_chunk_start_offset)

        chunks.append(
            {
                "chunk_id": _make_chunk_id(practice_id, document_id, document_version, chunk_index),
                "document_id": document_id,
                "practice_id": practice_id,
                "source_title": source_title,
                "source_filename": source_filename,
                "section": section_title,
                "page_number": page_number,
                "chunk_index": chunk_index,
                "document_version": document_version,
                "content": chunk_text,
                "metadata": {},
            }
        )
        current_chunk_parts = []
        current_chunk_size = 0
        # current_chunk_start_offset will be set to current_offset after flush

    current_offset = 0

    for para in paragraphs:
        para_size = len(para)

        # If adding this paragraph would exceed the target size and we already
        # have content, flush the current chunk first.
        if current_chunk_size > 0 and current_chunk_size + para_size > target_chunk_size:
            flush_chunk()
            current_chunk_start_offset = current_offset

        # If a single paragraph exceeds the target size, split it by sentences
        if para_size > target_chunk_size:
            sentences = _split_into_sentences(para)
            current_sentence_parts: list[str] = []
            current_sentence_size = 0
            for sentence in sentences:
                sent_size = len(sentence)
                if (
                    current_sentence_size > 0
                    and current_sentence_size + sent_size > target_chunk_size
                ):
                    chunk_text = " ".join(current_sentence_parts)
                    s_title, s_page = section_at_offset(current_offset)
                    chunks.append(
                        {
                            "chunk_id": _make_chunk_id(
                                practice_id,
                                document_id,
                                document_version,
                                len(chunks),
                            ),
                            "document_id": document_id,
                            "practice_id": practice_id,
                            "source_title": source_title,
                            "source_filename": source_filename,
                            "section": s_title,
                            "page_number": s_page,
                            "chunk_index": len(chunks),
                            "document_version": document_version,
                            "content": chunk_text,
                            "metadata": {},
                        }
                    )
                    current_sentence_parts = []
                    current_sentence_size = 0
                current_sentence_parts.append(sentence)
                current_sentence_size += sent_size

            if current_sentence_parts:
                chunk_text = " ".join(current_sentence_parts)
                s_title, s_page = section_at_offset(current_offset)
                chunks.append(
                    {
                        "chunk_id": _make_chunk_id(
                            practice_id, document_id, document_version, len(chunks)
                        ),
                        "document_id": document_id,
                        "practice_id": practice_id,
                        "source_title": source_title,
                        "source_filename": source_filename,
                        "section": s_title,
                        "page_number": s_page,
                        "chunk_index": len(chunks),
                        "document_version": document_version,
                        "content": chunk_text,
                        "metadata": {},
                    }
                )
            current_chunk_parts = []
            current_chunk_size = 0
        else:
            current_chunk_parts.append(para)
            current_chunk_size += para_size

        current_offset += para_size

    # Flush any remaining content
    flush_chunk()

    # Add overlap between consecutive chunks
    if chunk_overlap > 0 and len(chunks) > 1:
        _apply_overlap(chunks, chunk_overlap)

    return chunks


def _split_into_paragraphs(text: str) -> list[str]:
    """Split text into paragraphs on double-newlines."""
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def _split_into_sentences(text: str) -> list[str]:
    """Split text into sentences on sentence boundaries."""
    # Simple sentence split on . ! ? followed by whitespace and capital letter
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    return [p.strip() for p in parts if p.strip()]


def _make_chunk_id(
    practice_id: str, document_id: str, document_version: int, chunk_index: int
) -> str:
    """Deterministic chunk ID: practice_id:document_id:version:chunk_index."""
    return f"{practice_id}:{document_id}:{document_version}:{chunk_index}"


def _apply_overlap(chunks: list[dict], overlap: int) -> None:
    """Add trailing overlap from each chunk to the next.

    Modifies chunks in place. The last characters of chunk N are prepended
    to chunk N+1 (up to `overlap` characters), preserving word boundaries.
    """
    for i in range(len(chunks) - 1):
        current = chunks[i]
        next_chunk = chunks[i + 1]
        current_text = current["content"]
        if len(current_text) > overlap:
            overlap_text = current_text[-overlap:]
            # Find the start of the last complete word
            space_idx = overlap_text.find(" ")
            if space_idx > 0:
                overlap_text = overlap_text[space_idx + 1 :]
            next_chunk["content"] = overlap_text + " " + next_chunk["content"]


__all__ = [
    "chunk_document",
    "_make_chunk_id",
]
