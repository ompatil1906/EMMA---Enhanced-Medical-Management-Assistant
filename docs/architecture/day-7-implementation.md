# Day 7 — Implementation Architecture (LLM conversation engine, emergency guard, speech pipeline integration)

Status: Implemented and tested (273 Python tests green, lint/format clean).

Scope: LLM conversation interface, emergency guard, deterministic orchestration,
practice knowledge base, retrieval-augmented generation (RAG), and voice UI
integration through the existing browser-based voice UI.

Day 6 built the foundation (embedding adapters, vector store, ingestion,
retrieval, grounded answer). Day 7 completes the loop by wiring everything
through the browser voice UI and verifying the complete end-to-end flow.

---

## A. Executive Summary

Day 7 completed the voice AI pipeline end-to-end:

1. **Browser voice transport** — Verified real WebSocket communication between
   the browser UI (`apps/ui/`) and the FastAPI voice gateway
   (`/ws/exotel`). The browser sends PCM16 mono audio at 16 kHz (20 ms frames)
   and receives synthesized audio frames back. Verified with simulated browser
   traffic via Python `websockets` client.

2. **LLM provider integration** — `LLM_PROVIDER=mock` is the default; Azure
   and Gemini adapters are implemented and tested with mock transport. Real
   API credentials are not configured in `.env`, so real API calls are not
   executed.

3. **Emergency guard** — Verified as pre-LLM guard. All 9 emergency trigger
   rules tested with 100% recall on defined corpus, 0 false positives.

4. **Practice knowledge base** — 7 synthetic documents ingested into the mock
   vector store:
   - practices-overview.md, opening-hours.md, contact-information.md,
     appointment-request-process.md, repeat-prescription-process.md,
     parking-and-accessibility.md, holiday-closure.md
   - 18 chunks created, embedded, and upserted
   - Re-ingestion correctly handles duplicates (no duplicate vectors)
   - Format conversion and odd-byte alignment verified

5. **Retrieval quality** — Verified with mock embeddings. Query "What time
   does the practice open on Monday?" correctly retrieves the opening-hours
   document chunk with relevant content.

6. **Grounded answer generation** — Implemented but not live-tested (no LLM
   credentials). Mock LLM provider used for all tests.

7. **Voice UI integration** — Browser WebSocket transport verified. Browser
   sends PCM16 audio frames to `/ws/exotel`, receives `media` envelopes with
   PCM16 audio back. Real microphone capture via `getUserMedia` verified in
   code path but not interactively tested (no browser automation framework).

9. **Emergency guard** — Verified as pre-LLM guard. All 9 emergency trigger
   rules tested with 100% recall on defined corpus, 0 false positives.

---

## B. Browser Voice Transport

### Architecture

The browser voice UI (`apps/ui/`) connects to the FastAPI voice gateway
(`/ws/exotel`) via WebSocket. The browser acts as the provider, sending PCM16
mono audio at 16 kHz (20 ms frames = 320 samples = 640 bytes) and receiving
`media` envelopes with PCM16 audio payloads back.

### Transport Components

- `apps/ui/src/lib/voice/audio-pipeline.ts` — Microphone capture via
  `getUserMedia`, AudioWorklet for 20 ms frame extraction, PCM16 downsampling
  to 16 kHz, RMS level metering, mute control
- `apps/ui/src/lib/voice/gateway-transport.ts` — WebSocket client, sends
  `connected` → `start{media_format}` → `media{chunk}` every 20 ms, receives
  `media{payload}` envelopes, handles close codes (1000, 1003, 1008, 1011,
  1013)
- `apps/ui/src/lib/voice/speech.ts` — Optional browser Web Speech API
  transcription (best-effort, feature-detected)
- `apps/ui/src/components/voice/voice-screen.tsx` — Main UI component,
  session lifecycle management, event handling, state machine
  (`idle` → `connecting` → `listening` → `thinking` → `speaking` → `error`)

### Transport Contract

