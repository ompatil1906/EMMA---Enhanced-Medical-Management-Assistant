# Day 6 — Implementation Architecture (LLM Conversation Engine, Emergency Guard & Practice Knowledge Base)

Status: Implemented and tested (273 Python tests green, lint/format clean).

Scope: Streaming speech pipeline from the existing voice UI through the FastAPI
gateway, through the Day-5 deterministic voice pipeline (STT → emergency guard
→ LLM interpretation → deterministic conversation controller → TTS), with the
Day-6 practice knowledge base and retrieval-augmented generation (RAG) layered
on top for practice-information queries.

This document describes the Day 6 RAG implementation. The Day 2 echo path, Day 3
speech pipeline, Day 4 NestJS backend, and Day 5 LLM conversation engine remain
intact.

---

## 1. Knowledge-Base Domain Model

`app/domain/knowledge.py` — `KnowledgeDocument`, `KnowledgeChunk`, `IngestionReport`

- `KnowledgeDocument`: one ingested practice document with deterministic ID
  derived from `practice_id:source_filename:document_version`
- `KnowledgeChunk`: one chunk of a document with all metadata needed for
  embedding and storage (practice-scoped)
- `IngestionReport`: summary of one ingestion run (documents parsed, chunks
  created, embeddings generated, vectors inserted, failures)

## 2. Retrieval Domain Model

`app/domain/retrieval.py` — `RetrievalQuery`, `RetrievedChunk`, `RetrievalResult`

- `RetrievalQuery`: one retrieval request with practice-scoped filtering
- `RetrievedChunk`: one retrieved chunk with relevance score and full metadata
- `RetrievalResult`: result of one retrieval operation with total count and
  latency

## 3. Embedding Provider Interface

`app/integrations/embeddings/base.py` — `EmbeddingProvider` protocol

- `EmbeddingRequest`: text + model + dimension
- `EmbeddingResponse`: vector + model + dimension + latency
- Providers: `MockEmbeddingProvider` (deterministic, hash-based vectors), 
  `GeminiEmbeddingProvider` (httpx-based REST API call to Gemini embedContent)
- Central factory: `EmbeddingProviderFactory` — fails clearly without
  credentials, never silently falls back to mock

## 4. Vector Store Provider Interface

`app/integrations/vector_store/base.py` — `VectorStoreProvider` protocol

- `VectorDocument`: one document chunk with PCM16-compatible vector metadata
- `VectorSearchResult`: one search result with cosine similarity score
- Providers: `MockVectorStoreProvider` (in-memory, deterministic),
  `QdrantVectorStoreProvider` (REST API client)
- Central factory: `VectorStoreProviderFactory` — fails clearly without
  `QDRANT_URL`, never silently falls back to mock

All vector store operations are practice-scoped — every search includes a
server-side `practice_id` filter, making cross-practice leakage impossible.

## 5. Document Processing Pipeline

`app/services/knowledge_base/` — `document_parser.py`, `document_chunker.py`,
`ingestion_service.py`

### Document Parser (`document_parser.py`)
- Supports `.md`, `.txt`, `.pdf` files
- Extracts plain text with section and page metadata
- Rejects unsupported formats and empty/unreadable documents clearly
- Computes SHA-256 content hash for deduplication

### Document Chunker (`document_chunker.py`)
- Structure-aware chunking: splits by paragraphs first, then sentences
- Preserves source title, section, page number metadata
- Stores chunk index and original character boundaries
- Configurable target chunk size (default 600 chars) and overlap (default 80)
- Never combines content from different documents or practices

### Ingestion Service (`ingestion_service.py`)
- Orchestrates: parse → chunk → embed → upsert
- Deterministic document IDs from `practice_id:source_filename:version`
- Re-ingesting identical document produces no duplicates (content hash dedup)
- Re-ingesting changed document produces new version, deactivates old
- Returns `IngestionReport` with counts and failures

## 6. Synthetic Demo GP Knowledge Base

`apps/voice-gateway/data/knowledge_base/demo-practice/`

Seven fictional practice documents, all clearly marked as synthetic demo data:

- `practice-overview.md` — practice overview and services
- `opening-hours.md` — weekly opening hours by day
- `contact-information.md` — practice address, phone, email, website
- `appointment-request-process.md` — how to request appointments
- `repeat-prescription-process.md` — repeat prescription request process
- `parking-and-accessibility.md` — parking, timezone, public transport
- `holiday-closure.md` — holiday closures

No clinical information, diagnoses, treatment recommendations, or real NHS
practice details are included.

## 7. Retrieval Service

`app/services/retrieval/retriever.py` — `Retriever`

- Embeds the query using the configured embedding provider
- Searches the vector store scoped to a single `practice_id`
- Returns ranked chunks with cosine similarity scores
- Configurable top-k (default 5) and minimum score (default 0.3)
- All searches are practice-scoped — cross-practice leakage is impossible

## 8. Grounded Answer Service

`app/services/retrieval/knowledge_answer_service.py` — `KnowledgeAnswerService`

- Receives retrieved chunks and a caller question
- Sends only retrieved context + question to the LLM (Gemini via Day-5 adapter)
- Validates all citations against retrieved chunks
- Abstains when evidence is missing or conflicting
- Never invents facts, opening hours, contact details, or medical advice
- Returns `GroundedAnswer` with answer text, citations, abstention flag

