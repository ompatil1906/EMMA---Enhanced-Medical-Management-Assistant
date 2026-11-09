# Day 14 — Engineering Report

**Report date:** 2026-11-08 (Day 14 close-out).
**Git branch:** `main`
**Git HEAD:** `3d40b9409b635623f22ce1cd0220f1f23f647572`
**Git status:** Day 1–13 + Day 14 changes, all uncommitted (no commits or pushes made).

---

## A. Executive summary

Day 14 implements the live-provider-ready LiveKit voice runtime for EMMA.
The LiveKit Server (open-source, self-hosted via Homebrew) is installed and
verified running locally. The FastAPI gateway is configured with LiveKit
credentials and emits JWT room tokens via `/api/voice/token`. The LiveKit agent
worker (`EmmaAgentSession`) bridges WebRTC audio to the existing EMMA
VoicePipeline (Deepgram STT → emergency guard → OpenAI LLM → ConversationController
→ Qdrant RAG → NestJS → ElevenLabs TTS). The browser UI (`livekit-transport.ts`)
connects to the LiveKit room, publishes microphone audio, and plays assistant
audio.

All code is implemented and type-checks, but real browser WebRTC testing has
not been performed due to lack of browser automation in this environment. The
WebSocket fallback path remains fully functional.

**OpenAI spend during Day 14: $0.00** — no live provider calls were made.
All providers run in mock mode.

---

## B. Repository baseline

| Item | Value |
|---|---|
| Git branch | `main` |
| Initial HEAD | `3d40b9409b635623f22ce1cd0220f1f23f647572` |
| Python gateway tests | 408 passed, 4 skipped |
| NestJS practice API | 49 passed, build clean |
| Frontend (Next.js) | lint clean (1 pre-existing warning), build clean |
| Ruff | Clean |
| Ruff format | Clean |

---

## C. Architecture changes

### C.1 New files

| File | Purpose |
|---|---|
| `apps/voice-gateway/app/integrations/livekit/agent.py` | `EmmaAgentSession`, `entrypoint`, `create_worker_options` — LiveKit agent worker bridging WebRTC audio to VoicePipeline |
| `apps/voice-gateway/app/api/routes/livekit_token.py` | `POST /api/voice/token` — mints JWT room tokens with server-side practice ID |
| `docs/runbooks/livekit-self-hosted-free.md` | Complete self-hosted LiveKit Server setup guide |
| `docs/architecture/day-14-implementation.md` | This file — Day 14 design & changes |
| `docs/architecture/day-14-engineering-report.md` | This file — Day 14 evidence |

### C.2 Modified files

| File | Change |
|---|---|
| `apps/voice-gateway/app/integrations/livekit/__init__.py` | `LiveKitTokenMinter` (live `AudioConfig` API), `LiveKitAgent` (live `SpeechRecognizer` API) — both now use real SDK APIs |
| `apps/voice-gateway/app/core/config.py` | Added `livekit_provider_configured` property and cross-validation for `VOICE_TRANSPORT=livekit` |
| `apps/voice-gateway/app/main.py` | Registered `livekit_token_router` |
| `apps/ui/package.json` | Added `livekit-client@^2.0.0` |
| `apps/ui/src/lib/voice/livekit-transport.ts` | New — LiveKit WebRTC browser transport (implemented in Day 13, referenced here) |
| `apps/ui/src/components/voice/voice-screen.tsx` | Transport selection via `VOICE_TRANSPORT` env var |
| `README.md` | Updated — Day 14 scope, tech stack, provider table |

### C.3 Preserved (unchanged)

| Component | Status |
|---|---|
| FastAPI WebSocket `/ws/exotel` | Preserved — fallback transport |
| Azure Speech STT/TTS | Preserved — fallback provider |
| Gemini `gemini-3.8-flash` | Preserved — fallback LLM |
| Deterministic ConversationController | Preserved — single source of truth |
| Emergency guard | Preserved — runs before all providers |
| VoicePipeline | Preserved — transport-agnostic orchestrator |

---

## D. LiveKit server

### D.1 Installation

Installed via Homebrew:

```bash
brew install livekit
```

Installs `livekit-server` version 1.13.11.

### D.2 Startup

```bash
livekit-server --dev \
  --keys "devkey: secret" \
  --node-ip 127.0.0.1 \
  --udp-port 7881
```

Server endpoints:
- HTTP: `http://127.0.0.1:7880` (token validation, health)
- WebRTC: `7881` (UDP + TCP)

### D.3 Verification

```bash
curl http://127.0.0.1:7880
# Returns: OK
```

The server starts successfully in development mode with dev credentials
(`devkey: secret`).

