# Day 3 — Final engineering report (streaming speech + practice backend)

**Report date:** 2026-11-06 (Day 3 close-out).
**Scope:** streaming speech pipeline (STT → FSM → deterministic response → TTS) on the
existing Exotel gateway + NestJS/MySQL practice backend contract.

---

## 0. Bottom line (honest)

| Claim | Status |
|---|---|
| Day-2 gateway behaviour preserved (106 tests) | ✅ **verified locally** — suite went from 106 → **207 passed** (net **+101** new tests) |
| Day-3 speech pipeline (mock providers) implemented & tested | ✅ **verified locally** (unit + true-uvicorn integration) |
| Lint / format clean (ruff) | ✅ **verified locally** |
| Azure Speech SDK integration (documented streaming API + adapters + mock parity) | ⚠️ **partially verified** — docs + local SDK import verified; **real Azure API run NOT executed** (no credentials) |
| Real Exotel inbound→speech→outbound call | ❌ **not executed** (no account / live credentials / real call) |
| Docker Compose (3 services) runtime | ❌ **not executed** (no Docker daemon on authoring machine) |
| NestJS practice backend build + tests | ❌ **NOT green** as of report time (build: 38 TS errors; jest: cannot even load ESM `@nestjs/typeorm`) — subagent-driven completion attempt crashed twice on provider usage limits; scaffold present but incomplete |

The final success criterion for Day 3 is **approached and substantially satisfied on
the Python side**; the backend half of the criterion is **not yet demonstrably green**
and must be closed on Day 4 before final sign-off.

---

## 1. Repository files created and modified

### Modified (Day 2 code, additive only)

* `apps/voice-gateway/app/core/config.py` — Day-3 settings (`VOICE_PIPELINE`,
  `STT_PROVIDER`, `TTS_PROVIDER`, `AZURE_SPEECH_KEY/REGION/LANGUAGE/VOICE`,
  `SPEECH_CONNECTION_TIMEOUT`, `SPEECH_REQUEST_TIMEOUT`, `MOCK_STT_*`,
  `PRACTICE_API_*`), blank→None validators, speech readiness properties.
* `apps/voice-gateway/app/core/lifecycle.py` — `GatewayState` gains
  `speech_factory` + `practice_client`.
* `apps/voice-gateway/app/main.py` — builds speech factory + practice client in
  lifespan; version bumped to `0.3.0`; startup log additive fields.
* `apps/voice-gateway/app/api/routes/health.py` — additive readiness checks
  (`voice_pipeline`, `stt_provider`, `tts_provider`, `speech_provider_configured`).
* `apps/voice-gateway/app/api/routes/websocket.py` — split-mode route:
  `_session_loop_echo` (Day-2, untouched behaviour) + `_session_loop_speech`
  (pump task, pipeline wiring, renegotiation rejection, provider misconfig → 1011).
* `apps/voice-gateway/pyproject.toml` / `uv.lock` — added
  `azure-cognitiveservices-speech>=1.53,<2.0`; moved `httpx` to main deps.
* `apps/voice-gateway/tests/integration/conftest.py` — pins `VOICE_PIPELINE=echo`
  unless a test opts into `speech`.
* `.env.example` — added all Day-3 variables (azure, speech, practice).

### Created (Day 3 Python)

* `apps/voice-gateway/app/domain/speech.py` — frozen STT/TTS domain events.
* `apps/voice-gateway/app/domain/fsm.py` — `VoiceState`, `TransitionReason`,
  `VALID_TRANSITIONS`, `VoiceTransition`, `VoiceStateMachine`, errors.
* `apps/voice-gateway/app/domain/speech_session.py` — `SpeechSession` (format lock,
  provider states, bounded transcripts, pending utterance/response, cleanup).
* `apps/voice-gateway/app/speech/{base,mock_stt,mock_tts,audio_convert,azure_stt,azure_tts,factory}.py`
* `apps/voice-gateway/app/services/{voice_pipeline,response_handler,audio_framer,metrics}.py`
* `apps/voice-gateway/app/integrations/practice_api.py` — typed client + outcomes.
* `apps/voice-gateway/tests/unit/{test_fsm,test_speech_events,test_speech_session,test_mock_stt,test_mock_tts,test_audio_convert,test_response_handler,test_audio_framer,test_metrics}.py`
* `apps/voice-gateway/tests/integration/test_speech_pipeline.py`
* `docs/architecture/day-3-implementation.md` (new)
* `docs/architecture/day-3-engineering-report.md` (this file, new)
* `infrastructure/docker/docker-compose.yml` (rewritten: gateway + practice-api + MySQL)
* `apps/practice-api/Dockerfile` (new)
* `apps/practice-api/**` (large scaffold: modules, entities, migrations, scripts, tests — see §8)

