# Day 9 — Implementation: Stateful Multi-Turn Administrative Voice Assistant

**Status:** implemented and verified offline; live Gemini/Qdrant paths exercised.
**Scope:** conversation reliability for administrative requests. No new frontend,
no new LLM/embedding/vector provider, no FSM, no conversation framework.

---

## 1. Overview

Day 9 turns the single-turn voice gateway into a stateful administrative
assistant. The model still only *interprets* one final transcript at a time;
the deterministic `ConversationController` owns all conversation state and is
the only component allowed to trigger a backend submission.

The implementation adds five reliability layers on top of the verified Day 8
stack (Gemini LLM, Gemini embeddings, local persistent Qdrant):

1. **Typed request lifecycle** — every administrative request has its own
   independent state machine.
2. **Deterministic slot policy** — required fields are computed server-side,
   never taken from the model.
3. **Explicit confirmation** — lexical, deterministic classification of the
   caller's reply; a model interpretation can never authorise a submission.
4. **Turn integrity** — per-session serialisation plus duplicate/out-of-order
   rejection, so a stale response cannot overwrite a newer correction.
5. **Contextual follow-ups** — bounded server-built context for slot filling
   and multi-turn RAG, plus barge-in cancellation.

---

## 2. Goals and non-goals

### Goals

- Collect missing appointment details across turns without losing earlier
  values.
- Let the caller correct a captured value; the latest explicit value wins.
- Keep multiple simultaneous requests independent (appointment + prescription).
- Require an explicit yes before any backend call.
- Never claim an appointment is booked or a prescription is fulfilled unless
  the backend confirms it.
- Answer practice-information follow-ups in context ("What about Saturday?").
- Isolate sessions and reject duplicate/out-of-order final transcripts.
- Keep the emergency guard ahead of the LLM, embeddings, Qdrant, and backend.

### Non-goals

- Clinical triage, diagnosis, or treatment advice.
- Exotel/PSTN integration.
- A new conversation framework or FSM.
- Real patient data.
- Full transcript logging.

---

## 3. Architecture at a glance

```
final transcript
      │
      ▼
┌─────────────────────────────┐
│ VoicePipeline._process_turn │  per-session asyncio.Lock
│  duplicate/out-of-order     │  sequence <= last → drop
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│ Emergency guard (fixed)     │  before any provider/backend call
└─────────────┬───────────────┘
              │ no match
              ▼
┌─────────────────────────────┐
│ Deterministic slot fill     │  short explicit date/time answer
│ (controller.extract_…)      │  → skips the LLM when unambiguous
└─────────────┬───────────────┘
              │ not a slot answer
              ▼
┌─────────────────────────────┐
│ IntentInterpreter → LLM     │  bounded server-built context
│ Pydantic validation         │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│ ConversationController      │  deterministic state owner
│  clarify / confirm / cancel │
│  submit / backend result    │
└──────┬───────────────┬──────┘
       │               │
       ▼               ▼
   TTS response    Practice API (only after explicit confirmation,
                   only for requests marked READY_FOR_CONFIRMATION)
```

The FSM remains the authority for call state (`LISTENING`, `PROCESSING`,
`SPEAKING`, …). The controller only recommends responses and backend actions.

---

## 4. Conversation state model

**File:** `app/domain/conversation.py`

### 4.1 Request lifecycle

`RequestStatus`:

```
NEW → COLLECTING_INFORMATION → READY_FOR_CONFIRMATION → SUBMITTING
                                                        ├─→ SUBMITTED
                                                        └─→ FAILED
COLLECTING_INFORMATION / READY_FOR_CONFIRMATION → CANCELLED
```

`AdministrativeRequest` carries:

- `request_type`
- `details` (merged caller values)
- `missing_fields` (derived deterministically)
- `source_quotes`
- `status`, `turn_id`, `confirmed_at`, `submitted_at`,
  `failure_reason`, `idempotency_key`

Invariants enforced by the model:

- A submitted request cannot be rewritten.
- A cancelled request cannot be submitted.
- A request cannot be submitted twice.
- `is_active` is false for `SUBMITTED`, `FAILED`, and `CANCELLED`.

### 4.2 The merged-status fix

`with_updated_details()` previously inherited the status chosen from the
*latest utterance* only. A second turn that completed a request could leave it
in `COLLECTING_INFORMATION`. The status is now derived from the **merged**
missing fields:

```python
status = READY_FOR_CONFIRMATION if not missing else COLLECTING_INFORMATION
```

This is the core multi-turn slot-filling fix.

### 4.3 Turn history and context

`ConversationState` also stores:

- bounded turn history (`MAX_HISTORY_TURNS`),
- `awaiting_field`,
- `confirmation_pending` / `confirmation_target`,
- `last_grounded_answer`, `last_grounded_topic`,
  `last_grounded_citation_ids`, `last_grounded_turn_id`,
- `confirmed_submissions`,
- `backend_errors`.

Only final transcripts enter the controller; partial transcripts never do.

