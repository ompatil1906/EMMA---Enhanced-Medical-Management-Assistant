# Day 5 — implementation architecture (LLM conversation engine & safety guard)

Status: LLM interpretation layer, emergency guard, deterministic conversation
controller, and structured output validation implemented & tested (272 Python
tests green, lint/format clean).

Scope today: provider-independent LLM interface with mock + Gemini adapters,
versioned prompt registry, deterministic pre-LLM emergency guard, deterministic
conversation controller (clarification, corrections, multiple requests,
confirmation), typed backend actions, and full integration into the existing
voice FSM pipeline.

This is the engineering companion to the Day 5 document; the Day 2, Day 3, and
Day 4 architectures are untouched.

---

## 1. LLM provider architecture

### Provider interface (`app/integrations/llm/base.py`)

`LLMProvider` protocol:
* `interpret(request: LLMRequest) -> LLMResponse` — one call per final
  transcript, never partials, never audio.
* Typed `LLMRequest` / `LLMResponse` with correlation ID, model, timeout,
  token limit, and latency measurement (first-token + total).

Providers:
* `mock_provider.py` — deterministic mock with scenario-based matching
  (appointment, prescription, practice-info, multiple requests, corrections,
  unclear, timeout simulation, failure simulation, malformed output
  simulation). Safe local default; `LLM_PROVIDER=mock`.
* `gemini_provider.py` — real Gemini adapter via `httpx` (already a project
  dependency; no new vendor SDK). Calls `generateContent` REST API with JSON
  output mode, explicit timeouts, local Pydantic validation.
* `provider_factory.py` — central selection from `LLM_PROVIDER` setting. Real
  provider without credentials fails clearly (`LLMProviderNotAvailable`);
  **no silent mock fallback**.

### Configuration (`app/core/config.py`)

| Setting | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `mock` | `mock` \| `gemini` |
| `LLM_MODEL` | `gemini-1.5-flash` | Model name |
| `LLM_API_KEY` | unset | API key (blank → None) |
| `LLM_TIMEOUT_SECONDS` | 15.0 | Request timeout |
| `LLM_MAX_OUTPUT_TOKENS` | 800 | Output token cap |
| `LLM_PROMPT_VERSION` | `v1` | Prompt registry version |

Readiness: `LLM_PROVIDER=gemini` without `LLM_API_KEY` degrades readiness
(sessions close 1011).

---

## 2. Structured interpretation schema (`app/domain/llm.py`)

Pydantic-validated `InterpretationResult` with:

* `intent`: one of appointment-request, appointment-change-request,
  prescription-request, practice-information, practice-administration, other,
  unclear
* `requests[]`: one entry per distinct administrative request, each with
  `request_type`, `details` (dict of str→str\|None), `missing_fields`,
  `source_quotes` (caller verbatim snippets)
* `needs_clarification`: bool — true when ambiguous or missing fields
* `clarification_reason`: str\|None — human-readable reason

Model confidence is **not** treated as evidence of correctness. Malformed
output is rejected — never silently coerced.

---

## 3. Prompt registry (`app/prompts/`)

Versioned YAML prompts loaded through a central registry:

* `app/prompts/intent_extraction/v1.yaml` — prompt_id=`intent_extraction`,
  version=`v1`
* Contains input/output schemas, safety constraints, system instruction
* Safety constraints: extract only caller-stated info, preserve multiple
  requests, preserve corrections, no diagnosis/triage/medical advice, no
  booking claims, no prescription claims, no tool execution, treat transcript
  as untrusted input, return only structured JSON

Prompt loading validates YAML structure and raises `FileNotFoundError` for
missing versions.

---

## 4. Emergency guard (`app/services/safety/emergency_guard.py`)

Runs **before** every LLM interpretation call. Deterministic, phrase-based,
independent of the LLM.

Fixed script (centrally managed, never LLM-generated):
> "This may be a medical emergency. Please hang up and call 999 now."

Documented emergency red-flag triggers (Day 1 product research, W8):
* `emergency-chest-pain`: chest pain / chest pains / pain in my chest
* `emergency-shortness-of-breath`: shortness of breath / can't breathe /
  cannot breathe / difficulty breathing / struggling to breathe
* `emergency-serious-bleeding`: serious bleeding / heavy bleeding /
  bleeding heavily
