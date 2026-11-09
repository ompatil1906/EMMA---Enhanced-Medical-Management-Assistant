# Voice Provider Configuration — EMMA Day 16

**Purpose:** Document Deepgram Flux STT, OpenAI GPT-6 Luna LLM, and ElevenLabs
TTS provider configuration — models, credentials, timeouts, error handling,
budget safeguards, and validation.

**Status:** Day 18 update — IMPLEMENTED and offline-TESTED against local
protocol servers (real WebSocket / real HTTP). Live provider behavior remains
UNVERIFIED: no live Deepgram or ElevenLabs request has been made from this
environment (see `docs/LIVE_PROVIDER_ACCEPTANCE.md` §1 for the credential
rotation blocker).

**Security:** Variable names only — never values. `.env` is git-ignored.
`.env.example` contains placeholder comments only.

**Constraint:** Do not make billable Deepgram, OpenAI, or ElevenLabs API calls.
Do not spend OpenAI credits. Do not use OpenAI Realtime or OpenAI audio APIs.

---

## 1. Provider Matrix

| Layer | Provider | Model | Env var (key) | Env var (model) |
|---|---|---|---|---|
| STT | Deepgram Flux | `flux-general-en` | `DEEPGRAM_API_KEY` | `DEEPGRAM_MODEL` |
| LLM | OpenAI | `gpt-6-luna` | `OPENAI_API_KEY` | `OPENAI_MODEL` |
| TTS | ElevenLabs | `eleven_flash_v2_5` | `ELEVENLABS_API_KEY` | `ELEVENLABS_MODEL` |
| TTS voice | ElevenLabs | (configured voice) | `ELEVENLABS_VOICE_ID` | — |

Alternate paths (Azure fallback): STT `azure`, TTS `azure`, LLM `gemini`.
See `docs/runbooks/azure-speech-setup.md`, `docs/runbooks/gemini-*`.

## 2. Deepgram Flux STT

**File:** `apps/voice-gateway/app/speech/deepgram_stt.py`

- Endpoint is chosen by the model family (Day 18 correction):
  - Flux models (`flux-general-en`) → `wss://api.deepgram.com/v2/listen`
    with `encoding=linear16&sample_rate=<rate>&channels=1`, schema `TurnInfo`
    (`Update`/`EagerEndOfTurn` → partial, `EndOfTurn` → final,
    `StartOfTurn`/`TurnResumed` ignored), `{"type":"CloseStream"}` to flush.
  - Any other model (e.g. `nova-3`) → `wss://api.deepgram.com/v1/listen`
    with `language`, `interim_results`, `endpointing` and classic `Results`.
- Model: `DEEPGRAM_MODEL` only — no alternative-model fallback, no silent
  switch between the two schemas.
- Language: en-GB (UK English); implied by the Flux model name
- Audio: PCM16 LE mono at the negotiated rate (16 kHz for LiveKit)
- Features: partial + final transcripts, provider-native end-of-turn
  detection, monotonic sequence numbers, session cleanup on `stop()`,
  graceful `finish()` for one-shot streams
- Credentials: `DEEPGRAM_API_KEY` required — no silent mock fallback
  (production: settings refuse to load without it)
- Runtime dependency: `websockets>=15` (moved out of the dev group on Day 18 —
  a production install previously could not have used this adapter)

**Validation:**
- Missing `DEEPGRAM_API_KEY` → `ProviderConfigurationError` at construction
- `STT_PROVIDER=deepgram` without key → readiness degraded, sessions close 1011
- No API calls during offline testing — `websockets` mocked

## 3. OpenAI GPT-6 Luna LLM

**File:** `apps/voice-gateway/app/integrations/llm/openai_provider.py`

- Endpoint: `https://api.openai.com/v1/chat/completions` (REST, via `httpx`)
- Model: `gpt-6-luna` only — **no GPT-6.1 Terra, no expensive fallback,
  no OpenAI Realtime API, no OpenAI audio/transcription API**
- Output: JSON object, validated against `InterpretationResult`
- Timeouts: explicit request + total timeouts, no automatic retries
- Budget: `OpenAIBudgetGuard` application-level cap ($2.50 daily / $1.00 dev)
- Credentials: `OPENAI_API_KEY` required — no silent mock fallback

**Budget safeguards:**
- Pre-call estimate checked before any network request
- Actual usage recorded from provider response (`usage.prompt_tokens`,
  `usage.completion_tokens`, `usage.prompt_tokens_details.cached_tokens`)
- `OpenAIBudgetExceededError` → `LLMConnectionError`, request refused
- Budget guard is application-side only — not a replacement for provider billing

**No silent fallback:** `LLM_PROVIDER=openai` without `OPENAI_API_KEY` →
`LLMProviderNotAvailable`, readiness degraded.

## 4. ElevenLabs TTS

**File:** `apps/voice-gateway/app/speech/elevenlabs_tts.py`

- Endpoint (Day 18 correction):
  `POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream?output_format=pcm_<rate>`
  with `xi-api-key`, `Content-Type: application/json` and body
  `{text, model_id, voice_settings}`; response is chunked raw PCM.
  (The Day 12 code used `wss://…/stream/input`, which is not an endpoint.)
