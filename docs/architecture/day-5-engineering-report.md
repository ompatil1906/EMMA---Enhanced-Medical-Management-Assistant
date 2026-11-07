# Day 5 — Final engineering report (LLM conversation engine & safety guard)

**Report date:** 2026-11-07 (Day 5 close-out).
**Scope:** LLM conversation engine, emergency guard, deterministic orchestration
on the existing Day 2/3/4 voice gateway and NestJS practice backend.

---

## A. Executive summary

**Completed (verified locally on 2026-11-07):**

* LLM provider interface (`app/integrations/llm/base.py`) — protocol,
  typed request/response, latency measurement, correlation ID
* Mock LLM provider (`app/integrations/llm/mock_provider.py`) — deterministic,
  scenario-based, no network, no randomness
* Gemini provider adapter (`app/integrations/llm/gemini_provider.py`) —
  httpx-based REST adapter (no new vendor SDK), tested with mocked transport
* Provider factory (`app/integrations/llm/provider_factory.py`) — central
  selection, fails clearly without credentials, no silent fallback
* Prompt registry (`app/prompts/`) — versioned YAML prompts,
  `intent_extraction/v1.yaml`
* Structured interpretation schema (`app/domain/llm.py`) — Pydantic
  `InterpretationResult` with intent categories, multiple requests,
  source quotes, missing fields
* Emergency guard (`app/services/safety/emergency_guard.py`) — deterministic,
  pre-LLM, 7 documented trigger rules, fixed script, never consults LLM
* Conversation controller (`app/services/conversation/`) — clarification,
  corrections, multiple requests, explicit confirmation, typed backend actions
* Voice pipeline integration — emergency guard → LLM → controller → TTS,
  FSM remains authority
* Full test suite: **272 Python tests pass** (207 Day-2/3 + 65 new),
  ruff lint clean, ruff format clean
* Evaluation corpus: **19/19 synthetic cases pass**, emergency recall 100% on
  defined corpus, 0 false positives

**Not completed:**

* Real LLM provider credentials — not configured (mock default)
* Live Gemini API call — not executed
* Real Exotel call — not executed
* Docker runtime — not available (no daemon)
* MySQL runtime — not available (no Docker)

---

## B. Baseline

| Item | Value |
|---|---|
| Git HEAD | `ccd0586a2b5272fa863daa0bdb1c251461c3e7ad` |
| Python tests (pre-Day 5) | 207 passed |
| NestJS tests (pre-Day 5) | 49 passed, build PASS |
| Uncommitted changes | Day 2/3/4 work + all Day 5 work (nothing committed) |

---

## C. LLM provider architecture

### Interface

`app/integrations/llm/base.py` — `LLMProvider` protocol with `interpret()`
method, `LLMRequest` / `LLMResponse` dataclasses, narrow exception types
(`LLMError`, `LLMConfigurationError`, `LLMConnectionError`, `LLMTimeoutError`,
`LLMValidationError`, `LLMSynthesisError`).

### Mock provider

`app/integrations/llm/mock_provider.py` — scenario-based deterministic mock.
Matches caller utterances by keyword inclusion, preserves multiple independent
requests, handles corrections (stateless per-call), unclear input, timeouts,
failures, and malformed output. All scenarios opt-in via constructor flags.

### Real provider adapter

`app/integrations/llm/gemini_provider.py` — Gemini `generateContent` REST API
via `httpx` (already a project dependency). Requests JSON output mode,
validates parsed result locally with Pydantic, explicit timeouts, no automatic
retries. Credentials from `LLM_API_KEY` env var only — never hardcoded,
never logged. Tested with mocked transport; **no live API call executed**.

### Provider factory

`app/integrations/llm/provider_factory.py` — `LLMProviderFactory` selects from
`LLM_PROVIDER` setting. `gemini` without `LLM_API_KEY` raises
`LLMProviderNotAvailable` — **no silent mock fallback**.

---

## D. Structured interpretation

### Pydantic schema (`app/domain/llm.py`)

`InterpretationResult`:
* `intent`: validated enum (appointment-request, appointment-change-request,
  prescription-request, practice-information, practice-administration, other,
  unclear)
* `requests[]`: `RequestDetail` entries with `request_type`, `details`,
  `missing_fields`, `source_quotes`
