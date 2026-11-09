# Day 9 — Engineering Report

**Report date:** 2026-11-08 (Day 9 close-out).
**Git HEAD:** `3d40b9409b635623f22ce1cd0220f1f23f647572`
**Git status:** uncommitted Day 9 changes only (no commits or pushes made).

---

## A. Executive Summary

Day 9 delivers a reliable, stateful multi-turn administrative voice assistant
on the verified Day 8 stack. The deterministic controller now owns conversation
state, slot filling, corrections, multi-request independence, explicit
confirmation, cancellation, and the backend submission lifecycle. The LLM
remains an interpreter only.

Measured outcomes:

- **Offline regression:** `338 passed, 4 skipped` (the 4 skips are opt-in live
  tests).
- **Day 9 corpus:** 34 synthetic multi-turn scenarios, all passing.
- **Focused unit tests:** confirmation classification, targeted confirmation,
  correction invalidation, duplicate-submission prevention, tenant-field
  stripping, context-aware mock slot filling, and barge-in.
- **Live Gemini LLM:** a full appointment utterance extracted
  `preferred_date=Tuesday`, `preferred_time=morning` in ~3.3–3.9 s across runs.
- **Live pipeline:** one real Gemini turn reached `READY_FOR_CONFIRMATION` and
  asked for confirmation, with no backend client and no submission.
- **Live retrieval:** 4/4 queries returned in-practice chunks;
  p50 ≈ 806 ms, p95 ≈ 852 ms (single run).
- **Cross-practice isolation:** zero chunks for a different practice id.
- **Live grounded answer:** cited answer produced from the local Qdrant store.
- **Azure TTS:** live synthesis produced 33 chunks / 104,832 bytes of raw PCM.
- **Azure STT:** WAV-file recognition returned the exact phrase; the adapter's
  push-stream continuous path returned no transcript in this environment.
- **Ruff lint/format:** clean.

Emergency ordering, explicit confirmation, no-false-claim behaviour, session
isolation, and duplicate/out-of-order turn rejection are covered by the corpus.

---

## B. Day 8 Baseline and What Carried Forward

Day 8 was verified live with:

- LLM `gemini-3.8-flash`,
- embeddings `gemini-embedding-2` at 768 dimensions,
- Qdrant local persistent mode at `./data/qdrant`,
- 7 documents / 18 chunks / 18 vectors,
- 5/5 retrieval queries and 4/4 grounded answers,
- zero practice leakage.

Day 9 preserves that stack unchanged. The only provider-side fixes are:

1. **Gemini embedding timeout bug:** the request timeout used
   `request.dimension` as the timeout value. It now uses
   `self._timeout_seconds`.
2. **Grounded answer attribute bug:** the pipeline referenced a nonexistent
   `answer_result.answer.response_text`. It now uses
   `answer_result.answer.answer`.

Both were latent defects, not new provider integrations.

---

## C. What Was Built

### C.1 Typed request lifecycle

`app/domain/conversation.py` defines:

- `RequestStatus`: `NEW`, `COLLECTING_INFORMATION`,
  `READY_FOR_CONFIRMATION`, `SUBMITTING`, `SUBMITTED`, `FAILED`, `CANCELLED`.
- `AdministrativeRequest` with merged details, derived missing fields, and
  lifecycle timestamps.
- `ConversationState` with bounded turn history, awaiting-field state,
  confirmation state, grounded-answer context, confirmed submissions, and
  backend errors.

The status is derived from the **merged** missing fields, which fixes multi-turn
slot filling: a second turn that completes a request now moves it to
`READY_FOR_CONFIRMATION`.

### C.2 Deterministic controller

`app/services/conversation/conversation_controller.py` now provides:

- deterministic required-field policy,
- lexical confirmation classification,
- correction detection and confirmation invalidation,
- targeted cancellation,
- tenant-field sanitisation,
- deterministic slot-fill backstop,
- per-request submission marking and backend success/failure handling.

### C.3 Pipeline

`app/services/voice_pipeline.py` now provides:

- a per-session turn lock,
- duplicate/out-of-order final-transcript rejection,
- emergency ordering before every provider/backend call,
- bounded LLM context,
- deterministic slot fill before the LLM,
- confirmation/correction/cancel routing,
- RAG routing and multi-turn follow-ups,
- per-request submission lifecycle,
- barge-in cancellation.

### C.4 Evaluation harness

`tests/evaluation/day9/` drives the real `VoicePipeline` and controller with a
scripted LLM, a fake practice backend, and a fake retriever. It contains 34
scenarios plus session-isolation, bounded-context, and RAG-answer tests.

`tests/unit/test_day9_dialogue.py` covers the deterministic pieces directly.

`tests/live/test_day9_live_smoke.py` contains four opt-in live tests.

---

## D. Conversation State Model

The lifecycle is per request, not per call:

```
NEW
 └─→ COLLECTING_INFORMATION
      └─→ READY_FOR_CONFIRMATION
           ├─→ SUBMITTING ─→ SUBMITTED
           │                └─→ FAILED
           └─→ CANCELLED
```

Rules enforced by the model and controller:

- submitted requests are immutable,
- cancelled requests cannot be submitted,
- a request cannot be submitted twice,
- one request's failure does not mark another request successful,
- confirmation is scoped to the request(s) being confirmed.

---

## E. Slot Filling

Required fields are deterministic policy:

| Request type | Required fields |
| --- | --- |
| `appointment-request` | `preferred_date`, `preferred_time` |
| `appointment-change-request` | `preferred_date`, `preferred_time` |
| `prescription-request` | none |
| `practice-administration` | none |

The model's `missing_fields` is ignored for policy decisions. This prevents a
model from declaring an incomplete request ready.

Two mechanisms fill slots:

1. **LLM extraction** with bounded context.
2. **Deterministic backstop** (`extract_awaited_slot`) for a short, explicit
   date/time answer while one request is awaiting that field.

The backstop exists because live `gemini-3.8-flash` returned an empty
`requests` list for a bare "Wednesday" even with
`awaiting_field: preferred_date` in the context. It is deliberately narrow: it
does not run during a pending confirmation, does not accept administrative
keywords or yes/no, and does not guess when several requests are waiting for
different slots.

---

## F. Corrections

When a caller changes a value after read-back:

- `interpretation_changes_state()` detects that the new value differs,
- the pending confirmation is invalidated,
- the merged request keeps the latest explicit value,
- the controller re-reads the corrected details and asks for confirmation
  again.

A correction never submits by itself. A stale model response cannot overwrite a
newer correction because turns are serialised and out-of-order sequences are
rejected.

---

## G. Multiple Requests and Independent Lifecycles

One utterance can produce several requests (for example an appointment and a
prescription). They are stored independently:

- each has its own details and missing fields,
- filling one request's slot does not touch the other,
- cancelling one request leaves the other ready,
- submitting one request does not submit the other,
- a backend failure for one request marks only that request `FAILED`.

The corpus covers combined submission, independent missing fields, targeted
cancellation, and partial backend failure.

---

## H. Explicit Confirmation

Confirmation is classified lexically by the controller:

- `confirm`: "yes", "yeah", "go ahead", "that's right", …
- `reject`: "no", "not now", …
- `cancel`: "cancel that", "never mind", …
- `ambiguous`: "maybe", "I'm not sure", …
- `other`.

No backend call happens for `reject`, `cancel`, `ambiguous`, or `other`.
Cancellation wins over a bare "no". The model is never the authority for
submission.

The pipeline marks each request `SUBMITTING` only immediately before its
backend call, and marks it `SUBMITTED` only after backend confirmation. The
spoken success text explicitly says the action is a request, not a confirmed
appointment or prescription.

---

## I. Contextual RAG Follow-ups

- Practice-information questions route to retrieval + grounded answer.
- Retrieval always uses the trusted server-side `practice_id` and active
  document status.
- A short follow-up after a grounded answer is resolved against the previous
  topic ("What about Saturday?" after opening hours).
- Empty retrieval abstains honestly.
- The grounded answer is spoken verbatim and never claims an administrative
  action.
- The bounded LLM context includes the previous grounded topic but never the
  full transcript.

---

## J. Emergency Safety

The deterministic emergency guard runs before:

- the Gemini LLM,
- embeddings,
- Qdrant retrieval,
- any ordinary backend submission.

On a match:

- all pending requests are cancelled,
- pending confirmations are invalidated,
- the fixed script is spoken:

> This may be a medical emergency. Please hang up and call 999 now.

The corpus covers emergency on the first turn, during slot filling, during
confirmation, and combined with an administrative request. All stay emergency
and perform zero LLM/retrieval/backend calls.

---

## K. Test Results

### K.1 Offline suite

Command:

```bash
cd apps/voice-gateway
uv run pytest -o addopts="" -q
```

Result:

```
338 passed, 4 skipped in 11.83s
```

The 4 skips are the opt-in live tests (`EMMA_RUN_LIVE_TESTS` not set).

### K.2 Day 9 corpus

34 scenarios covering:

- appointment request → confirmation → submission,
- rejection and ambiguous confirmation,
- cancellation before confirmation,
- correction after read-back,
- missing date/time and multi-turn slot filling,
- prescription and appointment-change requests,
- multiple independent requests,
- targeted cancellation,
- practice-information and follow-ups,
- emergency on first turn / during slot filling / during confirmation,
- duplicate and out-of-order turns,
- empty and low-confidence transcripts,
- repeated confirmation without double submission,
- backend rejection/timeout and partial multi-request failure,
- model cannot mark an incomplete request ready,
- model-supplied practice/tenant IDs stripped,
- retrieval uses the trusted server practice id.

### K.3 Focused unit tests

