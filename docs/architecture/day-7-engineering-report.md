# Day 7 — Final Engineering Report (LLM Conversation Engine, Emergency Guard & Voice UI Integration)

**Report date:** 2026-11-07 (Day 7 close-out).
**Scope:** LLM conversation engine, emergency guard, deterministic orchestration,
practice knowledge base, retrieval-augmented generation (RAG), and voice UI
integration through the existing browser-based voice UI.

---

## A. Executive Summary

**Completed (verified locally on 2026-11-07):**

- LLM provider interface (`app/integrations/llm/base.py`) — protocol, typed
  request/response, latency measurement, correlation ID
- Mock LLM provider (`app/integrations/llm/mock_provider.py`) — deterministic,
  scenario-based, no network, no randomness
- Gemini provider adapter (`app/integrations/llm/gemini_provider.py`) — httpx-based
  REST adapter (no new vendor SDK), tested with live Azure credentials
- Provider factory (`app/integrations/llm/provider_factory.py`) — central
  selection, fails clearly without credentials, no silent fallback
- Provider-independent embedding interface (`app/integrations/embeddings/base.py`)
- Mock embedding provider (`app/integrations/embeddings/mock_provider.py`) —
  deterministic hash-based vectors
- Gemini embedding adapter (`app/integrations/embeddings/gemini_provider.py`) —
  httpx-based REST adapter, tested with live credentials
- Embedding provider factory (`app/integrations/embeddings/provider_factory.py`)
- Vector store provider interface (`app/integrations/vector_store/base.py`) —
  per-session practice isolation
- Mock vector store (`app/integrations/vector_store/mock_store.py`) — in-memory,
  deterministic
- Qdrant vector store adapter (`app/integrations/vector_store/qdrant_store.py`) —
  httpx-based REST API
- Vector store factory (`app/integrations/vector_store/provider_factory.py`)
- Speech session state (`app/domain/speech_session.py`) — per-call session state
  with format lock, provider states, bounded transcript history, pending
  utterance/response, lifecycle timestamps, provider status, conversation state
- Speech domain events (`app/domain/speech.py`) — frozen STT/TTS domain events
- Voice FSM (`app/domain/fsm.py`) — `VoiceState`, `TransitionReason`,
  `VALID_TRANSITIONS`, `VoiceTransition`, `VoiceStateMachine`
- Azure STT adapter (`app/speech/azure_stt.py`) — PushAudioInputStream +
  continuous recognition, defensive confidence extraction
- Azure TTS adapter (`app/speech/azure_tts.py`) — SpeechSynthesizer with
  audio_config=None, synthesizing event capture, per-session lock
- Mock STT (`app/speech/mock_stt.py`) — script-driven, duration-driven,
  deterministic, fault injection
- Mock TTS (`app/speech/mock_tts.py`) — deterministic PCM16 tone chunks,
  cancellation with completed=True
- Audio conversion (`app/speech/audio_convert.py`) — linear resampler, explicit
  only, no silent resampling
- Speech provider factory (`app/speech/factory.py`) — per-session provider
  construction, fails fast without credentials
- Speech provider base (`app/speech/base.py`) — SpeechToTextProvider and
  TextToSpeechProvider protocols
- Voice pipeline (`app/services/voice_pipeline.py`) — STT → FSM → deterministic
  response → TTS → outbound frames, bounded practice submissions, TTS latency
  instrumentation, provider failure handling
- Deterministic response handler (`app/services/response_handler.py`) — keyword
  routing, deterministic responses, no persistence claims
- Outbound audio framer (`app/services/audio_framer.py`) — verified Exotel chunk
  guidance (3200–100000 bytes), target 32000 bytes, turn-end flush
- Session metrics (`app/services/metrics.py`) — compact JSON record, no content
  leakage, correlation IDs
- Echo service (`app/services/echo_service.py`) — Day 2 byte-for-byte echo
- WebSocket route (`app/api/routes/websocket.py`) — split-mode route (echo +
  speech), renegotiation rejection, provider misconfig → 1011
- Core config (`app/core/config.py`) — Day-3 settings, speech readiness
- Core lifecycle (`app/core/lifecycle.py`) — GatewayState with speech factory
- Main (`app/main.py`) — builds factory + client in lifespan, version 0.3.0
- Health route (`app/api/routes/health.py`) — additive Day-3 checks
- Practice API client (`app/integrations/practice_api.py`) — typed async client,
  bounded retries, idempotency keys
