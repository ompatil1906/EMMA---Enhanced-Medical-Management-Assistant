# LiveKit Self-Hosting — EMMA Day 16

**Purpose:** Run EMMA's browser voice path with a self-hosted, open-source LiveKit
Server — no LiveKit Cloud account, no paid LiveKit services, no infrastructure
purchase.

**Status:** IMPLEMENTED — code and docs complete. Locally runnable with
Homebrew-installed LiveKit Server 1.13.11. Browser/microphone testing remains
UNVERIFIED in this environment (no browser binary available here).

**Security:** Never print, log, display, or commit credentials. Variable names
only appear below; values live in each app's git-ignored `.env`.

---

## 1. Overview

EMMA's browser voice path (LiveKit WebRTC) works end-to-end without any
LiveKit Cloud dependency:

```
browser mic → LiveKit room (WebRTC) → LiveKit agent → Deepgram Flux STT →
emergency guard → OpenAI GPT-6 Luna → ConversationController → Qdrant RAG →
NestJS backend → ElevenLabs TTS → LiveKit room → browser speaker
```

The FastAPI gateway mints short-lived JWT room tokens server-side
(`POST /api/voice/token`). API keys never reach the browser.

## 2. Local LiveKit Server Installation

### macOS (Homebrew)

```bash
brew install livekit
livekit-server --version   # expect: livekit-server version 1.13.11
```

### Other platforms

See the official LiveKit Server self-hosting guide:
https://docs.livekit.io/transport/self-hosting/local/

## 3. Local Startup (development)

### 3.1 Start the LiveKit server

```bash
livekit-server --dev \
  --keys "devkey: secret" \
  --node-ip 127.0.0.1 \
  --udp-port 7881
```

| Flag | Purpose |
|---|---|
| `--dev` | Development mode. **Not for production.** |
| `--keys "devkey: secret"` | API key/secret pair for token validation |
| `--node-ip 127.0.0.1` | Advertise localhost |
| `--udp-port 7881` | WebRTC media port |

Server endpoints:

| Service | Port | Protocol |
|---|---|---|
| HTTP (health, token validation) | 7880 | HTTP |
| WebRTC (media transport) | 7881 | UDP + TCP |

Readiness: `curl http://127.0.0.1:7880` → `200 OK`

**Important:** `--dev` credentials (`devkey: secret`) are for local testing
only. Never use them in production.

### 3.2 Start the FastAPI voice gateway

```bash
cd apps/voice-gateway
uv sync --all-groups          # once

# with dev credentials (local only):
LIVEKIT_URL=ws://127.0.0.1:7880 \
LIVEKIT_API_KEY=devkey \
LIVEKIT_API_SECRET=secret \
VOICE_TRANSPORT=livekit \
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Verify:

```bash
curl http://127.0.0.1:8000/health/live     # → {"status":"alive", ...}
curl http://127.0.0.1:8000/health/ready    # → {"status":"ready", ...}
```

### 3.3 Start the LiveKit agent worker

```bash
cd apps/voice-gateway
LIVEKIT_URL=ws://127.0.0.1:7880 \
LIVEKIT_API_KEY=devkey \
LIVEKIT_API_SECRET=secret \
PRACTICE_ID=demo-practice-001 \
STT_PROVIDER=mock \
TTS_PROVIDER=mock \
LLM_PROVIDER=mock \
uv run python -m app.integrations.livekit.agent
```

The worker registers with the LiveKit server and waits for dispatch requests
from browser clients.

### 3.4 Start the frontend

```bash
cd apps/ui
pnpm install          # once
NEXT_PUBLIC_VOICE_GATEWAY_URL=http://localhost:8000 \
NEXT_PUBLIC_VOICE_TRANSPORT=livekit \
pnpm dev              # → http://localhost:3000
```

Open `http://localhost:3000` → `/voice`. Enter caller details, tap "Call EMMA
Reception", grant microphone permission.

## 4. Environment Variables

