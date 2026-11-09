# Day 12 — Engineering Report

**Report date:** 2026-11-08 (Day 12 close-out).
**Git branch:** `main`
**Git HEAD:** `3d40b9409b635623f22ce1cd0220f1f23f647572`
**Git status:** Day 9 + Day 10 + Day 11 + Day 12 changes, all uncommitted
(no commits or pushes made).

**CRITICAL CONSTRAINT:** Total OpenAI API balance is **$2.50**. No live OpenAI
calls have been made (no `OPENAI_API_KEY` configured, `gpt-6-luna` model
availability unverified). No Deepgram, ElevenLabs, or LiveKit credentials are
configured. No live provider verification is possible in this environment.

All claims below are either:
- **Verified offline** (code imports, type-checks, tests pass with mocked
  transport, no API credits consumed), or
- **Unverified** (requires credentials and/or browser — explicitly marked).

---

## A. Executive summary

Day 12 implements the advanced voice architecture selected by the user:

**Primary path:** LiveKit Agents + Deepgram Flux + OpenAI GPT-6 Luna +
ElevenLabs Flash v2.5 + Gemini Embedding 2 + Qdrant + deterministic controller
+ NestJS backend.

**Fallback path:** Existing FastAPI WebSocket + Azure Speech + Gemini
(preserved unchanged).

**Implemented (offline-verified):**
- OpenAI budget guard ($2.50 total, $1.00 dev cap, $0.75 demo reserve)
- OpenAI GPT-6 Luna LLM provider (Chat Completions only, no Realtime/transcription)
- Deepgram Flux STT provider (flux-general-en)
- ElevenLabs Flash TTS provider (eleven_flash_v2_5)
- LiveKit integration module (agent + token minter, lazy imports)
- Speech and LLM provider factories updated with new options
- 33 new offline tests (budget guard, OpenAI provider, Deepgram, ElevenLabs,
  factory configuration)

**Not verified (no credentials, no browser):**
- Live OpenAI GPT-6 Luna calls — `gpt-6-luna` model availability unknown
- Live Deepgram Flux calls
- Live ElevenLabs calls
- Live LiveKit agent / WebRTC browser session
- `gpt-6-luna` model verification

**No results are fabricated.** No silent model fallback. No OpenAI Realtime or
transcription API used.

---

## B. Repository baseline

| Item | Value |
|---|---|
| Git branch | `main` |
| Initial HEAD | `3d40b9409b635623f22ce1cd0220f1f23f647572` |
| Uncommitted changes | Yes (Day 9–12) |
| Day 11 gateway tests | 350 passed, 4 skipped |
| Day 11 NestJS tests | 49 passed, build clean |
| Day 11 frontend | lint clean (1 pre-existing warning), build clean |
| Day 11 live smoke | 4/4 passed |
| Day 12 new tests | 33 passed (offline, mocked transport) |
| Day 12 total | 383 passed, 4 skipped |
| Ruff | clean |
| Ruff format | clean |

---

## C. Architecture changes

### C.1 New modules

| Module | Purpose |
|---|---|
| `app/services/openai_budget.py` | Application-level OpenAI spending guard |
| `app/integrations/llm/openai_provider.py` | GPT-6 Luna LLM adapter (httpx-based) |
| `app/speech/deepgram_stt.py` | Deepgram Flux streaming STT adapter |
| `app/speech/elevenlabs_tts.py` | ElevenLabs Flash streaming TTS adapter |
| `app/integrations/livekit/__init__.py` | LiveKit agent + token minter |

### C.2 Modified modules

| Module | Change |
|---|---|
| `app/core/config.py` | New settings: VOICE_TRANSPORT, STT/TTS/LLM provider options, OpenAI/LiveKit/Deepgram/ElevenLabs credentials, budget settings |
| `app/integrations/llm/provider_factory.py` | Added `openai` option with budget guard integration |
| `app/speech/factory.py` | Added `deepgram` STT and `elevenlabs` TTS options |

### C.3 Preserved (unchanged)

Gemini Embedding 2, Qdrant, deterministic ConversationController, emergency
guard, Azure Speech fallback, FastAPI WebSocket fallback, NestJS backend,
existing Next.js UI.

---

## D. LiveKit implementation

**Status:** Implemented but not live-tested (no `livekit` SDK, no credentials,
no browser).

`LiveKitAgent` bridges WebRTC audio to the existing pipeline:
- Connects to LiveKit room using server-minted credentials
- Subscribes to participant microphone audio
- Pushes PCM audio to STT provider
- Consumes pipeline outbound TTS audio and publishes to room
- Handles reconnection, cancellation, clean disconnection