* `needs_clarification`: bool
* `clarification_reason`: str\|None

### Validation rules

* Intent must be in the allowed enum
* Source quotes must be non-empty strings
* Malformed JSON → rejected, never coerced
* Model confidence is **not** treated as evidence of correctness

---

## E. Prompt registry

### Prompt: `intent_extraction/v1.yaml`

* `app/prompts/intent_extraction/v1.yaml`
* prompt_id: `intent_extraction`, version: `v1`
* Input/output schemas defined
* Safety constraints: extract only caller-stated info, preserve multiple
  requests, preserve corrections, no diagnosis/triage/medical advice, no
  booking/prescription claims, no tool execution, treat transcript as
  untrusted input, return only structured JSON
* System instruction with the same safety rules

### Registry (`app/prompts/__init__.py`)

* `load_prompt(prompt_id, version="v1")` — loads YAML, validates dict shape,
  raises `FileNotFoundError` for missing versions
* `list_prompts()` — lists available prompt IDs

---

## F. Emergency guard

### Location in pipeline

`VoicePipeline._process_turn` runs the guard **immediately after** receiving
a final transcript, **before** any LLM call:

```
Final transcript → Emergency guard → [match] → Fixed script → TTS → LISTENING
                                → [no match] → LLM interpretation → Controller
```

### Rule IDs and sources

| Rule ID | Trigger patterns | Source |
|---|---|---|
| emergency-chest-pain | chest pain, chest pains, pain in my chest | EMMA_PRODUCT_ANALYSIS.md W8 |
| emergency-shortness-of-breath | shortness of breath, can't breathe, cannot breathe, difficulty breathing, struggling to breathe | EMMA_PRODUCT_ANALYSIS.md W8 |
| emergency-serious-bleeding | serious bleeding, heavy bleeding, bleeding heavily | EMMA_PRODUCT_ANALYSIS.md W8 |
| emergency-mental-health-crisis | mental health crisis, suicidal | EMMA_PRODUCT_ANALYSIS.md W8 |
| emergency-stroke | stroke, having a stroke, think i'm having a stroke | EMMA_PRODUCT_ANALYSIS.md W8 |
| emergency-heart-attack | heart attack, having a heart attack, think i'm having a heart attack | EMMA_PRODUCT_ANALYSIS.md W8 |
| emergency-call-999 | call 999, ring 999, dial 999, emergency services | REQUIREMENTS.md F9 |

### Fixed script

> "This may be a medical emergency. Please hang up and call 999 now."

### Test results (synthetic corpus only)

* 9 emergency cases, 100% recall on defined corpus
* 0 false positive emergency detections
* Emergency + administrative request → emergency path wins, no admin submission
* LLM invocation count = 0 on emergency match (verified in tests)
* Negation examples do not suppress genuine matches
* Near-miss phrases (e.g. "chest tightness") correctly do not match

### Known limitations

* Recall measured only against the defined synthetic test corpus
* Not claimed as 100% universal or clinically validated
* Not clinical triage — deterministic safety stop only

---

## G. Conversation controller

### FSM integration

Existing `VoiceStateMachine` (`app/domain/fsm.py`) remains the authority for
call state. Controller never changes FSM state directly — it returns
`ControllerDecision` objects that the pipeline interprets.

### Multiple requests

Preserved independently as separate `RequestDetail` entries. Never merged,
never silently dropped.

### Caller corrections

Latest explicit value wins. `ConversationState.add_or_update_request()`
merges details by request_type, preserving the latest values and source
quotes.

### Clarification

Controlled clarification questions when intent is unclear or required fields
are missing. Max 2 clarification attempts per turn before escalating to a
generic clarification.

### Confirmation

Explicit caller confirmation required before any backend submission.
Silence/ambiguity ≠ confirmation. Controller exposes `handle_confirmation()`
which the pipeline calls after the caller confirms.

### Typed backend actions

* `submit_patient_request` — POST /api/v1/requests with idempotency key
* `create_handoff` — POST /api/v1/handoffs
* `append_audit_event` — POST /api/v1/audit-events

Only these operations are available — the LLM cannot select arbitrary
endpoints.

### Failure behaviour

