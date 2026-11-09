# Live Provider Acceptance — EMMA Day 18

**Purpose:** Validate EMMA's real provider integrations —

```
Browser → LiveKit → agent → real Deepgram STT → ConversationController →
mock LLM → real ElevenLabs TTS → LiveKit → browser playback
```

with **no OpenAI usage** (the OpenAI provider stays disabled and its budget
guard untouched).

**Status (Day 18 close):** `BLOCKED` for live calls — see §2. Everything that
does not require a provider request is `IMPLEMENTED` and `TESTED` offline:
the two adapters were corrected to their documented API contracts and are
covered by protocol tests against **local** servers (real WebSocket / real
HTTP), plus a spend guard and an opt-in acceptance harness.

**Status (Day 18B, attempt 2):** `BLOCKED` at the same credential gate — and
for a second, independent reason. See §1.2. **Still 0 live provider requests.**

**No live provider request was made on Day 18: 0 Deepgram sessions,
0 ElevenLabs requests, $0.00.** Nothing in this document is a claim about
live provider behaviour.

**Security:** never print, log or commit credentials. Only variable *names*
appear here. `.env` is git-ignored; `.env.example` holds placeholders only.

---

## 1. Why the live phases are blocked

The brief requires, before any real request:

> Confirm the Deepgram API key has been replaced with a newly generated key.
> Confirm the ElevenLabs API key has been replaced with a newly generated key.
> … If rotation cannot be established … stop live calls for that provider and
> report the blocker.

Rotation **could not be established**:

| Check | Result |
|---|---|
| `.env` git-ignored | ✅ `.gitignore:2` matches `.env` |
| `.env` tracked by git | ✅ not tracked, absent from `git status` |
| `.env.example` contains placeholders only | ✅ provider keys are commented-out blank names |
| Rotation evidence in `.env`/repo | ❌ **none** — `.env` last modified `2026-11-08 17:06`, i.e. **before** the Day 17 session (2026-11-09) in which the keys were declared compromised; no rotation note exists anywhere in the repository |
| Credential values inspected | ❌ not permitted in this environment (secret-scanning policy blocks reading `.env` contents) |
| Outbound reachability of `api.deepgram.com` / `api.elevenlabs.io` | ❌ probing external provider hosts is blocked by the same policy |

With no evidence that the previously chat-exposed keys were replaced, using
them would mean spending money with a **compromised** credential. The correct
action is to stop and hand the decision back, not to guess.

### 1.1 To unblock

1. Rotate both keys in the provider dashboards (invalidate the old ones).
2. Put the **new** values into the repository `.env` (`DEEPGRAM_API_KEY`,
   `ELEVENLABS_API_KEY`); confirm `ELEVENLABS_VOICE_ID` still resolves to a
   voice on the rotated account.
3. Re-run §5 / §6 below — the harness refuses to run without
   `EMMA_LIVE_CREDENTIALS_ROTATED=1`, which is exactly the operator
   attestation "new keys are in place".

Nothing else in the harness needs to change.

### 1.2 Day 18B gate check (attempt 2) — result

Day 18B re-ran the gate. It failed again, on evidence:

| Check | Command used | Result |
|---|---|---|
| `.env` contains `EMMA_LIVE_*` at all | `grep -c EMMA_LIVE .env` | **0** |
| `.env` contains `EMMA_LIVE_PROVIDER_SPEND_APPROVED` | `grep -c … .env` | **0** |
| `.env` changed since Day 18 preflight | `ls -l .env` | **no** — `3761` bytes, mtime `Nov 8 17:06` (identical to the Day 18 reading) |

So neither attestation flag (`EMMA_LIVE_CREDENTIALS_ROTATED=1`,
`EMMA_LIVE_PROVIDER_SPEND_APPROVED=1`) was present, and `.env` had not been
edited since before the compromise was declared. Per the Day 18B brief the run
stopped at the gate: **no provider request was made** (0 Deepgram sessions,
0 ElevenLabs requests).

Rotation therefore remains **UNVERIFIED / not user-attested**. Nothing in this
document should be read as a claim that the old keys were replaced.

### 1.3 Second, independent blocker — command execution

In the Day 18B session the command executor refused every project execution
command (pytest, uvicorn, playwright, ruff, `git status`, even
`.venv/bin/python -V`) with *"This action has too much or invalid context for
automatic review"*. Only read-only listings succeeded. Consequences:

* the offline regression could **not** be re-run in that session (the Day 18
  numbers below remain the last verified run);
* local LiveKit/gateway/UI services could neither be inspected nor started;
* no runtime code was modified blind — shipping untested runtime changes was
  judged worse than documenting the gap (see §4 of the Day 18B report).

### 1.4 Runtime spend guard — IMPLEMENTED and TESTED (Day 18C)

Day 18 flagged that the budget guard covered only the standalone harness. Day
18C closed that gap: the guard now wraps the live adapters inside the real
runtime path.

```
LiveKit agent entrypoint
  └── build_speech_factory(settings)          # app/integrations/livekit/agent.py
        └── SpeechProviderFactory              # single construction point
              ├── BudgetedSTT(DeepgramFluxSTTProvider, ledger)
              └── BudgetedTTS(ElevenLabsFlashTTSProvider, ledger)
                    └── LiveProviderLedger     # app/services/live_provider_budget.py
```

See §14 for the ledger format, locking, restart behaviour, reservation rules,
health surface, platform assumptions and test evidence.

---

## 2. Provider configuration (as implemented)

| Layer | Provider | Model (env var) | Endpoint used | API key env var |
|---|---|---|---|---|
| STT | Deepgram Flux | `DEEPGRAM_MODEL` (`flux-general-en`) | `wss://api.deepgram.com/v2/listen` | `DEEPGRAM_API_KEY` |
| STT (alt) | Deepgram classic | e.g. `nova-3` | `wss://api.deepgram.com/v1/listen` | `DEEPGRAM_API_KEY` |
| TTS | ElevenLabs Flash | `ELEVENLABS_MODEL` (`eleven_flash_v2_5`) | `POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream` | `ELEVENLABS_API_KEY` |
| TTS voice | ElevenLabs | — | — | `ELEVENLABS_VOICE_ID` |
| LLM | **mock (unchanged)** | `LLM_PROVIDER=mock` | — | — |

