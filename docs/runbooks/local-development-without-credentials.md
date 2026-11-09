# Local Development Without Credentials — EMMA Runbook

**Purpose:** Run and test EMMA locally without any LiveKit, Deepgram,
ElevenLabs, or OpenAI credentials. All providers run in mock mode — zero API
calls, zero cost.

**Status:** Fully runnable in this coding environment — no browser binary,
no LiveKit server, no provider credentials required.

---

## 1. What works without credentials

| Component | Mode | Notes |
|---|---|---|
| Voice gateway (FastAPI) | Mock STT/TTS/LLM | Deterministic, script-driven |
| Conversation controller | Deterministic | Full slot filling, confirmation, cancellation |
| Qdrant RAG | Local persistent | 18 demo-practice vectors |
| NestJS practice API | SQLite | Idempotent submission, audit events |
| Browser UI (WebSocket path) | Mock audio | Echo + deterministic responses |
| LiveKit transport | Not available | Requires `LIVEKIT_*` credentials + browser |

---

## 2. Startup

### 2.1 Voice gateway

```bash
cd apps/voice-gateway
uv sync --all-groups          # once
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Verify:
```bash
curl http://127.0.0.1:8000/health/live     # → {"status":"alive", ...}
curl http://127.0.0.1:8000/health/ready    # → {"status":"ready", ...}
```

The readiness endpoint reports which providers are configured. Without
credentials, `speech_provider_configured` will be `true` (mock providers are
always configured), `llm_provider_configured` will be `true` (mock LLM), and
`rag_configured` will be `true` (mock embeddings + local Qdrant).

### 2.2 Frontend

```bash
cd apps/ui
pnpm install          # once
pnpm dev              # → http://localhost:3000
```

Open `http://localhost:3000` → `/voice`. The UI uses the WebSocket transport
by default (`VOICE_TRANSPORT=websocket`).

### 2.3 Practice API (optional)

```bash
cd apps/practice-api
pnpm install          # once
pnpm dev              # local API
```

---

## 3. Demo scenarios (mock mode)

All scenarios work with the mock providers. The mock STT returns scripted
transcripts; the mock LLM returns structured interpretations; the mock TTS
returns deterministic PCM audio.

| Scenario | What happens |
|---|---|
| Appointment | Mock STT returns "I want an appointment" → mock LLM extracts date/time → controller asks for confirmation → mock TTS speaks |
| Correction | Second turn changes date → controller invalidates confirmation → re-read-back |
| Multiple requests | One utterance → two independent requests |
| Cancellation | Cancel before confirm → request marked CANCELLED |
| Emergency | "Severe chest pain" → fixed 999 script, zero LLM/Qdrant/backend calls |
| Practice info | "Opening hours Monday?" → Qdrant retrieval → grounded answer |

---

## 4. Running the test suite

### 4.1 Python voice gateway

```bash
cd apps/voice-gateway
uv run ruff check .
uv run ruff format --check .
uv run pytest -o addopts="" -q
# → 401 passed, 4 skipped
```

The 4 skipped tests are opt-in live tests requiring
`EMMA_RUN_LIVE_TESTS=1` and real credentials.

### 4.2 LiveKit-specific tests (offline, mocked)

```bash
cd apps/voice-gateway
uv run pytest tests/unit/test_livekit_token.py \
            tests/unit/test_livekit_agent.py \
            tests/unit/test_livekit_integration.py -v
# → 18 passed
```

These test the LiveKit token endpoint, agent session, and agent↔controller
boundary using mocked providers — no LiveKit SDK, no credentials, no network.

### 4.3 NestJS practice API

```bash
cd apps/practice-api
pnpm test       # 49 passed
pnpm build      # clean
```

### 4.4 Frontend

```bash
cd apps/ui
pnpm lint       # clean (1 pre-existing warning)
pnpm build      # clean
```

---

## 5. Readiness report

The gateway `/health/ready` endpoint reports provider readiness:

```json
{
  "status": "ready",
  "checks": {
    "speech_provider_configured": true,   // mock STT/TTS always available
    "llm_provider_configured": true,      // mock LLM always available
    "embedding_provider_configured": true, // mock embeddings always available
    "vector_store_configured": true,       // local Qdrant always available
    "rag_configured": true,
    "voice_pipeline": "speech",
    "voice_transport": "websocket",
    "provider_mode": "simulation"
  }
}
```

When LiveKit credentials are added, set `VOICE_TRANSPORT=livekit` and the
LiveKit transport becomes available. Without credentials, the WebSocket path
remains fully functional.

---

## 6. What requires credentials (not available here)

| Capability | Required env vars |
|---|---|
| LiveKit WebRTC transport | `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` |
| Deepgram Flux STT | `DEEPGRAM_API_KEY` |
| ElevenLabs Flash TTS | `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` |
| OpenAI GPT-6 Luna | `OPENAI_API_KEY` ($2.50 budget cap) |

Do not add fake credentials to `.env`. Update `.env.example` with documented
placeholder names only.

---

## 7. Shutdown

```bash
pkill -f "uvicorn app.main:app"
pkill -f "next dev"
```
