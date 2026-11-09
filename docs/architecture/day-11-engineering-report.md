# Day 11 — Engineering Report

**Report date:** 2026-11-08 (Day 11 close-out).
**Git branch:** `main`
**Git HEAD:** `3d40b9409b635623f22ce1cd0220f1f23f647572`
**Git status:** Day 9 + Day 10 + Day 11 changes, all uncommitted (no commits or pushes).
**Baseline (Day 10):** Gateway 350 passed / 4 skipped; NestJS 49 passed / build clean;
frontend lint clean (1 pre-existing warning) / build clean; live smoke 4/4; Ruff clean.

---

## A. Executive summary

Day 11 verified that the EMMA voice gateway starts locally, passes health checks,
and runs the complete real-provider speech pipeline end-to-end over WebSocket:

- **Azure STT:** exact final transcript, delivered exactly once (~2.7–3.2 s first final).
- **Azure TTS:** PCM_L16 16 kHz mono, first chunk ~0.9–1.7 s, totals match.
- **Gemini + Qdrant:** grounded practice-information answer with citation
  ("On Monday, the practice opens at 8:00 AM [1]") after setting `PRACTICE_ID=demo-practice-001`.

A configuration gap was found and fixed: `PRACTICE_ID` was empty, which made
practice-scoped Qdrant retrieval return no chunks. It is now set to
`demo-practice-001` (non-secret practice identifier matching the ingested
knowledge base).

**Critical limitation:** this environment has **no browser binary** (Chrome,
Chromium, Firefox) and **no browser automation tooling** (Playwright, Puppeteer,
WebDriver). Real microphone capture, live browser session, browser audio playback,
acoustic barge-in, and synthetic-browser-microphone testing could therefore NOT
be performed. They are reported as **UNVERIFIED**, not as passes. A manual
browser-voice demo runbook (`docs/runbooks/browser-voice-demo.md`) is provided
for user-executed validation.

EMMA's AI core (STT → guard → LLM → controller → RAG → TTS) is verified with
real providers. The complete browser voice loop is not yet demonstrated.

---

## B. Initial baseline

| Item | Value |
|---|---|
| Git branch | `main` |
| Initial HEAD | `3d40b9409b635623f22ce1cd0220f1f23f647572` |
| Uncommitted changes | Yes — Day 9 + Day 10 + Day 11 (see section O) |
| Day 10 gateway tests | 350 passed, 4 skipped (opt-in live) |
| Day 10 NestJS tests | 49 passed; build clean |
| Day 10 frontend | lint clean (1 pre-existing warning); build clean |
| Day 10 live smoke | 4/4 passed |
| Ruff | clean |
| Ruff format | clean |
| Environment readiness | Azure/Gemini/Qdrant credentials SET in `.env` (never printed); `PRACTICE_ID` was empty → set to `demo-practice-001` |

---

## C. Local deployment

### C.1 Processes, ports, URLs

| Component | Working directory | Command | Port | URL |
|---|---|---|---|---|
| Voice gateway | `apps/voice-gateway` | `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000` | 8000 | `http://127.0.0.1:8000` |
| WebSocket endpoint | `apps/voice-gateway` | (same gateway) | 8000 | `ws://127.0.0.1:8000/ws/exotel` |
| Health live | `apps/voice-gateway` | (same gateway) | 8000 | `http://127.0.0.1:8000/health/live` |
| Health ready | `apps/voice-gateway` | (same gateway) | 8000 | `http://127.0.0.1:8000/health/ready` |
| Frontend (Next.js) | `apps/ui` | `pnpm dev` | 3000 | `http://localhost:3000` (route `/voice`) |
| Practice API (NestJS) | `apps/practice-api` | `pnpm dev` | (from `.env` PORT) | local API |

Startup order: gateway first (or frontend/gateway independently — the frontend
connects to `NEXT_PUBLIC_VOICE_GATEWAY_URL`); practice API optional for the
speech loop (persistence disabled when `PRACTICE_API_BASE_URL` is unset).

### C.2 Health checks (verified)

```
curl http://127.0.0.1:8000/health/live
→ {"status":"alive","uptime_seconds":...}

curl http://127.0.0.1:8000/health/ready
→ {"status":"ready","checks":{
    "lifespan_complete":true,
    "provider_mode":"simulation",
    "provider_credentials_configured":false,   # Exotel live creds unset (expected in simulation)
    "ws_auth_required":false,
    "active_sessions":0,
    "max_concurrent_sessions":200,
    "voice_pipeline":"speech",
    "stt_provider":"azure",
    "tts_provider":"azure",
    "speech_provider_configured":true,
    ...
  }}
```