Required test configuration:

```env
VOICE_TRANSPORT=livekit
STT_PROVIDER=deepgram
TTS_PROVIDER=elevenlabs
LLM_PROVIDER=mock
```

Audio contract:

| Direction | Format | Notes |
|---|---|---|
| Deepgram input | PCM16 LE, mono, 16 kHz (`encoding=linear16`) | pushed as binary WebSocket frames |
| Flux output | `TurnInfo` messages | `Update` / `EagerEndOfTurn` → partial, `EndOfTurn` → final |
| ElevenLabs input | JSON body `{text, model_id, voice_settings}` | `xi-api-key` header |
| ElevenLabs output | `pcm_16000` (PCM16 LE mono) | requested explicitly, so no decode/resample step |

### 2.1 What changed on Day 18 (and why)

Both Day 12 adapters were written against guessed protocols; neither could
have worked against the real APIs.

| File | Day 12 (wrong) | Day 18 (documented) |
|---|---|---|
| `app/speech/deepgram_stt.py` | `model=flux-general-en` sent to `/v1/listen` with `interim_results`/`endpointing`, parsed classic `Results` | Flux models → `/v2/listen` + `TurnInfo`; other models keep `/v1/listen` + `Results`. Model identifiers unchanged |
| `app/speech/elevenlabs_tts.py` | `wss://…/{voice_id}/stream/input`, expected `{"type":"audio","audio_base64":…}` | `POST …/{voice_id}/stream?output_format=pcm_<rate>` streaming raw PCM with `xi-api-key`; `httpx` (already a runtime dep) |
| `pyproject.toml` | `websockets` was **dev-only** while imported by a runtime adapter | moved to runtime dependencies |
| `app/core/config.py` | blank `ELEVENLABS_VOICE_ID` / `DEEPGRAM_MODEL` could slip through | blank values normalised/rejected; production requires a Deepgram key when `STT_PROVIDER=deepgram` |
| `app/api/routes/health.py` | readiness did not expose the LLM selection | adds `llm_provider` so the live run can prove it is not on a live LLM |

No model was changed, no SDK was upgraded, and no provider was silently
swapped for a mock.

---

## 3. Spend limits (enforced in code)

`app/services/live_provider_budget.py` refuses a call **before** it happens:

| Limit | Value | Env override (optional) |
|---|---|---|
| Deepgram streaming sessions | 3 | `EMMA_LIVE_MAX_DEEPGRAM_SESSIONS` |
| Audio per Deepgram session | 15 s | `EMMA_LIVE_MAX_DEEPGRAM_AUDIO_SECONDS` |
| ElevenLabs synthesis requests | 3 | `EMMA_LIVE_MAX_ELEVENLABS_REQUESTS` |
| Characters per ElevenLabs request | 300 | `EMMA_LIVE_MAX_ELEVENLABS_CHARS` |

Additional rules honoured by the harness: no retries after an auth/billing
failure, no automatic retry loops, no parallel live requests, no load tests.

**Planned usage for one full Day 18 run** (this is the whole day, not per test):

| Phase | Deepgram | ElevenLabs |
|---|---|---|
| A — Deepgram smoke (`tests/live/test_day18_live_providers.py`) | 1 session, 3.7 s | — |
| B — ElevenLabs smoke (same file) | — | 1 request, 79 chars |
| C — browser conversation (`tests/e2e-live/…`) | 1 session, 3.7 s | 2 requests (greeting + one answer) |
| **Total** | **2 sessions, 7.4 s** | **3 requests, ≤300 chars each** |

Run Phases A/B **or** C per session if you want to stay at 1 Deepgram session.

---

## 4. Test phrases and audio fixtures

| Fixture | Content | Format | Use |
|---|---|---|---|
| `tests/fixtures/speech_gp_appointment.wav` | "Hello, I would like to book a routine GP appointment." (macOS `say`, voice `Daniel`, en-GB) | WAV / PCM16 / mono / 16 kHz, 3.67 s, peak −1.6 dBFS, RMS −19.4 dBFS, 67 % voiced | Phase A/C microphone input |
| generated at test time (`--trailing-silence-ms 15000`) | same phrase **+ 15 s silence** | same, 18.67 s | Phase C, so Chromium's looping fake microphone yields exactly one utterance |

The fixtures are machine-generated speech, not human recordings and not
synthetic tones: they verify *recognition and plumbing*, never human
microphone quality.

Regenerate either fixture locally (no network, no cost):

```bash
cd apps/voice-gateway
.venv/bin/python -m tools.make_speech_fixture \
  --out tests/fixtures/speech_gp_appointment.wav
.venv/bin/python -m tools.make_speech_fixture \
  --out /tmp/oneshot.wav --trailing-silence-ms 15000
```

Reference transcripts:

* STT: `Hello, I would like to book a routine GP appointment.`
* TTS: `Good morning. Thank you for calling your GP practice. How may I help you today?` (79 chars)

---

## 5. Phase A + B — provider smoke tests (opt-in)

```bash
cd apps/voice-gateway

# Preconditions: services configured with the real providers (see §7)
EMMA_RUN_LIVE_TESTS=1 EMMA_LIVE_CREDENTIALS_ROTATED=1 \
  .venv/bin/python -m pytest tests/live/test_day18_live_providers.py -q -s
```

Without both variables the module **skips** — a plain `pytest` run stays
offline, deterministic and free.

What it records (printed, then pasted into §9 after a real run): connect
latency, first-partial latency, final-transcript latency, observed transcript,
token overlap against the reference, ElevenLabs first-chunk latency, total
synthesis time, byte count, decoded audio duration, and the budget summary.

Failure expectations encoded in the test:

| Case | Expected |
|---|---|
| Missing/blank key | constructor raises `ProviderConfigurationError` (no mock fallback) |
| Invalid key | provider error surfaced (`ProviderSynthesisError` / `ProviderConnectionError`), no retry |
| Unknown voice id | `ProviderSynthesisError` naming `ELEVENLABS_VOICE_ID` (HTTP 404 path) |
| Silent/empty audio | refused (`elevenlabs TTS returned an empty audio response`) |
| Timeout | `ProviderSynthesisError` (transport failure), no retry |

---

## 6. Phase C — browser conversation (opt-in)

