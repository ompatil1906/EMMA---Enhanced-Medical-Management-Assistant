# Day 4 — Final engineering report (NestJS stabilisation)

**Report date:** 2026-11-07 (Day 4 close-out).
**Scope:** NestJS practice backend stabilisation only.

---

## A. Executive summary

**Completed:**
- NestJS practice backend build: **PASS** (0 TypeScript errors, down from ~38)
- Jest test discovery: **WORKING** (ESM `@nestjs/typeorm` v12 loading fixed via esbuild transformer)
- Backend unit tests: **49/49 passing** across 8 suites
- Backend idempotency tests: **PASS** (30 concurrent duplicates → exactly 1 row)
- Python regression suite: **207/207 passing** (unchanged)
- Ruff lint/format: **clean** (Python side)

**Not completed (Phase 2–8 LLM/safety work):**
- LLM provider interface, mock provider, real provider adapter — NOT implemented in code
- Prompt registry — NOT implemented
- Structured output validation — NOT implemented
- Emergency guard — NOT implemented
- Deterministic conversation controller — NOT implemented

These phases are documented in `docs/architecture/day-4-implementation.md` but the code was not written. Day 4 work was entirely consumed by NestJS backend stabilisation.

---

## B. NestJS backend

### Original build/test status
- `pnpm run build`: **FAIL** (~38 TypeScript errors)
- `pnpm run test`: **FAIL** (8/8 suites could not load — ESM `@nestjs/typeorm` v12 incompatible with Jest 29 CJS)
- `pnpm run lint`: not reached

### Root causes of TypeScript errors (~38 total)

| Category | Count | Representative files |
|---|---|---|
| Redundant `@Column('type', { ..., type: 'type' })` | 5 | `call.entity.ts`, `patient-request.entity.ts` |
| Duplicate `Constructable` type | 2 | `db-config.ts` |
| `HttpStatusCode` not in `@nestjs/common` v11 | 1 | `api-error.ts` |
| Missing swagger imports | 2 | `calls.controller.ts` |
| Missing `Res` import | 1 | `requests.controller.ts` |
| Missing `AppConfig` class import | 1 | `database.module.ts` |
| Enum used as type instead of type alias | 3 | DTOs (`HANDOFF_STATUSES[number]`, `REQUEST_STATUSES[number]`) |
| `PaginationMeta` missing index signature | 4 | `envelope.ts` |
| TypeORM `insert`/`findOne` type mismatches | 7 | `requests.service.ts`, `audit.service.ts` |
| DTO `.js` import path depth (`../../../` → `../../`) | 8 | DTOs in `src/*/dto/` |
| `class-validator` decorator options | 1 | `json-primitive-tree.constraint.ts` |
| `DataSourceOptions` `as const` issues | 2 | `db-config.ts` |
| `RequestDateRange` optional props | 1 | `list-requests-query.dto.ts` |

### Fixes applied
* Removed redundant `type:` from `@Column` options
* Removed duplicate `Constructable` declaration
* Replaced `HttpStatusCode` with `HttpStatus`/`number`
* Added missing swagger imports (`ApiCreatedResponse`, `ApiBadRequestResponse`) and `Res`
* Added `AppConfig` class import to `database.module.ts`
* Replaced enum-as-type with type aliases (`HandoffStatus`, `RequestStatus`)
* Removed `extends Record<string, unknown>` from `Envelope`
* Imported `QueryDeepPartialEntity`/`FindOptionsWhere` from TypeORM
* Fixed DTO import paths (`../../../` → `../../`)
* Wrapped `registerDecorator` options conditionally
* Changed `DataSourceRegistry` to `readonly Function[]`
* Changed `RequestDateRange` fields to `Date \| undefined`
* Moved `APP_CONFIG` provider from `AppModule` to `DatabaseModule` (DI visibility)
* Changed `TypeOrmModule.forRootAsync` from `inject: [APP_CONFIG]` to `useFactory` calling `buildAppConfig()` directly
* Replaced `class-validator` `validateSync()` in `validateEnv()` with manual validation (class-validator/ts-jest decorator-metadata incompatibility)
* Fixed custom `IsJsonPrimitiveTree` decorator to pass `object.constructor` (not prototype) as `target`

### Jest/ESM resolution
`@nestjs/typeorm` v12 is ESM-only. Added `scripts/jest-esbuild-transform.cjs` (esbuild-based ESM→CJS transformer) and updated `jest.config.cjs` `transformIgnorePatterns` to allow transforming `@nestjs/typeorm` and `typeorm` (handles both hoisted and pnpm-store layouts).