The browser voice interface must not report "connected" before the WebSocket is
established — the transport's `onopen` fires only after the real socket opens,
and connection failures emit an `error` event rather than a false connected
state.

### C.3 Startup convenience

No existing single-command script starts all components. Per instructions, no
complex orchestration system was built. The user starts components individually
using the commands in section C.1 (see `docs/runbooks/browser-voice-demo.md`
for the consolidated procedure).

---

## D. Browser audio implementation (code audit)

### D.1 Capture — `apps/ui/src/lib/voice/audio-pipeline.ts`

| Property | Value | Verified in code |
|---|---|---|
| Microphone permission | `navigator.mediaDevices.getUserMedia({audio:{echoCancellation,noiseSuppression,autoGainControl}})` | line 98 |
| Input sample rate | Browser's `ctx.sampleRate` (varies by device) | line 166 |
| Resampling | `downsampleInt16(block, ctx.sampleRate, WIRE_FRAME_SAMPLES)` → linear interpolation | lines 77–93, 166 |
| Channels | Mono — uses `input[0]` (first channel) | line 46 |
| Wire rate | `WIRE_RATE = 16_000` Hz | line 13 |
| Frame size | `WIRE_FRAME_SAMPLES = 320` samples @ 16 kHz = **640 bytes PCM16 mono per 20 ms** | lines 14–15 |
| Frame cadence | 20 ms (`FRAME_MS = 20`) | line 14 |
| Encoding | PCM16 little-endian `Int16Array` | lines 86–91 |
| Muting | In AudioWorklet (no silence frames on wire) | lines 48–51 |
| Cleanup | `teardown()` closes AudioContext, stops tracks, revokes blob URL | lines 128–147 |

Note: browsers do not guarantee 16 kHz input; the pipeline resamples the
device's `ctx.sampleRate` down to the 16 kHz wire rate. This is correctly
implemented.

### D.2 WebSocket transport — `apps/ui/src/lib/voice/gateway-transport.ts`

- Connects to `NEXT_PUBLIC_VOICE_GATEWAY_URL` + `?sample-rate=16000&control-events=1`
- Sends `connected` → `start{media_format}` → 20 ms PCM16 `media` frames
- Decodes base64 PCM16 `media` envelopes and plays via `pcm-player.ts`
- Renders `transcript` (partial/final), `assistant`, `state` control events
- Browser barge-in: mic level ≥ threshold for 2 consecutive 60 ms ticks while
  audio is playing → `player.stop()` + send `{event:"barge-in"}`
- Emits `output-level` for the orb waveform during playback
- Clean close with `{event:"stop"}` and WebSocket close 1000

### D.3 Playback — `apps/ui/src/lib/voice/pcm-player.ts` (new, Day 10)

- Base64 decode → `Int16Array` → `AudioBuffer(1, samples, sampleRate)`
- AudioContext scheduled back-to-back (`nextStartTime` timeline, +20 ms jitter lead)
- `playBase64()` queues a chunk; `stop()` cancels all scheduled + audible audio
  immediately (barge-in / teardown)
- `level()` returns smoothed output RMS (analyser)
- `close()` closes the AudioContext

### D.4 Frame-size validation (server side)

The gateway validates inbound PCM16 chunks via the Exotel adapter:
- `codec.decode_media_payload` enforces `max_chunk_bytes` (default 100,000)
- Empty/malformed base64 tolerated (protocol tests)
- Oversized messages → close 1009
- Non-text frames tolerated
- 640-byte (20 ms @ 16 kHz PCM16 mono) frames are the expected browser size

---

## E. Live provider verification

All calls below were made against real providers with credentials from `.env`
(never printed). None are mock tests.

| Provider | Call | Result |
|---|---|---|
| Azure STT | push-stream continuous, PCM16 16 kHz mono, `en-GB` | Exact final transcript, exactly once; partials + finals; ~2.7–3.2 s first final |
| Azure TTS | PCM_L16 16 kHz mono, `en-GB-SoniaNeural` | First chunk ~0.9–1.7 s; synthesis totals match chunks/bytes; `cancelled=False` |
| Gemini LLM | `gemini-3.8-flash` interpretation | Appointment extraction + confirmation read-back (~2.85 s isolated) |
| Gemini embeddings | `gemini-embedding-2`, 768-d | Query embedding for retrieval |
| Qdrant | local persistent, `./data/qdrant`, `demo-practice-001` | Practice-scoped chunks returned (18 vectors) |
| NestJS practice API | SQLite dev/test | 49 tests passed; build clean; idempotency/duplicate/rejection covered |