Preconditions: LiveKit server, gateway **with the real providers**, UI dev
server, and no other agent worker running (the harness starts its own).

```bash
cd apps/ui
EMMA_RUN_LIVE_TESTS=1 EMMA_LIVE_CREDENTIALS_ROTATED=1 \
  npx playwright test -c playwright.live.config.js --reporter=list
```

The spec cannot be reached from the default config:

* `playwright.config.js` scans `tests/e2e` only (the eight Day 17 scenarios),
  so `npx playwright test` can never start a paid call;
* the live spec lives in `tests/e2e-live/` and runs only via
  `playwright.live.config.js`;
* both gates (`EMMA_RUN_LIVE_TESTS`, `EMMA_LIVE_CREDENTIALS_ROTATED`) are
  additionally checked inside the file.

Before the call it asserts the gateway really is on
`stt_provider=deepgram`, `tts_provider=elevenlabs`, `llm_provider=mock` and
refuses to continue otherwise. It then asserts: Deepgram transcript on the data
channel and in the UI, an `assistant` control event from the mock LLM,
non-silent agent audio in the room, an attached and playing
`<audio data-emma-remote-audio>` element, and a console-clean session. It ends
the call immediately afterwards to stop the looping fake microphone.

---

## 7. Service startup for a live run

```bash
# 1. LiveKit (self-hosted, unchanged)
livekit-server --dev --keys "<key>: <secret>" --node-ip 127.0.0.1 --udp-port 7881

# 2. Gateway with REAL speech providers and a MOCK LLM
cd apps/voice-gateway
STT_PROVIDER=deepgram TTS_PROVIDER=elevenlabs LLM_PROVIDER=mock \
VOICE_TRANSPORT=livekit \
  .venv/bin/python -m uvicorn app.main:app --port 8000

# 3. UI
cd apps/ui
NEXT_PUBLIC_VOICE_GATEWAY_URL=http://localhost:8000 \
NEXT_PUBLIC_VOICE_TRANSPORT=livekit pnpm dev
```

Verify before spending anything:

```bash
curl -s http://localhost:8000/health/ready | python3 -m json.tool
# expect: "stt_provider": "deepgram", "tts_provider": "elevenlabs",
#         "llm_provider": "mock", "speech_provider_configured": true
```

The agent worker is started by the harness with the same environment
(`STT_PROVIDER`/`TTS_PROVIDER`/`LLM_PROVIDER` are inherited). Do not run a
second worker.

---

## 8. Offline verification actually performed on Day 18

| Suite | What it proves | Tests |
|---|---|---|
| `tests/integration/test_deepgram_protocol.py` | real local WebSocket server sees `/v2/listen` + `flux-*` query, `Token` auth header, binary PCM frames, `TurnInfo`→partial/final mapping, `CloseStream`, classic `/v1/listen` path, fatal vs non-fatal errors, refused connection, no credential → refuse | 8 |
| `tests/integration/test_elevenlabs_protocol.py` | real local uvicorn server sees `POST /v1/text-to-speech/{id}/stream?output_format=pcm_16000`, `xi-api-key`, JSON body, chunked PCM streaming, 401/402/404 mapping, empty-audio failure, timeout, cancellation, unsupported rate, no request for empty text | 14 |
| `tests/unit/test_live_provider_budget.py` | caps enforced before the call, refusals recorded, report shape | 12 |
| `tests/unit/test_deepgram_stt.py` | Flux vs classic schema mapping, endpoint selection, provider name, credential guard | 8 |
| `tests/unit/test_elevenlabs_tts.py` | request shape via `httpx.MockTransport`, streaming events, cancel, empty text, error mapping | 10 |

These are **protocol-level** verifications against local servers. They say
nothing about the live services' behaviour, latency or account state.

**Not re-run on Day 18:** the Day 17 browser suite
(`apps/ui/tests/e2e/webrtc-acceptance.spec.ts`) — the local LiveKit server,
gateway and UI dev server were not running in this session, and restarting
them was refused by the session's safety policy, so the eight-scenario offline
suite could not be executed after the provider changes. That path does not use
the live adapters (it runs mock STT/TTS/LLM), but it must be re-run — together
with the live phases — once the services are up.

---

## 9. Latency methodology (and the results table to fill in)

Measured per phase with a single `time.perf_counter()` clock inside one
process; no cross-process timestamp arithmetic is used, so no "total
round-trip" claim mixes clocks:

| Measurement | Where |
|---|---|
| Deepgram connect | `start()` → connected |
| Time to first partial | first audio frame → first `PartialTranscript` |
| Final transcript | first audio frame → `EndOfTurn` |
| ElevenLabs first chunk | request sent → first non-empty chunk |
| ElevenLabs total | request sent → response body finished |
| Audio duration | decoded bytes ÷ (2 × sample rate) |

Not separated here (would need browser/network instrumentation): LiveKit hop
latency, browser playback start, and network-vs-provider split. Those stay
**UNVERIFIED** until a live run is performed.

Results (to be completed after a successful live run — deliberately empty
today, not guessed):

| Metric | Phase A | Phase B | Phase C |
|---|---|---|---|
| connect | — | — | — |
| first partial / first chunk | — | — | — |
| final / total synthesis | — | — | — |
| audio duration | — | — | — |
| observed transcript | — | n/a | — |
| browser playback | n/a | n/a | — |

---

## 10. Failure and resilience coverage

| Case | Coverage today |
|---|---|
| Invalid Deepgram credentials | offline: fatal `Error` maps to `RecognitionError(fatal)`; refused connection raises `ProviderConnectionError`. Live 401 not exercised (no call) |
| Invalid ElevenLabs credentials | offline: HTTP 401 → `ProviderSynthesisError` with auth wording, exactly one attempt |
| Missing voice id | config/constructor refusal + HTTP 404 mapping |
| Provider timeout | offline: `httpx.ReadTimeout` → `ProviderSynthesisError`; Deepgram connect timeout banner |
| Provider disconnect | offline: receiver loop ends the stream with a fatal `RecognitionError` |
| Empty transcription | offline: empty/whitespace `TurnInfo`/`Results` produce **no** event (never a fabricated transcript) |
| Empty TTS response | offline: HTTP 200 with zero bytes is a failure, not a silent success |
| Browser disconnect mid-request | not tested live; the pipeline's `stop()` closes provider streams (Day 12/13 behaviour) |
| User interruption (barge-in) during synthesis | offline: `cancel_turn` → `SynthesisCompleted(cancelled=True)`; intermediate file handling N/A (audio stays in memory) |
| Repeated failure calls | budget guard + no-retry design prevent retry storms |