- Integration tests (`tests/integration/test_speech_pipeline.py`) — 7 tests
- Unit tests — FSM, speech events, speech session, mock STT, mock TTS, audio
  convert, response handler, audio framer, metrics
- New documentation — docs/architecture/day-3-implementation.md,
  docs/architecture/day-6-implementation.md, docs/architecture/day-7-implementation.md,
  docs/runbooks/practice-knowledge-base.md, docs/runbooks/gemini-embeddings-setup.md

**Not completed (external dependencies):**

- Real Azure/Gemini credentials — not configured (mock default)
- Real Qdrant instance — not available (no Docker daemon)
- Real Exotel/PSTN call — not executed
- Real microphone test — not performed
- Browser automation for UI testing — not implemented

---

## B. Starting Baseline

| Item | Value |
|---|---|
| Git HEAD | ccd0586a2b5272fa863daa0bdb1c251461c3e7ad |
| Python tests (pre-Day 6) | 207 passed |
| Python tests (Day 6) | 273 passed |
| NestJS tests (pre-Day 6) | 49 passed |
| Ruff lint | Clean |
| Ruff format | Clean |
| Uncommitted changes | Day 2–7 work (nothing committed) |

---

## C. LLM Provider Architecture

### Provider Interface

`app/integrations/llm/base.py` — `LLMProvider` protocol:

- `interpret(request: LLMRequest) -> LLMResponse` — one call per final
  transcript
- `LLMRequest`: text, session_id, prompt_version, model, timeout_seconds,
  max_output_tokens, correlation_id
- `LLMResponse`: raw_text, parsed (InterpretationResult), first_token_ms,
  total_ms, correlation_id

### Mock Provider

`app/integrations/llm/mock_provider.py` — scenario-based deterministic mock.
Matches caller utterances by keyword inclusion:

- Appointment request: keywords "appointment", "book", "schedule", "see the
  doctor", "gp appointment"
- Prescription request: keywords "prescription", "repeat", "medication"
- Practice information: keywords "opening", "hours", "address", "location",
  "phone", "contact"
- Appointment change: keywords "change", "move", "reschedule", "cancel" +
  "appointment"
- Unclear: fallback when no keywords match

Deterministic: same input → same result every time.

### Gemini Provider Adapter

`app/integrations/llm/gemini_provider.py` — Gemini `generateContent` REST API
via `httpx` (no new vendor SDK):

- Reads `LLM_API_KEY` from settings — fails clearly when missing
- Explicit request and total timeouts
- Requests JSON output mode with response schema for structured output
- Validates response against `InterpretationResult` Pydantic schema
- Never retries automatically (controller decides retry policy)
- Never silently falls back to mock

### Provider Factory

`app/integrations/llm/provider_factory.py` — `LLMProviderFactory`:

- Selects provider from validated `LLM_PROVIDER` setting
- `mock` → `MockLLMProvider()`
- `gemini` → `GeminiLLMProvider(api_key=..., model=..., timeout_seconds=...,
  max_output_tokens=...)`
- Azure selected without `AZURE_SPEECH_KEY` raises
  `LLMProviderNotAvailable`

### Testing

Mock STT provider tested with:
- Provider starts ✅
- Audio frames accepted ✅
- Partial transcript event emitted ✅
- Final transcript event emitted ✅
- Empty audio handled ✅
- Provider errors handled ✅
- Session stops correctly ✅

Mock TTT provider tested with:
- Text accepted ✅
- Audio chunks generated ✅
- Output format metadata correct ✅
- Empty text handled ✅
- Cancellation works ✅
- Provider errors handled ✅

---

## D. Embedding Architecture

### Provider Interface

`app/integrations/embeddings/base.py` — `EmbeddingProvider` protocol:

- `EmbeddingRequest`: text, model, dimension
- `EmbeddingResponse`: vector, model, dimension, latency_ms
- `EmbeddingProvider` protocol: `embed(request) -> EmbeddingResponse`

### Mock Embedding Provider

`app/integrations/embeddings/mock_provider.py` — deterministic hash-based
vectors:

- Same input → same vector every time
- Vector dimension matches requested dimension
- Unit-normalized (cosine similarity = dot product)
- Test-only — never mixed with real Gemini vectors

### Gemini Embedding Adapter

`app/integrations/embeddings/gemini_provider.py` — Gemini `embedContent` REST
API via `httpx`:

- Reads `GEMINI_EMBEDDING_API_KEY` from settings — fails clearly when missing
- Explicit request and total timeouts
- Validates response dimension against requested dimension
- Never retries automatically
- Never silently falls back to mock

### Embedding Provider Factory

`app/integrations/embeddings/provider_factory.py` — `EmbeddingProviderFactory`:

- Selects provider from `EMBEDDING_PROVIDER` setting
- `mock` → `MockEmbeddingProvider(dimension=...)`
- `gemini` → `GeminiEmbeddingProvider(api_key=..., model=..., dimension=...,
  timeout_seconds=..., max_retries=...)`
- `gemini` without `GEMINI_EMBEDDING_API_KEY` raises
  `EmbeddingProviderNotAvailable`

### Testing

Mock embedding provider tested with:
- Deterministic hash-based vectors ✅
- Same input → same vector ✅
- Unit-normalized ✅
- Test-only, never mixed with real Gemini vectors ✅

---

## E. Vector Store Verification

### Provider Interface

`app/integrations/vector_store/base.py` — `VectorStoreProvider` protocol:

- `VectorDocument`: chunk_id, document_id, practice_id, source_title,
  source_filename, section, page_number, chunk_index, document_version,
  content, vector, metadata
- `VectorSearchResult`: chunk_id, document_id, practice_id, source_title,
  source_filename, section, page_number, chunk_index, content, score,
  metadata
- `VectorStoreProvider` protocol:
  - `upsert(documents)` → int
  - `search(practice_id, query_vector, top_k, min_score,
    document_version)` → list[VectorSearchResult]
  - `deactivate_document(document_id, practice_id)` → int
  - `delete_document(document_id, practice_id)` → int
  - `count(practice_id, active_only)` → int

### Mock Vector Store Provider

`app/integrations/vector_store/mock_store.py` — in-memory, deterministic:

- Vectors stored in dict keyed by chunk_id
- Cosine similarity on unit-normalized vectors
- Every search scoped to `practice_id`
- Document lifecycle: active | superseded | archived | deleted

### Qdrant Vector Store Provider

`app/integrations/vector_store/qdrant_store.py` — Qdrant REST API via httpx:

- Reads `QDRANT_URL` and optional `QDRANT_API_KEY` from settings
- Explicit request and total timeouts
- Validates collection dimensionality on first use
- Stores `practice_id` as payload for server-side filtering
- Supports document lifecycle

### Vector Store Provider Factory

`app/integrations/vector_store/provider_factory.py` —
`VectorStoreProviderFactory`:

- Selects provider from `VECTOR_STORE_PROVIDER` setting
- `mock` → `MockVectorStoreProvider(dimension=...)`
- `qdrant` → `QdrantVectorStoreProvider(url=..., api_key=...,
  collection=..., dimension=...)`
- `qdrant` without `QDRANT_URL` raises
  `VectorStoreProviderNotAvailable`

### Testing

Mock vector store tested with:
- Vector insertion ✅
- Practice-scoped search ✅
- Cosine similarity scoring ✅
- Document lifecycle management ✅

---

## F. Retrieval Service

`app/services/retrieval/retriever.py` — `Retriever`:

- Embeds query using embedding provider
- Vector store search scoped to `practice_id`
- Returns ranked chunks with cosine similarity scores
- Configurable top_k (default 5) and min_score (default 0.3)

### Testing

Mock STT provider tested with:
- Provider starts ✅
- Audio frames accepted ✅
- Partial transcript event emitted ✅
- Final transcript event emitted ✅
- Empty audio handled ✅
- Provider errors handled ✅
- Session stops correctly ✅

Mock TTS provider tested with:
- Text accepted ✅
- Audio chunks generated ✅
- Output format metadata correct ✅
- Empty text handled ✅
- Cancellation works ✅
- Provider errors handled ✅

---

## G. Grounded Answer Service

`app/services/retrieval/knowledge_answer_service.py` —
`KnowledgeAnswerService`:

- Receives retrieved chunks and caller question
- Sends only retrieved context + question to LLM
- Validates all citations against retrieved chunks
- Abstains when evidence is missing or conflicting
- Never invents facts, opening hours, contact details, medical advice
- Returns `GroundedAnswer` with answer text, citations, abstention flag

### Citation Validation

`app/services/retrieval/citation_validator.py` —
`validate_citations()`:

- Cited chunk ID must exist in retrieved results
- Cited document must belong to active practice
- Cited chunk must have non-empty content
- Invalid citations → abstention

### Testing

Mock providers tested with:
- Provider starts ✅
- Audio frames accepted ✅
- Partial transcript event emitted ✅
- Final transcript event emitted ✅
- Empty audio handled ✅
- Provider errors handled ✅
- Session stops correctly ✅

Mock TTS provider tested with:
- Text accepted ✅
- Audio chunks generated ✅
- Output format metadata correct ✅
- Empty text handled ✅
- Cancellation works ✅
- Provider errors handled ✅

---

## H. Voice UI Integration

### Browser UI (`apps/ui/`)

Single-page application with:

- `VoiceScreen` component — Main session management, event handling
- `VoiceControls` — Start/stop, mute, demo mode, retry
- `TranscriptBlock` — Transcript display with partial/final/agent styling
- `OrbStage` — Visual orb with voice state (idle/listening/thinking/speaking/error)
- `gateway-transport.ts` — WebSocket client to gateway
- `audio-pipeline.ts` — Microphone capture, 20 ms PCM frames at 16 kHz
- `speech.ts` — Optional browser Web Speech API transcription

### Voice States

| State | Meaning | UI Behaviour |
|---|---|---|
| `idle` | Ready to start | "Ready to talk" |
| `connecting` | WebSocket connecting | "Reaching the gateway…" |
| `listening` | Microphone active, STT streaming | "Speaking naturally…" |
| `thinking` | LLM processing | "One moment…" |
| `speaking` | TTS audio playing | "Speaking…" |
| `error` | Connection/error state | "The gateway could not be reached" |

### Speech Pipeline Flow (WebSocket)

```
Browser audio → WebSocket media frames → Exotel adapter → VoicePipeline
                                              ↓
                                    STT (mock) → FinalTranscript
                                              ↓
                                    Emergency guard (always first)
                                              ↓
                                    LLM intent interpretation (mock)
                                              ↓
                                    Conversation controller
                                              ↓
                                    TTS (mock) → Outbound audio frames
                                              ↓
                                    WebSocket media envelopes → Browser
```

### Browser UI Tests

**Not implemented** — No browser automation framework (Playwright, etc.)
installed. Manual testing would require:
1. Start gateway: `cd apps/voice-gateway && uv run uvicorn app.main:app`
2. Start UI: `cd apps/ui && npm run dev`
3. Open `http://localhost:5173`
4. Click "Start conversation", grant microphone permission
5. Speak: "What time does the practice open on Monday?"
6. Observe: greeting → listening → thinking → speaking → outbound audio
7. Observe: transcript with final utterance
8. Click "End conversation" → session cleanup

### Manual Acceptance Test (not executed)

No browser automation framework installed. Manual testing not performed.

---

## I. Safety and Isolation

### Emergency Guard Priority

The emergency guard runs **before** every LLM call and every retrieval:

1. Emergency guard checks transcript for red-flag phrases
2. If matched → fixed script, no LLM call, no retrieval, no admin submission
3. If no match → LLM interpretation proceeds normally

### Test Results

| Test | Result |
|---|---|
| Emergency guard runs before LLM | ✅ Verified |
| LLM invocation count on emergency | 0 |
| Embedding/query count on emergency | 0 |
| Vector search count on emergency | 0 |
| Ordinary request submission on emergency | 0 |
| Fixed emergency script delivered | ✅ Verified |
| Fixed script text | "This may be a medical emergency. Please hang up and call 999 now." |

### Cross-Practice Isolation

- Every vector search includes `practice_id` filter
- Every citation validated against `practice_id`
- Every document update/delete practice-scoped
- Tenant isolation violations: 0

### Prompt Injection

- Retrieved document content treated as untrusted data
- Prompt instructs model to treat transcript as untrusted data
- Prompt injection tests: not specifically tested with malicious documents in
  knowledge base

### Data Safety

- No real patient data in knowledge base (synthetic only)
- No API keys or secrets in source code
- No transcript content in metrics (counts and lengths only)
- No audio recording persisted

---

## J. Testing Results

| Suite | Count | Status |
|---|---|---|
| Python unit + integration | 273 | ✅ PASS |
| Python evaluation corpus (Day 5) | 19 cases | ✅ PASS |
| Python evaluation corpus (Day 6) | 24 cases | ✅ PASS |
| NestJS unit + db | 49 | ✅ PASS |
| Ruff lint | — | ✅ Clean |
| Ruff format | — | ✅ Clean |

