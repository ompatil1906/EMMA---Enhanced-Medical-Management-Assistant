# EMMA — Enhanced Medical Management Assistant (QuantumLoopAI assignment)

A 14-day technical assignment: prototype an EMMA-style AI voice receptionist
for NHS GP practices — an Exotel-compatible voice gateway with streaming
STT → deterministic conversation control → LLM interpretation → RAG → TTS,
and a practice backend. Day-1 research lives in `*(research docs)*.md` at the
repo root.

> **Prototype ≠ production service.** This repository is a technical prototype
> for an assignment evaluation. It is NOT an approved NHS system, NOT a medical
> device, provides NO clinical advice, and must never process real patient
> data. QuantumLoopAI's regulated production system is a separate, certified
> effort — don't conflate the two.

## What EMMA does (Day 12 complete)

EMMA receives a caller's voice through a browser, transcribes it with streaming
speech-to-text, runs a deterministic conversation controller (slot filling,
confirmation, correction, cancellation, multi-request independence), interprets
free speech with an LLM, answers practice-information questions from Qdrant
(RAG), and speaks replies with streaming text-to-speech — all while a
deterministic emergency guard runs first and performs zero administrative
actions on a match.

Two voice transport paths are available:

* **Primary (Day 14):** LiveKit WebRTC → LiveKit Agents → Deepgram Flux STT →
  OpenAI GPT-6 Luna → Controller → Qdrant RAG → NestJS → ElevenLabs TTS →
  LiveKit WebRTC playback.
* **Fallback (Day 10/11, preserved):** FastAPI WebSocket `/ws/exotel` → Azure
  STT → Gemini → Controller → Qdrant RAG → NestJS → Azure TTS → Web Audio
  playback.

The LLM is an **interpreter only**. A deterministic emergency guard runs before
the LLM, embeddings, Qdrant, and the backend; on a match it speaks the fixed
999 script and performs zero administrative actions.

### Day 19 additions

* **Named greeting.** The browser intake form's first name is carried server-side
  (LiveKit participant name/metadata, or a `caller-first-name` query parameter on
  the WebSocket fallback) so EMMA opens with *"Hello, Sarah."* — sanitised to
  letters/hyphen/apostrophe, length-capped, and never spoken raw. No name →
  the generic greeting.
* **Everyday conversation.** Deterministic small talk (greetings, thanks,
  goodbyes, time/date, identity) plus bounded general-knowledge answers
  (≤2 sentences). Clinical questions are refused deterministically before any
  model call; practice-specific questions stay on the RAG path and are never
  answered from world knowledge.
* **Live product (Jira as system of record).** With `CALL_RECORD_SINK=auto`
  (default) and `JIRA_MODE=live`, the gateway writes Jira directly: a confirmed
  request creates the issue during the call and the end-of-call summary is
  appended to it. The NestJS practice API is optional
  (`CALL_RECORD_SINK=practice_api|both`). Verified live against project `KAN`.
* **Call records in Jira.** At call end the gateway submits a deterministic
  summary (intake form + narrative + tasks derived from captured requests) to the
  practice API, which stores it and mirrors it to Jira as one issue per call.
  Masking, modes (`disabled`/`mock`/`live`) and idempotency:
  `docs/integrations/jira.md`.

## Architecture snapshot

```
Browser mic (PCM16 16 kHz)
      │ LiveKit WebRTC (primary) or WebSocket → /ws/exotel (fallback)
      ▼
LiveKit Agent / FastAPI Voice Gateway
  Deepgram/Azure STT → emergency guard → OpenAI/Gemini (interpreter only) →
  ConversationController → Qdrant/RAG (practice-scoped) → NestJS →
  ElevenLabs/Azure TTS
      │ Streaming audio + transcript/assistant/state control events
      ▼
Browser audio playback + transcript UI
```

Full diagram: `docs/architecture/final-system-architecture.md`.
Day 12 design: `docs/architecture/day-12-implementation.md`.
Day 12 evidence: `docs/architecture/day-12-engineering-report.md`.
Day 12 architecture decision: `docs/architecture/adr-012-voice-architecture.md`.

## Tech stack

Python 3.13 · FastAPI · Uvicorn · Pydantic v2 · pydantic-settings · pytest +
pytest-asyncio (async suite against real uvicorn) · `websockets` · `uv` (locked
`uv.lock`) · Next.js 16.4.5 (Turbopack, React 19) · pnpm · NestJS 11
(TypeORM) · Azure AI Speech (STT/TTS fallback) · Gemini (`gemini-3.8-flash`,
`gemini-embedding-2`, 768 dims) · Qdrant local persistent · LiveKit Agents
(WebRTC voice runtime) · Deepgram Flux (STT) · OpenAI `gpt-6-luna` (LLM) ·
ElevenLabs Flash v2.5 (TTS).

