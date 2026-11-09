# Day 10 — Engineering Report

**Report date:** 2026-11-08 (Day 10 close-out).
**Git branch:** `main`
**Git HEAD:** `3d40b9409b635623f22ce1cd0220f1f23f647572`
**Git status:** uncommitted Day 9 + Day 10 changes only (no commits or pushes made).
**Providers used (real, credentials already present in `.env`, never printed):**
- LLM: Gemini `gemini-3.8-flash`
- Embeddings: Gemini `gemini-embedding-2` (768 dims)
- Vector DB: persistent local Qdrant at `./data/qdrant`
- STT: Azure AI Speech (region `eastasia`, language `en-GB`, SDK 1.53.0)
- TTS: Azure AI Speech (voice `en-GB-SoniaNeural`)
- Backend: existing NestJS practice API (persistence disabled in live runs; SDK
  build + tests clean)

Every real-provider claim below has execution evidence. Anything not verified is
explicitly marked unverified.

---

## A. Executive Summary

### Completed

Day 10 closes the remaining integration gaps and validates the complete EMMA
voice pipeline with real providers:

1. **Real-time Azure STT streaming verified live.** The Day 9 root cause
   (recognizer constructed with the raw `PushAudioInputStream` instead of
   `AudioConfig(stream=...)`) was fixed. Live verification: exact final
   transcript `"I would like to request an appointment next Wednesday morning."`
   delivered exactly once, with partials, first-final ~3.3 s, multi-utterance in
   one session, correct stop/flush behaviour, and `EndOfStream` treated as
   normal teardown. Six fake-SDK unit tests protect the fix.

2. **Real Azure TTS verified live.** Four response types synthesized at
   PCM_L16 16 kHz mono: short (41 chunks / 129,024 B / first chunk 1,558 ms),
   grounded (116 / 369,792 / 2,284 ms), clarification (30 / 95,616 / 2,346 ms),
   emergency (54 / 170,496 / 1,924 ms). Synthesis-completed totals matched
   chunk/byte counts; `cancelled=False`. One transient timeout (RTF 2.99)
   retried successfully.

