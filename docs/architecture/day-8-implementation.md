# Day 8 — Implementation: Real Gemini Embeddings, Persistent Qdrant & RAG Integration

**Report date:** 2026-11-07 (Day 8 close-out).
**Scope:** Activate real Gemini embeddings, persistent Qdrant local storage,
re-ingest synthetic GP knowledge base, verify semantic retrieval, and connect
grounded answers to the existing browser voice UI.

---

## 1. Overview

Day 8 moves the knowledge-retrieval system from mock execution to genuine
semantic retrieval using:

| Component | Before (Day 6-7) | After (Day 8) |
|-----------|-------------------|----------------|
| Embeddings | Mock (hash-based) | Real Gemini `gemini-embedding-2` via REST API |
| Vector store | Mock (in-memory) | Qdrant local persistent mode (file-based, no Docker) |
| Knowledge base | Not ingested | 7 documents, 18 chunks, 18 vectors inserted |
| Retrieval | Deterministic mock | Real semantic search via Gemini + Qdrant |
| Grounded answers | Not connected | Gemini LLM + citation validation |

**No Docker daemon required.** Qdrant runs in local persistent mode via the
`qdrant-client` Python SDK (`QdrantClient(path="./data/qdrant")`).

---

## 2. Key Decisions

### 2.1 Gemini LLM model

The configured `gemini-1.5-flash` model returned HTTP 404 (not found for the
`v1beta` API). The live API lists `gemini-3.8-flash` as available for
`generateContent`. Configuration updated:

- `LLM_MODEL=gemini-3.8-flash` (was `gemini-1.5-flash`)

Live LLM call verified: intent extraction works with 5-second latency.

### 2.2 Gemini embedding model

Both `gemini-embedding-001` and `gemini-embedding-2` are available via the
`embedContent` REST API. Both return 768-dimensional vectors. Kept
`gemini-embedding-2` (already configured).

### 2.3 Qdrant local mode

The `qdrant-client` Python SDK (v1.20.0) supports local persistent storage:

```python
from qdrant_client import QdrantClient
client = QdrantClient(path="./data/qdrant")
```

Configuration added:

- `QDRANT_MODE=local` (was implicitly remote via `QDRANT_URL`)
- `QDRANT_LOCAL_PATH=./data/qdrant`
- `QDRANT_URL=` (empty when in local mode)

The existing `QdrantVectorStoreProvider` was extended to support both REST
(remote) and SDK (local) modes through the same `VectorStoreProvider` protocol
interface. No second vector-store implementation was introduced.

### 2.4 Chunk ID → UUID mapping

The local Qdrant SDK requires UUID-formatted point IDs. String chunk IDs
(e.g. `demo-practice-001:hash:1:0`) are converted deterministically via
`uuid.uuid5()` with a fixed namespace. The original chunk ID is preserved in
the payload under `original_chunk_id` for citation validation.

### 2.5 Citation markers

The grounded-answer LLM returns citation marker numbers (`[1]`, `[2]`) rather
than full chunk IDs. `validate_citations()` was extended to accept both:
- Full chunk IDs (existing behavior)
- 1-based numeric markers mapped to `retrieved_chunks` by position

---

## 3. Files Changed

### New files

- `scripts/test_embedding_models.py` — temporary (deleted after testing)
- `scripts/test_embedding_dimensions.py` — temporary (deleted after testing)
- `docs/architecture/day-8-implementation.md` (this file)
- `docs/architecture/day-8-engineering-report.md`
- `docs/runbooks/qdrant-local-persistence.md`

### Modified files

- `apps/voice-gateway/app/core/config.py` — `LLM_MODEL` default changed to
  `gemini-3.8-flash`; added `QDRANT_MODE`, `QDRANT_LOCAL_PATH`; updated
  `vector_store_configured` and `rag_configured` for local mode
- `apps/voice-gateway/app/integrations/embeddings/gemini_provider.py` — fixed
  missing `?key=` query parameter on embedContent URL; fixed latency
  measurement (`t0` now recorded before request loop)
- `apps/voice-gateway/app/integrations/embeddings/base.py` — added
  `model_name` property to `EmbeddingProvider` protocol
- `apps/voice-gateway/app/integrations/embeddings/gemini_provider.py` — added
  `model_name` property
- `apps/voice-gateway/app/integrations/embeddings/mock_provider.py` — added
  `model_name` property
- `apps/voice-gateway/app/integrations/vector_store/qdrant_store.py` — added
  local persistent SDK mode alongside REST remote mode; UUID conversion for
  chunk IDs; `collection_exists`-based collection setup
