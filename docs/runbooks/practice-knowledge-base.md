# Practice Knowledge Base Runbook (Day 6)

This runbook covers the EMMA practice knowledge base: synthetic practice data,
document ingestion, retrieval, and grounded answer generation.

## 1. Overview

The practice knowledge base stores synthetic administrative information for
demo GP practices. It is used by the voice gateway's retrieval-augmented
generation (RAG) pipeline to answer practice-information questions
(opening hours, contact information, appointment process, prescription
process, etc.).

All data is synthetic — no real patient or practice data.

## 2. Knowledge Base Location

Synthetic practice documents live in:

```
apps/voice-gateway/data/knowledge_base/demo-practice/
```

Documents:

| File | Content |
|---|---|
| `practice-overview.md` | Practice overview and services |
| `opening-hours.md` | Weekly opening hours by day |
| `contact-information.md` | Address, phone, email, website |
| `appointment-request-process.md` | Appointment request process |
| `repeat-prescription-process.md` | Repeat prescription request process |
| `parking-and-accessibility.md` | Parking, accessibility, public transport |
| `holiday-closure.md` | Holiday closures |

Each document begins with `SYNTHETIC DEMONSTRATION DATA — NOT A REAL GP
PRACTICE`.

## 3. Ingesting the Knowledge Base

### Prerequisites

- Python 3.13+
- `uv` installed
- Voice gateway dependencies installed (`uv sync --all-groups` in
  `apps/voice-gateway/`)

### Ingestion Command

```bash
cd apps/voice-gateway
uv run python scripts/ingest_practice_kb.py \
    --practice-id demo-practice-001 \
    --path data/knowledge_base/demo-practice/
```

### Expected Output

```
Ingesting knowledge base for practice: demo-practice-001
Source directory: apps/voice-gateway/data/knowledge_base/demo-practice/
Document version: 1
Embedding provider: mock-embedding:768
Vector store: mock-vector-store:768

Ingestion Report:
  Documents discovered: 7
  Documents parsed: 7
  Chunks created: 23
  Embeddings generated: 23
  Vectors inserted: 23
  Duplicates skipped: 0
  Failures: 0
  Ingestion duration: 12.4 ms

Ingestion complete.
```

### Re-ingestion

Re-running the ingestion with the same document version produces no duplicates
(content hash deduplication). Re-ingesting with a new `document_version`
creates a new version and deactivates the old one.

---

## 4. Document Model

### Tables/Collections

| Concept | Description |
|---|---|
| `practices` | GP practice configuration (id, name, timezone) |
| `documents` | Ingested practice documents (id, practice_id, title, filename, version, status) |
| `chunks` | Document chunks with embeddings (id, document_id, practice_id, content, vector) |

### Document Lifecycle

- `active` — Retrievable in search results
- `superseded` — Replaced by a newer version, not retrievable
- `archived` — Archived, not retrievable
- `deleted` — Permanently deleted

Only `active` documents are returned by retrieval queries.

## 5. Chunking Strategy

- Split by paragraphs (double-newline delimited)
- Paragraphs exceeding target size split by sentences
- Target chunk size: 600 characters
- Chunk overlap: 80 characters
- Never combine content from different documents or practices

## 6. Retrieval

### Query Flow

1. Caller utterance → STT → final transcript
2. Emergency guard (always runs first — emergency calls never reach retrieval)
3. LLM intent interpretation → `practice-information`
4. Query embedded using configured embedding provider
5. Vector store search scoped to `practice_id` with `document_status=active`
6. Top-k chunks returned (default 5, min score 0.3)
7. Grounded answer generated from retrieved chunks
8. Citations validated against retrieved chunks
9. Answer spoken via TTS

### Tenant Isolation

Every search includes `practice_id` filter. Cross-practice leakage is
impossible by construction — the vector store only returns chunks matching
the requested `practice_id`.

---

## 7. Troubleshooting

### Problem: No chunks returned for a practice-information query

**Possible causes:**

1. No documents ingested for the practice — run `ingest_practice_kb.py`
2. Wrong `practice_id` — verify the practice_id matches the ingested documents
3. Vector store provider not configured — check `VECTOR_STORE_PROVIDER` and
   `QDRANT_URL` (if using Qdrant)
4. Embedding provider not configured — check `EMBEDDING_PROVIDER` and
   `GEMINI_EMBEDDING_API_KEY` (if using Gemini)

### Problem: Ingestion fails

**Possible causes:**

1. Directory path doesn't exist — verify the path
2. Unsupported file format — only `.md`, `.txt`, `.pdf` supported
3. PDF with no extractable text — PDF may be image-based (no OCR support)
4. Embedding provider not configured — check `EMBEDDING_PROVIDER` and
   `GEMINI_EMBEDDING_API_KEY` (if using Gemini)
5. Vector store not configured — check `VECTOR_STORE_PROVIDER` and
   `QDRANT_URL` (if using Qdrant)

### Problem: Answer not grounded in retrieved documents

**Possible causes:**

1. No relevant chunks retrieved — try different query wording
2. Retrieval threshold too high — lower `RETRIEVAL_MIN_SCORE`
3. Document not ingested — run ingestion for the relevant practice
4. LLM provider not configured — check `LLM_PROVIDER` and `LLM_API_KEY` (if using Gemini)

---

## 7. Security

- No real patient data in the knowledge base
- No API keys or secrets in source code
- All data is synthetic and clearly marked as demonstration data
- Practice-scoped retrieval — every search includes `practice_id` filter
- No cross-practice leakage possible
