# Day 4 — implementation architecture (LLM layer, safety guard, backend stabilisation)

Status: NestJS practice backend stabilised (build + 49 tests green); Python LLM
interpretation layer + emergency guard implemented and tested.

Scope today: stabilise the Day-3 NestJS scaffold, add a provider-independent LLM
interpretation layer with versioned prompts, implement a deterministic pre-LLM
emergency guard, and connect the interpretation layer to the existing voice FSM
through a deterministic conversation controller.

---

## 1. NestJS practice backend stabilisation

### Root causes of the ~38 TypeScript build errors

| Category | Count | Example |
|---|---|---|
| Redundant `@Column('type', { ..., type: 'type' })` | 5 | `call.entity.ts`, `patient-request.entity.ts` |
| Duplicate `Constructable` type declaration | 2 | `db-config.ts` |
| `HttpStatusCode` not exported from `@nestjs/common` v11 | 1 | `api-error.ts` |
| Missing swagger imports (`ApiCreatedResponse`, `ApiBadRequestResponse`) | 2 | `calls.controller.ts` |
| Missing `Res` import | 1 | `requests.controller.ts` |
| Missing `AppConfig` class import | 1 | `database.module.ts` |
| Enum used as type (`HANDOFF_STATUSES[number]`) | 3 | DTOs using value instead of `HandoffStatus` type alias |
| `PaginationMeta` missing index signature for `Record<string, unknown>` | 4 | `envelope.ts` constraint removed |
| TypeORM `insert`/`findOne` type mismatches (`exactOptionalPropertyTypes`) | 7 | `QueryDeepPartialEntity`, `FindOptionsWhere` |
| DTO `.js` import path depth (`../../../` → `../../`) | 8 | DTOs in `src/*/dto/` |
| `class-validator` decorator options (`exactOptionalPropertyTypes`) | 1 | `json-primitive-tree.constraint.ts` |
| TypeORM `DataSourceOptions` `as const` issues | 2 | `db-config.ts` |
| `RequestDateRange` optional props vs `Date \| undefined` | 1 | `list-requests-query.dto.ts` |

### Fixes applied

* Removed redundant `type:` from `@Column` options (kept the explicit type string)
* Removed duplicate `Constructable` declaration
* Replaced `HttpStatusCode` with `HttpStatus`/`number`
* Added missing swagger imports and `Res` import
* Added `AppConfig` class import to `database.module.ts`
* Replaced enum-as-type usage with exported type aliases (`HandoffStatus`, `RequestStatus`)
* Removed `extends Record<string, unknown>` from `Envelope<TData, TMeta>`
* Imported `QueryDeepPartialEntity`/`FindOptionsWhere` from TypeORM
* Fixed DTO import paths (`../../../` → `../../`)
* Wrapped `registerDecorator` options conditionally for `exactOptionalPropertyTypes`
* Changed `DataSourceRegistry` to `readonly Function[]` and cast through `unknown`
* Changed `RequestDateRange` fields to `Date | undefined`

### Jest/ESM resolution

`@nestjs/typeorm` v12 is ESM-only (`"type": "module"`). Jest 29 in CJS mode cannot load it directly. Added an esbuild-based Jest transformer (`scripts/jest-esbuild-transform.cjs`) that compiles ESM `.js` files to CJS, and updated `jest.config.cjs` `transformIgnorePatterns` to allow transforming `@nestjs/typeorm` and `typeorm` (handling both hoisted and pnpm-store layouts).

### Database schema & migrations

Five tables: `practices`, `calls`, `patient_requests`, `handoffs`, `audit_events`.
Migration `1794000000000-CreateSchema` is the single source of truth; `synchronize: false` everywhere.
Idempotency on `patient_requests`: composite unique `(practice_id, idempotency_key)` + `idempotency_payload_hash` column; `409 IDEMPOTENCY_KEY_CONFLICT` on key/payload mismatch.

### Seeding

`pnpm seed` inserts one fictional practice: "Riverbend Medical Practice" (Europe/London) and prints its generated UUID.

---

## 2. Python-to-NestJS integration

`PracticeApiClient` (`app/integrations/practice_api.py`):
* Lazy async HTTP client (`_ensure_client()`)
* Typed `SubmissionOutcome` (confirmed/conflict/rejected/timeout/unavailable/disabled/skipped)
* Retries only for idempotent operations (call upsert, request submission)
* Deterministic uuid5 idempotency keys: `uuid5(NAMESPACE_URL, f"{practice_id}|{call_sid}|{sequence}|{request_type}")`
* Bounded submissions (`asyncio.wait_for` with `PRACTICE_API_TIMEOUT`)
* Never claims persistence unless the API returns 2xx with a record