- `apps/voice-gateway/app/integrations/vector_store/provider_factory.py` —
  updated to pass `local_path` and support `QDRANT_MODE=local`
- `apps/voice-gateway/app/integrations/llm/base.py` — added optional
  `response_schema` field to `LLMRequest` for configurable response validation
- `apps/voice-gateway/app/integrations/llm/gemini_provider.py` — use
  `response_schema` for API `responseSchema`; non-strict validation when
  custom schema provided; added `_INTENT_EXTRACTION_SCHEMA` constant
- `apps/voice-gateway/app/services/knowledge_base/ingestion_service.py` — use
  `model_name` instead of `provider_name` for `EmbeddingRequest.model`
- `apps/voice-gateway/app/services/retrieval/retriever.py` — use
  `model_name` instead of `provider_name` for `EmbeddingRequest.model`
- `apps/voice-gateway/app/services/retrieval/knowledge_answer_service.py` —
  added `GroundedAnswerSchema` Pydantic model; pass `response_schema` to LLM
- `apps/voice-gateway/app/services/retrieval/citation_validator.py` — accept
  numeric citation markers (1-based indices) in addition to chunk IDs
- `.env` — `LLM_MODEL=gemini-3.8-flash`; `QDRANT_MODE=local`;
  `QDRANT_LOCAL_PATH=./data/qdrant`; `QDRANT_URL=` (empty)
- `.gitignore` — added `apps/voice-gateway/data/qdrant/`

---

## 4. Verification Results

### 4.1 Live Gemini LLM

| Test | Result |
|------|--------|
| Single intent extraction | PASS — `appointment-request` intent, 5146ms |
| Missing fields identified | PASS — `patient_name`, `date_of_birth`, `reason_for_appointment` |

### 4.2 Live Gemini Embeddings

| Test | Result |
|------|--------|
| Single embedding | PASS — `gemini-embedding-2`, 768 dims, 989ms |
| Dimension consistency | PASS — two calls, same dimension |
| Finite values | PASS — all values finite |

### 4.3 Qdrant Local Persistence

| Test | Result |
|------|--------|
| Collection creation | PASS |
| Upsert 3 chunks | PASS |
| Practice-scoped search | PASS — correct results per practice |
| Cross-practice isolation | PASS — zero leakage |
| Persistence after reconnect | PASS — count matches after reconnect |

### 4.4 Knowledge Base Ingestion

| Metric | Result |
|--------|--------|
| Documents discovered | 7 |
| Documents parsed | 7 |
| Chunks created | 18 |
| Embeddings generated | 18 |
| Vectors inserted | 18 |
| Failures | 0 |

### 4.5 Semantic Retrieval

| Query | Expected | Result |
|-------|----------|--------|
| "What time does the practice open on Monday?" | Opening Hours | PASS — score 0.75 |
| "When can I visit at start of working week?" | Opening Hours | PASS — score 0.79 |
| "How can I get in touch with the practice?" | Contact Information | PASS — score 0.79 |
| "Procedure for repeat prescription?" | Repeat Prescription | PASS — score 0.84 |
| "Specialist available tomorrow?" | Abstention | PASS — no specialist info |

### 4.6 Grounded Answers

| Question | Abstained | Citations | Result |
|----------|-----------|-----------|--------|
| Opening hours Monday | No | 1 | PASS |
| Contact practice | No | 2 | PASS |
| Specialist tomorrow | Yes | 0 | PASS — controlled abstention |
| Repeat prescription | No | 4 | PASS |

### 4.7 Practice Isolation

| Test | Result |
|------|--------|
| Practice A query | Only Practice A chunks | PASS |
| Practice B query | Only Practice B chunks | PASS |
| Cross-practice leakage | Zero | PASS |

---

## 5. Known Limitations

- **Azure STT/TTS** — credentials unavailable; mock providers used. Real
  speech recognition not verified.
- **Exotel live calls** — no account; not tested.
- **Docker runtime** — no daemon; Qdrant local mode used instead.
- **Real Qdrant server** — not tested; local persistent mode only.
- **Browser voice UI** — manual testing recommended; no browser automation
  stack installed.
- **Gemini model availability** — `gemini-1.5-flash` is deprecated/unavailable;
  using `gemini-3.8-flash` instead.

---

## 6. Day 9 Readiness

| Capability | Status |
|------------|--------|
| Multi-turn voice conversations | Ready — FSM + controller in place |
| Live Gemini intent interpretation | Verified |
| Live Gemini embeddings | Verified |
| Persistent RAG | Verified (Qdrant local mode) |
| Grounded answers | Verified |
| Browser voice UI | Wired, needs manual testing |
| Failure and recovery | Not yet tested |
| Performance optimisation | Not yet done |