### Citation Validation (`citation_validator.py`)
- Every cited chunk ID must exist in retrieved results
- Every cited document must belong to the active practice
- Every cited chunk must have non-empty content
- Invalid citations cause abstention with a controlled message

## 9. Voice Pipeline Integration

`app/services/voice_pipeline.py` — `VoicePipeline._process_turn()`

The Day-3 speech pipeline is extended to route `practice_information` intents
through RAG:

```
Final transcript → Emergency guard → [match] → Fixed emergency script → TTS
                                → [no match] → LLM intent interpretation
                                                              → PROCESSING
                                                              → Conversation controller
                                                              → appointment-request
                                                                → typed backend submission
                                                              → practice-information
                                                                → retrieval
                                                                → grounded answer
                                                                → TTS
```

The FSM remains the authority for call state. Interpretation happens only on
final transcripts, never partials. Emergency guard runs before every LLM call.

## 10. Environment Configuration

`app/core/config.py` — Day 6 settings:

| Setting | Default | Description |
|---|---|---|
| `EMBEDDING_PROVIDER` | `mock` | `mock` \| `gemini` |
| `GEMINI_EMBEDDING_MODEL` | `gemini-embedding-2` | Embedding model name |
| `GEMINI_EMBEDDING_DIMENSION` | 768 | Vector dimension |
| `GEMINI_EMBEDDING_API_KEY` | unset | API key (blank → None) |
| `GEMINI_EMBEDDING_TIMEOUT_SECONDS` | 15.0 | Request timeout |
| `GEMINI_EMBEDDING_MAX_RETRIES` | 2 | Max retries |
| `VECTOR_STORE_PROVIDER` | `mock` | `mock` \| `qdrant` |
| `QDRANT_URL` | unset | Qdrant URL (blank → None) |
| `QDRANT_API_KEY` | unset | Qdrant API key |
| `QDRANT_COLLECTION` | `practice_knowledge` | Collection name |
| `RETRIEVAL_TOP_K` | 5 | Max chunks to retrieve |
| `RETRIEVAL_MIN_SCORE` | 0.3 | Minimum similarity score |
| `RETRIEVAL_CHUNK_SIZE` | 600 | Target chunk size in chars |
| `RETRIEVAL_CHUNK_OVERLAP` | 80 | Overlap between chunks |

Readiness: `RAG` is configured when both embedding provider and vector store
are configured. `rag_configured` property returns False if either is missing.

## 11. Ingestion CLI

`scripts/ingest_practice_kb.py` — CLI for ingesting practice knowledge base:

```bash
uv run python scripts/ingest_practice_kb.py \
    --practice-id demo-practice-001 \
    --path apps/voice-gateway/data/knowledge_base/demo-practice/
```

Outputs ingestion report with documents parsed, chunks created, embeddings
generated, vectors inserted, duplicates skipped, failures.

## 12. Voice UI Integration

The existing FastAPI WebSocket gateway (`/ws/exotel`) serves the browser-based
voice UI (`apps/ui/`). The browser acts as the provider, streaming PCM16 audio
at 16 kHz to the gateway. The gateway processes audio through the speech
pipeline (STT → emergency guard → LLM → controller → TTS) and streams PCM16
audio back to the browser.

For practice-information queries, the pipeline routes through RAG:
1. Simulated audio → STT final transcript
2. Emergency guard (always runs first)
3. LLM intent interpretation → `practice-information`
4. Practice-scoped retrieval from knowledge base
5. Grounded answer generation with citation validation
6. TTS audio streamed back to browser
7. Citations displayed in UI (source title, section, page)

## 13. Observability

Session metrics (`SessionMetrics`) track:
- STT provider, sample rate, partials, finals, low-confidence count
- TTS provider, first-chunk time, completion time, chunks, bytes
- Practice-API outcomes (submit_request, submit_handoff)
- Provider errors (stt, tts, practice)
- State transitions
- Retrieval metrics (chunks retrieved, latency)
- Grounded answer metrics (citations, abstentions)

No raw audio, full transcripts, or patient data is logged.

## 14. Verification Status

| Item | Status |
|---|---|
| Python unit + integration tests | 273 passed |
| NestJS unit + db tests | 49 passed |
| Ruff lint | Clean |
| Ruff format | Clean |
| Evaluation corpus (24 cases) | Passed |
| Emergency guard recall | 100% on defined corpus |
| Emergency false positives | 0 |
| Real Azure credentials | Not configured (mock default) |
| Real Gemini credentials | Not configured (mock default) |
| Real Qdrant instance | Not available (mock store) |
| Docker runtime | Not available (no daemon) |

## 15. Remaining Limitations

- No real Azure/Gemini/Qdrant credentials configured — mock providers used
- Mock vector store is in-memory only — no persistent storage
- Mock embedding provider uses deterministic hash-based vectors — not semantic
- No real Exotel/PSTN calls — WebSocket simulation only
- No browser automation for UI integration testing
- No production deployment

## 16. Day 7 Readiness

The architecture is ready for:
- Voice conversation quality testing with real credentials
- Multi-turn practice-information questions (session state persists)
- Retrieval improvements (reranking, hybrid search)
- Latency optimization (caching, batching)
- Browser-based end-to-end evaluation (manual testing)
- More advanced conversation testing (multi-turn dialogues)