## Repository layout

```
apps/voice-gateway      the gateway service (app code, tests, Dockerfile)
apps/ui                 Next.js browser voice interface
apps/practice-api       NestJS practice backend (SQLite dev/test)
packages/contracts      cross-service contract placeholder
infrastructure/docker   gateway-only compose file
docs/                   protocol verification · architecture · runbooks
scripts/                websocket_simulator.py (provider-role client)
*.md at root            Day-1: REQUIREMENTS · ADRs · evaluation plan · risks
```

## Quickstart

```bash
# 1. Voice gateway
cd apps/voice-gateway
uv sync --all-groups                    # venv + locked deps
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# 2. Browser UI (second shell)
cd ../ui
pnpm install
pnpm dev                                # http://localhost:3000 → /voice

# 3. Practice API (optional, third shell)
cd ../practice-api
pnpm install
pnpm dev
```

Health checks: `curl http://127.0.0.1:8000/health/live` and `/health/ready`.

The browser voice interface opens at `/voice`. Start a live session from the
intake form; it connects to `NEXT_PUBLIC_VOICE_GATEWAY_URL` (default
`ws://localhost:8000/ws/exotel`) and streams the microphone at 16 kHz PCM16,
plays assistant audio, and renders transcripts.

## Environment

```bash
cp .env.example .env   # optional; simulation defaults need no credentials
```

Secrets stay in `.env` (git-ignored) and are never printed, logged, or
committed. Variable **names** only (values never recorded):

### Voice gateway (`apps/voice-gateway/.env`)
`APP_ENV` · `APP_HOST` · `APP_PORT` · `CORS_ORIGINS` · `LOG_LEVEL` ·
`EXOTEL_MODE` · `EXOTEL_API_KEY` · `EXOTEL_API_TOKEN` · `WS_AUTH_TOKEN` ·
`VOICE_PIPELINE` · `VOICE_TRANSPORT` · `STT_PROVIDER` · `TTS_PROVIDER` ·
`AZURE_SPEECH_KEY` · `AZURE_SPEECH_REGION` · `AZURE_SPEECH_LANGUAGE` ·
`AZURE_SPEECH_VOICE` · `DEEPGRAM_API_KEY` · `DEEPGRAM_MODEL` ·
`ELEVENLABS_API_KEY` · `ELEVENLABS_MODEL` · `ELEVENLABS_VOICE_ID` ·
`LIVEKIT_URL` · `LIVEKIT_API_KEY` · `LIVEKIT_API_SECRET` ·
`SPEECH_CONNECTION_TIMEOUT` · `SPEECH_REQUEST_TIMEOUT` ·
`MOCK_STT_SCRIPT` · `MOCK_STT_FINALIZE_MS` · `MOCK_STT_UTTERANCE_MS` ·
`MOCK_STT_ERROR_AT_FRAME` · `PRACTICE_API_BASE_URL` · `PRACTICE_API_TIMEOUT` ·
`PRACTICE_API_MAX_ATTEMPTS` · `PRACTICE_ID` · `LLM_PROVIDER` · `LLM_API_KEY` ·
`LLM_MODEL` · `LLM_PROMPT_VERSION` · `LLM_TIMEOUT_SECONDS` ·
`LLM_MAX_OUTPUT_TOKENS` · `OPENAI_MODEL` · `OPENAI_API_KEY` ·
`OPENAI_DAILY_BUDGET_USD` · `OPENAI_DEV_BUDGET_USD` ·
`OPENAI_INPUT_USD_PER_1K_TOKENS` · `OPENAI_OUTPUT_USD_PER_1K_TOKENS` ·
`OPENAI_CACHED_USD_PER_1K_TOKENS` · `EMBEDDING_PROVIDER` ·
`GEMINI_EMBEDDING_API_KEY` · `GEMINI_EMBEDDING_MODEL` ·
`GEMINI_EMBEDDING_DIMENSION` · `GEMINI_EMBEDDING_TIMEOUT_SECONDS` ·
`GEMINI_EMBEDDING_MAX_RETRIES` · `VECTOR_STORE_PROVIDER` · `QDRANT_MODE` ·
`QDRANT_URL` · `QDRANT_API_KEY` · `QDRANT_LOCAL_PATH` · `QDRANT_COLLECTION` ·
`RETRIEVAL_TOP_K` · `RETRIEVAL_MIN_SCORE` · `RETRIEVAL_CHUNK_SIZE` ·
`RETRIEVAL_CHUNK_OVERLAP` · `NEXT_PUBLIC_VOICE_GATEWAY_URL` · `BACKEND_URL` ·
`FRONTEND_URL` · `GENERAL_CHAT_ENABLED` · `GENERAL_CHAT_MAX_SENTENCES` ·
`CALL_SUMMARY_ENABLED` · `CALL_SUMMARY_PROVIDER` ·
`CALL_SUMMARY_INCLUDE_TRANSCRIPT` · `CALL_SUMMARY_TIMEOUT`