Temporary audio: nothing is written to disk in either adapter — PCM stays in
memory and is handed to the pipeline. The only files created are the local
speech fixtures (§4) and Playwright's per-test artefacts.

---

## 11. Safety and conversation logic (unchanged)

* Emergency guard runs **before** LLM/RAG with the exact approved wording
  (`This may be a medical emergency. Please hang up and call 999 now.`);
  Day 18 touched no guard, no response text and no controller logic.
* `ConversationController` remains the only authority for administrative
  decisions; the LLM stays mocked, so no model-generated admin action is
  possible in this milestone.
* Explicit confirmation, required appointment fields, practice isolation and
  duplicate-submission protection are untouched.
* No patient data, no NHS production systems, no telephony, no OpenAI, no
  Gemini, no LiveKit Cloud, no voice cloning, no `.env` rewrite.

---

## 12. Remaining limitations

1. **Nothing live was verified** — the adapters are contract-correct against
   the documented APIs and locally tested, but the providers themselves were
   never contacted from this environment. Treat them as `IMPLEMENTED` +
   `TESTED (offline)`, never `VERIFIED`.
2. **Contract source** — endpoint paths, parameter names and message schemas
   come from Deepgram's and ElevenLabs' public documentation. They were not
   confirmed against this account's SDK/plan (no network access here). If a
   live run returns 400/404 on the endpoint, check the current docs first.
3. **Voice quality** — no listening evaluation exists for the ElevenLabs
   output; PCM is verified only as non-silent, plausible-length audio.
4. **`optimize_streaming_latency`** is deliberately not sent (deprecated
   upstream); latency may be slightly higher than the historical default.
5. **Browser-run spend guard** — `live_provider_budget` guards the Python
   smoke tests; the browser scenario is bounded by design (one utterance, one
   answer) and by the runbook's instruction to end the call, not by an
   in-process guard inside the LiveKit agent. Adding that guard belongs to the
   agent runtime, not to this acceptance milestone.
6. **Barge-in during a real Deepgram/ElevenLabs turn** — the data-level path is
   implemented and offline-tested; acoustic barge-in with real providers is
   unverified.

---

## 13. Cleanup backlog (recorded, not actioned — per Day 18 scope)

* `docs/runbooks/livekit-setup.md`, `livekit-self-hosted-free.md`,
  `livekit-browser-demo.md` and `docs/LIVEKIT_SELF_HOSTING.md` overlap heavily.
* `docs/runbooks/browser-voice-demo.md` and `livekit-browser-demo.md` describe
  the same demo steps twice.
* `docs/VOICE_PROVIDER_CONFIGURATION.md` duplicates
  `docs/runbooks/{deepgram-flux-setup,elevenlabs-setup,openai-gpt6-luna-setup}.md`.
* Scratch Playwright specs remain: `tests/e2e/debug.spec.ts`,
  `debug2.spec.ts`, `test_f_debug.spec.ts`, `livekit-browser.spec.ts`.
* `apps/ui/src/lib/voice/pcm-player.ts` duplicates the playback logic now in
  `livekit-transport.ts`.
* `app/integrations/livekit/__init__.py::LiveKitAgent` is not used by the
  `livekit-agents` worker path (`agent.py` uses `rtc.AudioSource` directly) —
  keep or retire once the worker path is final.
* `docs/architecture/day-1x-implementation.md` files restate their
  `-engineering-report.md` siblings.
* Day 18's engineering report existed only in the chat transcript until Day
  18B created `docs/architecture/day-18-engineering-report.md`; older days may
  be the same — verify each milestone has an on-disk record.
* `docs/LIVE_PROVIDER_ACCEPTANCE.md` §1.1–§1.3 now holds three separate gate
  records (Day 18, Day 18B attempt, execution blocker). Keep the history but
  collapse it into one timeline table at final cleanup.
* `tools/make_speech_fixture.py` is a test utility living in `tools/` next to
  the product-adjacent `room_probe`/`room_purge`; group them under a
  `tools/acceptance/` package at cleanup time.
* (Day 18C) `app/services/live_provider_budget.py` now holds two layers — the
  in-process budget used by the live harness and the persistent ledger used by
  the runtime. Keep both, but split into `live_provider_budget.py` (limits +
  in-process) and `live_provider_ledger.py` once the public imports settle.
* (Day 18C) The live harness (`tests/live/test_day18_live_providers.py`) builds
  adapters directly with the in-process budget; migrate it onto
  `LiveProviderLedger` + the wrappers so there is exactly one accounting path.
* (Day 18C) `app/speech/budget_guard.py` types its decorands as `Any`; replace
  with the existing `SpeechToTextProvider` / `TextToSpeechProvider` protocols
  (the wrappers satisfy them structurally).
* (Day 18C) `tests/integration/test_health_live_guard.py` re-implements the
  uvicorn fixture from `tests/integration/conftest.py` in order to use
  `tmp_path` env values; extract a shared `running_gateway(**env)` fixture.
* (Day 18C) `docs/architecture/day-18-engineering-report.md` is becoming a
  running log (§19 Day 18B, §20 Day 18C); split per-milestone at final cleanup.
* (Day 18C) The local LiveKit dev keypair was pasted into chat during this
  milestone; `.env` still holds it and must be rotated before any shared use.
* (Day 18E) `tests/live/` has no `__init__.py`; `tests/integration/test_live_harness_shared_ledger.py`
  imports it via implicit namespace packages. Add the package marker at cleanup.
* (Day 18E) The live harness still asserts `seconds <= 15.0` before streaming
  although `BudgetedSTT` now enforces the same cap per frame — keep one of them.
* (Day 18E) The acceptance doc now holds four separate "blocked/preflight"
  records (§1.2, §1.3, §15, §16); consolidate into one timeline at cleanup.
* (Day 18E) `.env` currently selects `azure/azure/gemini`, while the live docs
  assume `deepgram/elevenlabs/mock`; make `.env.example` state the Day 18E
  override trio explicitly so a future run cannot start a paid LLM by accident.