`tests/unit/test_day9_dialogue.py` covers:

- confirmation classification,
- request-type inference,
- no submission without explicit confirmation,
- targeted confirmation,
- incomplete request cannot be submitted,
- correction invalidates confirmation,
- duplicate submission prevention,
- tenant-field stripping,
- context-aware mock slot filling,
- last-mentioned date wins,
- follow-up topic resolution,
- Day 5 behaviour preserved without context,
- barge-in cancellation with and without in-flight synthesis.

### K.4 Lint and format

```bash
uv run ruff check .
uv run ruff format --check .
```

Both clean.

---

## L. Live Provider Status

### L.1 Gemini LLM

- Model: `gemini-3.8-flash`.
- A full utterance — "I'd like to book an appointment for Tuesday morning
  please." — extracted one appointment request with
  `preferred_date=Tuesday`, `preferred_time=morning`.
- Measured interpretation latency: ~3.3–3.9 s across two runs (variable).
- A live pipeline turn reached `READY_FOR_CONFIRMATION` with no backend client.

An important finding: with a free-form `details` object, the live model returned
empty details and invented `patient_name`/`date_of_birth` missing fields. The
Gemini response schema now declares the allowed detail keys explicitly, and the
prompt forbids invented identity fields. After that change, extraction worked.

### L.2 Gemini embeddings and Qdrant

- Embeddings: `gemini-embedding-2`, 768 dimensions.
- Store: Qdrant local persistent mode at `./data/qdrant`.
- Active vectors for the demo practice: 18.
- Cross-practice vectors: 0.
- Retrieval (4 queries) across two runs: p50 662–806 ms, p95 852–975 ms,
  min 621–743 ms, max 852–975 ms. Latency varies run to run.

### L.3 Grounded answer

The live answer service produced:

> On Monday, Riverbend Medical Practice opens at 8:00 AM [1]. Phone lines also
> open at 8:00 AM [2].

with valid chunk citations.

### L.4 Azure TTS

Live synthesis of "I would like to book an appointment for Tuesday morning."
produced:

- 33 chunks,
- 104,832 bytes of raw PCM at 16 kHz,
- `completed=True`, `cancelled=False`,
- ~3.2 s latency.

### L.5 Azure STT

Two paths were exercised:

- **WAV file path:** the synthesized PCM was wrapped in a 16 kHz WAV file and
  recognized with `recognize_once()`. Result:
  `RecognizedSpeech`, text exactly
  "I would like to book an appointment for Tuesday morning."
- **Push-stream continuous path (the adapter's production path):** the same
  PCM produced 0 partials, 0 finals, and no recognition errors in this
  environment. This path remains unverified.

The file-based result proves the Azure credentials, region, language, and audio
are valid. The push-stream gap is reported honestly and is not claimed as
working.

### L.6 Microphone / acoustic barge-in

Not measured. No browser automation or real microphone capture was used.
Barge-in is verified with mock providers only (queued-frame discard,
framer reset, and cooperative TTS cancellation).

---

## M. Limitations and Outstanding Work

- **Azure STT push-stream path:** no transcript in local testing despite valid
  credentials and successful file-based recognition. Needs focused debugging
  before claiming real-time streaming STT.
- **Acoustic barge-in:** not measured with a real microphone or browser.
- **Exotel/PSTN:** out of scope and unverified.
- **Docker/MySQL:** runtime unavailable; Qdrant local mode used.
- **Live latency:** Gemini interpretation was ~3.3 s in the measured run; this
  is acceptable for the prototype but should be tracked.
- **Model output shape:** live models can still return unexpected shapes; the
  deterministic controller and schema constraints are the safety net, not a
  guarantee of model obedience.

---

## N. Git Status

```
 M apps/voice-gateway/app/api/routes/websocket.py
 M apps/voice-gateway/app/domain/conversation.py
 M apps/voice-gateway/app/integrations/embeddings/gemini_provider.py
 M apps/voice-gateway/app/integrations/llm/base.py
 M apps/voice-gateway/app/integrations/llm/gemini_provider.py
 M apps/voice-gateway/app/integrations/llm/mock_provider.py
 M apps/voice-gateway/app/services/conversation/conversation_controller.py
 M apps/voice-gateway/app/services/conversation/intent_interpreter.py
 M apps/voice-gateway/app/services/voice_pipeline.py
 M apps/voice-gateway/pyproject.toml
 M apps/voice-gateway/tests/integration/test_speech_pipeline.py
?? apps/voice-gateway/tests/evaluation/day9/
?? apps/voice-gateway/tests/live/
?? apps/voice-gateway/tests/unit/test_day9_dialogue.py
?? docs/architecture/day-9-engineering-report.md
?? docs/architecture/day-9-implementation.md
?? docs/runbooks/multiturn-conversation-testing.md
```

HEAD: `3d40b9409b635623f22ce1cd0220f1f23f647572`

No commits or pushes were made.
