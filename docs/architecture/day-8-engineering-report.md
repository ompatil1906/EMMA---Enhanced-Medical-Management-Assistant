# Day 8 — Engineering Report

**Report date:** 2026-11-07 (Day 8 close-out).
**Git HEAD:** `ccd0586a2b5272fa863daa0bdb1c251461c3e7ad`
**Git status:** Uncommitted changes (no commits made per instructions).

---

## A. Executive Summary

Day 8 successfully activated real Gemini-powered semantic retrieval with
persistent Qdrant storage, without requiring Docker. All core components are
verified working:

- **Gemini LLM** (`gemini-3.8-flash`): Live intent extraction verified
- **Gemini embeddings** (`gemini-embedding-2`): Live 768-dimension vectors verified
- **Qdrant local persistent mode**: File-based storage, no Docker/server required
- **Knowledge base ingestion**: 7 documents, 18 chunks, 18 vectors inserted
- **Semantic retrieval**: 5/5 test queries return correct sources
- **Grounded answers**: Gemini generates answers with validated citations
- **Practice isolation**: Zero cross-practice leakage
- **Python tests**: 273 passing (regression preserved)
- **NestJS**: 49 tests passing, build passing
- **Ruff lint/format**: Clean

Mock providers remain available for ordinary tests. Real Azure STT/TTS,
Exotel live calls, and Docker/Qdrant server are not available and remain
unverified.

---

## B. Day 7 Baseline and Discrepancy

### Was the Day 7 Gemini response genuinely live?

**No.** The Day 7 report contained a contradiction:

1. One section showed a JSON intent-extraction result that appeared to be from
   a real Gemini API call.
2. Another section stated no real Gemini API call was executed because
   credentials were unavailable.

**Resolution:** Phase 0 investigation confirmed:

- The `LLM_MODEL=gemini-1.5-flash` in `.env` returns HTTP 404 — this model is
  not available for the `v1beta` `generateContent` API.
- A live smoke test with `gemini-3.8-flash` succeeded (intent extraction,
  5146ms latency).
- The embedding provider had a bug: the API key was not passed as a `?key=`
  query parameter on the `embedContent` URL, causing HTTP 403 errors. This was
  fixed in Day 8.
- The JSON shown in the Day 7 report was likely from a mocked provider or a
  hand-constructed fixture, not a live API call.

**Initial Git HEAD:** `ccd0586a2b5272fa863daa0bdb1c251461c3e7ad`

**Actual test counts:**
- Python unit + integration: 273 passed
- Day 5 evaluation: 19 cases (part of the 273)
- Day 6 evaluation: 24 cases (part of the 273)
- NestJS: 49 passed

**Existing uncommitted changes:** All Day 3-7 work plus Day 8 changes are
uncommitted (no commits made per instructions).

---

## C. Gemini LLM

| Attribute | Value |
|-----------|-------|
| Provider | Gemini (Google Generative Language API) |
| Model identifier | `gemini-3.8-flash` |
| Credential availability | Configured via `LLM_API_KEY` in `.env` (key present) |
| Live API request result | PASS — intent extraction successful |
| Intent extraction | `appointment-request` with missing fields identified |
| Multiple request extraction | PASS — multiple independent requests preserved |
| Latency | ~5.1 seconds (first token == total, non-streaming) |
| Failure handling | `LLMConnectionError` on HTTP errors, `LLMTimeoutError` on timeout, `LLMSynthesisError` on validation failure |

**Evidence:** Live call to `https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent` returned valid JSON with `intent: "appointment-request"`.

---

## D. Gemini Embeddings

| Attribute | Value |
|-----------|-------|
| Provider | Gemini (Google Generative Language API) |
| Model identifier | `gemini-embedding-2` |
| Vector dimension | 768 |
| Live API request result | PASS — embedding call successful |
| Latency | ~989ms per call |
| Dimension consistency | PASS — two calls, same 768-dimension output |
| Finite values | PASS — all values are finite floats |
| Failure handling | `EmbeddingConnectionError` on HTTP errors, `EmbeddingTimeoutError` on timeout |

