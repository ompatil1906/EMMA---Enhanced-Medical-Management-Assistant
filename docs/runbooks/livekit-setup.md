# LiveKit Setup — EMMA Runbook

**Purpose:** Configure and verify LiveKit WebRTC voice runtime for EMMA Day 12/13.
Credentials live in `.env` (git-ignored) and are never printed or committed.

**Status:** LiveKit Server 1.13.11 installed via Homebrew and verified working
locally. See `docs/runbooks/livekit-self-hosted-free.md` for the complete
self-hosted setup guide.

---

## 1. Required environment variables

Set in `apps/voice-gateway/.env` (values redacted — never record secrets):

| Variable | Purpose |
|---|---|
| `LIVEKIT_URL` | LiveKit server URL (e.g. `ws://127.0.0.1:7880` for local, `wss://...` for production) |
| `LIVEKIT_API_KEY` | LiveKit API key |
| `LIVEKIT_API_SECRET` | LiveKit API secret |
| `VOICE_TRANSPORT` | `livekit` to enable WebRTC path; `websocket` for fallback |

## 2. How the gateway uses LiveKit

* When `VOICE_TRANSPORT=livekit`, the browser connects to a LiveKit room via
  WebRTC instead of the FastAPI WebSocket `/ws/exotel`.
* The LiveKit Agent (`app/integrations/livekit/`) bridges WebRTC audio to the
  existing EMMA pipeline (Deepgram STT → OpenAI → Controller → Qdrant → NestJS
  → ElevenLabs TTS).
* Room tokens are minted server-side by `LiveKitTokenMinter` — the browser
  never receives API credentials.
* Practice identity is bound server-side in token metadata.

## 3. Local LiveKit server

See `docs/runbooks/livekit-self-hosted-free.md` for the complete self-hosted
setup guide.

### Quick start

```bash
# Install LiveKit server (macOS, Homebrew):
brew install livekit

# Start the server in development mode:
livekit-server --dev \
  --keys "devkey: secret" \
  --node-ip 127.0.0.1 \
  --udp-port 7881

# Server endpoints:
#   HTTP: http://127.0.0.1:7880
#   WebRTC: 7881 (UDP + TCP)
```

### Local development credentials

For local testing only — never use in production:

| Variable | Local Dev Value |
|---|---|
| `LIVEKIT_URL` | `ws://127.0.0.1:7880` |
| `LIVEKIT_API_KEY` | `devkey` |
| `LIVEKIT_API_SECRET` | `secret` |

## 4. Verification (when credentials available)

```bash
cd apps/voice-gateway
# Confirm LiveKit module imports
uv run python -c "from app.integrations.livekit import LiveKitAgent, LiveKitTokenMinter; print('OK')"

# Confirm settings load
uv run python -c "
from app.core.config import get_settings
s = get_settings()
print('VOICE_TRANSPORT:', s.VOICE_TRANSPORT)
print('LIVEKIT_URL set:', bool(s.LIVEKIT_URL))
print('LiveKit configured:', bool(s.LIVEKIT_API_KEY and s.LIVEKIT_API_SECRET))
"
```

## 5. Security

* Never print, log, or commit `LIVEKIT_API_SECRET`.
* Tokens are short-lived and minted server-side.
* Browser cannot choose practice identity — bound in server token metadata.
* Development credentials (`devkey: secret`) are for local testing only.
  Never use them in a public deployment.