### Not committed

All Day-3 work is currently **uncommitted** in the working tree (repo HEAD:
`ccd0586`). Nothing was committed by the driving agent during Day 3; the NestJS
subagent was forbidden from git commands.

---

## 2. Azure documentation verified (2026-11-06) — URLs

Verified against official Microsoft Learn (Foundry/Speech service docs) on
2026-11-06, plus the installed Python SDK `azure-cognitiveservices-speech==1.53.0`
(local import verified):

* Azure Speech overview: <https://learn.microsoft.com/en-us/azure/ai-services/speech-service/>
* Speech SDK (Python/C#/…): <https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-sdk>
* Recognize speech (incl. push-stream/continuous recognition): <https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-recognize-speech>
* Audio input streams: <https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-use-audio-input-streams>
* Multichannel recognition: <https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-recognize-speech-multichannel>
* Python API reference (PushAudioInputStream): <https://learn.microsoft.com/en-us/python/api/azure-cognitiveservices-speech/azure.cognitiveservices.speech.audio.pushaudioinputstream?view=azure-python>
* Exotel AgentStream WSS protocol: <https://github.com/exotel/Agent-Stream/blob/main/docs/AGENTSTREAM_WSS_PROTOCOL.md>
* Exotel developer docs: <https://developer.exotel.com/docs/agentstream/websocket-protocol>

---

## 3. STT implementation details

* Interface (`app/speech/base.py`): `SpeechToTextProvider` protocol — `start`,
  `push_audio`, `events() -> AsyncIterator[SttEvent]`, `stop` (timeouts bounded).
* Events (`app/domain/speech.py`): `RecognitionStarted`, `PartialTranscript`,
  `FinalTranscript`, `RecognitionStopped`, `RecognitionError` (frozen, validated).
* Mock STT (`app/speech/mock_stt.py`): duration-driven deterministic recognition
  (`MOCK_STT_UTTERANCE_MS` threshold, `MOCK_STT_FINALIZE_MS` finalize latency, script
  cycling, `MOCK_STT_ERROR_AT_FRAME` fault injection). Documented as duration-based,
  not audio-derived (honest mock contract).
* Azure STT (`app/speech/azure_stt.py`): `PushAudioInputStream` +
  `AudioStreamFormat(samples_per_second, bits_per_sample=16, channels=1)` +
  continuous recognition (`recognizing`/`recognized`/`canceled`);
  SDK-thread callbacks → `loop.call_soon_threadsafe`; connect/stop timeouts via
  `asyncio.to_thread(handle.get)` + `asyncio.wait_for`; confidence defensively
  extracted from result JSON (`Confidence` / `NBest[0]`).

## 4. TTS implementation details

* Interface (`app/speech/base.py`): `TextToSpeechProvider` — `synthesize(text, session_id, audio_format) -> AsyncIterator[TtsEvent]`, `cancel_turn`.
* Events: `SynthesisStarted`, `SynthesisChunk`, `SynthesisCompleted` (always last;
  `cancelled=True` on cancellation).
* Mock TTS (`app/speech/mock_tts.py`): deterministic PCM16 tone, 100 ms chunks, N×70 ms
  per char capped at 4.8 s, 0-byte synthesis for empty text, cooperative cancellation.
* Azure TTS (`app/speech/azure_tts.py`): `SpeechSynthesizer(speech_config, audio_config=None)`
  (capture), `synthesizing` event → chunks, `synthesis_completed`/`canceled` → end;
  per-session serialization lock; raw PCM output via `Raw{8,16,24}Khz16BitMonoPcm`;
  `en-GB-SoniaNeural`; request timeout enforced via `asyncio.wait_for`.

## 5. Audio formats & resampling

* Negotiated set: PCM16 mono, 8 kHz / 16 kHz / 24 kHz (`SUPPORTED_SAMPLE_RATES`).
* Format locked at pipeline build (from `start.media_format` / `?sample-rate=`); any
  later change → `MediaFormatChangedError` → close 1008 (`unsupported media renegotiation`).
* Explicit conversion layer `app/speech/audio_convert.py` (`convert_pcm16`,
  `convert_if_needed`): linear resampler, identity passthrough, PCM16 alignment
  (odd trailing byte dropped + counted), unsupported shapes refused. The Day-2
  `AudioFormat` model itself already enforces encoding/rate/channels, so adapter-level
  conversion errors are defence-in-depth.

## 6. FSM & session state

States: `IDLE, CONNECTING, LISTENING, PROCESSING, SPEAKING, HANDOFF, ENDED, ERROR`.
Transitions implemented explicitly in `app/domain/fsm.py`; typed reasons; history &
snapshots recorded. Key rules: non-terminal states may end (→ `ENDED` terminal);
`SPEAKING → LISTENING` only on response completion (no barge-in); `ERROR → ENDED`
only; low-confidence policy (<0.5) → clarification, no submission.

## 7. Session management behaviour

Per-call `SpeechSession` (format lock, provider states, bounded transcript history,
pending utterance/response, lifecycle counters, idempotent cleanup). Registry cleanup
guaranteed in the route `finally` (all termination paths); verified by integration test
(`poll_active_sessions → 0` after abrupt disconnect).

## 8. NestJS practice backend (architecture as scaffolded)

Location: `apps/practice-api/` (NestJS 11 + TypeORM + class-validator +
swagger + jest + typeorm CLI-style migration scripts). Modules: `practices`, `calls`,
`requests`, `handoffs`, `audit`, `health`, `common` (envelope/error codes/idempotency
util/pagination/json-tree constraint), `database` (dual driver), `config`.

* Five tables: `practices`, `calls`, `patient_requests`, `handoffs`, `audit_events`
  (migration `migrations/1794000000000-CreateSchema`).
* Six routes: `POST /api/v1/calls`, `GET /api/v1/calls/:id`,
  `POST /api/v1/requests`, `GET /api/v1/practices/:practiceId/requests`,
  `POST /api/v1/handoffs`, `POST /api/v1/audit-events` (+ `GET /api/v1/health`).
* Idempotency: `Idempotency-Key` header on `POST /api/v1/requests`;
  unique `(practiceId, idempotencyKey)` + payload-hash conflict → `409
  IDEMPOTENCY_KEY_CONFLICT`; concurrent duplicates race-safe via unique constraint +
  transaction re-select.
* Envelope: success `{ data, meta? }`; errors `{ error: { code, message, details? } }`.

**Verification state (honest):** as of this report the scaffold exists on disk but
`pnpm run build` fails (~38 TS errors: missing imports such as `ApiCreatedResponse`,
`HandoffEntity`, `PatientRequestEntity`, `Res`, `AppConfig`; `.js`-suffix module
resolution issues; `exactOptionalPropertyTypes` friction with TypeORM/class-validator
types; `PaginationMeta` constraint; enum-as-type usage); `pnpm test` cannot start
(jest ESM `SyntaxError: Unexpected token 'export'` loading `@nestjs/typeorm`);
`pnpm lint` status not reached. These must be resolved before the backend criterion
is met. (A completion subagent was relaunched twice; both attempts hit the provider
free-usage limit before doing any work.)

## 9–12. Remaining report sections

* **§9 Five-table schema** — as described in §8 (details in migration file; MySQL JSON
  column / SQLite TEXT duality handled by driver-aware column types, per scaffold design).
* **§10 Six route specs** — per assignment §9 (create-or-get call; get call w/ summaries;
  submit request w/ idempotency; list requests w/ pagination/filters; create handoff
  w/ status semantics; append audit event). Swagger at `/docs`.
* **§11 Idempotency** — implementation + tests authored (`test/db/idempotency-concurrency.spec.ts`
  etc.); final green-run status **not yet obtained**.
* **§12 Python→NestJS integration evidence** — `PracticeApiClient`
  (`app/integrations/practice_api.py`): lazy async client, timeouts, retries only for
  idempotent ops, `SubmissionOutcome` (confirmed/conflict/rejected/timeout/unavailable/
  disabled/skipped), deterministic uuid5 idempotency keys, bounded submissions, no
  persistence claims without API confirmation. Live HTTP integration test against a
  running practice-api is **pending** (backend currently not runnable green).

---

## 13. Exact test counts

| Suite | Result |
|---|---|
| Day-2 baseline (pre-Day-3 edits) | **106 passed**, 0 failed |
| Day-3 final full suite | **207 passed**, 0 failed (unit + integration) |
| Net new tests this day | **+101** |
| Unit suites added (this day) | FSM (12), speech events (11), speech session (12), mock STT (9), mock TTS (9), audio convert (10), response handler (11), framer (12), metrics (8) — plus speech integration (7) |
| Ruff `check` (`app` + `tests`) | **All checks passed!** |
| Ruff `format --check` | **All files formatted** |
| Python type-check (`mypy/pyright`) | **not configured** (ruff only) |

## 14. Ruff / lint / type-check results

* `uv run ruff check tests app` → **All checks passed!**
* `uv run ruff format --check tests app` → **66 files already formatted** (after one
  reformat of `tests/unit/test_metrics.py`).
* Python type-check tooling: none configured in `pyproject.toml` → no mypy/pyright run
  performed/possible without adding config.

## 15. Docker build/runtime status

* Authored: `infrastructure/docker/docker-compose.yml` (services: `voice-gateway`,
  `practice-api`, `mysql:8.0` + `mysql_data` volume; healthchecks; gateway→practice-api
  ordering; practice-api→mysql ordering), `apps/practice-api/Dockerfile` (multi-stage
  node build), gateway Dockerfile from Day 2 (extended env by compose).
* **Not executed**: no Docker CLI/daemon on the authoring machine → all runtime
  verification is **pending / unverified**. Commands documented in
  `docs/runbooks/local-development.md` §6 and `docs/architecture/day-3-implementation.md`.

## 16. Real Azure tests vs mock tests

| Behaviour | Test coverage | Status |
|---|---|---|
| Mock STT start/push/final/partial/stop/error | unit + integration | ✅ green |
| Mock TTS synthesis/cancel/empty/format | unit | ✅ green |
| Azure adapters (stubbed SDK via `sys.modules`) | unit | ✅ written & green (per checkpoint) |
| Real Azure recognition/synthesis run | — | ❌ **not executed** (no key/region/credentials) |
| Real Exotel call end-to-end | — | ❌ **not executed** |

## 17. Remaining external requirements (Day 3 close-out)

1. Resolve ~38 NestJS TypeScript errors + jest ESM setup so `apps/practice-api`
   builds and its suites pass (backend half of success criterion).
2. Run `pnpm migration:run` / `pnpm seed` against throwaway SQLite; capture seeded
   practice UUID for the report.
3. Real Azure credentials + first streaming STT/TTS run (F0).
4. Real Exotel onboarding + inbound call completing a speech round trip.
5. Docker daemon availability for full compose verification.
6. Langfuse emission (Day 4), LLM orchestration (Day 4).

## 18. Day 4 readiness

Ready to slot in: LLM orchestration behind the deterministic handler seam; Azure
credential wiring; Langfuse; emergency/human handoff policy evaluation; full
backend persistence integration; Docker runtime verification.

---

## 19. Final success criteria — honest assessment

**Achieved (verified locally on 2026-11-06):**
- Existing gateway preserved; Day-2 tests green (106).
- Speech pipeline: mocked STT/TTS produce normalized events; FSM drives; deterministic
  response; TTS audio framed outbound (≥ 3,200 B chunks, 32,000 B target); clean closes;
  renegotiation rejected; injected errors tolerated; cleanup verified.
- Full suite green, lint/format clean.

**Not achieved (require external/unfinished work):**
- Real Azure speech execution; real Exotel call; Docker runtime; NestJS backend green;
  Python↔NestJS live HTTP test.

**Safety posture (carried into Day 4):** deterministic response handler contains no
clinical logic; no LLM yet; prototype is not a medical device, not NHS-connected, not
DTAC/MHRA approved; synthetic data only; no persistence claims without API confirmation.

---

*Report closes Day 3. Remaining work tracked for Day 4: finish the NestJS backend
build/test repair, Azure credential wiring, live calls, Docker verification, LLM
orchestration, Langfuse.*
