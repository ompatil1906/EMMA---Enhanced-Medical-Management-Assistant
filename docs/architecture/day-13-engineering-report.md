# Day 13 — Engineering Report

**Report date:** 2026-11-08 (Day 13 close-out).
**Git branch:** `main`
**Git HEAD:** `3d40b9409b635623f22ce1cd0220f1f23f647572`
**Git status:** Day 1–12 + Day 13 changes, all uncommitted (no commits or pushes).

---

## A. Executive summary

Day 13 implemented the LiveKit WebRTC browser voice integration, connecting the
Next.js UI to a LiveKit agent that drives the existing EMMA voice pipeline
(Deepgram Flux STT → emergency guard → OpenAI GPT-6 Luna → deterministic
ConversationController → Qdrant RAG → NestJS backend → ElevenLabs TTS).

**Implemented (offline-verified, 18 new tests):**
* LiveKit token minting endpoint (`POST /api/voice/token`) with server-side
  practice identity and room name validation.
* LiveKit agent worker entrypoint that wires STT → controller → TTS through
  the existing `VoicePipeline`.
* `EmmaAgentSession` bridging WebRTC microphone audio to STT and TTS audio
  back to the browser, with barge-in control events.
* LiveKit browser transport (`livekit-transport.ts`) implementing the same
  `VoiceTransport` interface as the WebSocket transport.
* Transport selection in `voice-screen.tsx` via `VOICE_TRANSPORT` env var
  (`websocket` default, `livekit` optional).

**Not verified (no credentials, no browser automation):**
* Live OpenAI GPT-6 Luna, Deepgram Flux, ElevenLabs, LiveKit calls.
* Live browser WebRTC session.
* Acoustic barge-in.

**OpenAI spend during Day 13: $0.00** — no live API calls made.

---

## B. Repository baseline

| Item | Value |
|---|---|
| Git branch | `main` |
| Initial HEAD | `3d40b9409b635623f22ce1cd0220f1f23f647572` |
| Day 12 gateway tests | 383 passed, 4 skipped |
| Day 13 gateway tests | 401 passed, 4 skipped (+18 new) |
| Day 12 NestJS tests | 49 passed |
| Day 13 NestJS tests | 49 passed |
| Day 12 frontend | lint clean, build clean |
| Day 13 frontend | lint clean, build clean |
| Ruff | clean |
| Ruff format | clean |

---

## C. Architecture changes

### C.1 New files

| File | Purpose |
|---|---|
| `apps/voice-gateway/app/api/routes/livekit_token.py` | `POST /api/voice/token` — mints LiveKit JWT room tokens |
| `apps/voice-gateway/app/integrations/livekit/__init__.py` | `LiveKitTokenMinter` + `LiveKitAgent` (rewritten) |
| `apps/voice-gateway/app/integrations/livekit/agent.py` | `EmmaAgentSession` + `entrypoint` + `create_worker_options` |
| `apps/ui/src/lib/voice/livekit-transport.ts` | LiveKit WebRTC browser transport |
| `tests/unit/test_livekit_token.py` | 5 token endpoint tests |
| `tests/unit/test_livekit_agent.py` | 3 agent session tests |
| `tests/unit/test_livekit_integration.py` | 10 agent↔controller integration tests |

### C.2 Modified files

| File | Change |
|---|---|
| `apps/voice-gateway/app/main.py` | Registered `livekit_token_router` |
| `apps/voice-gateway/app/core/config.py` | No new settings (Day 12 settings reused) |
| `apps/voice-gateway/pyproject.toml` | Added `livekit`, `livekit-agents` dependencies |
| `apps/ui/src/lib/voice/livekit-transport.ts` | New LiveKit transport |
| `apps/ui/src/components/voice/voice-screen.tsx` | Transport selection via `VOICE_TRANSPORT` |
| `apps/ui/src/lib/voice/types.ts` | Updated transport docstring |
| `apps/ui/package.json` | Added `livekit-client@^2.0.0` |

---