`LiveKitTokenMinter` mints server-side JWTs (credentials never reach browser;
practice identity bound server-side).

**Fallback:** `VOICE_TRANSPORT=websocket` preserves the existing FastAPI
WebSocket path.

---

## E. Deepgram Flux live verification

**Status:** NOT verified — no `DEEPGRAM_API_KEY` configured.

Implemented:
- WebSocket connection to `wss://api.deepgram.com/v1/listen`
- Model: `flux-general-en`
- Partial + final transcripts
- Native end-of-turn detection (`endpointing=300`)
- PCM16 16 kHz mono input

Offline tests (6): credential check, partial/final emission, error handling,
provider name. All pass with mocked message handling (no network).

---

## F. GPT-6 Luna live verification

**Status:** NOT verified — no `OPENAI_API_KEY` configured, `gpt-6-luna` model
availability cannot be confirmed (no web access).

Implemented:
- OpenAI Chat Completions API only (no Realtime, no transcription)
- Model: `gpt-6-luna` (configured via `OPENAI_MODEL`)
- Strict Pydantic validation against `InterpretationResult`
- Budget guard integration (pre-call estimate + actual usage from response)

Offline tests (4): payload construction, usage accounting, error handling
(HTTP errors, malformed JSON, validation failure), budget cap refusal.
All pass with `httpx.MockTransport` (no network, no API credits).

---

## G. OpenAI usage and remaining balance estimate

**Status:** No calls made — $2.50 budget fully intact.

| Allocation | Maximum | Used | Remaining |
|---|---|---|---|
| Development testing | $0.50 | $0.00 | $0.50 |
| Regression testing | $0.50 | $0.00 | $0.50 |
| Demo reserve | $0.75 | $0.00 | $0.75 |
| Emergency reserve | $0.75 | $0.00 | $0.75 |
| **Total** | **$2.50** | **$0.00** | **$2.50** |

Application-level dev cap: $1.00 (initial conservative setting).
When credentials become available, the `OpenAIBudgetGuard` will:
- Track input/output/cached tokens per call
- Record estimated USD per call and cumulatively
- Stop requests when dev cap is reached (preserving demo reserve)
- Expose `snapshot()` for cumulative state

**Important:** This is application-side accounting only. The user should also
configure a usage limit in the OpenAI platform. This guard does NOT claim a
technical hard cap.

---

## H. ElevenLabs live verification

**Status:** NOT verified — no `ELEVENLABS_API_KEY` or `ELEVENLABS_VOICE_ID`
configured.

Implemented:
- WebSocket streaming TTS to ElevenLabs API
- Model: `eleven_flash_v2_5`, voice from `ELEVENLABS_VOICE_ID`
- Streaming synthesis, cancellation support
- PCM output at negotiated sample rate

Offline tests (5): credential check, empty-text contract, cancellation,
provider name. All pass.

---

## I. Browser WebRTC verification

**Status:** NOT verified — no browser binary (Chrome, Chromium, Firefox),
no browser automation (Playwright, Puppeteer, WebDriver) in this environment.

The Next.js UI builds and type-checks cleanly. LiveKit WebRTC integration is
implemented but cannot be exercised without a browser.

---

## J. Barge-in verification

**Status:** NOT verified acoustically (no microphone).

Implemented:
- Server-side energy-gated barge-in (PCM16 RMS threshold + 40 ms gate)
- Browser playback cancellation via `pcm-player.stop()`
- LiveKit `cancel_turn()` for TTS cancellation
- `barge-in` control event from browser

Not tested with real audio.

---

## K. Existing controller integration

**Status:** Verified offline (383 tests pass).

The deterministic ConversationController is the single source of truth.
LiveKit agent and all new providers delegate to the existing `VoicePipeline`,
which owns the controller. No second state machine created.

---

## L. Emergency safety results

**Status:** Verified offline (existing corpus + code review).

Emergency guard runs before OpenAI, Gemini embeddings, Qdrant, and backend.
On match:
- Fixed 999 script spoken verbatim
- Zero OpenAI calls, zero Gemini embedding calls, zero Qdrant calls,
  zero ordinary backend submissions
- Pending confirmations invalidated

Covered by Day 9 corpus (emergency on first turn, during slot filling, during
confirmation, combined with admin request) and live pipeline guard ordering.

---

## M. RAG and citation results

**Status:** Verified offline (Day 11 live E2E with `PRACTICE_ID=demo-practice-001`).

Grounded answer example:
> "On Monday, the practice opens at 8:00 AM [1].\n\nSources:\n[1] Riverbend
> Medical Practice — Opening Hours..."