---

## 5. Deterministic controller

**File:** `app/services/conversation/conversation_controller.py`

### 5.1 Required-field policy

Required fields are a server policy, not model output:

| Request type | Required fields |
| --- | --- |
| `appointment-request` | `preferred_date`, `preferred_time` |
| `appointment-change-request` | `preferred_date`, `preferred_time` |
| `prescription-request` | none |
| `practice-administration` | none |

The model's `missing_fields` is advisory only. The controller recomputes
missing fields from `REQUIRED_FIELDS`, so a model cannot mark an incomplete
request ready. `reason_as_stated` is optional and captured when volunteered.

### 5.2 Confirmation is lexical and deterministic

`classify_confirmation_act()` returns:

- `confirm` — "yes", "go ahead", "that's right", …
- `reject` — "no", "not now", …
- `cancel` — "cancel that", "never mind", …
- `ambiguous` — "maybe", "I'm not sure", …
- `other`

Cancellation wins over a bare "no". Ambiguous replies always ask for a clear
yes/no and never submit. The model is never the authority for submission.

### 5.3 Correction handling

While a confirmation is pending, `interpretation_changes_state()` checks
whether the new interpretation supplies a different value. If so, the pending
confirmation is invalidated and the controller re-reads the corrected details
and asks for confirmation again. A bare "yes" is not treated as a correction.

### 5.4 Targeted cancellation

`infer_request_type_from_text()` maps "cancel the prescription" to
`prescription-request`. `handle_cancel(request_type=...)` cancels only that
request, invalidates any confirmation that covered it, and re-reads back any
remaining ready request so it can still be confirmed independently.

### 5.5 Tenant-field sanitisation

`practice_id`, `tenant_id`, and variants are stripped from model-supplied
`details` before storage or submission. Retrieval always uses the trusted
server-side `practice_id`. A model cannot choose which practice receives a
request.

### 5.6 Deterministic slot-fill backstop

Live Gemini occasionally returned an empty `requests` list for a bare slot
answer such as "Wednesday" even with `awaiting_field: preferred_date` in the
context. `extract_awaited_slot()` handles the narrow, unambiguous case:

- exactly one active request is waiting for the slot (or exactly one candidate
  request is missing that field),
- the utterance is short (≤ 6 words),
- the utterance is an explicit date/time phrase,
- the utterance is not an administrative keyword, cancellation, or rejection,
- no confirmation is pending.

It fills only the awaited field and never guesses. Everything else still goes
to the LLM.

### 5.7 Backend lifecycle

- The controller returns a `submit` decision with per-request payloads.
- The pipeline marks each request `SUBMITTING` immediately before its backend
  call.
- Only a confirmed backend outcome calls `handle_backend_success()`, which
  moves the request to `SUBMITTED` and records one logical confirmation.
- A rejection/timeout calls `handle_backend_failure()`, which marks that request
  `FAILED` and speaks a truthful failure message. One request failing never
  marks another as successful.
- The spoken success text always says "This is a request — not a confirmed
  appointment or prescription."

---

## 6. Pipeline changes

**File:** `app/services/voice_pipeline.py`

### 6.1 Turn integrity

- `_turn_lock` serialises turn processing per session.
- `_last_turn_sequence` rejects a final transcript whose sequence is less than
  or equal to the last processed one. This protects against duplicate delivery
  and out-of-order STT events, so a stale turn cannot overwrite a newer
  correction.
- Empty transcripts are ignored; low-confidence transcripts ask the caller to
  repeat before any LLM call.

### 6.2 Emergency ordering

The guard runs before:

- the LLM,
- embeddings,
- Qdrant retrieval,
- any ordinary backend submission.

On match, the controller cancels all pending requests, invalidates pending
confirmations, and the pipeline speaks the fixed script:

> This may be a medical emergency. Please hang up and call 999 now.

An emergency phrase combined with an administrative request stays an
emergency.

### 6.3 Bounded LLM context

`_build_llm_context()` sends only:

- active request types, captured fields, and missing fields,
- `awaiting_field`,
- `pending_confirmation`,
- `last_grounded_topic`,
- the last assistant message (truncated to 200 chars).

It never replays the full transcript and never includes model-generated facts.
The context is capped well below 1 KB in tests.

### 6.4 RAG routing and follow-ups

- Practice-information interpretations route to retrieval + grounded answer.
- A short follow-up after a grounded answer is resolved against the previous
  topic (e.g. "What about Saturday?" after opening hours).
- Retrieval uses the trusted `practice_id` and the store's active-document
  filter.
- Empty retrieval abstains honestly; it never fabricates an answer.
- The grounded answer text is spoken verbatim and never claims an
  administrative action.

### 6.5 Submission lifecycle

For each confirmed request:

1. mark `SUBMITTING` with a per-request idempotency key,
2. call the practice API with a bounded timeout,
3. on confirmation → `SUBMITTED`; on failure → `FAILED`,
4. speak a truthful aggregate result.

### 6.6 Barge-in