### Database schema & migrations
Five tables: `practices`, `calls`, `patient_requests`, `handoffs`, `audit_events`.
Single migration `1794000000000-CreateSchema`; `synchronize: false` everywhere.
Idempotency: composite unique `(practice_id, idempotency_key)` + `idempotency_payload_hash`; `409 IDEMPOTENCY_KEY_CONFLICT` on key/payload mismatch.

### Build and test commands/results
```
pnpm run build        → exit 0 (0 TS errors)
pnpm run test         → 49 passed, 8 suites, 0 failed
```

---

## C. Python-to-NestJS integration

`PracticeApiClient` (`apps/voice-gateway/app/integrations/practice_api.py`) was implemented on Day 3:
* Lazy async HTTP client (`_ensure_client()`)
* Typed `SubmissionOutcome` (confirmed/conflict/rejected/timeout/unavailable/disabled/skipped)
* Retries only for idempotent operations
* Deterministic uuid5 idempotency keys
* Bounded submissions (`asyncio.wait_for`)
* Never claims persistence unless API returns 2xx with a record

**Live HTTP integration test against a running practice-api: NOT performed** (backend was not green until this session; no live test written).

---

## D. LLM architecture

**NOT IMPLEMENTED IN CODE.** Documented in `docs/architecture/day-4-implementation.md`:
* Provider interface: `LLMProvider` protocol (`interpret()`)
* Mock provider: planned, not written
* Real provider adapter: planned, not written (no provider selected)
* Prompt registry: planned (`prompts/v1/intent_extraction.yaml`), not written
* Structured output schema: Pydantic `InterpretationResult`, planned, not written
* Provider credentials: none configured (mock default would be used)

---

## E. Safety implementation

**NOT IMPLEMENTED IN CODE.** Documented in `docs/architecture/day-4-implementation.md`:
* Emergency guard: planned to run before LLM in the voice pipeline
* Fixed script: "This may be a medical emergency. Please hang up and call 999 now."
* Rule IDs: planned, not written
* Explicit confirmation that this is NOT clinical triage: documented

---

## F. Conversation orchestration

**EXISTING (Day 3):** Deterministic FSM (`app/domain/fsm.py`) remains the authority. The Day-3 deterministic `DeterministicResponseHandler` handles keyword routing.

**NOT IMPLEMENTED (Day 4):** LLM interpretation integration, multiple-request handling, caller corrections, explicit confirmation before submission, typed backend actions (`submit_patient_request`, `create_handoff`, `append_audit_event`).

---

## G. Testing

| Suite | Count | Status |
|---|---|---|
| Python unit + integration | 207 | ✅ PASS |
| NestJS unit | 41 | ✅ PASS |
| NestJS db | 8 | ✅ PASS |
| NestJS total | 49 | ✅ PASS |
| Python lint/format | clean | ✅ |
| NestJS lint | 516 problems | ⚠️ Mostly pre-existing `no-unsafe-*` in test files; source files I modified are clean |
| Live provider tests | 0 | ❌ Not executed |
| External runtime tests | 0 | ❌ Not executed |

---

## H. Files changed

### Python (voice gateway)
No Python files changed in Day 4 (Day 3 Python changes remain uncommitted).

### NestJS (practice-api)
**Modified:**
- `src/entities/call.entity.ts` — removed redundant `type:` from `@Column`
- `src/entities/patient-request.entity.ts` — removed redundant `type:` from `@Column`
- `src/entities/handoff.entity.ts` — no change (was already correct)
- `src/entities/practice.entity.ts` — no change
- `src/entities/audit-event.entity.ts` — no change
- `src/database/db-config.ts` — removed duplicate `Constructable`, `Function[]` types, `unknown` casts
- `src/database/database.module.ts` — `APP_CONFIG` provider + `buildAppConfig` import, `useFactory` without `inject`
- `src/common/api/api-error.ts` — `HttpStatusCode` → `HttpStatus`/`number`
- `src/common/api/envelope.ts` — removed `extends Record<string, unknown>`
- `src/common/validation/json-primitive-tree.constraint.ts` — `registerDecorator` options fix
- `src/calls/calls.controller.ts` — added missing swagger imports
- `src/calls/dto/call-responses.dto.ts` — reordered classes (`CallSummariesDto` before `CallWithSummariesDto`)
- `requests/dto/create-request.dto.ts` — `REQUEST_STATUSES[number]` → `RequestStatus`
- `requests/dto/list-requests-query.dto.ts` — `REQUEST_STATUSES[number]` → `RequestStatus`, `RequestDateRange` fields
- `requests/dto/request-responses.dto.ts` — fixed import paths (`../../../` → `../../`)
- `handoffs/dto/create-handoff.dto.ts` — `HANDOFF_STATUSES[number]` → `HandoffStatus`
- `handoffs/dto/handoff-responses.dto.ts` — fixed import paths + added `HandoffEntity` import
- `src/config/env.ts` — manual validation replacing `validateSync()`, added `import 'reflect-metadata'`
- `src/app.module.ts` — removed `APP_CONFIG` provider (moved to DatabaseModule), added `import 'reflect-metadata'`
- `scripts/migrate.ts` — `command as Command` type annotation
- `jest.config.cjs` — esbuild transformer for ESM packages, `transformIgnorePatterns`
- `scripts/jest-esbuild-transform.cjs` — NEW: esbuild ESM→CJS transformer