---

## 14. Runtime spend guard (Day 18C) — architecture and evidence

**Classification:** IMPLEMENTED · TESTED (offline, local servers) · the live
provider behaviour it protects remains UNVERIFIED (no live call has been made).

### 14.1 What is enforced

| Resource | Limit | Behaviour on breach |
|---|---:|---|
| Deepgram streaming sessions | 3 per run | session refused **before** the socket opens |
| Audio per Deepgram session | 15 s | frame refused, provider stream closed, fatal `RecognitionError` emitted |
| ElevenLabs synthesis requests | 3 per run | request refused **before** the HTTP call |
| Text per ElevenLabs request | 300 chars | request refused before the HTTP call |
| OpenAI / Gemini | 0 | not constructed anywhere in this path |

Counted conservatively: the reservation is committed **before** the provider is
contacted, is never refunded after a cancellation or provider error, and a
refused request is not counted (the ledger write is skipped on refusal).
Attempts are counted per *reservation*, so a browser reconnect — which creates a
new pipeline and therefore a new STT session — consumes another session.

### 14.2 Runtime wiring

* `app/speech/budget_guard.py` — `BudgetedSTT`, `BudgetedTTS` decorators.
* `app/speech/factory.py` — the **only** provider construction point in `app/`
  (`grep 'DeepgramFluxSTTProvider(|ElevenLabsFlashTTSProvider('` matches only
  this file). When the guard is enabled, `create_stt`/`create_tts` wrap the live
  adapters; when the ledger is unusable they raise `ProviderConfigurationError`
  instead of returning an unguarded provider (fail closed, never a mock
  fallback).
* `app/integrations/livekit/agent.py` — `build_speech_factory(settings)` is the
  function the agent entrypoint uses; it logs guard state/warnings and is the
  seam used by the integration test.
* Guard activation: `EMMA_LIVE_GUARD_ENABLED=1` **or** `EMMA_RUN_LIVE_TESTS=1`
  (a live run cannot forget the flag). Mock/Azure providers are never wrapped
  and never touch the ledger.
* Day 18D addition: while the guard is armed, constructing a live provider also
  requires **both** operator attestations (`EMMA_LIVE_CREDENTIALS_ROTATED=1`,
  `EMMA_LIVE_PROVIDER_SPEND_APPROVED=1`); otherwise construction raises and
  readiness degrades. See §15.

### 14.3 Ledger

Location: `EMMA_LIVE_LEDGER_PATH`, else `<system-temp>/emma-live-provider-ledger.json`.
Deliberately outside the repository (runtime state; a committed ledger would
hand the next run an exhausted budget). Sidecar `<file>.lock` holds the lock.

```json
{"created_at": 1794222000.1, "deepgram": {"audio_seconds": 3.7, "sessions": 1},
 "elevenlabs": {"chars": 40, "requests": 1}, "run_id": "day18-live",
 "updated_at": 1794222010.4, "version": 1}
```

* **Fixed schema** (7 keys, integer/finite-float counters) → the file cannot
  grow without bound; anything over 64 KiB is rejected as corrupt.
* **No secrets, no personal data** — counters, a run id and timestamps only
  (asserted by a test).
* **Strict validation** — version, run id syntax, non-negative integers/finite
  floats; a wrong type or negative value is corruption, not a re-interpretation.
* **Run scoping** — a different `EMMA_LIVE_RUN_ID` starts a fresh budget on the
  next reservation; the same run id resumes the existing one. Reads never
  mutate the file.

### 14.4 Locking, atomicity, interrupted writes

* **Inter-process + inter-thread**: every mutation takes `fcntl.flock(LOCK_EX)`
  on the sidecar lock file, so the gateway process and the agent worker
  serialise. Verified with 10 threads and 6 forked processes racing for fewer
  slots than there are contenders.
* **Atomic publish**: the record is written to `<file>.tmp.<pid>`, `fsync`ed and
  `os.replace`d over the data file, so a crash leaves either the old or the new
  record — never a half-written one. Leftover `.tmp` files are never read.
* **Malformed ledger → fail closed**: empty, truncated, non-JSON, wrong version
  or bad counters raise `LiveLedgerCorrupt`. There is **no silent reset**: the
  bytes are preserved for diagnosis, the provider refuses to construct, and
  readiness degrades.
* **Unwritable location → fail closed**: `LiveLedgerUnavailable`.

### 14.5 Restart behaviour

A restarted worker builds a new ledger object over the same file and continues
from the recorded usage (tested: 2 sessions → restart → 3rd allowed → 4th
refused). Only changing `EMMA_LIVE_RUN_ID` resets the budget, which is the
deliberate "new run" act.

### 14.6 Health / observability

`/health/ready` → `checks.live_provider_guard`:

```json
{"enabled": true, "applicable": true, "ledger_available": true,
 "ledger_file": "emma-live-provider-ledger.json", "run_id": "day18-live",
 "usage": {"deepgram_sessions": 1, "deepgram_audio_seconds": 3.7,
           "elevenlabs_requests": 1, "elevenlabs_chars": 40},
 "limits": {"max_deepgram_sessions": 3, "max_deepgram_audio_seconds": 15.0,
            "max_elevenlabs_requests": 3, "max_elevenlabs_chars": 300},
 "remaining": {"deepgram_sessions": 2, "deepgram_audio_seconds": 11.3,
               "elevenlabs_requests": 2, "elevenlabs_chars": 260},
 "exhausted": false, "configuration_ready": true, "error": null}
```

* `applicable` is false when no live provider is selected, so mock development
  is never degraded by guard state.
* Live providers selected + unusable/corrupt ledger → `configuration_ready`
  false → **HTTP 503 / `degraded`** (fail closed).
* The payload contains only the ledger *basename*, counts and limits — never
  keys, headers, environment values or the filesystem layout.

### 14.7 Platform assumptions

* POSIX (`fcntl.flock`, `os.replace`): macOS (development) and Linux (Docker).
  On a platform without `fcntl` the guard raises `LiveLedgerUnavailable`
  instead of running unprotected — no lock-free fallback.
* Single filesystem for the sidecar lock and the data file (same directory), so
  `flock` semantics apply to one inode.
* No network filesystem is targeted; NFS `flock` behaviour is out of scope.

