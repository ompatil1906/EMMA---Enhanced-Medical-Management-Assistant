# Day 12 — Implementation

**Report date:** 2026-11-08 (Day 12 close-out).
**Git branch:** `main`
**Git HEAD:** `3d40b9409b635623f22ce1cd0220f1f23f647572`
**Status:** All work uncommitted; no commits or pushes made.
**Scope:** Advanced voice architecture upgrade — LiveKit Agents + Deepgram Flux +
OpenAI GPT-6 Luna + ElevenLabs Flash v2.5 as the primary voice path, with the
existing Azure + FastAPI WebSocket path preserved as fallback. Preserves Gemini
Embedding 2, Qdrant, the deterministic controller, the emergency guard, and the
existing backend.

---

## 1. What changed

### 1.1 Configuration (`app/core/config.py`)

New settings (names only, values in ignored `.env`):

| Setting | Values | Default | Purpose |
|---|---|---|---|
| `VOICE_TRANSPORT` | `websocket\|livekit` | `websocket` | Browser voice transport selection |
| `STT_PROVIDER` | `mock\|azure\|deepgram` | `mock` | STT provider selection |
| `TTS_PROVIDER` | `mock\|azure\|elevenlabs` | `mock` | TTS provider selection |
| `LLM_PROVIDER` | `mock\|gemini\|openai` | `mock` | LLM provider selection |
| `OPENAI_MODEL` | string | `gpt-6-luna` | GPT-6 Luna only (no expensive fallback) |
| `OPENAI_API_KEY` | secret | unset | OpenAI API key |
| `DEEPGRAM_API_KEY` | secret | unset | Deepgram Flux API key |
| `DEEPGRAM_MODEL` | string | `flux-general-en` | Deepgram Flux model |
| `ELEVENLABS_API_KEY` | secret | unset | ElevenLabs API key |
| `ELEVENLABS_MODEL` | string | `eleven_flash_v2_5` | ElevenLabs Flash v2.5 |
| `ELEVENLABS_VOICE_ID` | secret | unset | ElevenLabs voice ID (British English) |
| `LIVEKIT_URL` | URL | unset | LiveKit server URL |
| `LIVEKIT_API_KEY` | secret | unset | LiveKit API key |
| `LIVEKIT_API_SECRET` | secret | unset | LiveKit API secret |
| `OPENAI_DAILY_BUDGET_USD` | float | 2.50 | Total OpenAI budget (hard constraint) |
| `OPENAI_DEV_BUDGET_USD` | float | 1.00 | Initial dev cap |
| `OPENAI_*_USD_PER_1K_TOKENS` | float | see code | Estimated pricing placeholders |

### 1.2 OpenAI budget guard (`app/services/openai_budget.py`)

New module implementing application-level spending control:

* `OpenAIBudgetGuard` tracks input/output/cached tokens and estimated USD per
  call and cumulatively.
* `record_usage()` enforces the dev budget cap — raises `OpenAIBudgetExceededError`
  when the cap would be exceeded, preserving the demonstration reserve.
* `snapshot()` returns current state (never exposes credentials).
* `block()` / `unblock()` for emergency stop.
* Application-side accounting is NOT a replacement for provider billing controls.

### 1.3 OpenAI GPT-6 Luna LLM provider (`app/integrations/llm/openai_provider.py`)

New adapter following the Gemini provider pattern (httpx-based, no extra SDK):

* Uses OpenAI Chat Completions API only — **no Realtime audio API, no
  transcription API**.
* Model: `gpt-6-luna` only (configured via `OPENAI_MODEL`).
* Strict Pydantic validation against `InterpretationResult`.
* Budget guard integration — pre-call estimate and actual usage from provider
  response.
* Fails fast on missing `OPENAI_API_KEY` — no silent mock fallback.

### 1.4 Deepgram Flux STT provider (`app/speech/deepgram_stt.py`)

New streaming STT adapter (WebSocket-based):

