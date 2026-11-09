# Voice Deployment — EMMA Day 16

**Purpose:** Document the voice deployment topology, TLS requirements,
networking, and the browser/deployment URL audit for EMMA's two voice
transports (FastAPI WebSocket and LiveKit WebRTC).

**Status:** IMPLEMENTED — documentation complete. Remote deployment readiness is
UNVERIFIED (no remote server available in this environment).

**Security:** Variable names only — never values. `.env` is git-ignored.

---

## 1. Deployment Topology

EMMA voice has two independent transport paths. The browser UI selects between
them via `VOICE_TRANSPORT`:

| Transport | Protocol | Backend endpoint | Media |
|---|---|---|---|
| `websocket` | FastAPI WebSocket | `/ws/exotel` | Base64 PCM16 over WS |
| `livekit` | LiveKit WebRTC | `/api/voice/token` (JWT) + LiveKit room | SRTP via LiveKit Server |

### Browser → Gateway → Providers

```
Browser (localhost:3000)
  │
  ├─ LiveKit path: POST /api/voice/token  →  JWT
  │               WebRTC → LiveKit Server (127.0.0.1:7880/7881)
  │               → LiveKit Agent → Deepgram/OpenAI/ElevenLabs
  │
  └─ WebSocket path: WS /ws/exotel  →  Azure STT → Gemini → Azure TTS
```

Both paths share the same `ConversationController` (emergency guard,
explicit confirmation, cancellation, practice isolation).

## 2. Browser & Deployment URL Audit

### 2.1 Current setting

`NEXT_PUBLIC_VOICE_GATEWAY_URL=ws://emma.heyompatil.me/ws/exotel` (root `.env`,
line 10).

### 2.2 Findings

| Question | Answer |
|---|---|
| Is this the correct gateway URL? | Only for the **WebSocket/Exotel** path. Not a LiveKit URL. |
| Is `/ws/exotel` Exotel-specific? | Yes — it is the Exotel AgentStream WebSocket route. |
| Does the deployed gateway support persistent WS? | UNVERIFIED — remote endpoint not tested in this environment. |
| Does the domain have working TLS? | UNVERIFIED — `ws://` (not `wss://`) indicates no TLS on this route. |
| Can HTTPS browser clients connect securely? | No — `ws://` is plaintext. Browsers require secure contexts for microphone access. |
| Should it use `wss://`? | Yes, if used over public internet from an HTTPS page. |
| Does the deployment platform support WebSocket? | UNVERIFIED — platform not confirmed. |

### 2.3 Recommendation

Do **not** blindly replace `NEXT_PUBLIC_VOICE_GATEWAY_URL`. For local
LiveKit development use:

```
NEXT_PUBLIC_VOICE_GATEWAY_URL=http://localhost:8000
NEXT_PUBLIC_VOICE_TRANSPORT=livekit
```

The gateway URL is only used for the token endpoint (`/api/voice/token`) in
LiveKit mode — media goes directly browser↔LiveKit Server via WebRTC.

Keep the browser voice UI independent of PSTN/Exotel. Do not implement phone
calls in this milestone.

## 3. TLS & Secure Context

- Browser microphone access (`getUserMedia`) requires a **secure context**:
  HTTPS, or `localhost` / `127.0.0.1` (treated as secure by modern browsers).
- Local development: `http://localhost:3000` + `ws://127.0.0.1:7880` — OK.
- Public deployment: WSS for LiveKit media, HTTPS for gateway + frontend.
- The `ws://emma.heyompatil.me/ws/exotel` route is plaintext and unsuitable
  for microphone use from an HTTPS page.

## 4. Networking & Firewall

### Local development (no firewall changes)

| Service | Port | Protocol | Access |
|---|---|---|---|
| LiveKit Server HTTP | 7880 | TCP | localhost |
| LiveKit Server WebRTC | 7881 | UDP + TCP | localhost |
| FastAPI gateway | 8000 | TCP | localhost |
| Next.js frontend | 3000 | TCP | localhost |

### Public deployment

- HTTPS (443) for frontend + gateway token endpoint
- WSS WebRTC ports for LiveKit media (default 7881+)
- TURN server for clients behind NAT (optional but recommended)
- Domain name + TLS certificate

LiveKit Server software is free/open-source; hosting, bandwidth, and TURN are
infrastructure costs borne by the deployer — not LiveKit charges.

## 5. Fallback Path

`VOICE_TRANSPORT=websocket` uses the existing FastAPI WebSocket `/ws/exotel`
with Azure Speech + Gemini. Requires `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION`.

There is **no silent fallback** between transports. Misconfiguration degrades
readiness (sessions close 1011) rather than switching providers silently.

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| UI never connects | Wrong `NEXT_PUBLIC_VOICE_GATEWAY_URL` | Set to `http://localhost:8000` for local LiveKit |
| Token endpoint 503 | LiveKit credentials missing | Set `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` |
| Browser can't join LiveKit room | LiveKit server not running | Start `livekit-server --dev` |
| Mic permission denied | Browser blocked mic | Site lock → allow microphone |
| No assistant audio | AudioContext suspended | Click page to resume |
| WSS connection fails | `ws://` used from HTTPS page | Use `wss://` or develop on localhost |
| Readiness degraded | Provider credentials missing | Check `.env` required vars |
| `--dev` credentials rejected | `APP_ENV=production` | Use real production credentials |

## 7. What is NOT deployed here

- No LiveKit Cloud account or services
- No paid hosting or infrastructure purchases
- No Exotel/PSTN implementation
- No OpenAI API calls during setup or offline testing
- No remote server provisioning