| Variable | Local dev value | Production value | Required when |
|---|---|---|---|
| `LIVEKIT_URL` | `ws://127.0.0.1:7880` | `wss://your-server.example.com` | `VOICE_TRANSPORT=livekit` |
| `LIVEKIT_API_KEY` | `devkey` | Your production key | `VOICE_TRANSPORT=livekit` |
| `LIVEKIT_API_SECRET` | `secret` | Your production secret | `VOICE_TRANSPORT=livekit` |
| `VOICE_TRANSPORT` | `livekit` | `livekit` or `websocket` | No (defaults to `websocket`) |
| `PRACTICE_ID` | `demo-practice-001` | Your practice ID | No (defaults to `demo-practice-001`) |

All provider keys (`AZURE_SPEECH_KEY`, `DEEPGRAM_API_KEY`, `OPENAI_API_KEY`,
`ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`) are set per-provider — see
`docs/VOICE_PROVIDER_CONFIGURATION.md`.

`.env` is git-ignored. `.env.example` contains placeholder comments only — no
real values.

## 5. Token Authorization & Practice Isolation

`POST /api/voice/token` mints a short-lived (3600s) LiveKit JWT:

- Request body: `{ "room_name": "..." }`
- Response: `{ "token", "room", "practice_id" }`
- `practice_id` is resolved **server-side** from `PRACTICE_ID` — the client
  cannot choose another practice's identity.
- Room names are validated (`^[a-zA-Z0-9_-]{1,64}$`) to prevent injection.
- The token embeds `practice_id` in its metadata; the LiveKit agent reads it
  server-side.

Expired or invalid tokens are rejected by the LiveKit server at room-join
time; the gateway returns 503 when credentials are missing.

## 6. Production Deployment Prerequisites

EMMA does not require LiveKit Cloud. A public self-hosted deployment needs:

- A server/VPS to run LiveKit Server (cost varies by provider — not a LiveKit
  charge)
- A domain name and TLS certificate (WSS required for WebRTC from browsers)
- WebRTC UDP ports open (default 7881+) for media
- A TURN server for clients behind NAT (optional but recommended)
- The FastAPI gateway reachable over HTTPS for the token endpoint

**Do not use `--dev` credentials in production.** The gateway rejects
`LIVEKIT_API_KEY=devkey` and `LIVEKIT_API_SECRET=secret` when
`APP_ENV=production`.

## 7. Browser & Deployment URL

`NEXT_PUBLIC_VOICE_GATEWAY_URL` must point at the FastAPI gateway (HTTP for
the token endpoint, then WebRTC for media via LiveKit). For local development:

```
NEXT_PUBLIC_VOICE_GATEWAY_URL=http://localhost:8000
```

The legacy `ws://emma.heyompatil.me/ws/exotel` value is an Exotel-specific
WebSocket route for the Azure fallback path — it is **not** a LiveKit URL and
is not used when `VOICE_TRANSPORT=livekit`.

Browser microphone access requires a secure context (HTTPS or `localhost`).
Local development at `http://localhost:3000` is treated as secure by modern
browsers.

## 8. Fallback Path

If LiveKit is unavailable or misconfigured, EMMA falls back to the existing
FastAPI WebSocket `/ws/exotel` path (Azure Speech + Gemini):

```bash
VOICE_TRANSPORT=websocket
```

This requires `AZURE_SPEECH_KEY` and `AZURE_SPEECH_REGION`. There is no silent
fallback between LiveKit and WebSocket — the configured transport is used, and
misconfiguration degrades readiness (sessions close 1011) rather than silently
switching providers.

## 9. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Token endpoint returns 503 | `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` not set | Set both in `.env` |
| Browser can't join room | LiveKit server not running | Start `livekit-server --dev` |
| Browser can't connect (WSS) | `wss://` with localhost | Use `ws://` for local dev |
| Microphone permission denied | Browser blocked mic | Site lock icon → allow microphone |
| No assistant audio | AudioContext suspended | Click page to resume AudioContext |
| Agent doesn't join room | Worker not running | Start the LiveKit agent worker |
| Readiness degraded | Provider credentials missing | Check `.env` against required vars |
| Production rejects credentials | `devkey: secret` in `APP_ENV=production` | Use real production credentials |

## 10. What is NOT covered here

- Real PSTN / telephone / Exotel calls (out of scope for Day 16)
- OpenAI API calls (no credits consumed during setup or offline testing)
- LiveKit Cloud (not used — self-hosted open-source server only)
- Paid hosting or infrastructure purchases