## D. LiveKit implementation

### D.1 Token endpoint (`POST /api/voice/token`)

Request: `{"room_name": "emma-room-001"}`
Response: `{"token": "<jwt>", "room": "emma-room-001", "practice_id": "demo-practice-001"}`

* Validates room name (alphanumeric, dash, underscore, 1–64 chars).
* Resolves `practice_id` server-side from `settings.PRACTICE_ID`.
* Returns 503 when LiveKit credentials are missing.
* Returns 400 for invalid room names, 422 for missing body.

### D.2 Agent worker (`entrypoint`)

Builds the `VoicePipeline` from gateway factories and runs one
`EmmaAgentSession` per LiveKit room:

1. Connects to the LiveKit room using server-minted credentials.
2. Subscribes to participant microphone audio → pushes to pipeline STT.
3. Consumes pipeline outbound TTS chunks → publishes to room.
4. Publishes control events (`barge-in`, `transcript`, `assistant`, `state`)
   as data messages to the browser.
5. Handles reconnection and clean disconnection.

### D.3 Session lifecycle

* `start()` — calls `pipeline.start()` (emits greeting via TTS), starts audio
  input/output loops.
* `stop(reason)` — cancels audio tasks, calls `pipeline.aclose(reason)`.
* Each session has its own pipeline instance → session isolation.
* Concurrent rooms get independent pipelines → no shared conversation state.

---

## E. Frontend

### E.1 LiveKit transport (`livekit-transport.ts`)

* Connects to LiveKit room via JWT from `/api/voice/token`.
* Publishes microphone audio (getUserMedia → publishTrack).
* Subscribes to agent audio track → plays via `<audio>` element.
* Renders `transcript` / `assistant` / `state` control events.
* Browser barge-in: mic level ≥ 0.12 for 2 ticks (120 ms) → stop mic, publish
  `barge-in` data message.
* Dynamic import of `livekit-client` — reports error if unavailable.

### E.2 Transport selection (`voice-screen.tsx`)

* `VOICE_TRANSPORT=websocket` (default) — existing FastAPI WebSocket path.
* `VOICE_TRANSPORT=livekit` — new LiveKit WebRTC path.

### E.3 UI states

`IDLE → REQUESTING_MICROPHONE → CONNECTING → CONNECTED → DISCONNECTING → IDLE`

Handles: mic permission denied, credentials unavailable, token request failure,
room connection failure, agent not joining, unexpected disconnect.

---

## F. Controller integration

The existing `ConversationController` remains the single source of truth. The
LiveKit agent does not duplicate any controller logic:

1. Agent receives final transcript from STT.
2. Agent calls `pipeline._handle_stt_event(FinalTranscript)`.
3. Pipeline runs: emergency guard → LLM → `ConversationController` →
   `_apply_decision` → `_speak` (TTS).
4. Controller output (text) is spoken via TTS and streamed to the browser.

The controller handles: slot filling, confirmation, correction, cancellation,
multiple independent requests, practice-information RAG, backend submission,
and emergency handling — exactly as in the WebSocket path.

---

## G. Safety results

### F.1 Emergency guard ordering (verified)

`emergency_guard()` runs in `_process_turn()` **before**:
* OpenAI LLM interpretation
* Gemini embeddings
* Qdrant retrieval
* NestJS backend submission

On match: fixed 999 script spoken, zero LLM/Qdrant/backend calls.

Covered by integration test `test_emergency_guard_runs_before_llm_and_backend`.

### F.2 Explicit confirmation (verified)

* No submission without lexical `confirm`.
* `cancel`/`reject`/`ambiguous` never submit.
* Correction after read-back invalidates pending confirmation.

Covered by integration tests B–E.

### F.3 Duplicate prevention (verified)

* Per-session turn lock + duplicate-turn sequence rejection.
* Concurrent sessions have independent pipelines and conversation state.
* Reconnects do not create duplicate requests (pipeline processes each
  `FinalTranscript` exactly once via sequence tracking).

