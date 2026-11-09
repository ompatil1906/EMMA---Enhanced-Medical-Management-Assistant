# Day 18 — Live Provider Integration Report (historical record)

**Report date:** 2026-11-09 (Day 18 close).
**Git branch:** `main`
**Git HEAD:** `3d40b9409b635623f22ce1cd0220f1f23f647572`
**Git status:** all Day 1–18 changes uncommitted (no commit, push, reset or clean).

> This file preserves the Day 18 record exactly as closed. The Day 18B
> continuation is appended in §19 and does **not** revise the Day 18 outcome:
> Day 18 stopped at the credential gate.

---

## 1. Outcome — STOPPED at the credential gate

Day 18 was required to make one controlled browser conversation with real
Deepgram STT and real ElevenLabs TTS (mock LLM). Rotation of the previously
chat-exposed provider keys **could not be established**, so by instruction the
live phases did not run.

| Metric | Day 18 result |
|---|---|
| Deepgram sessions | **0** |
| ElevenLabs requests | **0** |
| OpenAI / Gemini requests | **0** |
| Spend | **$0.00** |
| Success criterion | **NOT MET (blocked)** |

## 2. Defects found and fixed (offline)

1. **Deepgram contract mismatch** — `model=flux-general-en` was sent to the
   classic `/v1/listen` endpoint with `interim_results`/`endpointing`, parsing
   classic `Results`. Flux is served from `/v2/listen` with a `TurnInfo`
   schema. Fixed: model family selects endpoint + schema; model identifiers
   unchanged.
2. **ElevenLabs contract mismatch** — the adapter used
   `wss://…/{voice_id}/stream/input` (not an endpoint) and expected
   `{"type":"audio","audio_base64":…}` frames. Replaced with the documented
   HTTP streaming endpoint and `output_format=pcm_<rate>`, which also yields
   the pipeline's exact PCM16 mono format with no decode step.
3. **Runtime dependency bug** — `websockets` was dev-only while imported by a
   runtime adapter; a production install could not have used Deepgram STT.
4. **Fatal-error ordering** — a fatal provider error queued its stream sentinel
   before the error event, so teardown silently swallowed the error.
5. **Whitespace voice id accepted** — a blank `ELEVENLABS_VOICE_ID` passed
   validation and would have become a 404 mid-call.
6. **Cancellation misclassified** — closing an in-flight ElevenLabs response
   (barge-in) raised `httpx.ReadError` and was reported as a provider failure
   instead of a cancelled turn.
7. **Production validation gap** — `STT_PROVIDER=deepgram` had no production
   credential check; blank model/voice values were not normalised.
8. **Readiness blind spot** — `/health/ready` did not expose the LLM selection,
   so a live run could not prove it was not on a live LLM.

## 3. What was verified offline

| Suite | Tests | Level |
|---|---|---|
| `tests/integration/test_deepgram_protocol.py` | 8 | real local WebSocket server: path/query/auth header/binary frames/`TurnInfo` mapping/`CloseStream`/classic path/errors |
| `tests/integration/test_elevenlabs_protocol.py` | 14 | real local uvicorn server: request shape/chunked PCM/401·402·404/empty audio/timeout/cancellation/rate guard |
| `tests/unit/test_live_provider_budget.py` | 12 | caps enforced before the call, refusals recorded, report shape |
| `tests/unit/test_deepgram_stt.py` | 8 | Flux vs classic mapping, endpoint selection, credential guard |
| `tests/unit/test_elevenlabs_tts.py` | 10 | request shape via `httpx.MockTransport`, streaming, cancel, empty text |
| `tests/live/test_day18_live_providers.py` | 2 | opt-in, double-gated, **skipped** |

Classification: **IMPLEMENTED** and **TESTED (offline)** — explicitly **not**
`VERIFIED` against the live providers.

## 4. Regression counts (last verified run, Day 18)