### Browser UI (`apps/ui/.env`)
`NEXT_PUBLIC_VOICE_GATEWAY_URL`

### Practice API (`apps/practice-api/.env`)
`APP_ENV` · `CORS_ORIGINS` · `DB_DRIVER` · `PORT` · `SQLITE_PATH` ·
`JIRA_MODE` · `JIRA_BASE_URL` · `JIRA_EMAIL` · `JIRA_API_TOKEN` ·
`JIRA_PROJECT_KEY` · `JIRA_ISSUE_TYPE_MAP` · `JIRA_EPIC_KEY` · `JIRA_PII_MODE` ·
`JIRA_TIMEOUT_MS` (see `docs/integrations/jira.md`)

Key switches: `VOICE_PIPELINE=speech|echo` · `VOICE_TRANSPORT=websocket|livekit` ·
`STT_PROVIDER=azure|deepgram|mock` · `TTS_PROVIDER=azure|elevenlabs|mock` ·
`LLM_PROVIDER=gemini|openai|mock` · `EMBEDDING_PROVIDER=gemini|mock` ·
`VECTOR_STORE_PROVIDER=qdrant|mock` · `EXOTEL_MODE=simulation|live` (live
requires `EXOTEL_API_KEY`/`EXOTEL_API_TOKEN`) · `JIRA_MODE=disabled|mock|live`.

## Tests / lint

```bash
# Voice gateway (Python)
cd apps/voice-gateway
uv run ruff check .
uv run ruff format --check .
uv run pytest -o addopts="" -q
# → 408 passed, 4 skipped (4 skips are opt-in live tests; set
#    EMMA_RUN_LIVE_TESTS=1 to run them)

# Live provider smoke (requires real credentials in .env)
EMMA_RUN_LIVE_TESTS=1 uv run pytest tests/live/test_day9_live_smoke.py -q

# Practice API (Node/NestJS)
cd apps/practice-api
pnpm test   # 49 passed
pnpm build  # clean

# Browser UI
cd apps/ui
pnpm lint   # clean
pnpm build  # clean
```

No commits or pushes are made for Day 14; all changes remain uncommitted on
`main`.

## Docker

```bash
cd infrastructure/docker
docker compose up -d && curl http://127.0.0.1:8000/health/ready
docker compose down
```

## Documentation index