### 14.8 Test evidence (Day 18C)

| File | Tests | Covers |
|---|---:|---|
| `tests/unit/test_live_provider_ledger.py` | 24 | creation, restart persistence, thread + forked-process concurrency, malformed/empty/oversized/typed records, stale `.tmp` recovery, run scoping, secret-free record |
| `tests/unit/test_live_provider_guard.py` | 18 | session cap, streaming audio cap (exact boundary), fatal error on exhaustion, no refund on provider failure, TTS request/char caps, cancellation accounting, concurrent TTS |
| `tests/unit/test_speech_provider_factory_guard.py` | 17 | wrap/no-wrap by flag, `EMMA_RUN_LIVE_TESTS` implies guard, mock untouched, fail-closed on corrupt/unwritable ledger, agent construction path, status shape, limits cannot be raised |
| `tests/integration/test_live_guard_runtime.py` | 4 | real Deepgram/ElevenLabs adapters behind the wrappers against local fake servers; refused calls never open a socket/HTTP request |
| `tests/integration/test_health_live_guard.py` | 5 | readiness payload, mock-dev unaffected, 503 on unusable/corrupt ledger, secret-free errors |

Full suite: **549 passed, 6 skipped** (481 + 68 new), ruff clean, 149 files
formatted.

### 14.9 What the guard is not

It is an **application-side** request/usage guard. It is not provider billing,
not a provider-enforced spending limit, and it cannot stop spend caused by
another process using the same provider keys outside this application.

### 14.10 Remaining limitations (Day 18C)

1. Live provider behaviour is still UNVERIFIED — no request has been made.
2. The standalone live harness (`tests/live/…`) constructs adapters directly
   with its own in-process `LiveProviderBudget`; the *runtime* path is the
   ledger-guarded one. Unifying the harness onto the ledger is a small
   follow-up (Day 19 cleanup candidate).
3. The ledger records usage, not dollars; no pricing is modelled.
4. Guard state is per-run-id: an operator who changes `EMMA_LIVE_RUN_ID` can
   deliberately reset the budget — that is the documented escape hatch, not a
   bypass of the caps themselves.

---

## 15. Day 18D — credential rotation readiness and safe preflight

**Classification:** VERIFIED (source inspection) · IMPLEMENTED + TESTED (gate
enforcement) · **USER ACTION REQUIRED** (rotation, spend approval) ·
**BLOCKED** for live calls.

**No provider request was made during Day 18D** — 0 Deepgram, 0 ElevenLabs,
0 OpenAI, 0 Gemini. No credential value was read, printed or stored, no `.env`
access, no connectivity probe, and neither attestation flag was set by the
agent.

### 15.1 Rotation is not the same as spend approval

| Step | Flag | Who | What it means |
|---|---|---|---|
| Rotation | `EMMA_LIVE_CREDENTIALS_ROTATED=1` | user | The exposed keys were revoked and the replacements are in `.env` |
| Spend | `EMMA_LIVE_PROVIDER_SPEND_APPROVED=1` | user | Balances/pricing were reviewed and the bounded run is authorised |

Both are **user attestations**. The code can only observe that the flags are
set — it cannot verify rotation or billing state, and the presence of a flag is
never evidence that either step happened. Neither flag substitutes for the
other.

### 15.2 Credential rotation checklist (user action)

**Deepgram** — env names: `DEEPGRAM_API_KEY`, `DEEPGRAM_MODEL`
1. Revoke the previously exposed key in the Deepgram dashboard.
2. Create a replacement key; grant only the permissions needed for streaming
   speech recognition (no project/admin scope if avoidable).
3. Put the new value into the ignored `.env` **manually**; never paste it into
   chat, an issue, a commit or a report.
4. Keep `DEEPGRAM_MODEL=flux-general-en` unless a deliberate model change is
   authorised (the adapter selects `/v2/listen` for `flux-*` models).

**ElevenLabs** — env names: `ELEVENLABS_API_KEY`, `ELEVENLABS_MODEL`,
`ELEVENLABS_VOICE_ID`
1. Revoke the previously exposed key.
2. Create a replacement key; restrict it to text-to-speech where the account
   supports scoped keys.
3. Confirm the intended voice locally (the voice must exist on the rotated
   account) and keep the identifier in `.env` only.
4. Never share the key or the voice configuration contents.

**LiveKit** — env names: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
1. Treat the previously shared local development keypair as compromised.
2. Generate a fresh local API key/secret.
3. Update **both** sides consistently: the `livekit-server` keypair and the
   gateway/agent `.env`.
4. Never place replacement values in tracked files or documentation.

**OpenAI** — env names: `OPENAI_API_KEY`, `OPENAI_MODEL`
1. Revoke and replace the exposed key for account security.
2. Do **not** use OpenAI in this milestone: `LLM_PROVIDER=mock` stays selected,
   and the OpenAI budget guard remains untouched. Zero OpenAI requests.

### 15.3 Required configuration names (no values)

```env
# paid providers under test
DEEPGRAM_API_KEY=…            # rotated by the user
DEEPGRAM_MODEL=flux-general-en
ELEVENLABS_API_KEY=…          # rotated by the user
ELEVENLABS_MODEL=eleven_flash_v2_5
ELEVENLABS_VOICE_ID=…         # existing voice, do not substitute silently

# transport + mock LLM
VOICE_TRANSPORT=livekit
LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET   # rotated local keypair

# bounded-run controls (Day 18C/18D)
EMMA_LIVE_GUARD_ENABLED=1
EMMA_RUN_LIVE_TESTS=1
EMMA_LIVE_RUN_ID=<run-id>
EMMA_LIVE_LEDGER_PATH=<optional override>
EMMA_LIVE_CREDENTIALS_ROTATED=1        # attestation 1 (rotation)
EMMA_LIVE_PROVIDER_SPEND_APPROVED=1    # attestation 2 (spend)
```

### 15.4 Limits, and what they do not promise

| Resource | Enforced maximum |
|---|---:|
| Deepgram sessions | 3 per run id |
| Deepgram audio | 15 seconds per session |
| ElevenLabs synthesis requests | 3 per run id |
| ElevenLabs text | 300 characters per request |
| OpenAI requests | 0 |
| Gemini requests | 0 |

