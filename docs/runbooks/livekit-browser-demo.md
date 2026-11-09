# LiveKit Browser Demo — EMMA Runbook

**Purpose:** Reproduce the EMMA browser voice receptionist using the LiveKit
WebRTC voice path. A real user can open the browser interface, grant microphone
access, speak, receive an AI-generated response, hear it through audio playback,
and continue a multi-turn conversation.

**Status:** Not demonstrated in this coding environment — no browser binary,
no LiveKit server, no LiveKit/Deepgram/ElevenLabs/OpenAI credentials. This
runbook documents the procedure for when those become available.

**Security:** Do not print, log, or commit credentials. `.env` is git-ignored.
Variable names only appear below — never values.

---

## 1. Prerequisites

* Python 3.13 + `uv` (voice-gateway virtualenv)
* Node.js + `pnpm` (frontend)
* A modern browser with WebRTC and microphone support (Chrome, Edge, Firefox,
  Safari)
* LiveKit server (self-hosted via `livekit-server` or LiveKit Cloud)
* Credentials for LiveKit, Deepgram, ElevenLabs, OpenAI (provided separately)

Environment variables by **name only** (values in ignored `.env` files):

* Voice gateway: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`,
  `DEEPGRAM_API_KEY`, `DEEPGRAM_MODEL`, `ELEVENLABS_API_KEY`,
  `ELEVENLABS_VOICE_ID`, `ELEVENLABS_MODEL`, `OPENAI_API_KEY`, `OPENAI_MODEL`,
  `VOICE_TRANSPORT`, `STT_PROVIDER`, `TTS_PROVIDER`, `LLM_PROVIDER`,
  `PRACTICE_ID`, `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`,
  `GEMINI_EMBEDDING_API_KEY`, `VOICE_PIPELINE`
* Frontend: `NEXT_PUBLIC_VOICE_GATEWAY_URL`, `NEXT_PUBLIC_VOICE_TRANSPORT`

Required non-secret values: `PRACTICE_ID=demo-practice-001`,
`VOICE_TRANSPORT=livekit`, `STT_PROVIDER=deepgram`,
`TTS_PROVIDER=elevenlabs`, `LLM_PROVIDER=openai`,
`OPENAI_MODEL=gpt-6-luna`, `DEEPGRAM_MODEL=flux-general-en`,
`ELEVENLABS_MODEL=eleven_flash_v2_5`.

For local LiveKit server setup, see
`docs/runbooks/livekit-self-hosted-free.md`.

---

## 2. Startup

### 2.1 Voice gateway

```bash
cd apps/voice-gateway
uv sync --all-groups          # once, to create the locked venv
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Verify: `curl http://127.0.0.1:8000/health/ready` → `{"status":"ready", ...}`
The readiness endpoint reports `speech_provider_configured`, `llm_provider_configured`,
and `rag_configured`.

### 2.2 LiveKit agent worker

The agent runs as a separate process that joins LiveKit rooms:

```bash
cd apps/voice-gateway
uv run python -m app.integrations.livekit.agent
```

This starts a LiveKit worker that connects to `LIVEKIT_URL` and joins rooms as
they are created. One agent process can handle multiple rooms sequentially.

Alternatively, use the LiveKit CLI:

```bash
uv run python -c "
from app.integrations.livekit.agent import create_worker_options
from livekit.agents import cli
cli.run_app(create_worker_options())
"
```

### 2.3 Frontend

```bash
cd apps/ui
pnpm install          # once
pnpm dev              # → http://localhost:3000
```

Open `http://localhost:3000` → `/voice`. The UI connects to
`NEXT_PUBLIC_VOICE_GATEWAY_URL` (the FastAPI gateway) for the token, then to
the LiveKit room for audio.

### 2.4 Practice API (optional)

The speech loop works without the practice backend
(`PRACTICE_API_BASE_URL` unset). To enable backend submission:

```bash
cd apps/practice-api
pnpm install          # once
pnpm dev              # local API on its configured PORT
```

---

## 3. Demo steps (user-executed)

1. **Open the UI:** `http://localhost:3000` → `/voice`.
2. **Enter caller details** on the intake form and tap "Call EMMA Reception".
3. **Grant microphone permission** when the browser prompts. The UI should show
   "Connected to EMMA voice agent" and the orb should react to input level.
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
     8:00 AM [1]..."), spoken via ElevenLabs TTS.
7. **Contextual follow-up:** "And what about Saturday?"
   - Expect: context-aware retrieval based on the previous topic.
8. **Emergency:** "I am having severe chest pain."
   - Expect: the fixed script spoken immediately:
     "This may be a medical emergency. Please hang up and call 999 now."
     - No appointment, no LLM-based interpretation, no backend action.
9. **Interrupt (barge-in):** while the assistant is speaking, begin speaking.
   - Expect: browser playback stops; a `barge-in` event is sent; your new
     utterance is processed.
10. **Disconnect:** tap "End Conversation"; the WebSocket/LiveKit session
    closes cleanly.

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
pkill -f "livekit.agent"
```

---

## 6. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| UI never shows "Connected" | Wrong gateway URL or gateway not running | Set `NEXT_PUBLIC_VOICE_GATEWAY_URL` to `http://localhost:8000`; start gateway |
| Token request fails (503) | LiveKit credentials not configured | Set `LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` |
| Microphone permission denied | Browser blocked mic | Click site lock icon → allow microphone; refresh |
| No assistant audio | AudioContext suspended | Interact with the page (click) to resume |
| "Trouble understanding" | LLM not configured or transient failure | Check `OPENAI_API_KEY`; retry |
| Practice info returns nothing | `PRACTICE_ID` not set or mismatched | Set `PRACTICE_ID=demo-practice-001` |
| Agent doesn't join room | LiveKit worker not running | Start `uv run python -m app.integrations.livekit.agent` |

---

## 7. What is NOT demonstrated here

* Real telephone / Exotel / PSTN call (out of scope).
* Acoustic barge-in measured with a real microphone (requires browser + mic).
* End-to-end speech-end→first-audio latency (requires browser timing).
* Live provider calls (require credentials not available in this environment).

If any step cannot be completed because the environment lacks credentials or a
browser, that step remains **unverified** — do not claim it passed.