* Model: `flux-general-en` only.
* Partial + final transcripts, native end-of-turn detection.
* PCM16 16 kHz mono input over WebSocket binary frames.
* Fails fast on missing `DEEPGRAM_API_KEY`.

### 1.5 ElevenLabs Flash TTS provider (`app/speech/elevenlabs_tts.py`)

New streaming TTS adapter (WebSocket-based):

* Model: `eleven_flash_v2_5` only, voice from `ELEVENLABS_VOICE_ID`.
* Streaming synthesis, first-audio latency, cancellation support.
* Fails fast on missing `ELEVENLABS_API_KEY` or `ELEVENLABS_VOICE_ID`.

### 1.6 Provider factories updated

* `SpeechProviderFactory`: added `deepgram` STT and `elevenlabs` TTS options.
* `LLMProviderFactory`: added `openai` option with budget guard integration.

### 1.7 LiveKit integration (`app/integrations/livekit/__init__.py`)

New module (lazy imports — importable without `livekit` SDK installed):

* `LiveKitAgent`: bridges WebRTC audio to the existing voice pipeline.
* `LiveKitTokenMinter`: server-side JWT minting (credentials never reach browser).

### 1.8 Tests (all offline, no API credits)

| File | Tests |
|---|---|
| `tests/unit/test_openai_budget.py` | 10 — budget recording, cap enforcement, reserve preservation |
| `tests/unit/test_openai_provider.py` | 4 — payload construction, usage accounting, error handling, budget cap |
| `tests/unit/test_deepgram_stt.py` | 6 — credential check, partial/final emission, error handling |
| `tests/unit/test_elevenlabs_tts.py` | 5 — credential check, empty-text contract, cancellation |
| `tests/unit/test_day12_providers.py` | 8 — factory config, provider construction, token minter |

---

## 2. What was preserved (unchanged)

| Component | Status |
|---|---|
| Gemini Embedding 2 + Qdrant (RAG) | Preserved |
| Deterministic ConversationController | Preserved (single source of truth) |
| Emergency guard | Preserved (runs before all providers) |
| Azure Speech STT/TTS | Preserved (fallback path) |
| FastAPI WebSocket `/ws/exotel` | Preserved (fallback transport) |
| NestJS practice API | Preserved |
| Existing Next.js UI | Preserved (WebSocket path) |

---

## 3. What was NOT implemented (no credentials available)

| Item | Reason |
|---|---|
| Live OpenAI GPT-6 Luna calls | No `OPENAI_API_KEY` configured; model availability unverified |
| Live Deepgram Flux calls | No `DEEPGRAM_API_KEY` configured |
| Live ElevenLabs calls | No `ELEVENLABS_API_KEY`/`ELEVENLABS_VOICE_ID` configured |
| Live LiveKit agent | No `LIVEKIT_URL`/credentials; `livekit` SDK not installed |
| Live browser WebRTC session | No browser binary or automation in this environment |
| `gpt-6-luna` model verification | No web access, no API key — cannot confirm model exists |

All live provider tests are opt-in (`EMMA_RUN_LIVE_TESTS=1` + credentials required).
No results are fabricated.

---

## 4. Bugs fixed

None — Day 12 is purely additive. No existing code was modified except
configuration (new settings only) and provider factories (new options added).

---

## 5. Deliverables status

| Deliverable | Status |
|---|---|
| `docs/architecture/day-12-implementation.md` | this file |
| `docs/architecture/day-12-engineering-report.md` | to be written |
| `docs/architecture/adr-012-voice-architecture.md` | to be written |
| `docs/runbooks/livekit-setup.md` | to be written |
| `docs/runbooks/deepgram-flux-setup.md` | to be written |
| `docs/runbooks/openai-gpt6-luna-setup.md` | to be written |
| `docs/runbooks/elevenlabs-setup.md` | to be written |
| `docs/runbooks/judge-demo-runbook.md` | to be written |
| `README.md` | to be updated |
| `final-system-architecture.md` | to be updated |