| Gate | Result |
|---|---|
| Python `pytest` | 481 passed, 6 skipped |
| `ruff check` | clean |
| `ruff format --check` | 143 files formatted |
| UI `tsc --noEmit` | 0 errors |
| UI `pnpm lint` | 0 errors, 3 warnings (scratch specs) |
| UI `pnpm build` | clean |
| NestJS `pnpm test` | 8 suites, 49 passed |
| Day 17 Playwright (8 scenarios) | could not run — local services were down |

## 5. Safety posture (unchanged)

Emergency guard before LLM/RAG with the approved 999 wording, explicit
confirmation, practice isolation, duplicate-submission prevention, mock LLM.
No patient data, no NHS systems, no OpenAI/Gemini/Exotel, no deployment.

## 6. Files added on Day 18

`app/services/live_provider_budget.py`, `tools/make_speech_fixture.py`,
`tests/fixtures/speech_gp_appointment.wav`,
`tests/integration/test_deepgram_protocol.py`,
`tests/integration/test_elevenlabs_protocol.py`,
`tests/unit/test_live_provider_budget.py`,
`tests/live/test_day18_live_providers.py`,
`apps/ui/tests/e2e-live/live-provider-acceptance.spec.ts`,
`apps/ui/playwright.live.config.js`, `docs/LIVE_PROVIDER_ACCEPTANCE.md`.

Modified: `app/speech/deepgram_stt.py`, `app/speech/elevenlabs_tts.py`,
`app/core/config.py`, `app/api/routes/health.py`, `pyproject.toml`, `uv.lock`,
`tests/unit/test_{deepgram_stt,elevenlabs_tts}.py`,
`apps/ui/tests/e2e/helpers/{browser,harness}.ts`,
`apps/ui/playwright.config.js`, `docs/VOICE_PROVIDER_CONFIGURATION.md`,
`tests/fixtures/README.md`.

---

# 19. Day 18B addendum — attempt 2 (2026-11-09)

**Result: `BLOCKED` at the same credential gate. Still 0 live provider
requests. The Day 18 outcome in §1 is unchanged.**

## 19.1 Gate re-check (evidence, values never read)

| Check | Result |
|---|---|
| `grep -c EMMA_LIVE .env` | **0** — no attestation flags present |
| `grep -c EMMA_LIVE_PROVIDER_SPEND_APPROVED .env` | **0** |
| `ls -l .env` | `3761` bytes, mtime `Nov 8 17:06` — **unchanged** since the Day 18 preflight reading |

Neither `EMMA_LIVE_CREDENTIALS_ROTATED=1` nor
`EMMA_LIVE_PROVIDER_SPEND_APPROVED=1` was set, so rotation is
**USER-ATTESTED: no**. No live call was attempted, in line with the brief.

## 19.2 Second blocker: command execution unavailable

Every project execution command in the Day 18B session was refused by the
session's command reviewer (*"too much or invalid context for automatic
review"*): pytest, uvicorn, playwright, ruff, `git status`, and even
`.venv/bin/python -V`. Read-only listings worked.

Consequences, stated plainly:

* Day 18B **did not** re-run the offline regression; the §4 numbers above are
  the last verified run and are labelled as such.
* Local LiveKit/gateway/UI services could not be inspected or (re)started, so
  the Day 17 browser suite and the live browser scenario could not run.
* No runtime code was modified in that session: with no ability to execute a
  single test, shipping a runtime budget guard blind would have been an
  unverifiable change to the gateway's startup path.

## 19.3 What Day 18B changed

Documentation only:

* `docs/LIVE_PROVIDER_ACCEPTANCE.md` gained §1.2 (gate result), §1.3 (execution
  blocker) and §1.4 (the runtime-guard design that must land before a live
  run).