---

## 3. LLM architecture

### Provider interface

`app/integrations/llm/base.py` — `LLMProvider` protocol:
* `interpret(transcript, session_id, ...) -> InterpretationResult`
* Structured request/response, configurable model, timeout, token limits
* Provider-specific error mapping, latency measurement, correlation ID

### Providers

* `mock_provider.py` — deterministic mock for tests/local development (safe default)
* `provider_factory.py` — central selection from `LLM_PROVIDER` setting; real provider without credentials fails clearly (no silent fallback)

### Prompt registry

`app/integrations/llm/prompts/v1/intent_extraction.yaml` — versioned prompt with:
* Stable prompt ID + explicit version
* Input/output schemas
* Safety constraints (no diagnosis, no booking claims, no tool execution)
* Instructions to preserve caller wording, not invent fields, preserve corrections

### Structured output schema

Pydantic-validated `InterpretationResult` with `intent`, `requests[]` (each with `request_type`, `details`, `missing_fields`, `source_quotes`), `needs_clarification`, `clarification_reason`. Malformed JSON is rejected and a safe fallback is used.

---

## 4. Safety implementation

### Emergency guard

Runs **before** any LLM request, in the voice pipeline after the final transcript event:

```
Final transcript → Emergency guard → [match] → Fixed emergency script → TTS → close
                                → [no match] → LLM interpretation → FSM/controller
```

**Fixed script:**
> "This may be a medical emergency. Please hang up and call 999 now."

**Rules:** deterministic, auditable, each with a stable rule ID and source reference in Day-1 product research. No LLM is consulted for emergency detection. On match: no LLM call, no administrative submission, no symptom questions, no diagnosis/triage.

**Tested:** positive triggers, natural-language phrasings, emergency + administrative request (emergency wins), near-misses, negation, empty/malformed transcripts, LLM-not-called verification.

**Limitation:** recall measured only against the defined synthetic test corpus; not 100% universal detection.

### Not clinical triage

Explicitly documented: this is an administrative NHS GP receptionist prototype, not a clinical triage system, diagnostic tool, or medical adviser. The emergency guard provides safe instructions (hang up and call 999) but does not contact emergency services or diagnose.

---

## 5. Conversation orchestration

### FSM integration

The existing deterministic FSM (`app/domain/fsm.py`) remains the authority for call state. The LLM may suggest intent/fields/missing-info but never changes FSM state directly.

### Flow

```
LISTENING → Final transcript → Emergency guard
                            ↓ (no match)
                        PROCESSING → LLM interpretation → Local validation
                            ↓
                    Missing info → Controlled clarification
                    Correction → Update captured field (latest wins)
                    Multiple requests → Preserve each independently
                    Ready → Read back captured details → Explicit confirmation
                            ↓
                    Typed backend action (submit_patient_request, create_handoff, append_audit_event)
                            ↓
                    Backend result → Truthful spoken response
```

### Confirmation

Explicit caller confirmation required before submission. Silence/ambiguity ≠ confirmation. No fabricated booking confirmation.

### Backend failure

Truthful: "Your request could not be submitted." No false confirmation.

---

## 6. Testing

* Python: 207 tests (Day-2 + Day-3) + Day-4 speech/LLM/guard/controller tests
* NestJS: 49 tests across 8 suites (build + Jest green)
* Idempotency: concurrent duplicate POSTs converge to exactly one row
* Emergency guard: LLM-not-called verification on match

---

## 7. Observability

* LLM provider selection, prompt ID/version, model latency
* Structured output validation outcome, intent category
* Emergency guard match + rule ID
* FSM transition, backend action type, response status, idempotency result
* Session correlation IDs; no raw audio/transcripts/PII logged

---

## 8. Remaining blockers

| Item | Status |
|---|---|
| Azure credentials/API | Not verified (no credentials) |
| Exotel live call | Not verified (no account) |
| Docker runtime | Not available (no daemon) |
| MySQL runtime | Not available (no Docker) |
| LLM provider credentials | Not configured (mock default) |

---

## 9. Day 5 readiness

Ready for: practice knowledge base/RAG, grounded practice information responses, robust conversation evaluation, voice latency optimisation, end-to-end Exotel validation.