Real Gemini embedding + Qdrant retrieval, practice-scoped, citation included.

---

## N. A/B benchmark

**Status:** NOT performed — requires live credentials for both stacks.

No benchmark numbers are fabricated. The Day 11 baseline measurements (Azure
STT ~2.7–3.2s first final, Azure TTS ~1.5–2.3s first chunk, Gemini ~2.85s
interpretation) are preserved as reference but not claimed as comparable to
the Day 12 stack.

---

## O. Latency p50/p95 and sample counts

**Status:** Not measured for Day 12 stack (no live calls).

Day 11 reference (single run, not production benchmarks):
- STT first partial: ~1.9–2.4s
- STT first final: ~2.7–3.2s
- Gemini interpretation: ~2.85s
- TTS first chunk: ~0.9–1.7s

---

## P. Accuracy results

**Status:** Not measured — no live provider calls.

Word error rate, intent accuracy, slot accuracy, grounded answer accuracy
cannot be measured without live Deepgram/OpenAI/ElevenLabs credentials.

---

## Q. Cost per conversation

**Status:** Not measurable — no live calls made.

The `OpenAIBudgetGuard` will track per-call estimated USD when credentials
are available. No cost figures are fabricated.

---

## R. Test counts

| Suite | Result |
|---|---|
| Python gateway (all) | 383 passed, 4 skipped |
| Day 12: test_openai_budget.py | 10 passed |
| Day 12: test_openai_provider.py | 4 passed |
| Day 12: test_deepgram_stt.py | 6 passed |
| Day 12: test_elevenlabs_tts.py | 5 passed |
| Day 12: test_day12_providers.py | 8 passed |
| NestJS | 49 passed, build clean |
| Frontend | lint clean, build clean |
| Ruff | clean |
| Ruff format | clean |

---

## S. Files changed

**Modified:**
- `app/core/config.py` — new provider and budget settings
- `app/integrations/llm/provider_factory.py` — OpenAI option + budget guard
- `app/speech/factory.py` — Deepgram/ElevenLabs options

**New:**
- `app/services/openai_budget.py` — budget guard
- `app/integrations/llm/openai_provider.py` — GPT-6 Luna provider
- `app/speech/deepgram_stt.py` — Deepgram Flux STT
- `app/speech/elevenlabs_tts.py` — ElevenLabs Flash TTS
- `app/integrations/livekit/__init__.py` — LiveKit agent + token minter
- `tests/unit/test_openai_budget.py` — 10 tests
- `tests/unit/test_openai_provider.py` — 4 tests
- `tests/unit/test_deepgram_stt.py` — 6 tests
- `tests/unit/test_elevenlabs_tts.py` — 5 tests
- `tests/unit/test_day12_providers.py` — 8 tests

**New documentation:**
- `docs/architecture/day-12-implementation.md`
- `docs/architecture/day-12-engineering-report.md` (this file)
- `docs/architecture/adr-012-voice-architecture.md`
- `docs/runbooks/livekit-setup.md`
- `docs/runbooks/deepgram-flux-setup.md`
- `docs/runbooks/openai-gpt6-luna-setup.md`
- `docs/runbooks/elevenlabs-setup.md`
- `docs/runbooks/judge-demo-runbook.md`

---

## T. Remaining blockers

| Blocker | Impact |
|---|---|
| No `OPENAI_API_KEY` | Cannot verify GPT-6 Luna; cannot confirm model exists |
| No `DEEPGRAM_API_KEY` | Cannot verify Deepgram Flux |
| No `ELEVENLABS_API_KEY`/`ELEVENLABS_VOICE_ID` | Cannot verify ElevenLabs TTS |
| No `LIVEKIT_URL`/credentials | Cannot test LiveKit agent/WebRTC |
| `livekit` SDK not installed | Cannot import LiveKit at runtime |
| No browser binary | Cannot test WebRTC browser session |
| No web access | Cannot verify `gpt-6-luna` model availability |
| $2.50 OpenAI budget | Limits live testing to essential calls only |

---

## U. Judge demonstration instructions

See `docs/runbooks/judge-demo-runbook.md` for the complete procedure.

**Demo readiness:** The AI core pipeline (controller, RAG, emergency guard) is
implemented and offline-tested. The new provider adapters are implemented and
offline-tested with mocked transport. However, **no live provider calls have
been made** and **no browser session has been executed**. The demo cannot be
run until credentials and a browser are available.

---

## V. Git status

```
Branch: main
HEAD:   3d40b9409b635623f22ce1cd0220f1f23f647572 (uncommitted)

No commits or pushes were made. `.env` remains git-ignored (never committed).
```