- The route now calls `pipeline.should_cancel_for_barge_in` rather than
  checking only the FSM `SPEAKING` state.
- `cancel_tts()` discards queued outbound frames, clears the framer, and
  signals the TTS provider's cooperative cancellation only when a synthesis is
  genuinely in flight.
- `should_cancel_for_barge_in` is true while speaking, while a TTS utterance is
  in flight, or while outbound audio is still queued.

This is verified with mock providers only; real microphone/acoustic barge-in
has not been measured (see limitations).

---

## 7. LLM contract and providers

### 7.1 Contract

**File:** `app/integrations/llm/base.py`

`LLMRequest` gains an optional `context: str | None`. It is a bounded,
server-built summary of the active dialogue. It is never treated as new caller
statements and never contains model-generated facts.

### 7.2 Gemini provider

**File:** `app/integrations/llm/gemini_provider.py`

- The prompt includes the bounded context and explicit multi-turn rules.
- Rule 8 tells the model to fill `awaiting_field` when the caller supplies it.
- Rules 9–10 define the exact detail keys (`preferred_date`,
  `preferred_time`, `reason_as_stated`, `medication_query`, `topic`) and forbid
  invented patient identity fields.
- The response schema now declares those detail properties explicitly. This
  was necessary: with a free-form `details` object, live `gemini-3.8-flash`
  returned empty details and invented `patient_name`/`date_of_birth`
  missing fields.
- `missing_fields` is documented as server-owned and left empty by the model.

### 7.3 Mock provider

**File:** `app/integrations/llm/mock_provider.py`

The mock provider remains deterministic and context-aware for ordinary tests:

- fills an awaited date/time from a bare answer,
- resolves a follow-up topic from `last_grounded_topic`,
- "last mentioned date wins" for corrections.

It is never a silent fallback for a real provider.

---

## 8. Safety and data-protection invariants

- Emergency guard first, always.
- Only the deterministic controller can trigger a backend action.
- Explicit confirmation is required before submission.
- No submission claim without backend confirmation.
- No prescription-fulfilment claim.
- No clinical triage, diagnosis, or treatment.
- No model-supplied practice/tenant IDs.
- Retrieval always uses the trusted server-side `practice_id` and active
  document status.
- No full transcripts in logs or metrics.
- Credentials are read from settings only and never printed.

---

## 9. Files changed

### New

- `apps/voice-gateway/tests/evaluation/day9/__init__.py`
- `apps/voice-gateway/tests/evaluation/day9/test_corpus.py`
- `apps/voice-gateway/tests/unit/test_day9_dialogue.py`
- `apps/voice-gateway/tests/live/test_day9_live_smoke.py`

### Modified

- `apps/voice-gateway/app/domain/conversation.py`
- `apps/voice-gateway/app/services/conversation/conversation_controller.py`
- `apps/voice-gateway/app/services/conversation/intent_interpreter.py`
- `apps/voice-gateway/app/services/voice_pipeline.py`
- `apps/voice-gateway/app/integrations/llm/base.py`
- `apps/voice-gateway/app/integrations/llm/gemini_provider.py`
- `apps/voice-gateway/app/integrations/llm/mock_provider.py`
- `apps/voice-gateway/app/integrations/embeddings/gemini_provider.py`
- `apps/voice-gateway/app/api/routes/websocket.py`
- `apps/voice-gateway/tests/integration/test_speech_pipeline.py`
- `apps/voice-gateway/pyproject.toml`

---

## 10. Verification summary

- Offline suite: `338 passed, 4 skipped` (the 4 skips are the opt-in live
  tests).
- Day 9 corpus: 34 scenarios, all passing.
- Day 9 focused unit tests: confirmation classification, targeted
  confirmation, correction invalidation, duplicate-submission prevention,
  tenant stripping, context-aware mock slot filling, barge-in.
- Ruff lint and format: clean.
- Live Gemini + Qdrant:
  - retrieval n=4, p50 662–806 ms, p95 852–975 ms across two runs (varies),
  - cross-practice retrieval returned zero chunks,
  - grounded answer produced with valid citations,
  - full appointment utterance extracted `preferred_date=Tuesday`,
    `preferred_time=morning`,
  - live pipeline turn reached `READY_FOR_CONFIRMATION` with no backend client.
- Azure TTS: live synthesis produced 33 chunks / 104,832 bytes of raw PCM.
- Azure STT: file-based recognition returned the exact phrase. The adapter's
  push-stream continuous path returned no transcript in this environment and
  remains unverified (see the engineering report).

---

## 11. Known limitations

- Real microphone/browser acoustic barge-in was not measured; barge-in is
  verified with mock TTS only.
- The Azure STT push-stream path did not produce a transcript in local
  testing, although the same audio recognized correctly from a WAV file.
- Exotel/PSTN is out of scope and unverified.
- Docker/MySQL runtime is unavailable; Qdrant local mode is used.
- Live model latency is variable; the measured interpretation was ~3.3 s.