**Evidence:** Live call to `https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2:embedContent` returned a 768-element vector.

---

## E. Qdrant

| Attribute | Value |
|-----------|-------|
| Qdrant client version | `qdrant-client==1.20.0` |
| Mode | Local persistent (file-based) |
| Storage directory | `apps/voice-gateway/data/qdrant/` |
| Collection | `practice_knowledge` |
| Vector dimension | 768 |
| Distance metric | Cosine |
| Actual vector count | 18 (after ingestion) |
| Ingestion result | 7 docs, 18 chunks, 18 vectors, 0 failures |
| Restart persistence | PASS — count matches after reconnecting to same path |

**Configuration:**
```env
VECTOR_STORE_PROVIDER=qdrant
QDRANT_MODE=local
QDRANT_LOCAL_PATH=./data/qdrant
QDRANT_COLLECTION=practice_knowledge
```

---

## F. Retrieval

### Test corpus

Five synthetic queries against the `demo-practice-001` practice:

| # | Query | Expected source |
|---|-------|-----------------|
| 1 | What time does the practice open on Monday? | Opening Hours |
| 2 | When can I visit at start of working week? | Opening Hours |
| 3 | How can I get in touch with the practice? | Contact Information |
| 4 | Procedure for repeat prescription? | Repeat Prescription |
| 5 | Specialist available tomorrow? | Abstention (unsupported) |

### Metrics (live, Gemini + Qdrant)

| Metric | Result |
|--------|--------|
| Recall@5 | 4/4 (queries 1-4 return correct source in top 5) |
| Precision@5 | Varies by query; correct source always in top 1-2 |
| Correct-source retrieval rate | 100% (queries 1-4) |
| Irrelevant-result rate | 0% for supported queries |
| Empty-result rate | 0% (all queries return results) |
| Cross-practice leakage | 0% (verified with Practice A vs Practice B) |
| Stale document exclusion | Not yet tested (document lifecycle) |
| Retrieval latency | ~570-1320ms (embedding + search) |

**Mock vs live:** Mock metrics are NOT reported as semantic retrieval results.
Mock embeddings remain available for unit tests only.

---

## G. Grounded Generation

| Test | Result |
|------|--------|
| Gemini answer generation | PASS — grounded answers generated for supported questions |
| Citation validation | PASS — numeric markers mapped to chunk IDs, validated against practice |
| Unsupported questions | PASS — controlled abstention with reason |
| Fabricated citations | PASS — rejected (not in retrieved chunks) |
| Cross-practice citation protection | PASS — practice_id filter enforced |
| Superseded document versions | Not yet tested (document lifecycle) |

**Citation validation rules enforced:**
1. Cited chunk ID must exist in retrieval results
2. Chunk must belong to the requesting practice
3. Chunk content must be non-empty
4. Invalid citations → answer abstained

---

## H. Browser Voice Integration

| Attribute | Status |
|-----------|--------|
| WebSocket endpoint | `ws://127.0.0.1:8000/ws/exotel` (existing, unchanged) |
| Audio format | PCM16 (existing, unchanged) |
| Real Gemini invocation | Wired via `KnowledgeAnswerService` + `GeminiLLMProvider` |
| Real embedding invocation | Wired via `Retriever` + `GeminiEmbeddingProvider` |
| Qdrant retrieval | Wired via `QdrantVectorStoreProvider` (local mode) |
| Grounded answer | Wired via `KnowledgeAnswerService` |
| Citation display | Returned to UI as citation objects with source metadata |
| TTS status | Mock TTS provider (Azure TTS not available — credentials missing) |
| Manual microphone testing | Not performed (no browser automation stack) |

**Note:** The voice pipeline's speech loop (`_session_loop_speech` in
`apps/voice-gateway/app/api/routes/websocket.py`) already wires the retriever
and knowledge answer service when `rag_configured` is true. No architectural
changes were needed.

---

## I. Emergency Safety

The emergency guard runs before every LLM and retrieval call:

| Test | Result |
|------|--------|
| Emergency guard execution | PASS — deterministic pattern matching, no LLM involved |
| Gemini LLM calls for emergency | 0 (guard blocks before LLM) |
| Embedding calls for emergency | 0 (guard blocks before embedding) |
| Qdrant queries for emergency | 0 (guard blocks before Qdrant) |
| Backend submissions for emergency | 0 (no submission) |
| Fixed emergency response | "This may be a medical emergency. Please hang up and call 999 now." |

**Trigger patterns:** chest pain, shortness of breath, serious bleeding, mental health crisis, stroke, heart attack, 999/emergency services.

**Important:** This is a deterministic safety stop, NOT clinical triage. Not
clinically validated.

---

## J. Test Results

| Suite | Count | Status |
|-------|-------|--------|
| Python unit tests | 273 | PASS |
| Python integration tests | Included in 273 | PASS |
| Day 5 evaluation | 19 cases | PASS |
| Day 6 evaluation | 24 cases | PASS |
| Day 8 Gemini live tests | Manual | PASS |
| Day 8 embedding tests | Manual | PASS |
| Day 8 Qdrant tests | Manual | PASS |
| Retrieval evaluation | 5 queries | PASS |
| NestJS tests | 49 | PASS |
| NestJS build | tsc | PASS |
| Ruff check | Clean | PASS |
| Ruff format | Clean | PASS |
| Browser UI tests | Not performed | Manual recommended |
| Microphone tests | Not performed | Not available |

---

## K. Files Changed

### New
- `docs/architecture/day-8-implementation.md`
- `docs/architecture/day-8-engineering-report.md` (this file)
- `docs/runbooks/qdrant-local-persistence.md`

### Modified
- `apps/voice-gateway/app/core/config.py`
- `apps/voice-gateway/app/integrations/embeddings/base.py`
- `apps/voice-gateway/app/integrations/embeddings/gemini_provider.py`
- `apps/voice-gateway/app/integrations/embeddings/mock_provider.py`
- `apps/voice-gateway/app/integrations/llm/base.py`
- `apps/voice-gateway/app/integrations/llm/gemini_provider.py`
- `apps/voice-gateway/app/integrations/vector_store/qdrant_store.py`
- `apps/voice-gateway/app/integrations/vector_store/provider_factory.py`
- `apps/voice-gateway/app/services/knowledge_base/ingestion_service.py`
- `apps/voice-gateway/app/services/retrieval/citation_validator.py`
- `apps/voice-gateway/app/services/retrieval/knowledge_answer_service.py`
- `apps/voice-gateway/app/services/retrieval/retriever.py`
- `.env`
- `.gitignore`

### Removed
- Temporary debug scripts (`scripts/test_embedding_models.py`,
  `scripts/test_embedding_dimensions.py`, `scripts/debug_citations.py`,
  `scripts/debug_prescription.py`) — used for verification only

---

## L. Outstanding Blockers

| Blocker | Details |
|---------|---------|
| Azure STT/TTS credentials | Not available — mock providers used; real speech not verified |
| Exotel live calls | No account — not tested |
| Docker runtime | No daemon — Qdrant local mode used instead |
| Real Qdrant server | Not tested — local persistent mode only |
| Browser automation | No stack installed — manual UI testing recommended |
| MySQL / NestJS backend | Not connected to voice gateway (PRACTICE_API_BASE_URL unset) |
| Real Azure API | No credentials — verified SDK importability only |

---

## M. Day 9 Readiness

| Capability | Readiness |
|------------|-----------|
| Multi-turn voice conversations | Ready — FSM + controller in place |
| Live Gemini intent interpretation | Verified |
| Live Gemini embeddings | Verified |
| Persistent RAG | Verified (Qdrant local mode) |
| Grounded answers | Verified |
| Browser voice UI | Wired, manual testing needed |
| Failure and recovery testing | Not yet performed |
| Performance optimisation | Not yet performed |

Day 8 is complete. The core Gemini + Qdrant RAG pipeline is verified and ready
for Day 9 multi-turn voice conversation work.

---

## N. Git Status

```
git status --short
git rev-parse HEAD
```

(No commits made per instructions. All changes are uncommitted.)
