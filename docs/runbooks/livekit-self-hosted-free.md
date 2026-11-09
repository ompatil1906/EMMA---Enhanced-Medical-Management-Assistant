# LiveKit Self-Hosted (Free) Setup — EMMA Runbook

**Purpose:** Run the EMMA voice gateway with a self-hosted, open-source LiveKit
Server — no LiveKit Cloud account, no paid plan, no infrastructure cost.

**Status:** Fully runnable in this coding environment. LiveKit Server 1.13.11
is installed via Homebrew and verified working.

---

## 1. What is LiveKit Server?

LiveKit Server is the open-source media server that powers LiveKit's WebRTC
infrastructure. It handles:

- WebRTC media transport (browser ↔ agent)
- Room management and participant sessions
- Real-time audio streaming
- Secure token-based authentication

**Self-hosted LiveKit ≠ LiveKit Cloud:**

| | Self-Hosted LiveKit Server | LiveKit Cloud |
|---|---|---|
| Cost | Free (open source) | Paid tiers available |
| Infrastructure | You run it (your machine) | Managed by LiveKit |
| Account required | No | Yes |
| Data stays local | Yes | No (LiveKit's cloud) |
| EMMA Day 14 support | ✅ Primary | Not required |

**EMMA does not require LiveKit Cloud.** The open-source server runs locally on
your development machine at no cost.

---

## 2. Installation

### macOS (Homebrew)

```bash
brew install livekit
```

This installs the `livekit-server` binary (version 1.13.11 verified).

### Verification

```bash
livekit-server --version
# Should print: livekit-server version 1.13.11
```

### Other platforms

See the official LiveKit Server documentation:
https://docs.livekit.io/transport/self-hosting/local/

---

## 3. Local Startup

Start the LiveKit server in development mode with dev credentials:

```bash
livekit-server --dev \
  --keys "devkey: secret" \
  --node-ip 127.0.0.1 \
  --udp-port 7881
```

**What this does:**

| Flag | Purpose |
|---|---|
| `--dev` | Development mode (debug logging, console formatter, /debug/pprof). **Not for production.** |
| `--keys "devkey: secret"` | API key/secret pair for token validation. Format: `key: secret` |
| `--node-ip 127.0.0.1` | Advertise localhost as the node IP |
| `--udp-port 7881` | WebRTC UDP port |

**Server endpoints:**

| Service | Port | Protocol |
|---|---|---|
| HTTP (token validation, health) | 7880 | HTTP |
| WebRTC (media transport) | 7881 | UDP + TCP |

**Server readiness:**

```bash
curl http://127.0.0.1:7880
# Should return a 200 OK response
```

**Important:** Development credentials (`devkey: secret`) are for local testing
only. Never use them in a public deployment.

---

## 4. Backend Startup (FastAPI Voice Gateway)

```bash
cd apps/voice-gateway
uv sync --all-groups          # once, to create the locked venv

# Start with LiveKit dev credentials:
LIVEKIT_URL=ws://127.0.0.1:7880 \
LIVEKIT_API_KEY=devkey \
LIVEKIT_API_SECRET=secret \
VOICE_TRANSPORT=livekit \
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Verify:**

```bash
curl http://127.0.0.1:8000/health/live     # → {"status":"alive", ...}
curl http://127.0.0.1:8000/health/ready    # → {"status":"ready", ...}
```

The readiness endpoint reports LiveKit configuration status.

---

## 5. Agent Worker Startup

The LiveKit agent worker bridges WebRTC audio to the EMMA pipeline:

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

---

## 6. Frontend Startup

```bash
cd apps/ui
pnpm install          # once
NEXT_PUBLIC_VOICE_GATEWAY_URL=http://localhost:8000 \
NEXT_PUBLIC_VOICE_TRANSPORT=livekit \
pnpm dev              # → http://localhost:3000
```

Open `http://localhost:3000` → `/voice`. The UI connects to the FastAPI gateway
for the token, then to the LiveKit room for audio.

---

## 7. Browser Connection

1. Open `http://localhost:3000` → `/voice`
2. Enter caller details and tap "Call EMMA Reception"
3. Grant microphone permission when the browser prompts
4. The UI requests a JWT token from `/api/voice/token`
5. The browser connects to the LiveKit room via WebRTC
6. Speak and hear the assistant's response

**Browser security:** Microphone access requires a secure context (HTTPS or
localhost). Local development at `http://localhost:3000` is treated as a secure
context by modern browsers.

---

## 7. Environment Variables

| Variable | Local Dev Value | Production Value | Required? |
|---|---|---|---|
| `LIVEKIT_URL` | `ws://127.0.0.1:7880` | `wss://your-server.example.com` | Yes (when VOICE_TRANSPORT=livekit) |
| `LIVEKIT_API_KEY` | `devkey` | Your production key | Yes (when VOICE_TRANSPORT=livekit) |
| `LIVEKIT_API_SECRET` | `secret` | Your production secret | Yes (when VOICE_TRANSPORT=livekit) |
| `VOICE_TRANSPORT` | `livekit` | `livekit` or `websocket` | No (defaults to `websocket`) |
| `PRACTICE_ID` | `demo-practice-001` | Your practice ID | No (defaults to demo-practice-001) |

**Never commit real credentials.** `.env` is git-ignored.

---

## 8. Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| Token endpoint returns 503 | LiveKit credentials not configured | Set `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` |
| Browser can't connect to room | LiveKit server not running | Start `livekit-server --dev` |
| Browser can't connect (WSS) | Using `wss://` with localhost | Use `ws://` for local development |
| Microphone permission denied | Browser blocked mic | Click site lock icon → allow microphone |
| No assistant audio | AudioContext suspended | Click on the page to resume AudioContext |
| Agent doesn't join room | Worker not running | Start the agent worker process |

---

## 9. Development vs Production

| Aspect | Local Development | Production |
|---|---|---|
| LiveKit URL | `ws://127.0.0.1:7880` | `wss://your-server.example.com` |
| API key | `devkey` | Production key |
| API secret | `secret` | Production secret |
| HTTPS | Not required (localhost) | Required (WSS) |
| TURN server | Not required (localhost) | May be required for NAT traversal |
| Credentials in `.env` | Never commit | Never commit |

**Do not use development credentials in production.** The `devkey: secret`
pair is for local testing only.

---

## 10. Network and Firewall

For local development, no firewall configuration is needed — everything runs
on localhost.

For public deployment, you would need to open:
- HTTP port 80 (or 443 for HTTPS) for the gateway
- WebRTC UDP ports for media (default: 7881+)
- TURN server ports if clients are behind NAT

**Public deployment cost considerations:** While the LiveKit Server software is
free and open source, running it publicly requires:
- A server/VPS to host it (cost varies by provider)
- Bandwidth for WebRTC media traffic
- A TURN server for NAT traversal (optional but recommended)
- A domain name and SSL certificate

EMMA does not require any of these for local development.

---

## 11. Fallback Path

If LiveKit is not available or configured, EMMA falls back to the existing
FastAPI WebSocket `/ws/exotel` path with Azure Speech + Gemini:

```bash
VOICE_TRANSPORT=websocket  # Default
```

This path requires Azure Speech credentials (`AZURE_SPEECH_KEY`,
`AZURE_SPEECH_REGION`) and is fully functional without LiveKit.