* This report file was created so the Day 18 record exists in the repository
  (Day 18's report had been delivered in chat only) and so the Day 18/18B
  distinction is explicit.

No code, test, configuration, `.env` or fixture was modified.

## 19.4 Open prerequisite before the next attempt

1. Rotate both keys, update the repository `.env`, and add
   `EMMA_LIVE_CREDENTIALS_ROTATED=1` + `EMMA_LIVE_PROVIDER_SPEND_APPROVED=1`.
2. Implement and **test** §1.4 of the acceptance doc (runtime ledger guard
   wired into `SpeechProviderFactory`, observable on `/health/ready`).
3. Start LiveKit + gateway (`STT_PROVIDER=deepgram`, `TTS_PROVIDER=elevenlabs`,
   `LLM_PROVIDER=mock`) + UI, then run Phases A/B/C.

Until all three are done the success criterion stays `NOT MET`, and Day 19
should not start.

---

# 20. Day 18C addendum — runtime spend guard (2026-11-09)

**Result: the §4 gap is closed. Guard IMPLEMENTED and TESTED offline. No live
provider request was made, so the Day 18 success criterion remains NOT MET.**

This section adds to the record; it does not revise §1 (Day 18 stopped at the
credential gate) or §19 (Day 18B could not execute commands).

## 20.1 Command execution recovered

The Day 18B execution blocker was environmental and cleared in this session:
Python 3.13.5 (project venv), uv 0.13.1, Node 26.7.0, pnpm 12.10.1 all usable,
and `pytest` runs normally. Local LiveKit 1.13.11, the gateway (mock providers)
and the Next.js dev server were restarted for the browser-suite attempt.

## 20.2 What was built

| Piece | Location | Status |
|---|---|---|
| Persistent, lock-protected ledger | `app/services/live_provider_budget.py` (`LiveProviderLedger`) | IMPLEMENTED + TESTED |
| Runtime wrappers | `app/speech/budget_guard.py` (`BudgetedSTT`, `BudgetedTTS`) | IMPLEMENTED + TESTED |
| Factory enforcement (single construction point) | `app/speech/factory.py` | IMPLEMENTED + TESTED |
| Agent construction seam | `app/integrations/livekit/agent.py` (`build_speech_factory`) | IMPLEMENTED + TESTED |
| Guard configuration, hard ceilings | `app/core/config.py` | IMPLEMENTED + TESTED |
| Readiness surface + fail-closed gate | `app/api/routes/health.py` | IMPLEMENTED + TESTED |

Design, ledger format, locking/atomicity, restart semantics, health payload and
platform assumptions: `docs/LIVE_PROVIDER_ACCEPTANCE.md` §14.

## 20.3 Evidence

* **68 new tests** (549 passed, 6 skipped total; was 481/6).
* Concurrency: 10 threads and 6 forked processes cannot exceed the cap.
* Restart: a new ledger object over the same file continues the budget.
* Fail closed: corrupt/unwritable ledger → provider construction raises and
  `/health/ready` returns 503 when a live provider is selected.
* Refusals never reach the network: local fake Deepgram/ElevenLabs servers
  record zero connections/requests for refused calls.

## 20.4 Regression (this session)

| Gate | Result |
|---|---|
| Python `pytest` | **549 passed, 6 skipped** |
| `ruff check app tests tools` | clean |
| `ruff format --check` | 149 files formatted |
| NestJS `pnpm test` | **8 suites, 49 passed** |
| UI `tsc --noEmit` | 0 errors |
| UI `pnpm lint` | 0 errors (3 warnings, scratch specs) |
| UI `pnpm build` | clean (`/`, `/voice`) |
| Day 17 offline Playwright (8 scenarios) | **NOT RUN** — LiveKit rejected the agent with `401 … ws://127.0.0.1:7880/agent` because the server was started with `--dev` default keys while the project `.env` holds different LiveKit credentials; reading `.env` to match them is blocked in this environment |

## 20.5 Still blocked

1. Credential rotation is still not user-attested (`.env` unchanged, no
   `EMMA_LIVE_*` flags), so no live provider call was permitted.
2. Live Deepgram/ElevenLabs behaviour, the browser round trip and any latency
   numbers remain UNVERIFIED.
3. Day 17 suite needs a LiveKit server started with the project's own
   `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET`.

