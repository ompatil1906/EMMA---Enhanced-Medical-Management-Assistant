# Browser Voice Demo — EMMA Local Runbook

**Purpose:** Reproduce the EMMA browser voice receptionist locally so a real
user can grant microphone access, speak, hear an AI response, continue a
multi-turn conversation, and test interruption.

**Status:** Day 17 update — this runbook documents the **manual** procedure.
The environment now has a real Chromium build and Playwright, and the browser
path is covered by an automated, repeatable acceptance suite (8 scenarios,
real Chromium → self-hosted LiveKit → agent → mock STT/LLM/TTS → browser
playback). See `docs/BROWSER_WEBRTC_ACCEPTANCE.md` and
`apps/ui/tests/e2e/webrtc-acceptance.spec.ts`.

What remains **unverified** here is the part that needs a human: real speech
recognition/synthesis quality, acoustic (voice-driven) barge-in, and
telephony. The automated suite uses a synthetic microphone and mock providers
and does not claim otherwise.

**Security:** Do not print, log, or commit credentials. `.env` is git-ignored.
Variable names only appear below — never values.

---

## 1. Prerequisites

- Python 3.13 + `uv` (voice-gateway virtualenv)
- Node.js + `pnpm` (frontend and practice API)
- A modern browser with microphone support (Chrome, Edge, Firefox, Safari)
- Local network access to `http://127.0.0.1:8000` and `http://localhost:3000`

Environment variables by **name only** (values live in each app's ignored
`.env`; never print them):

- Voice gateway: `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`, `LLM_API_KEY`,
  `GEMINI_EMBEDDING_API_KEY`, `PRACTICE_ID`, `QDRANT_LOCAL_PATH`,
  `QDRANT_COLLECTION`, `STT_PROVIDER`, `TTS_PROVIDER`, `VOICE_PIPELINE`,
  `LLM_PROVIDER`, `EMBEDDING_PROVIDER`, `VECTOR_STORE_PROVIDER`
- Frontend: `NEXT_PUBLIC_VOICE_GATEWAY_URL`
- Practice API: `PORT`, `SQLITE_PATH`, `DB_DRIVER`

Required non-secret value already set: `PRACTICE_ID=demo-practice-001` (matches
the ingested knowledge base).

---

## 2. Startup

### 2.1 Voice gateway

```bash
cd apps/voice-gateway
uv sync --all-groups          # once, to create the locked venv
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Expected: `Uvicorn running on http://127.0.0.1:8000`.

### 2.2 Verify the gateway is ready

```bash
curl http://127.0.0.1:8000/health/live    # → {"status":"alive", ...}
curl http://127.0.0.1:8000/health/ready   # → {"status":"ready", ...}
```

`/health/ready` should report `speech_provider_configured: true` and
`voice_pipeline: speech`. If it reports `speech_provider_configured: false`,
check that `AZURE_SPEECH_KEY` and `AZURE_SPEECH_REGION` are set in
`apps/voice-gateway/.env`.

### 2.3 Frontend

```bash
cd apps/ui
pnpm install     # once
pnpm dev         # → http://localhost:3000
```

Open `http://localhost:3000` in the browser. The voice interface is at the
**`/voice` route** — open `http://localhost:3000/voice` directly (`/` also
renders it, but `/voice` is the documented entry point).

Transport selection: `NEXT_PUBLIC_VOICE_TRANSPORT=livekit` uses the LiveKit
WebRTC path (LiveKit agent worker required, see
`docs/runbooks/livekit-browser-demo.md`); unset/other values keep the legacy
gateway WebSocket transport.

### 2.4 Practice API (optional)

The speech loop works without the practice backend
(`PRACTICE_API_BASE_URL` unset → persistence disabled, no false submission
claims). To enable backend submission:

```bash
cd apps/practice-api
pnpm install     # once
pnpm dev         # local API on its configured PORT
```

Startup order: gateway and frontend can be started in any order; the frontend
connects to `NEXT_PUBLIC_VOICE_GATEWAY_URL`.

---

## 3. Demo steps (user-executed)