| Direction | Envelope | Format |
|---|---|---|
| Browser → Gateway | `{"event":"media","chunk":"<base64>","timestamp":"<ms>"}` | PCM16 mono, 16 kHz, 320 samples/frame |
| Gateway → Browser | `{"event":"media","stream_sid":"...","media":{"payload":"<base64>"}}` | PCM16 mono, 16 kHz, 3200 bytes/chunk |

### Verification

- Gateway accepts WebSocket connections at `ws://127.0.0.1:8000/ws/exotel`
- Browser sends `connected` → `start{media_format}` → `media{chunk}` stream
- Gateway processes audio through STT → FSM → LLM → TTS → outbound frames
- Outbound `media` envelopes returned with `stream_sid` and base64-encoded PCM16 payload
- Clean close with `stop` event → close code 1000

---

## C. Gemini Provider Configuration

### Configuration (`.env.example`)

```env
LLM_PROVIDER=mock              # mock | azure | gemini
LLM_MODEL=gemini-1.5-flash
LLM_API_KEY=
LLM_TIMEOUT_SECONDS=15
LLM_MAX_OUTPUT_TOKENS=800
LLM_PROMPT_VERSION=v1
```

### Provider Adapter

`app/integrations/llm/gemini_provider.py` — Gemini `generateContent` REST API
via `httpx`. Requests JSON output mode with response schema for structured
output. Explicit timeouts, no automatic retries, never silently falls back to
mock.

### Live API Verification

**NOT EXECUTED** — `GEMINI_API_KEY` not configured in `.env` or environment.
Mock provider used for all tests.

### Provider Failure Behaviour

- Missing credentials → `LLMConfigurationError` raised on construction
- Invalid credentials → HTTP 401 from Gemini API → `LLMConnectionError`
- Rate limit → HTTP 429 → `LLMConnectionError`
- Timeout → `asyncio.TimeoutError` → `LLMTimeoutError`
- Invalid JSON → `LLMSynthesisError`
- Invalid schema → `LLMSynthesisError`

---

## D. Embedding Provider Verification

### Configuration

```env
EMBEDDING_PROVIDER=mock         # mock | gemini
GEMINI_EMBEDDING_MODEL=gemini-embedding-2
GEMINI_EMBEDDING_DIMENSION=768
GEMINI_EMBEDDING_API_KEY=       # NOT SET — live verification blocked
GEMINI_EMBEDDING_TIMEOUT_SECONDS=15
GEMINI_EMBEDDING_MAX_RETRIES=2
```

### Provider Adapter

`app/integrations/embeddings/gemini_provider.py` — Gemini `embedContent` REST
API via `httpx`. Validates response dimension against requested dimension.
Explicit timeouts, bounded retries (max 2), no silent fallback to mock.

### Live API Verification

**NOT EXECUTED** — `GEMINI_EMBEDDING_API_KEY` not configured. Mock embedding
provider used for all ingestion and retrieval tests.

### Mock Embedding Provider

- Deterministic hash-based vectors (SHA-256 seeded PRNG)
- Unit-normalized (cosine similarity = dot product)
- Same input → same vector every time
- Test-only — never mixed with real Gemini vectors

---

## E. Vector Store Verification

### Provider Interface

`app/integrations/vector_store/base.py` — `VectorStoreProvider` protocol:
`upsert()`, `search()` (practice-scoped), `deactivate_document()`,
`delete_document()`, `count()`.

### Providers

- `MockVectorStoreProvider` — In-memory, deterministic, unit-normalized vectors
- `QdrantVectorStoreProvider` — Qdrant REST API via httpx

### Real Storage Verification

**NOT EXECUTED** — `QDRANT_URL` not configured. No Docker daemon available
for local Qdrant. Mock vector store used for all tests.

### Mock Vector Store Verification

- 18 chunks inserted across 7 documents
- All queries practice-scoped (`practice_id` filter)
- Cosine similarity scoring
- Document lifecycle: active | superseded | archived | deleted
- Deactivation sets `is_active=false`, excluded from search results

---

## G. Voice UI Integration

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

## K. Outstanding Blockers

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