**Grounded answer (real):**
- Utterance: "What time does the practice open on Monday?"
- Answer: "On Monday, the practice opens at 8:00 AM [1].\n\nSources:\n[1] Riverbend Medical Practice — Opening Hours..."
- Real Gemini embedding + Qdrant retrieval with citation; spoken via Azure TTS.

No mock provider was substituted for any of the above live calls.

---

## F. Browser testing

| Item | Result |
|---|---|
| Browser binary available | NONE (Chrome, Chromium, Firefox all absent) |
| Browser automation (Playwright/Puppeteer/WebDriver) | NONE installed |
| Frontend production build | PASS (Next.js 16.4.5, Turbopack, TypeScript strict) |
| Frontend lint | PASS (1 pre-existing unused-import warning in intake-form.tsx) |
| Live browser session | UNVERIFIED — no browser |
| Microphone permissions | UNVERIFIED — no browser |
| Synthetic browser microphone input | UNVERIFIED — no browser |
| Physical microphone input | UNVERIFIED — no browser/mic |
| Real manual browser session | UNVERIFIED — no browser |
| Browser audio playback | UNVERIFIED — no AudioContext runtime |
| Audio playback result | UNVERIFIED |

**Honest status:** Browser build passing does **not** prove microphone operation,
and WebSocket integration tests passing do **not** prove browser UI execution.
The browser voice loop remains unverified until executed in a browser
environment.

---

## G. Barge-in

| Layer | Implementation | Verified |
|---|---|---|
| Browser playback cancellation | `pcm-player.stop()` cancels all scheduled/audible audio | Code only (builds) |
| Browser `barge-in` control event | Transport sends `{event:"barge-in"}` when mic level ≥ threshold × 2 ticks | Code only |
| Protocol-level barge-in | `ExotelUnknownEvent` with `event=="barge-in"` → `pipeline.cancel_tts()` | Code only |
| Server-side energy-gated cancel | PCM16 RMS threshold (~250) + 40 ms energy gate; silence does not interrupt | Unit-tested (`_pcm16_rms`) |
| Real acoustic interruption | NOT tested | UNVERIFIED — no microphone |

Browser playback cancellation and protocol-level barge-in are implemented but
**acoustic barge-in is unverified**. Per instructions, protocol-level + playback
cancellation is not claimed as acoustic barge-in.

---

## H. Multi-turn acceptance (scenarios A–J)

| Scenario | Verified how | Result |
|---|---|---|
| A — Appointment request | Pipeline-level live E2E (Day 10) + corpus | Exact transcript + confirmation read-back; no submission before confirmation |
| B — Correction | Day 9 corpus (mock) | Confirmation invalidated; re-read-back; new confirmation required |
| C — Multiple requests | Day 9 corpus (mock) | Independent request lifecycles; no cross-submission |
| D — Cancellation | Day 9 corpus (mock) | Targeted request cancelled; cannot be re-submitted |
| E — Practice information | Live E2E (Day 11, this report) | **VERIFIED** — grounded Qdrant answer with citation via Azure TTS |
| F — Contextual follow-up | Day 9 corpus (mock) | Previous topic resolved; practice-scoped retrieval |
| G — Emergency interruption | Corpus + unit (mock) + live pipeline guard ordering | Guard before LLM/Qdrant/backend; fixed 999 script; zero admin calls |
| H — Backend failure | NestJS tests (real SQLite) + corpus | Request marked FAILED; honest response; other requests unaffected |
| I — Rejection / ambiguous | Day 9 corpus (mock) | Clarification / no submission |
| J — Session isolation | Not browser-tested | UNVERIFIED at browser level; pipeline turn lock + per-session state at code level |

**Browser-based multi-turn acceptance:** UNVERIFIED (no browser). Pipeline-level
behaviour for scenarios A, E, G verified with real providers; B, C, D, F, H, I
covered by the 34-scenario Day 9 corpus with mock providers; J verified only at
the code level (per-session turn lock, duplicate-turn rejection).

---

## I. Emergency and security

### I.1 Emergency guard ordering (verified)

`emergency_guard(utterance.text)` runs in `_process_turn()` **before**:
- Gemini LLM interpretation
- embeddings
- Qdrant retrieval
- NestJS practice-API submission

On match: `controller.handle_emergency()` invalidates pending requests/
confirmations; the fixed script is spoken verbatim
("This may be a medical emergency. Please hang up and call 999 now."); zero
LLM/embedding/Qdrant/backend calls occur.