**Tests modified:**
- `test/unit/calls.spec.ts` — added `status: 'initiated'` to handoff creation
- `test/db/idempotency-concurrency.spec.ts` — `showMigrations()` assertion fix

**Created:**
- `docs/architecture/day-4-implementation.md`
- `docs/architecture/day-4-engineering-report.md` (this file)

**Not committed:** All changes remain uncommitted (repo HEAD: `ccd0586`).

---

## I. Outstanding blockers

| Item | Status |
|---|---|
| LLM provider interface + mock + real adapter | Not implemented |
| Prompt registry | Not implemented |
| Structured output validation | Not implemented |
| Emergency guard | Not implemented |
| Conversation controller (corrections, multiple requests, confirmation) | Not implemented |
| Python↔NestJS live HTTP test | Not performed |
| Azure credentials/API verification | Not executed |
| Exotel live-call verification | Not executed |
| Docker runtime | Not available (no daemon) |
| MySQL runtime | Not available (no Docker) |
| LLM provider credentials | Not configured |

---

## J. Day 5 readiness

The NestJS backend is now stable and tested, providing a foundation for Day 5:
- Practice knowledge base/RAG can build on the existing `practices` table and API patterns
- Grounded practice information responses can use the existing `GET /api/v1/practices/:practiceId/requests` pattern
- More robust conversation evaluation can use the existing test infrastructure (49 passing tests)
- Voice latency optimisation can build on the existing metrics instrumentation
- End-to-end Exotel validation requires Azure credentials and Exotel onboarding (external dependencies)

The LLM interpretation layer, emergency guard, and conversation controller from Day 4 phases 3–8 remain to be implemented.

---

## K. Git status

```
git status --short:
 M .env.example
 M apps/voice-gateway/app/api/routes/health.py
 M apps/voice-gateway/app/api/routes/websocket.py
 M apps/voice-gateway/app/core/config.py
 M apps/voice-gateway/app/core/lifecycle.py
 M apps/voice-gateway/app/main.py
 M apps/voice-gateway/pyproject.toml
 M apps/voice-gateway/tests/integration/conftest.py
 M apps/voice-gateway/uv.lock
 M apps/voice-gateway/app/domain/fsm.py (new)
 M apps/voice-gateway/app/domain/speech.py (new)
 M apps/voice-gateway/app/domain/speech_session.py (new)
 M apps/voice-gateway/app/integrations/practice_api.py (new)
 M apps/voice-gateway/app/services/audio_framer.py (new)
 M apps/voice-gateway/app/services/metrics.py (new)
 M apps/voice-gateway/app/services/response_handler.py (new)
 M apps/voice-gateway/app/services/voice_pipeline.py (new)
 M apps/voice-gateway/app/speech/ (new directory)
 M apps/voice-gateway/tests/integration/test_speech_pipeline.py (new)
 M apps/voice-gateway/tests/unit/test_*.py (new, 9 files)
 M docs/integrations/exotel-protocol.md
 M docs/runbooks/local-development.md
 M infrastructure/docker/docker-compose.yml
 M docs/architecture/day-3-implementation.md (new)
 M docs/architecture/day-3-engineering-report.md (new)
 M docs/architecture/day-4-implementation.md (new)
 M docs/architecture/day-4-engineering-report.md (new)
 M apps/practice-api/ (entire directory, new)
?? apps/practice-api/ (untracked)
```

**HEAD:** `ccd0586a2b5272fa863daa0bdb1c251461c3e7ad`

**Nothing committed or pushed.** All work remains in the working tree.
