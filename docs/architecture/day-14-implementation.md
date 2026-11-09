# Day 14 — Implementation

**Report date:** 2026-11-08 (Day 14 close-out).
**Git branch:** `main`
**Git HEAD:** `3d40b9409b635623f22ce1cd0220f1f23f647572`
**Status:** All work uncommitted; no commits or pushes made.
**Scope:** Day 14 implements the live-provider-ready LiveKit voice runtime for EMMA.
The browser UI transport (`gateway-transport.ts`) was implemented in Day 13;
Day 14 completes the server-side live infrastructure: local LiveKit server
installation, FastAPI WebSocket gateway configured with LiveKit credentials,
LiveKit agent worker running and registered, and end-to-end WebRTC audio path
ready for browser testing.

---

## 1. What changed

### 1.1 LiveKit Server installation

Installed LiveKit Server 1.13.11 via Homebrew (`brew install livekit`).
Verified working with `livekit-server --dev --keys "devkey: secret"`.
Server listens on HTTP port 7880 and WebRTC UDP port 7881.

### 1.2 FastAPI gateway with LiveKit credentials

The gateway (`apps/voice-gateway/app/main:app`) is configured with:
- `VOICE_TRANSPORT=livekit` — enables the LiveKit browser transport path
- `LIVEKIT_URL=ws://127.0.0.1:7880` — local LiveKit server WebSocket URL
- `LIVEKIT_API_KEY=devkey` — development API key
- `LIVEKIT_API_SECRET=secret` — development API secret

The `/api/voice/token` endpoint (`apps/voice-gateway/app/api/routes/livekit_token.py`)
mints short-lived JWT room tokens using `LiveKitTokenMinter`. The practice ID
is resolved server-side (`demo-practice-001`); the browser cannot choose
another practice's identity.

### 1.3 LiveKit agent worker

`apps/voice-gateway/app/integrations/livekit/agent.py` — `EmmaAgentSession`
bridges WebRTC audio from the browser to the `VoicePipeline`:
- Subscribes to participant microphone audio → pushes to pipeline STT
- Consumes pipeline outbound TTS audio → publishes to LiveKit room
- Emits control events (`transcript`, `assistant`, `state`, `barge-in`) as
  WebRTC data messages
- Handles session lifecycle: start, run, stop, cleanup

The `entrypoint()` function builds the full VoicePipeline (STT, LLM, TTS,
retriever, knowledge answer service, practice client) and runs one
`EmmaAgentSession` per LiveKit room.

### 1.4 Browser UI with LiveKit transport

`apps/ui/src/lib/voice/livekit-transport.ts` — Next.js browser transport that:
- Requests a JWT token from `/api/voice/token`
- Connects to the LiveKit room via WebRTC
- Publishes microphone audio to the room
- Subscribes to assistant audio and plays via `<audio>` element
- Renders `transcript`/`assistant`/`state` control events
- Implements browser barge-in: mic level threshold → stop mic, publish
  `barge-in` data message
- Dynamic import of `livekit-client` — works without the package installed

`apps/ui/src/components/voice/voice-screen.tsx` — transport selection based
on `VOICE_TRANSPORT` env var (`websocket` default, `livekit` optional).

### 1.5 Configuration validation

`apps/voice-gateway/app/core/config.py` — added `livekit_provider_configured`
property and cross-validation:
- `VOICE_TRANSPORT=livekit` requires `LIVEKIT_URL`, `LIVEKIT_API_KEY`,
  `LIVEKIT_API_SECRET`
- `APP_ENV=production` rejects development credentials (`devkey: secret`)

---

## 2. What was preserved (unchanged)

| Component | Status |
|---|---|
| FastAPI WebSocket `/ws/exotel` | Preserved — fallback transport |
| Azure Speech STT/TTS | Preserved — fallback provider |
| Gemini `gemini-3.8-flash` | Preserved — fallback LLM |
| Deterministic ConversationController | Preserved — single source of truth |
| Emergency guard | Preserved — runs before all providers |
| VoicePipeline | Preserved — transport-agnostic orchestrator |

---

## 3. What remains to be verified (no browser automation available)

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

## 4. Test results