Covered by corpus cases (emergency on first turn / during slot filling / during
confirmation / combined with admin request) and live pipeline guard ordering.

### I.2 Backend submission safety (verified)

- No submission without explicit lexical `confirm`
- Request marked `SUBMITTING` only immediately before the backend call
- `SUBMITTED` only after backend confirmation
- Spoken text describes a request, never a confirmed appointment
- NestJS idempotency + duplicate-submission prevention + rejection/timeout tests (49 passed)

### I.3 Security audit

| Concern | Status |
|---|---|
| API keys exposed to browser | NO — Next.js only has `NEXT_PUBLIC_VOICE_GATEWAY_URL`; secrets never in `.env.local`/bundled |
| `.env` bundled in frontend | NO — `.env` is git-ignored; `NEXT_PUBLIC_*` prefix only for URL |
| Raw transcripts logged | NO — metrics record counts/latencies only |
| Caller-supplied practice ID trusted | NO — `PRACTICE_ID` is server-side (`settings.PRACTICE_ID`); tenant fields stripped |
| Browser control events → backend actions | NO — control events are UI-only (transcript/assistant/state/barge-in); backend submission requires server-side confirmation |
| Confirmation checks preserved | YES — server-side |
| Emergency guard ordering | YES — verified |
| No clinical triage claims | YES — EMMA is not a medical device |

### I.4 Credential audit

No API keys, secrets, or credential values appear in any Day 11 documentation,
terminal output, or committed file. `.env` remains git-ignored at root, UI, and
practice-api levels.

---

## J. Performance

Measurements from real providers over local WebSocket (single run; latency
varies run to run — not production benchmarks).

| Metric | Value | Boundary |
|---|---|---|
| Azure STT first partial | ~1.9–2.4 s | stream start → first `PartialTranscript` |
| Azure STT first final | ~2.7–3.2 s | stream start → first `FinalTranscript` |
| Azure STT partials/finals | 6–14 partials; 1–2 finals | event counter |
| Gemini interpretation | ~2.85 s | isolated `IntentInterpreter.interpret()` |
| Azure TTS first chunk | ~0.9–1.7 s | `SynthesisChunk` |
| Azure TTS total | ~1.2–4.5 s | `SynthesisCompleted` |
| Grounded answer E2E | ~4.6 s | STT final → Gemini → Qdrant → TTS answer |
| E2E audio out (practice-info turn) | 58 frames / 183,168 bytes | session metrics |
| Retrieval (Qdrant) | p50 662–806 ms, p95 852–975 ms | Day 9 live smoke (prior runs) |
| Browser microphone startup | NOT measured | UNVERIFIED |
| Browser playback first audio | NOT measured | UNVERIFIED |
| End-to-end speech-end→first-audio | NOT measured | UNVERIFIED (requires browser) |
| Barge-in cancellation time | NOT measured | UNVERIFIED (acoustic) |

Sample size: single local session per metric. Not production benchmarks.

---

## K. LiveKit evaluation (ADR-011, no migration)

See `docs/architecture/adr-011-voice-runtime-evaluation.md`.

**Recommendation:** Do NOT migrate EMMA to LiveKit during Day 11. Keep the
existing Gemini + Qdrant + NestJS + emergency-first controller architecture.
The LiveKit/WebRTC alternative offers better browser audio reliability and
turn detection but introduces significant migration cost (WebRTC client,
LiveKit Agents, new STT/TTS provider integration, Docker/MySQL operational
complexity) with no compensating benefit for this prototype. Reassess if
telephone (PSTN) integration becomes a hard requirement.

---

## L. Test results

Separate counts — never merged into one generic number.

| Suite | Result |
|---|---|
| Python gateway unit tests | Included in 350 passed |
| Python gateway integration tests | Included in 350 passed |
| Day 5 evaluation | Part of 350 passed |
| Day 6 evaluation | Part of 350 passed |
| Day 9 evaluation corpus | Part of 350 passed (34 scenarios) |
| Day 10 tests | Part of 350 passed (incl. new `test_azure_stt_lifecycle.py` 6, `test_day10_control_events.py` 5) |
| Day 11 tests | No new automated tests (no browser); code audited |
| Live provider tests | 4/4 live smoke passed; focused practice-info E2E passed (this report) |
| Browser tests | NONE — no browser automation |
| Microphone tests | NONE — no microphone |
| Barge-in tests | Unit-level (`_pcm16_rms`, control events) only; acoustic NOT tested |
| Frontend lint | Clean (1 pre-existing warning) |
| Frontend build / type-check | Clean |
| NestJS build | Clean |
| NestJS tests | 49 passed, 8 suites |
| Ruff check | Clean |
| Ruff format | Clean |