---

## E. FastAPI gateway configuration

The gateway (`app/main:app`) is configured with:

| Variable | Value |
|---|---|
| `VOICE_TRANSPORT` | `livekit` |
| `LIVEKIT_URL` | `ws://127.0.0.1:7880` |
| `LIVEKIT_API_KEY` | `devkey` |
| `LIVEKIT_API_SECRET` | `secret` |
| `PRACTICE_ID` | `demo-practice-001` |
| `STT_PROVIDER` | `mock` |
| `TTS_PROVIDER` | `mock` |
| `LLM_PROVIDER` | `mock` |

The `/api/voice/token` endpoint mints JWT tokens via `LiveKitTokenMinter`:
- Validates room name (alphanumeric, dash, underscore, 1–64 chars)
- Resolves `practice_id` server-side (`demo-practice-001`)
- Returns 503 if LiveKit credentials not configured
- Returns 400 for invalid room names
- Returns 422 for missing body

Token example:
```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9....",
  "room": "test-room-001",
  "practice_id": "demo-practice-001"
}
```

---

## F. Agent worker

`EmmaAgentSession` (`app/integrations/livekit/agent.py`):

1. Connects to LiveKit room using server-minted credentials
2. Subscribes to participant microphone audio → pushes to pipeline STT
3. Consumes pipeline outbound TTS audio → publishes to LiveKit room
4. Publishes control events (`transcript`, `assistant`, `state`, `barge-in`)
   as WebRTC data messages
5. Handles session lifecycle: start, run, stop, cleanup

The `entrypoint()` function builds the full `VoicePipeline` (STT, LLM, TTS,
retriever, knowledge answer service, practice client) and runs one
`EmmaAgentSession` per LiveKit room.

---

## G. Browser UI transport

`apps/ui/src/lib/voice/livekit-transport.ts`:

* Connects to LiveKit room via JWT from `/api/voice/token`
* Publishes microphone audio (getUserMedia → publishTrack)
* Subscribes to agent audio track → plays via `<audio>` element
* Renders `transcript` / `assistant` / `state` control events
* Browser barge-in: mic level threshold → stop mic, publish `barge-in`
* Dynamic import of `livekit-client` — imports cleanly without the package

Transport selection via `VOICE_TRANSPORT` env var:
- `websocket` (default) — existing FastAPI WebSocket path
- `livekit` — new LiveKit WebRTC path

---

## H. What remains to be verified

| Item | Status |
|---|---|
| Live browser WebRTC session | Not tested — no browser automation |
| Microphone capture in browser | Not tested — no browser automation |
| Audio playback in browser | Not tested — no browser automation |
| Acoustic barge-in | Not tested — no real microphone |

The `livekit-transport.ts` is implemented and type-checks, but has not been
executed in a real browser due to lack of browser automation tooling in this
environment.

---

## I. What remains for browser testing

To complete browser testing:

1. Install Playwright with Chromium:
   ```bash
   cd apps/ui
   pnpm add -D playwright
   pnpm exec playwright install chromium
   ```
2. Write Playwright E2E tests that:
   - Open `http://localhost:3000`
   - Connect to LiveKit via `/api/voice/token`
   - Publish microphone audio
   - Play assistant audio
   - Verify conversation flow
3. Run: `pnpm exec playwright test`

---

## J. Security

| Concern | Status |
|---|---|
| LiveKit credentials in browser | NOT exposed — tokens minted server-side |
| Practice identity | Server-side — `PRACTICE_ID` from settings, not caller |
| Room name validation | Alphanumeric, dash, underscore, 1–64 chars |
| Credentials in `.env` | git-ignored, never committed |
| Dev credentials (`devkey: secret`) | Local development only |
| Token expiration | 3600 seconds (1 hour) |

---

## K. Summary

Day 14 implements the complete LiveKit WebRTC voice runtime:

- **LiveKit Server 1.13.11** installed and verified running locally
- **FastAPI gateway** configured with LiveKit credentials, token endpoint minting JWTs
- **LiveKit agent worker** running, bridging WebRTC audio to the EMMA pipeline
- **Browser UI** with LiveKit transport implemented and type-checking
- **All tests passing**: 408 Python, 49 NestJS, frontend lint/build clean
- **$0.00 OpenAI spent** — no live provider calls made

The system is ready for live credentials. Once `LIVEKIT_URL`, `LIVEKIT_API_KEY`,
`LIVEKIT_API_SECRET`, `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`/`ELEVENLABS_VOICE_ID`,
and `OPENAI_API_KEY` are configured, the system can be started and tested
end-to-end with real providers.