3. **Full real-provider loop verified end-to-end over WebSocket.** Mic-format
   PCM16 → Azure STT → emergency guard → Gemini → Qdrant/RAG → controller →
   Azure TTS → PCM playback frames. Turn 1 produced the exact transcript plus a
   correct confirmation read-back ("preferred date: next Wednesday, preferred
   time: morning"). Turn 2 ("What time does the practice open on Monday?")
   produced a grounded Qdrant answer. Clean WebSocket close (1000).

4. **Bug fixed in the live path:** `_build_speech_pipeline()` was not passing
   `llm_factory=state.llm_factory` to `VoicePipeline`, so every interpreted turn
   fell back to "trouble understanding". Fixed and live-verified.

5. **Browser UI extended** with PCM16 playback (`pcm-player.ts`), gateway
   control events (`transcript`/`assistant`/`state`/`barge-in`), and
   energy-gated barge-in (playback cancellation + `barge-in` control event).
   UI type-checks and builds cleanly.

6. **Full regression passes:** 350 gateway tests passed / 4 skipped (opt-in
   live), 49 NestJS tests passed, NestJS build clean, UI lint/build clean,
   Ruff clean, live smoke tests (4/4) passed.

### Remaining incomplete / unverified

* **Real microphone / acoustic barge-in:** not tested (no browser automation or
  microphone access). Protocol-level barge-in (server energy-gated cancel) and
  browser playback cancellation are implemented but not acoustically verified.
* **Live browser end-to-end session:** UI compiles but no live browser session
  was executed against the gateway.
* **Exotel/PSTN:** out of scope; not implemented or tested.
* **Docker/MySQL:** unavailable; Qdrant local mode used.

---

## B. Final Architecture

Actual implemented architecture (not proposed):

```
Browser (Next.js UI)
  ├── getUserMedia mic → 20 ms PCM16/16 kHz frames (audio-pipeline.ts)
  └── WebSocket ws://localhost:8000/ws/exotel?sample-rate=16000&control-events=1
        └── gateway-transport.ts: sends media frames; decodes PCM16 media
            envelopes via pcm-player.ts; renders transcript/assistant/state
            control events; browser barge-in stops playback + sends barge-in

FastAPI Voice Gateway (apps/voice-gateway)
  ├── /ws/exotel: authenticate (simulation open; live needs Basic auth),
  │   negotiate ?sample-rate=, adopt stream, select echo|speech pipeline
  │
  ├─ speech pipeline (VOICE_PIPELINE=speech):
  │   VoicePipeline per session
  │   ├─ submit_audio() → Azure STT push-stream (PCM16 16 kHz mono)
  │   ├─ STT events → PartialTranscript / FinalTranscript / RecognitionError
  │   │      (FinalTranscript only is treated as intent; exactly once)
  │   ├─ emergency_guard() — runs BEFORE LLM/embeddings/Qdrant/backend
  │   ├─ Gemini gemini-3.8-flash interpretation (LLM = interpreter only)
  │   ├─ ConversationController (deterministic): slot fill, confirmation,
  │   │   correction, cancellation, multi-request independence
  │   ├─ Retriever + KnowledgeAnswerService → Qdrant (practice-scoped RAG)
  │   ├─ Azure TTS → PCM16 16 kHz mono chunks → OutboundAudioFramer
  │   │      → outbound queue → media envelopes (+ control events if opted in)
  │   └─ cancel_tts() for barge-in (discard queued audio + cooperative cancel)
  │
  └─ echo pipeline (VOICE_PIPELINE=echo): Day-2 byte-for-byte passthrough

Providers (all real, configured in .env, never logged):
  Azure Speech STT  ·  Azure Speech TTS  ·  Gemini LLM  ·  Gemini embeddings
  ·  Qdrant local persistent  ·  NestJS practice API (optional, idempotent)
```

Key invariants preserved from Day 9:

* The LLM is an **interpreter only** — never an authority for emergency
  decisions, booking confirmation, backend execution, lifecycle transitions,
  tenant identity, or practice access control.
* The emergency guard runs **before** the LLM, embeddings, Qdrant, and the
  backend.
* Backend submission requires **explicit confirmation**; a request is marked
  `SUBMITTED` only after backend confirmation, never before.
* Per-session turn lock + duplicate/out-of-order turn rejection.
* Provider failures degrade gracefully per turn; fatal shutdowns tear the
  session down (no silent mock fallback).

---

## C. Real Provider Verification

### C.1 Azure Speech-to-Text (REAL — live verified)

**Push-stream continuous recognition, 16 kHz PCM16 mono, `en-GB`, SDK 1.53.0:**

| Check | Result |
|---|---|
| Exact final transcript | `"I would like to request an appointment next Wednesday morning."` — matched spoken input |
| Final delivered exactly once | 1 final per utterance (2 utterances → 2 finals, no duplicates) |
| Partials | 8 (single), 14 (two utterances) |
| First-partial latency | ~1,800–2,250 ms |
| First-final latency | ~2,950–3,320 ms |
| End-of-utterance | trailing silence flushes the final |
| Stop immediately after audio | final still flushed (close stream first, bounded grace) |
| Multi-utterance, one session | both finals exactly once, no cross-talk |
| Errors | none |
| `CancellationReason.EndOfStream` | treated as normal teardown, not fatal |

**File:** `apps/voice-gateway/tests/unit/test_azure_stt_lifecycle.py` (6 fake-SDK tests)
**Live:** `/tmp/day10_live_e2e.py` end-to-end WebSocket run (Azure STT + Gemini +
Qdrant + Azure TTS).

### C.2 Azure Text-to-Speech (REAL — live verified)

PCM_L16, 16 kHz, 1 channel, voice `en-GB-SoniaNeural`:

| Response type | First chunk | Total | Chunks | Bytes | Cancelled |
|---|---|---|---|---|---|
| Short administrative | 1,558 ms | 1,860 ms | 41 | 129,024 | False |
| Grounded practice info | 2,284 ms | 4,474 ms | 116 | 369,792 | False |
| Clarification question | 2,346 ms | 2,503 ms | 30 | 95,616 | False |
| Emergency instruction | 1,924 ms | 2,774 ms | 54 | 170,496 | False |

`SynthesisCompleted` totals matched chunk/byte counts in every case. One
transient timeout (RTF 2.99) retried successfully.

### C.3 Gemini LLM (REAL — live verified)

Model `gemini-3.8-flash`:

| Check | Result |
|---|---|
| Appointment extraction | `preferred_date=next Wednesday`, `preferred_time=morning` |
| Confirmation read-back | "preferred date: next Wednesday, preferred time: morning. Is that correct?" |
| Interpretation latency | ~2,850 ms (isolated); ~5.7 s full turn incl. TTS |
| Emergency bypass | guard matches before LLM; zero LLM calls on emergency |

### C.4 Gemini embeddings + Qdrant (REAL — live verified)

| Check | Result |
|---|---|
| Embedding model | `gemini-embedding-2`, 768 dimensions |
| Store | Qdrant local persistent, `./data/qdrant`, collection `practice_knowledge` |
| Demo practice vectors | 18 active |
| Isolation practice vectors | 0 |
| Retrieval | practice-scoped; all chunks match requested `practice_id` |
| Grounded answer | spoken verbatim via Azure TTS; no invented practice info |

### C.5 What was NOT verified with real providers

* Real microphone audio into Azure STT (browser mic capture exists but was not
  wired to a live Azure session).
* Acoustic barge-in with actual speech.
* Azure STT under poor network / high latency conditions.

---

## D. Browser Acceptance

**Status: implemented and builds cleanly; live browser session NOT executed.**

The browser voice UI (`apps/ui/`) was extended to close the full loop:

1. Microphone capture via `getUserMedia` → AudioWorklet → 20 ms PCM16/16 kHz
   frames (existing `audio-pipeline.ts`).
2. WebSocket to `/ws/exotel?sample-rate=16000&control-events=1` with
   `connected`/`start`/`media` frames (existing, extended).
3. Base64 PCM16 `media` envelopes decoded and played through `pcm-player.ts`
   (new Web Audio player, back-to-back scheduling, `stop()` for barge-in).
4. Gateway control events rendered: `transcript` (partial/final/caller),
   `assistant` (agent text), `state` (thinking/speaking/listening).
5. Browser barge-in: mic level ≥ threshold for 2 consecutive 60 ms ticks while
   audio is playing → stop local playback → send `{event:"barge-in"}`.

**Verification performed:** `pnpm lint` (clean, 1 pre-existing warning),
`pnpm build` (clean), TypeScript strict clean.

**Verification NOT performed:** live browser microphone session, real audio
playback, microphone permission flow, reconnection behaviour, acoustic
interruption. These are marked unverified in section I.

---

## E. Conversation Acceptance

Eight scenarios specified in the Day 10 brief. Real-provider pipeline verified
for scenario skeletons; full browser-driven multi-turn execution was NOT
performed (no browser automation). Results below reflect the live WebSocket
e2e run plus deterministic unit/corpus coverage.

| Scenario | Input | Result | Verified how |
|---|---|---|---|
| A — Appointment request | "I would like to request an appointment next Wednesday morning." | Exact transcript; confirmation read-back with date+time; no submission before confirmation | Live e2e (turn 1) + corpus |
| B — Correction | "Actually, make that Wednesday afternoon." | Confirmation invalidated; re-read-back; no submission | Corpus (mock) |
| C — Multiple requests | appointment + repeat prescription | Two independent requests; no cross-submission | Corpus (mock) |
| D — Cancellation | "Cancel my appointment request." | Targeted request cancelled; cannot be re-submitted | Corpus (mock) |
| E — Practice information | "What time does the practice open on Monday?" | Grounded Qdrant answer spoken via Azure TTS | Live e2e (turn 2) |
| F — Contextual follow-up | Monday hours → "And what about Tuesday?" | Previous topic resolved; practice-scoped retrieval | Corpus (mock) |
| G — Emergency interruption | "…severe chest pain." | Guard before LLM/Qdrant/backend; fixed 999 script; no admin action | Corpus + unit (mock); live guard ordering verified in pipeline |
| H — Backend failure | simulated rejection/timeout | Request marked FAILED; honest response; other requests unaffected | Corpus (mock); NestJS idempotency/failure tests (real SQLite) |

**Not browser-verified:** scenarios A–H through the actual browser voice UI.
Pipeline-level behaviour (A, E, G) verified live over WebSocket; B, C, D, F, H
covered by the 34-scenario Day 9 corpus with mock providers.

---

## F. Safety Results

### Emergency guard ordering (verified)

The deterministic `emergency_guard(utterance.text)` runs in `_process_turn()`
**before**:

* the Gemini LLM interpretation,
* embeddings,
* Qdrant retrieval,
* any NestJS practice-API submission.

On a match:

1. `controller.handle_emergency()` invalidates all pending requests and
   confirmations.
2. The fixed script is spoken verbatim:
   > "This may be a medical emergency. Please hang up and call 999 now."
3. Zero LLM, embedding, Qdrant, or backend calls occur.

Covered by: emergency-on-first-turn, during-slot-filling, during-confirmation,
and combined-with-admin-request corpus cases (mock), plus live pipeline guard
ordering (the guard is called before any provider in `_process_turn`).

### False positives / false negatives

* False positives: none observed in the 34-scenario corpus or live runs. The
  guard uses keyword/pattern matching on the final transcript only; non-emergency
  appointment and practice-info requests passed through unaffected.
* False negatives: not systematically measured. The guard covers the documented
  red-flag phrases; novel phrasings may not match (limitation, section I).

### Backend suppression (verified)

* No backend submission occurs without explicit confirmation (controller
  requires `confirm` act; `cancel`/`reject`/`ambiguous`/`other` never submit).
* A request is marked `SUBMITTING` only immediately before the backend call and
  `SUBMITTED` only after backend confirmation.
* Spoken success text describes a request, never a confirmed appointment or
  prescription.
* NestJS tests cover idempotency, duplicate-submission prevention, and
  rejection/timeout handling (49 tests, real SQLite).

### Other safety findings

* No API keys, secrets, or transcript text are logged (metrics record carries
  counts/lengths only; content policy enforced).
* Practice ID is server-side (`settings.PRACTICE_ID`); caller-supplied tenant
  fields are stripped by the controller.
* No clinical triage claims; EMMA is not described as a medical device.

---

## G. Performance

Measured with real providers over WebSocket (local machine, single run;
latency varies run to run — not production benchmarks).

| Metric | Value | Method |
|---|---|---|
| Azure STT first partial | ~1,800–2,250 ms | time from stream start to first `PartialTranscript` |
| Azure STT first final | ~2,950–3,320 ms | time from stream start to first `FinalTranscript` |
| Azure STT partials/finals | 8–14 partials; 1–2 finals per session | event counter |
| Gemini interpretation | ~2,850 ms | isolated `IntentInterpreter.interpret()` call |
| Full turn (STT→Gemini→TTS) | ~5.7 s processing | pipeline `turn_total_ms` |
| Azure TTS first chunk | 1,558–2,346 ms | time to first `SynthesisChunk` |
| Azure TTS total | 1,860–4,474 ms | time to `SynthesisCompleted` |
| Azure TTS output | 95,616–369,792 bytes; 30–116 chunks | `SynthesisCompleted` totals |
| E2E turn 1 (appointment) | final transcript ~3.3 s; confirmation ~5.7 s | live WebSocket e2e |
| E2E turn 2 (practice info) | grounded answer after second final | live WebSocket e2e |
| E2E audio out (both turns) | 172 frames / 544,896 bytes | session metrics |
| Retrieval (Qdrant) | p50 662–806 ms, p95 852–975 ms | Day 9 live smoke (prior runs) |

Conditions: local loopback WebSocket, Qdrant local persistent mode, no PSTN,
synthetic PCM16 input (generated by real Azure TTS as "mic" proxy), single
sequential session. End-to-end latency from end of caller speech to first
assistant audio was not separately instrumented (would require client-side
playback timestamping).

---

## H. Test Results

### H.1 Python voice gateway

```
uv run ruff check .            → clean
uv run ruff format --check .   → clean
uv run pytest -o addopts="" -q → 350 passed, 4 skipped
```

The 4 skips are opt-in live tests requiring `EMMA_RUN_LIVE_TESTS=1`.

Breakdown by suite:
* `tests/unit/` — includes new `test_azure_stt_lifecycle.py` (6) and
  `test_day10_control_events.py` (5), plus Day 9 dialogue/controller tests.
* `tests/integration/` — includes new control-events opt-in test, plus
  speech-pipeline, websocket-protocol, websocket-lifecycle, websocket-auth,
  health tests.
* `tests/evaluation/day9/` — 34-scenario corpus.
* `tests/live/test_day9_live_smoke.py` — 4/4 passed with
  `EMMA_RUN_LIVE_TESTS=1` (real Gemini + Qdrant).

### H.2 NestJS practice API

```
pnpm test   → 49 passed, 8 suites (unit + db)
pnpm build  → clean (tsc)
```

Covers requests, calls, health, audit, handoffs, request list, idempotency
(concurrency), and DB constraints — against SQLite (dev/test); MySQL runtime
unavailable.

### H.3 Browser UI

```
pnpm lint   → clean (1 pre-existing unused-import warning in intake-form.tsx)
pnpm build  → clean (Next.js 16.4.5, Turbopack, TypeScript strict)
```

### H.4 No regressions

All Day 1–9 tests pass unchanged. No assertions weakened, no tests deleted.
No credentials leaked. No unexpected generated files.

---

## I. Outstanding Limitations

| Limitation | Status |
|---|---|
| Real microphone / acoustic barge-in | Unverified — no browser automation or mic access |
| Live browser end-to-end session | Unverified — UI builds but no live browser run |
| Real PSTN / Exotel call | Out of scope — not implemented or tested |
| Docker/MySQL runtime | Unavailable — Qdrant local mode used; NestJS tests use SQLite |
| Azure STT under adverse network | Not tested |
| False-negative rate of emergency guard | Not systematically measured |
| End-to-end latency (speech-end → first assistant audio) | Not separately instrumented |
| Production performance claims | Not made — small local sample only |

---

## J. Repository State

```
Branch: main
HEAD:   3d40b9409b635623f22ce1cd0220f1f23f647572 (uncommitted)

Modified (Day 9 + Day 10):
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

New (Day 10):
  ?? apps/ui/src/lib/voice/pcm-player.ts
  ?? apps/ui/src/lib/voice/gateway-transport.ts   (rewritten)
  ?? apps/voice-gateway/tests/unit/test_azure_stt_lifecycle.py
  ?? apps/voice-gateway/tests/unit/test_day10_control_events.py
  ?? docs/architecture/day-10-implementation.md
  ?? docs/architecture/day-10-engineering-report.md
  ?? docs/runbooks/azure-speech-setup.md
  ?? docs/runbooks/final-local-acceptance-testing.md
  ?? docs/architecture/final-system-architecture.md

Carried forward from Day 9 (uncommitted):
  ?? apps/voice-gateway/tests/evaluation/day9/
  ?? apps/voice-gateway/tests/live/
  ?? apps/voice-gateway/tests/unit/test_day9_dialogue.py
  ?? docs/architecture/day-9-engineering-report.md
  ?? docs/architecture/day-9-implementation.md
  ?? docs/runbooks/multiturn-conversation-testing.md

No commits or pushes were made. `.env` remains ignored (never committed).
```