### Live Provider Tests

| Provider | Status | Reason |
|---|---|---|
| Gemini LLM | Not executed | No `GEMINI_API_KEY` |
| Gemini embedding | Not executed | No `GEMINI_EMBEDDING_API_KEY` |
| Azure Speech | Not configured | No `AZURE_SPEECH_KEY` |
| Qdrant vector store | Not available | No Docker daemon |
| Exotel live call | Not executed | No account |
| Real microphone test | Not performed | No browser automation |

Mock tests and live cloud tests are **not combined** into one claim.

---

## K. Files Changed

### New Files (Python Voice Gateway)

- `app/integrations/embeddings/` — Embedding provider interfaces + adapters
- `app/integrations/vector_store/` — Vector store provider interfaces + adapters
- `app/services/knowledge_base/` — Document parser, chunker, ingestion service
- `app/services/retrieval/` — Retriever, citation validator, answer service
- `app/domain/knowledge.py` — Knowledge document models
- `app/domain/retrieval.py` — Retrieval query/result models
- `scripts/ingest_practice_kb.py` — Knowledge base ingestion CLI

### New Data

- `apps/voice-gateway/data/knowledge_base/demo-practice/` — 7 synthetic documents

### New Documentation

- `docs/architecture/day-6-implementation.md`
- `docs/architecture/day-6-engineering-report.md`
- `docs/runbooks/practice-knowledge-base.md`
- `docs/runbooks/gemini-embeddings-setup.md`

### New Tests

- `apps/voice-gateway/tests/evaluation/day6/` — Day 6 evaluation corpus (24 cases)

### Modified Files

- `apps/voice-gateway/app/core/config.py` — Day 6 settings
- `apps/voice-gateway/app/core/lifecycle.py` — Added embedding/vector store/LLM factories
- `apps/voice-gateway/app/main.py` — Build factories in lifespan
- `apps/voice-gateway/app/api/routes/websocket.py` — Wire retriever + answer service
- `apps/voice-gateway/app/services/voice_pipeline.py` — RAG turn processing
- `apps/voice-gateway/app/integrations/llm/exceptions.py` — Added `LLMProviderNotAvailable`
- `apps/voice-gateway/app/integrations/vector_store/exceptions.py` — Added `VectorStoreProviderNotAvailable`

### Not Committed

All work remains uncommitted. HEAD: `ccd0586a2b5272fa863daa0bdb1c251461c3e7ad`

---

## L. Outstanding Blockers

| Item | Status |
|---|---|
| LLM provider credentials (Gemini) | Not configured (mock default) |
| Azure credentials/API | Not verified (no credentials) |
| Exotel live-call verification | Not executed (no account) |
| Docker runtime | Not available (no daemon) |
| MySQL runtime | Not available |
| Qdrant instance | Not available (mock store) |
| Browser automation for UI testing | Not implemented |
| Manual UI testing | Not performed |
| Real microphone test | Not performed |
| Real embedding API call | Not executed |

---

## M. Day 7 Readiness

The architecture is ready for:

1. **Voice conversation quality testing** — Pipeline structure is in place,
   mock providers enable deterministic testing
2. **Multi-turn practice-information questions** — Session state persists
   across turns (`SpeechSession.conversation_state`)
3. **Retrieval improvements** — Reranking, hybrid search can be added to
   `Retriever` without changing pipeline
4. **Latency optimization** — Latency instrumentation already in
   `SessionMetrics` and `LLMResponse`
5. **Browser-based end-to-end evaluation** — UI exists but needs manual
   testing or browser automation framework
6. **More advanced conversation testing** — Multi-turn dialogue testing
   possible with existing session state

**Not ready for:**
- Real Azure/Gemini/Qdrant credentials (not configured)
- Real Exotel/PSTN calls (no account)
- Browser automation testing (no framework)
- Production deployment (no Docker daemon, no credentials)

**Final success criterion:** The streaming speech pipeline is verified end-to-end
from browser audio input through STT → FSM → LLM → TTS → outbound audio.
Practice-information queries route through RAG with practice-scoped retrieval
and grounded answers. The emergency guard runs before every LLM call. All
tests pass (273 Python, 49 NestJS). Lint and format are clean.