- Model: `ELEVENLABS_MODEL` (`eleven_flash_v2_5`) only
- Voice: `ELEVENLABS_VOICE_ID` — read from configuration, never fabricated or
  substituted; a blank/whitespace value is rejected at construction
- Audio: `pcm_8000` / `pcm_16000` / `pcm_24000` matching the negotiated rate —
  exactly the pipeline's PCM16 mono, so no decode or resample step
- Latency: first-chunk and total synthesis latency are measurable per request
- Cancellation: `cancel_turn()` closes the streaming response (barge-in hook);
  a cancelled turn completes with `cancelled=True`
- Errors: 401/403 → authentication message naming `ELEVENLABS_API_KEY`;
  402 → credits; 404 → `ELEVENLABS_VOICE_ID`; 422/429 → provider message.
  One attempt only — never a retry storm against a rejected key
- Credentials: `ELEVENLABS_API_KEY` + `ELEVENLABS_VOICE_ID` required
  (production: settings refuse to load without them)
- Dependency: `httpx` (already a runtime dependency) — no SDK added

**`ELEVENLABS_VOICE_ID` validation:**
- Read from the ignored `.env`; the value is deliberately not repeated in
  tracked files (a voice id is not secret, but it is account-specific)
- Missing, empty or whitespace-only → `ProviderConfigurationError` at
  construction, and the settings validator normalises blank to unset
- `TTS_PROVIDER=elevenlabs` without voice ID → settings/serving refusal,
  readiness degraded
- No default voice ID is ever fabricated or silently substituted

## 5. Timeouts, Error Handling, Retry Limits

| Provider | Connect timeout | Request timeout | Retries |
|---|---|---|---|
| Deepgram STT | `SPEECH_CONNECTION_TIMEOUT` (10s) | `SPEECH_REQUEST_TIMEOUT` (15s) | None |
| ElevenLabs TTS | 5 s connect (capped by request timeout) | `SPEECH_REQUEST_TIMEOUT` (15s) | None |
| OpenAI LLM | — | `LLM_TIMEOUT_SECONDS` (15s) | None |

- No automatic retries — the `ConversationController` decides retry policy
- Fatal errors tear the session down; errors surface as typed exceptions
- Non-fatal provider errors emit `RecognitionError` / `ProviderSynthesisError`
- Safe provider failures: constructor raises on missing credentials; no partial
  initialization; no silent degradation to mock

## 6. Environment Validation

When `VOICE_TRANSPORT=livekit`:
- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` all required
- Missing any → `ValueError` at settings load, gateway refuses to start
- Dev credentials (`devkey` / `secret`) rejected when `APP_ENV=production`

When `TTS_PROVIDER=elevenlabs`:
- `ELEVENLABS_API_KEY` + `ELEVENLABS_VOICE_ID` required
- Missing → `ProviderConfigurationError`, readiness degraded
- Dev voice ID (`dev-voice-id`) rejected in production

Readiness (`GET /health/ready`) reports the selected `stt_provider`,
`tts_provider` and (Day 18) `llm_provider` so a live run can prove its
configuration before spending money.

No provider is ever silently selected in production. Mock providers are used
only when explicitly configured (`STT_PROVIDER=mock`, `TTS_PROVIDER=mock`,
`LLM_PROVIDER=mock`).

## 7. No API Keys in Frontend Bundles

- All provider API keys are server-side only (`apps/voice-gateway/.env`)
- The browser receives only a short-lived LiveKit JWT from `/api/voice/token`
- `NEXT_PUBLIC_*` variables contain no secrets — only URLs and transport mode
- `.env` is git-ignored; `.env.example` has placeholder comments only

## 8. Configuration Reference (`.env.example`)

All variables are documented in `.env.example` with placeholder comments.
Copy to `.env` and set real values (never commit `.env`).

Required non-secret values in `.env.example`:
- `APP_ENV=development`
- `VOICE_TRANSPORT=livekit` (or `websocket`)
- `VOICE_PIPELINE=speech`
- `STT_PROVIDER=mock` / `TTS_PROVIDER=mock` / `LLM_PROVIDER=mock` (safe local defaults)
- `DEEPGRAM_MODEL=flux-general-en`
- `OPENAI_MODEL=gpt-6-luna`
- `ELEVENLABS_MODEL=eleven_flash_v2_5`
- `OPENAI_DAILY_BUDGET_USD=2.50`, `OPENAI_DEV_BUDGET_USD=1.00`

Secret values (set in ignored `.env` only):
- `DEEPGRAM_API_KEY`, `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`,
  `ELEVENLABS_VOICE_ID`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`,
  `AZURE_SPEECH_KEY`, `LLM_API_KEY`, `GEMINI_EMBEDDING_API_KEY`

## 9. What is NOT configured here

- No LiveKit Cloud dependency
- No OpenAI Realtime or audio APIs
- No billable provider calls during setup or offline testing
- No default/fabricated voice IDs or model names
- No silent provider fallback to a more expensive model