---

## M. Outstanding limitations

| Limitation | Detail |
|---|---|
| Real microphone / acoustic barge-in | UNVERIFIED — no browser or microphone in this environment |
| Live browser session | UNVERIFIED — no browser binary |
| Browser audio playback | UNVERIFIED — no AudioContext runtime |
| Synthetic browser microphone | UNVERIFIED — no browser |
| Full multi-turn browser session | UNVERIFIED — no browser |
| End-to-end speech-end→first-audio latency | NOT instrumented (requires browser) |
| Session isolation at browser level | UNVERIFIED (code-level verified) |
| Exotel / PSTN | OUT OF SCOPE |
| Docker / MySQL | UNAVAILABLE — Qdrant local mode; NestJS uses SQLite |
| Production performance claims | NOT made — single local sample |
| Emergency guard false-negative rate | NOT systematically measured |

---

## N. Demonstration readiness

| Capability | Status |
|---|---|
| AI core (STT → guard → LLM → controller → RAG → TTS) | VERIFIED (real providers) |
| Browser UI (builds, type-checks) | PARTIALLY VERIFIED (code only) |
| Real STT | VERIFIED (live) |
| Real TTS | VERIFIED (live) |
| Complete browser voice loop | UNVERIFIED (no browser) |
| Multi-turn conversation | PARTIALLY VERIFIED (pipeline-level A/E/G; corpus B/C/D/F/H/I) |
| Acoustic interruption | UNVERIFIED (no mic) |
| Backend submission | VERIFIED (NestJS tests + corpus) |
| Emergency handling | VERIFIED (guard ordering + corpus) |
| Telephone integration | OUT OF SCOPE |

**EMMA is NOT yet ready for a live browser demonstration** until a browser
environment is available to execute the manual runbook. It is **NOT ready for a
real telephone demonstration** (Exotel/PSTN is out of scope).

---

## O. Git status

```
Branch: main
HEAD:   3d40b9409b635623f22ce1cd0220f1f23f647572 (uncommitted)

Modified:
  M README.md
  M apps/ui/src/components/voice/voice-screen.tsx
  M apps/ui/src/lib/voice/gateway-transport.ts
  M apps/ui/src/lib/voice/types.ts
  M .env                                                  <- PRACTICE_ID=demo-practice-001 (Day 11 fix)
  M apps/voice-gateway/app/api/routes/websocket.py
  M apps/voice-gateway/app/domain/conversation.py
  M apps/voice-gateway/app/integrations/embeddings/gemini_provider.py
  M apps/voice-gateway/app/integrations/llm/base.py
  M apps/voice-gateway/app/integrations/llm/gemini_provider.py
  M apps/voice-gateway/app/integrations/llm/mock_provider.py
  M apps/voice-gateway/app/services/conversation/conversation_controller.py
  M apps/voice-gateway/app/services/conversation/intent_interpreter.py
  M apps/voice-gateway/app/services/voice_pipeline.py
  M apps/voice-gateway/app/speech/azure_stt.py
  M apps/voice-gateway/pyproject.toml
  M apps/voice-gateway/tests/integration/test_speech_pipeline.py

New:
  ?? apps/ui/src/lib/voice/pcm-player.ts
  ?? apps/voice-gateway/tests/unit/test_azure_stt_lifecycle.py
  ?? apps/voice-gateway/tests/unit/test_day10_control_events.py
  ?? apps/voice-gateway/tests/unit/test_day9_dialogue.py
  ?? docs/architecture/day-10-engineering-report.md
  ?? docs/architecture/day-10-implementation.md
  ?? docs/architecture/day-10-engineering-report.md
  ?? docs/architecture/day-11-implementation.md
  ?? docs/architecture/day-11-engineering-report.md
  ?? docs/architecture/final-system-architecture.md
  ?? docs/architecture/adr-011-voice-runtime-evaluation.md
  ?? docs/runbooks/azure-speech-setup.md
  ?? docs/runbooks/browser-voice-demo.md
  ?? docs/runbooks/final-local-acceptance-testing.md
  ?? docs/runbooks/multiturn-conversation-testing.md
  ?? apps/voice-gateway/tests/evaluation/day9/
  ?? apps/voice-gateway/tests/live/

No commits or pushes were made. `.env` remains git-ignored (never committed).
```