| Doc | Contents |
|---|---|
| `docs/architecture/final-system-architecture.md` | full component diagram + invariants |
| `docs/architecture/day-13-implementation.md` | Day 12 design & changes |
| `docs/architecture/day-13-engineering-report.md` | Day 13 evidence, A–K report |
| `docs/architecture/adr-012-voice-architecture.md` | Day 12 architecture decision (LiveKit) |
| `docs/architecture/day-12-implementation.md` | Day 12 design & changes |
| `docs/architecture/day-12-engineering-report.md` | Day 12 evidence, A–V report |
| `docs/architecture/day-11-implementation.md` | Day 11 design & changes |
| `docs/architecture/day-11-engineering-report.md` | Day 11 evidence, A–O report |
| `docs/architecture/day-10-implementation.md` | Day 10 design & changes |
| `docs/architecture/day-10-engineering-report.md` | Day 10 evidence, A–J report |
| `docs/architecture/day-9-implementation.md` | Day 9 design |
| `docs/architecture/day-9-engineering-report.md` | Day 9 evidence/limitations |
| `docs/runbooks/livekit-browser-demo.md` | LiveKit browser demo procedure |
| `docs/runbooks/livekit-self-hosted-free.md` | self-hosted LiveKit server setup |
| `docs/runbooks/local-development-without-credentials.md` | offline dev procedure |
| `docs/runbooks/livekit-setup.md` | LiveKit configure + verify |
| `docs/runbooks/deepgram-flux-setup.md` | Deepgram Flux configure + verify |
| `docs/runbooks/openai-gpt6-luna-setup.md` | OpenAI GPT-6 Luna + budget guard |
| `docs/runbooks/elevenlabs-setup.md` | ElevenLabs configure + verify |
| `docs/runbooks/azure-speech-setup.md` | Azure Speech configure + verify |
| `docs/runbooks/browser-voice-demo.md` | browser voice demo procedure |
| `docs/runbooks/final-local-acceptance-testing.md` | acceptance checks |
| `docs/integrations/exotel-protocol.md` | verified protocol facts vs gateway policy |
| `docs/integrations/jira.md` | call summary → Jira: endpoints, modes, PII policy, idempotency |
| `docs/architecture/PIPECAT_FEATURE_GAP_ANALYSIS.md` | Pipecat vs EMMA capability audit |
| `docs/architecture/EMMA_ADVANCED_VOICE_ARCHITECTURE.md` | layer boundaries, invariants, tunables |
| `docs/architecture/VOICE_PIPELINE.md` | inbound → turn admission → worker → TTS → outbound |
| `docs/architecture/INTERRUPTION_AND_TURN_MANAGEMENT.md` | VAD barge-in, cancellation, race guards |
| `docs/architecture/PROVIDER_INTEGRATION.md` | provider contracts, selection, adding a provider |
| `docs/testing/VOICE_EVALUATION.md` | suites, measured latencies, unverified list |
| `docs/operations/VOICE_OBSERVABILITY.md` | metrics, `/api/v1/diagnostics`, content policy |
| `docs/operations/VOICE_TROUBLESHOOTING.md` | symptom → cause → fix runbook |
| `docs/implementation/PIPECAT_PORTING_CHANGELOG.md` | what was ported, changed, rejected |
| `docs/runbooks/local-development.md` | exact commands + troubleshooting |
| `REQUIREMENTS.md` / `ARCHITECTURE_DECISION_RECORD.md` / `EVALUATION_PLAN.md` / `RISKS_AND_ASSUMPTIONS.md` | Day-1 research |

## Known limitations

* **Real microphone / acoustic barge-in:** not tested (no browser automation or
  mic access in this environment). Server-side energy-gated barge-in cancel and
  browser playback cancellation are implemented but acoustically unverified.
* **Live browser end-to-end session:** UI builds and type-checks but was not
  driven against the gateway.
* **Live provider verification:** OpenAI GPT-6 Luna, Deepgram Flux, ElevenLabs
  Flash, and LiveKit are implemented but not live-tested — no credentials
  configured in this environment. `gpt-6-luna` model availability unverified.
* **Jira live sync:** implemented against Atlassian Cloud REST v3 and exercised
  in `mock` mode only. No live site/project/token was used, so the live request
  path is unverified. General-knowledge answers are unverified against a live
  model (they degrade to a deterministic fallback).
* **LiveKit self-hosted server:** installed and started locally via
  `livekit-server --dev`, but not connected to a browser client (no browser
  automation available).
* **Exotel/PSTN:** out of scope; not implemented or tested.
* **Docker/MySQL runtime:** unavailable; Qdrant local mode used; NestJS tests
  use SQLite.
* **$2.50 OpenAI budget:** hard constraint — live OpenAI testing limited to
  essential calls; demonstration reserve preserved.
* **Azure STT under adverse network / high latency:** not tested.
* **End-to-end latency (speech-end → first assistant audio):** not separately
  instrumented.
* No clinical triage claims; EMMA is not a medical device; no real patient
  data.

## Provider configuration

| Component | Primary (Day 13) | Fallback (Day 10/11) | Model |
|---|---|---|---|
| Voice runtime | LiveKit WebRTC | FastAPI WebSocket | — |
| LLM | OpenAI | Gemini | `gpt-6-luna` / `gemini-3.8-flash` |
| Embeddings | Gemini | Gemini | `gemini-embedding-2` (768 dims) |
| Vector DB | Qdrant | Qdrant | local persistent (`./data/qdrant`) |
| STT | Deepgram Flux | Azure AI Speech | `flux-general-en` / `en-GB` |
| TTS | ElevenLabs Flash | Azure AI Speech | `eleven_flash_v2_5` / `en-GB-SoniaNeural` |
| Backend | NestJS | NestJS | SQLite dev/test |

Real providers fail fast on missing credentials — there is no silent mock
fallback.