1. **Open the UI:** `http://localhost:3000/voice`.
2. **Enter caller details** on the intake form and tap "Call EMMA Reception".
3. **Grant microphone permission** when the browser prompts. The UI should
   show "Connected — streaming microphone at 16 kHz" and the orb should react
   to input level.
4. **Speak:** "I would like to request an appointment next Wednesday morning."
   - Expect: partial transcript as you speak, then a final transcript exactly
     matching what you said.
   - Expect: assistant asks for confirmation ("preferred date: next Wednesday,
     preferred time: morning. Is that correct?").
   - Expect: assistant audio plays through the browser speakers.
5. **Confirm:** "Yes."
   - Expect: one backend submission (if practice API is running) marked
     successful only after backend confirmation; assistant confirms the request.
6. **Practice information:** "What time does the practice open on Monday?"
   - Expect: grounded answer from Qdrant ("On Monday, the practice opens at
     8:00 AM [1]..."), spoken via Azure TTS.
7. **Contextual follow-up:** "And what about Saturday?"
   - Expect: context-aware retrieval based on the previous topic.
8. **Emergency:** "I am having severe chest pain."
   - Expect: the fixed script spoken immediately:
     "This may be a medical emergency. Please hang up and call 999 now."
     - No appointment, no LLM-based interpretation, no backend action.
9. **Interrupt (barge-in):** while the assistant is speaking, begin speaking.
   - Expect: browser playback stops; a `barge-in` event is sent; your new
     utterance is processed.
10. **Disconnect:** tap "End Conversation"; the WebSocket closes cleanly (1000).

---

## 4. Expected behaviour by scenario

| Scenario | User says | Expected EMMA response |
|---|---|---|
| Appointment request | "I want to request an appointment." | Asks for date, then time; reads back; asks for explicit confirmation |
| Correction | "Actually make it Wednesday afternoon." | Invalidates prior confirmation; re-reads; new confirmation required |
| Multiple requests | "Appointment and a repeat prescription." | Two independent requests; no cross-submission |
| Rejection | "No, don't submit." | No backend submission; honest response |
| Ambiguous | "Maybe." | Clarification ("clear yes or no"); no submission |
| Practice info | "What time does the practice open on Monday?" | Grounded Qdrant answer with citation, spoken via TTS |
| Follow-up | "And Saturday?" | Context-aware retrieval |
| Emergency | "Severe chest pain." | Fixed 999 script; zero admin/backend calls |
| Backend failure | (simulated rejection) | Request marked FAILED; honest error, no false success |

---

## 5. Shutdown

```bash
# In each app's terminal: Ctrl+C
# Or:
pkill -f "uvicorn app.main:app"
pkill -f "next dev"
pkill -f "nest start"
```

---

## 6. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| UI never shows "Connected" | Wrong `NEXT_PUBLIC_VOICE_GATEWAY_URL` or gateway not running | Set to `http://localhost:8000` for the LiveKit transport (`ws://localhost:8000/ws/exotel` only for the legacy WebSocket transport); start gateway |
| Microphone permission denied | Browser blocked mic | Click the site's lock icon → allow microphone; refresh |
| No assistant audio | AudioContext suspended / browser blocked autoplay | Interact with the page (click) to resume AudioContext |
| "Trouble understanding" | Gemini/LLM not configured or transient failure | Check `LLM_API_KEY` set; retry |
| Practice info returns nothing | `PRACTICE_ID` not set or mismatched | Set `PRACTICE_ID=demo-practice-001` in root `.env` |
| WebSocket close 1008 | Auth required | `WS_AUTH_TOKEN` unset for local dev (simulation mode) |
| WebSocket close 1011 | Speech provider misconfigured | Check Azure credentials; readiness endpoint reports the blocker |

---

## 7. What is NOT demonstrated here

- Real telephone / Exotel / PSTN call (out of scope)
- Acoustic barge-in measured with a real microphone (requires browser + mic)
- End-to-end speech-end→first-audio latency (requires browser timing)

If any of these steps cannot be completed because the environment lacks a
browser or microphone, that step remains **unverified** — do not claim it
passed.