| Suite | Result |
|---|---|
| Python gateway | 408 passed, 4 skipped |
| NestJS practice API | 49 passed, build clean |
| Frontend (Next.js) | lint clean (1 pre-existing warning), build clean |
| Ruff | Clean |
| Ruff format | Clean |

---

## 5. Deliverables status

| Deliverable | Status |
|---|---|
| `apps/voice-gateway/app/integrations/livekit/agent.py` | Implemented — `EmmaAgentSession`, `entrypoint`, `create_worker_options` |
| `apps/voice-gateway/app/api/routes/livekit_token.py` | Implemented — `POST /api/voice/token` JWT minting |
| `apps/voice-gateway/app/integrations/livekit/__init__.py` | Updated — `LiveKitTokenMinter`, `LiveKitAgent` with live SDK |
| `apps/ui/src/lib/voice/livekit-transport.ts` | Implemented — WebRTC browser transport |
| `apps/ui/src/components/voice/voice-screen.tsx` | Updated — transport selection |
| `apps/ui/src/lib/voice/types.ts` | Updated — transport docstring |
| `apps/ui/package.json` | Added `livekit-client@^2.0.0` |
| `apps/voice-gateway/pyproject.toml` | Added `livekit`, `livekit-agents` |
| `apps/voice-gateway/app/core/config.py` | Added `livekit_provider_configured`, cross-validation |
| `docs/runbooks/livekit-setup.md` | Updated — references self-hosted setup |
| `docs/runbooks/livekit-self-hosted-free.md` | New — complete self-hosted setup guide |
| `docs/runbooks/livekit-browser-demo.md` | Updated — references self-hosted setup |
| `docs/architecture/final-system-architecture.md` | Updated — LiveKit token endpoint |
| `README.md` | Updated — Day 14 scope, tech stack, provider table |

---

## 6. Environment variables for local development

| Variable | Local Dev Value | Purpose |
|---|---|---|
| `LIVEKIT_URL` | `ws://127.0.0.1:7880` | LiveKit server WebSocket URL |
| `LIVEKIT_API_KEY` | `devkey` | LiveKit API key (dev only) |
| `LIVEKIT_API_SECRET` | `secret` | LiveKit API secret (dev only) |
| `VOICE_TRANSPORT` | `livekit` | Enable WebRTC transport |
| `PRACTICE_ID` | `demo-practice-001` | Practice identity (server-side) |

**Never commit `.env`.** Development credentials (`devkey: secret`) are for
local testing only.

---

## 7. How to run the full stack locally

```bash
# Terminal 1: LiveKit server
livekit-server --dev --keys "devkey: secret" --node-ip 127.0.0.1 --udp-port 7881

# Terminal 2: FastAPI gateway
cd apps/voice-gateway
LIVEKIT_URL=ws://127.0.0.1:7880 LIVEKIT_API_KEY=devkey LIVEKIT_API_SECRET=secret \
  VOICE_TRANSPORT=livekit PRACTICE_ID=demo-practice-001 \
  STT_PROVIDER=mock TTS_PROVIDER=mock LLM_PROVIDER=mock \
  uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

# Terminal 3: LiveKit agent worker
LIVEKIT_URL=ws://127.0.0.1:7880 LIVEKIT_API_KEY=devkey LIVEKIT_API_SECRET=secret \
  PRACTICE_ID=demo-practice-001 STT_PROVIDER=mock TTS_PROVIDER=mock \
  LLM_PROVIDER=mock uv run python -m app.integrations.livekit.agent

# Terminal 4: Next.js frontend
cd apps/ui
NEXT_PUBLIC_VOICE_GATEWAY_URL=http://localhost:8000 \
NEXT_PUBLIC_VOICE_TRANSPORT=livekit pnpm dev
```

Then open `http://localhost:3000` → `/voice` in a browser.

---

## 8. What remains for browser testing

The `livekit-transport.ts` is implemented and type-checks, but has not been
executed in a real browser due to lack of browser automation tooling in this
environment. To complete browser testing:

1. Install Playwright with Chromium: `pnpm add -D playwright && pnpm exec playwright install chromium`
2. Write Playwright E2E tests that open `http://localhost:3000`, connect to
   LiveKit, capture microphone audio, and play assistant audio
3. Verify end-to-end audio flow: mic → WebRTC → LiveKit → agent → STT → LLM →
   controller → TTS → WebRTC → browser speakers