These are **usage limits enforced by this application**, not a monetary ceiling
in INR (or any currency). Pricing, plan tier, add-on charges and currency
conversion are account-side facts this code cannot see. Review provider
balances and billing settings before authorising, and treat the guard as a
blast-radius limiter, never as a spend guarantee.

### 15.5 Safe readiness verification (no spend)

Run these **before** setting either attestation; none of them contacts a
provider.

```bash
# 1. Ledger + guard status through the safe readiness surface (no secrets).
curl -s http://localhost:8000/health/ready \
  | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin)['checks']['live_provider_guard'], indent=2))"

# 2. Human-readable readiness (expect "ready" only when configuration is sound).
curl -s http://localhost:8000/health/ready | python3 -m json.tool

# 3. Offline gate/budget/factory/health suites (no provider traffic).
cd apps/voice-gateway
.venv/bin/python -m pytest tests/unit/test_live_provider_ledger.py \
  tests/unit/test_live_provider_guard.py \
  tests/unit/test_speech_provider_factory_guard.py \
  tests/integration/test_health_live_guard.py -q
```

Interpretation:

* `enabled: false` → the guard is off (mock development). Nothing to approve.
* `applicable: false` → no live provider is selected; guard irrelevant.
* `attestation.ok: false` → the run is **not authorised**; readiness reports
  `configuration_ready: false` and returns 503 while a live provider is
  selected.
* `remaining.*` must equal the full budget at the start of a new run id. Any
  pre-consumed budget means the run id is not fresh — stop and report.
* `error` non-null (e.g. `LiveLedgerCorrupt`, `LiveLedgerUnavailable`) → stop;
  do not delete the ledger, do not "fix" it by resetting.

Run-id rules (`LiveProviderLedger`): `^[A-Za-z0-9._-]{1,64}$` — letters, digits,
dot, underscore, hyphen only; no paths, spaces or secrets. A **new** run id
starts a fresh budget on the next reservation; the previous ledger is left in
place (never deleted or reset by hand).

### 15.6 Conditions required before Day 18E

All of the following must be true; anything else is a stop condition:

1. Old Deepgram, ElevenLabs, LiveKit and OpenAI keys revoked; replacements in
   `.env` only (never in chat, tracked files, screenshots or reports).
2. `ELEVENLABS_VOICE_ID` still resolves to the intended voice on the rotated
   account (checked by the user, locally, without sharing values).
3. `livekit-server` and gateway/agent share the **new** LiveKit keypair.
4. A fresh `EMMA_LIVE_RUN_ID` chosen for the Day 18E run.
5. `/health/ready` shows `live_provider_guard.remaining` at full budget and
   `configuration_ready: true` **after** the attestations are set.
6. The user has reviewed provider balances/pricing and set
   `EMMA_LIVE_CREDENTIALS_ROTATED=1` **and**
   `EMMA_LIVE_PROVIDER_SPEND_APPROVED=1` themselves.
7. The preflight results have been reviewed and approved by the user.

### 15.7 What changed in Day 18D (code)

The spend-approval gate was previously **documented only** — nothing enforced
`EMMA_LIVE_PROVIDER_SPEND_APPROVED`. Day 18D implemented it:

* `Settings.EMMA_LIVE_CREDENTIALS_ROTATED` / `EMMA_LIVE_PROVIDER_SPEND_APPROVED`
  plus `live_attestations_ok` and `live_attestation_problem`.
* `SpeechProviderFactory` refuses to construct a live provider while the guard
  is armed and either attestation is missing (`ProviderConfigurationError`), and
  `configuration_problem()` reports it so readiness degrades (503).
* `live_guard_status()` now carries an `attestation` block
  (`credentials_rotated`, `spend_approved`, `ok`) on `/health/ready`.
* The opt-in live harness and the browser live spec both require the same two
  flags and skip otherwise (they previously required rotation only).
* Mock/Azure providers and guard-disabled operation are unaffected: a broken or
  missing attestation never degrades mock development.

This is fail-closed by construction: no attestation → no live provider object →
no provider request.

---

## 16. Day 18E — controlled live acceptance: BLOCKED at the Phase 0 gate

**Classification:** **BLOCKED — USER ACTION REQUIRED** for the live phases ·
IMPLEMENTED + TESTED for the shared-ledger work · **no live call attempted**.

**No provider request was made during Day 18E** — 0 Deepgram, 0 ElevenLabs,
0 OpenAI, 0 Gemini. No attestation flag was set, no run id was created, no
ledger was reset, `.env` was not read or edited.

### 16.1 Phase 0 evidence (read through the app's own status, values never printed)

`Settings()` + `live_guard_status()` reported:

| Phase 0 requirement | Observed | Result |
|---|---|---|
| `STT_PROVIDER` is `deepgram` | `azure` | ❌ |
| `TTS_PROVIDER` is `elevenlabs` | `azure` | ❌ |
| `LLM_PROVIDER` is `mock` | **`gemini`** | ❌ (would spend Gemini) |
| `VOICE_TRANSPORT` is `livekit` | `livekit` | ✅ |
| `EMMA_LIVE_CREDENTIALS_ROTATED` | `false` | ❌ |
| `EMMA_LIVE_PROVIDER_SPEND_APPROVED` | `false` | ❌ |
| `attestation.ok` | `false` | ❌ |
| `EMMA_LIVE_RUN_ID` fresh/valid | `day18-live` (placeholder default) | ❌ |
| ledger healthy, counters zero | guard disabled → ledger not in use | ❌ |
| remaining budgets at full limits | `null` | ❌ |
| `configuration_ready` | `true` only because the guard is *not applicable* | ❌ |

The last row is the dangerous one: with Azure providers selected the guard
reports `applicable: false` and readiness stays 200, which **looks** healthy
while no live Deepgram/ElevenLabs path is armed at all. The browser live spec
asserts `stt_provider == deepgram` / `tts_provider == elevenlabs` /
`llm_provider == mock` before dialling, so it fails fast rather than spending.

### 16.2 Shared-ledger gap (Phase 1) — CLOSED

Day 18D noted the standalone live harness still used an in-process
`LiveProviderBudget`. It now uses the runtime's own accounting:

| Before | After |
|---|---|
| `LiveProviderBudget(LiveProviderLimits.from_env())` | `build_runtime_ledger(settings)` — same file, same lock, same run id |
| direct `DeepgramFluxSTTProvider` / `ElevenLabsFlashTTSProvider` | wrapped in `BudgetedSTT` / `BudgetedTTS` (no bypass path) |
| manual `check_*` / `record_*` calls | removed; the wrappers reserve before the provider is contacted |
| env-var gates (`os.getenv`) | same `Settings` gate the runtime uses (`live_attestation_problem`) |

Evidence (offline, local fakes only): `tests/integration/test_live_harness_shared_ledger.py`
(9 tests) proves the harness ledger and `SpeechProviderFactory._ledger` are the
same file and lock, that a harness reservation is visible in the factory status,
that combined reservations cannot exceed 3 sessions / 3 requests from either
side, that interleaved threads cannot bypass the cap, that a corrupt ledger
fails closed for the harness, and that the module no longer contains any
in-process budget call.

### 16.3 Request arithmetic (code-derived, not estimated)

| Stage | Deepgram | ElevenLabs |
|---|---|---|
| Harness `test_deepgram_transcribes_known_phrase` | 1 session (~3.7 s) | — |
| Harness `test_elevenlabs_synthesises_administrative_response` | — | 1 request (79 chars) |
| Browser scenario (greeting + one answer) | 1 session (~3.7 s) | **2 requests** |
| **Total for a full run** | **2 / 3** | **3 / 3** |

The browser scenario cannot use only one TTS request: `VoicePipeline.start()`
speaks a greeting (`_speak` at `voice_pipeline.py:159`) and every assistant turn
speaks again. So a full run lands **exactly on the ElevenLabs cap with zero
contingency**. Two safe options:

* run harness STT + browser only → 2 Deepgram, 2 ElevenLabs (one request spare);
* run all three stages → 2 Deepgram, 3 ElevenLabs (no spare — a provider failure
  leaves the run unable to retry, which is the intended behaviour).

Never raise the caps to create headroom.

### 16.4 Day 18E launch sequence (user-executed)

Environment variables **override** `.env`, so the paid-provider selection below
does not require editing `.env`:

```bash
# 0. Proof that the new keys are in place is YOUR attestation; the code cannot
#    verify it. Do not paste key values into a shell that logs commands.

# 1. LiveKit with the NEW local keypair (never the exposed one). Keep the
#    secret out of argv: export LIVEKIT_KEYS from your own shell profile.
export LIVEKIT_KEYS="$LIVEKIT_API_KEY: $LIVEKIT_API_SECRET"
livekit-server --dev --bind 127.0.0.1 --node-ip 127.0.0.1

# 2. Gateway with real speech providers, MOCK LLM, guard armed, fresh run id.
cd apps/voice-gateway
VOICE_TRANSPORT=livekit STT_PROVIDER=deepgram TTS_PROVIDER=elevenlabs \
LLM_PROVIDER=mock EMMA_LIVE_GUARD_ENABLED=1 EMMA_RUN_LIVE_TESTS=1 \
EMMA_LIVE_RUN_ID=day18e-a \
  .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 3. Same env for the UI (the Playwright harness inherits it for the agent).
cd apps/ui
VOICE_TRANSPORT=livekit STT_PROVIDER=deepgram TTS_PROVIDER=elevenlabs \
LLM_PROVIDER=mock EMMA_LIVE_GUARD_ENABLED=1 EMMA_RUN_LIVE_TESTS=1 \
EMMA_LIVE_RUN_ID=day18e-a \
NEXT_PUBLIC_VOICE_GATEWAY_URL=http://localhost:8000 \
NEXT_PUBLIC_VOICE_TRANSPORT=livekit \
  pnpm dev
```

Before authorising spend, check readiness (expect attestation false → 503):

```bash
curl -s http://localhost:8000/health/ready \
  | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin)['checks']['live_provider_guard'], indent=2))"
```

Then set the two attestations in the **same shell** that starts the gateway:

```bash
export EMMA_LIVE_CREDENTIALS_ROTATED=1
export EMMA_LIVE_PROVIDER_SPEND_APPROVED=1
```

Restart the gateway with those exported, re-check readiness (now
`attestation.ok: true`, `configuration_ready: true`, 200, `remaining` full), and
only then run:

```bash
EMMA_RUN_LIVE_TESTS=1 EMMA_LIVE_CREDENTIALS_ROTATED=1 \
EMMA_LIVE_PROVIDER_SPEND_APPROVED=1 \
  .venv/bin/python -m pytest tests/live/test_day18_live_providers.py -q -s

cd apps/ui
EMMA_RUN_LIVE_TESTS=1 EMMA_LIVE_CREDENTIALS_ROTATED=1 \
EMMA_LIVE_PROVIDER_SPEND_APPROVED=1 \
  npx playwright test -c playwright.live.config.js --reporter=list
```

Both commands must print the **same** ledger file name and run id; the browser
spec reads `checks.live_provider_guard` before and after the call and asserts
the shared counters moved by 1 Deepgram session and 1–2 ElevenLabs requests.

### 16.5 Stop conditions

Stop and report (do not retry, do not reset the ledger) when:

* `attestation.ok` is false, or readiness is 503 for any reason;
* providers are not exactly `deepgram` / `elevenlabs` / `mock`;
* `run_id` differs between the gateway, the harness and the browser spec;
* `remaining` is not at full budget before the first call;
* a provider returns 401/402/403/404/429;
* usage reaches a cap — the guard refuses further calls and that is the end of
  the run, not a reason to raise the caps.

### 16.6 Phase 2 audit performed (offline)

| Check | Finding |
|---|---|
| Live request ceiling | ≤2 Deepgram sessions, ≤3 ElevenLabs requests (see §16.3) |
| Sequential execution | pytest has no xdist; Playwright `workers: 1`, `fullyParallel: false`, live config `retries: 0` |
| Refused before network | wrapper tests assert zero provider calls on refusal; runtime integration asserts the local fake server sees no connection |
| Mock LLM cannot fall through | `LLMProviderFactory` raises for unknown/missing-credential providers; no fallback path exists |
| Browser test uses real adapters | gateway asserts `stt=deepgram`, `tts=elevenlabs`, `llm=mock`; agent worker inherits the same env |
| Offline suites | 156 passed (ledger, wrappers, factory, attestation, health, shared-ledger, protocols, config) |