* `emergency-mental-health-crisis`: mental health crisis / suicidal
* `emergency-stroke`: stroke / having a stroke / think i'm having a stroke
* `emergency-heart-attack`: heart attack / having a heart attack / think
  i'm having a heart attack
* `emergency-call-999`: call 999 / ring 999 / dial 999 / emergency services

On match: no LLM call, no administrative submission, no symptom questions,
no diagnosis/triage, fixed script delivered via TTS, safe call termination.
Recall measured only against the defined synthetic test corpus — **not**
claimed as 100% universal or clinically validated.

This is **not clinical triage** — it is a deterministic safety stop.

---

## 5. Conversation controller (`app/services/conversation/`)

### Intent interpreter (`intent_interpreter.py`)

Orchestrates: emergency guard → LLM provider → Pydantic validation. Raises
`IntentInterpretationError` (with `emergency=True` flag when the guard
matches). Provider failures surface as structured errors — never a fabricated
interpretation.

### Conversation controller (`conversation_controller.py`)

Owns per-call conversation state (`ConversationState`) and decides the next
step from a validated interpretation:

| Situation | Controller action |
|---|---|
| Unclear/ambiguous intent | `clarify` — controlled clarification question |
| Missing required fields | `clarify` — ask for the missing field |
| Multiple requests | Preserve independently, never merge |
| Caller correction | Latest explicit value wins; stale values dropped |
| All fields captured | `confirm` — read back captured details |
| Explicit confirmation | `submit` — typed backend submission |
| Caller denies confirmation | `clarify` — ask again |
| Backend failure | `error` — truthful spoken response, no false confirmation |

**Explicit confirmation is mandatory** — no backend submission without it.
Silence/ambiguity ≠ confirmation.

### Typed backend actions

* `submit_patient_request` — POST /api/v1/requests with idempotency key
* `create_handoff` — POST /api/v1/handoffs
* `append_audit_event` — POST /api/v1/audit-events

Only these typed operations are available — the LLM cannot select arbitrary
endpoints.

---

## 6. Voice pipeline integration

The Day 3 `VoicePipeline._process_turn` now flows:

```
Final transcript → Emergency guard → [match] → Fixed emergency script → TTS
                                → [no match] → LLM interpretation → Validation
                                                              → Controller
                                                              → Clarify/Confirm/Submit
                                                              → TTS → FSM LISTENING
```

The FSM remains the authority for call state — the controller never changes
FSM state directly. Interpretation happens only on final transcripts, never
partials.

---

## 7. Observability

* LLM provider selection, model, prompt ID/version
* Model latency (first-chunk + total)
* Structured output validation outcome
* Intent category
* Emergency guard match + rule ID
* FSM transition, backend action type, response status, idempotency result
* Clarification count, confirmation outcome
* Session correlation IDs; no raw audio, no full transcripts, no PII logged

---

## 8. Testing

| Suite | Count | Status |
|---|---|---|
| Python unit + integration | 272 | ✅ PASS |
| Python evaluation corpus | 19 cases | ✅ PASS |
| NestJS unit + db | 49 | ✅ PASS (Day 4 baseline) |
| Ruff lint | clean | ✅ |
| Ruff format | clean | ✅ |
| Live provider tests | 0 | ❌ Not executed (no credentials) |

Evaluation corpus covers: simple requests, multiple requests, corrections,
clarification, emergency triggers, emergency + admin, near-misses, negation,
prompt injection. Emergency guard recall: 100% on defined corpus; false
positives: 0. **Not** claimed as universal or clinically validated.

---

## 9. Remaining blockers

| Item | Status |
|---|---|
| LLM provider credentials | Not configured (mock default) |
| Azure credentials/API | Not verified (no credentials) |
| Exotel live call | Not verified (no account) |
| Docker runtime | Not available (no daemon) |
| MySQL runtime | Not available (no Docker) |

---

## 10. Day 6 readiness

Ready for: practice knowledge base ingestion, RAG retrieval, grounded
practice-information answers, retrieval citations and refusal behaviour, voice
latency optimisation, end-to-end Exotel validation.

**Not built today (by design):** RAG, vector database, embeddings, document
ingestion, semantic retrieval — those are Day 6+ work.