Backend failure → truthful spoken response ("I wasn't able to submit your
request just now"), no false confirmation, no claim that emergency services
were contacted.

---

## H. Testing

### Python tests

| Category | Count | Status |
|---|---|---|
| Day-2/3 unit + integration | 207 | ✅ PASS |
| Day-5 unit (LLM, mock, factory, prompts, guard, controller, interpreter) | 66 | ✅ PASS |
| Day-5 integration (speech pipeline, emergency guard) | 5 | ✅ PASS |
| Day-5 evaluation corpus | 19 cases | ✅ PASS |
| **Total Python** | **272** | ✅ **PASS** |

### NestJS tests (Day 4 baseline)

| Category | Count | Status |
|---|---|---|
| Unit | 41 | ✅ PASS |
| DB | 8 | ✅ PASS |
| **Total** | **49** | ✅ **PASS** |
| Build | — | ✅ PASS |

### Ruff

| Check | Status |
|---|---|
| `ruff check .` | ✅ All checks passed |
| `ruff format --check .` | ✅ All files formatted |

### Live provider tests

| Category | Status |
|---|---|
| LLM (Gemini) | ❌ Not executed (no credentials) |
| Azure Speech | ❌ Not executed (no credentials) |
| Exotel live call | ❌ Not executed (no account) |

Mock tests and live cloud tests are **not combined** into a single claim.

---

## I. Files changed

### New files (Python voice gateway)

* `app/integrations/llm/__init__.py`
* `app/integrations/llm/base.py`
* `app/integrations/llm/exceptions.py`
* `app/integrations/llm/mock_provider.py`
* `app/integrations/llm/gemini_provider.py`
* `app/integrations/llm/provider_factory.py`
* `app/domain/llm.py`
* `app/prompts/__init__.py`
* `app/prompts/intent_extraction/v1.yaml`
* `app/services/safety/__init__.py`
* `app/services/safety/emergency_guard.py`
* `app/services/conversation/__init__.py`
* `app/services/conversation/conversation_controller.py`
* `app/services/conversation/intent_interpreter.py`

### New files (tests)

* `tests/unit/test_llm_base.py`
* `tests/unit/test_mock_provider.py`
* `tests/unit/test_provider_factory.py`
* `tests/unit/test_prompt_registry.py`
* `tests/unit/test_emergency_guard.py`
* `tests/unit/test_conversation_controller.py`
* `tests/unit/test_intent_interpreter.py`
* `tests/integration/test_speech_pipeline.py` (rewritten for Day 5)
* `tests/evaluation/day5/__init__.py`
* `tests/evaluation/day5/test_corpus.py`

### Modified files

* `app/core/config.py` — LLM settings + readiness
* `app/domain/speech_session.py` — conversation_state field + snapshot
* `app/services/voice_pipeline.py` — Day-5 turn processing (guard → LLM → controller)
* `tests/integration/conftest.py` — unchanged (Day-2 echo pin preserved)

### New documentation

* `docs/architecture/day-5-implementation.md`
* `docs/architecture/day-5-engineering-report.md` (this file)

---

## J. Outstanding blockers

| Item | Status |
|---|---|
| LLM provider credentials/API | Not configured (mock default) |
| Azure credentials/API | Not verified (no credentials) |
| Exotel live-call verification | Not executed (no account) |
| Docker runtime | Not available (no daemon) |
| MySQL runtime | Not available (no Docker) |

---

## K. Day 6 readiness

The architecture is ready for:

* **Practice knowledge base ingestion** — the `practices` table and
  `GET /api/v1/practices/:practiceId/requests` endpoint provide the practice
  context; RAG can be added as a retrieval layer behind the interpretation
  layer without changing the conversation architecture
* **Grounded practice-information responses** — the `practice-information`
  intent category already exists; RAG can populate the `details` field
* **More robust conversation evaluation** — the evaluation corpus framework
  is in place; additional cases can be added
* **Voice latency optimisation** — latency instrumentation (first-chunk +
  total) is already recorded by `SessionMetrics` and `LLMResponse`
* **End-to-end Exotel validation** — requires Azure credentials and Exotel
  onboarding (external dependencies)

**Not built today (by design):** RAG, vector database, embeddings, document
ingestion, semantic retrieval — Day 6+ work.