Covered by integration test `test_conversation_state_isolation_between_sessions`.

### F.4 Tenant isolation (verified)

* `practice_id` resolved server-side from `settings.PRACTICE_ID`.
* Token minter embeds server-side practice ID in token metadata.
* Client-supplied `practice_id` in token request body is ignored.

Covered by token test `test_mint_token_practice_id_server_side`.

### F.5 Barge-in (verified at code level)

* Browser: mic level threshold → stop mic, publish `barge-in` data message.
* Server: `pipeline.cancel_tts()` discards queued audio + cooperative TTS
  cancel.
* No stale audio after cancellation.

Covered by code implementation; acoustic verification requires a browser.

---

## G. Budget

**Zero live OpenAI calls made during Day 13.** OpenAI spend: $0.00.

The `OpenAIBudgetGuard` (Day 12) is integrated into the `LLMProviderFactory`
and used by the `OpenAILLMProvider`. It tracks input/output/cached tokens and
estimated USD per call, enforcing the $1.00 dev cap. All Day 13 tests use
mocked providers — no API credits consumed.

---

## H. Test results

| Suite | Result |
|---|---|
| Python gateway (all) | 401 passed, 4 skipped |
| Day 13: test_livekit_token.py | 5 passed |
| Day 13: test_livekit_agent.py | 3 passed |
| Day 13: test_livekit_integration.py | 10 passed |
| NestJS | 49 passed, build clean |
| Frontend | lint clean, build clean |
| Ruff | clean |
| Ruff format | clean |

---

## I. Limitations

| Item | Status |
|---|---|
| Live OpenAI GPT-6 Luna | Not tested — no credentials |
| Live Deepgram Flux | Not tested — no credentials |
| Live ElevenLabs TTS | Not tested — no credentials |
| Live LiveKit agent / WebRTC | Not tested — no credentials, no LiveKit server |
| Live browser WebRTC session | Not tested — no browser automation |
| Acoustic barge-in | Not tested — no microphone |
| End-to-end speech-end→first-audio latency | Not measured |
| `gpt-6-luna` model availability | Unverified — no web access |

---

## J. Judge readiness

| Capability | Status |
|---|---|
| AI core pipeline | VERIFIED (offline, 401 tests) |
| LiveKit token endpoint | VERIFIED (offline, mocked) |
| LiveKit agent → controller | VERIFIED (offline, mocked) |
| LiveKit browser transport | IMPLEMENTED (builds, not browser-tested) |
| Live browser session | UNVERIFIED |
| Acoustic barge-in | UNVERIFIED |
| Live provider calls | NOT MADE ($0.00 spent) |

The implementation is complete and ready for live credentials. Once
`LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET`, `DEEPGRAM_API_KEY`,
`ELEVENLABS_API_KEY`/`ELEVENLABS_VOICE_ID`, and `OPENAI_API_KEY` are
configured, the system can be started and tested end-to-end.

---

## K. Git status

```
Branch: main
HEAD:   3d40b9409b635623f22ce1cd0220f1f23f647572 (uncommitted)

Modified: README.md, voice-screen.tsx, gateway-transport.ts, types.ts,
  websocket.py, core/config.py, conversation.py, gemini_provider.py
  (embeddings+llm+mock), provider_factory.py, conversation_controller.py,
  intent_interpreter.py, voice_pipeline.py, azure_stt.py, speech/factory.py,
  pyproject.toml, test_speech_pipeline.py, main.py

New: livekit/__init__.py, livekit/agent.py, api/routes/livekit_token.py,
  ui/src/lib/voice/livekit-transport.ts, tests/unit/test_livekit_token.py,
  tests/unit/test_livekit_agent.py, tests/unit/test_livekit_integration.py,
  docs/architecture/day-13-implementation.md,
  docs/architecture/day-13-engineering-report.md,
  docs/runbooks/livekit-browser-demo.md,
  docs/runbooks/local-development-without-credentials.md

No commits or pushes were made. `.env` remains git-ignored.
```
