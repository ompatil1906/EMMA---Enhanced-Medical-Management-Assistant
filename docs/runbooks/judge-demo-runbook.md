# Judge Demo Runbook — EMMA Day 12

**Purpose:** Reproducible local demonstration of the EMMA voice receptionist
for judge evaluation.

**Status:** Not demonstrated — no browser binary, no LiveKit credentials, no
OpenAI/Deepgram/ElevenLabs credentials available in this environment. This
runbook documents the procedure for when credentials and a browser become
available.

**Demo practice:** `demo-practice-001` (fictional Riverbend Medical Practice,
existing knowledge base).

---

## 1. Prerequisites

- Python 3.13 + `uv` (voice-gateway)
- Node.js + `pnpm` (frontend)
- A modern browser with microphone support
- Credentials for the selected transport/providers (see setup runbooks)

Environment variables by **name only** (never values):

- Voice gateway: `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`,
  `ELEVENLABS_VOICE_ID`, `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`,
  `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`, `GEMINI_EMBEDDING_API_KEY`,
  `PRACTICE_ID`, `VOICE_TRANSPORT`, `STT_PROVIDER`, `TTS_PROVIDER`, `LLM_PROVIDER`
- Frontend: `NEXT_PUBLIC_VOICE_GATEWAY_URL`

Required non-secret value: `PRACTICE_ID=demo-practice-001`.

---

## 2. Startup

### 2.1 Voice gateway

```bash
cd apps/voice-gateway
uv sync --all-groups
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Verify: `curl http://127.0.0.1:8000/health/ready` → `{"status":"ready", ...}`

### 2.2 Frontend

```bash
cd apps/ui
pnpm install
pnpm dev
```

Open `http://localhost:3000` → `/voice`.

---

## 3. Judge demo scenarios

### Scenario 1 — Appointment request

Caller: "I would like to request an appointment next Wednesday morning."

Expected:
- Exact transcript delivered once.
- EMMA asks for explicit confirmation ("preferred date: next Wednesday,
  preferred time: morning. Is that correct?").
- No backend submission before confirmation.

### Scenario 2 — Correction

Caller: "Actually, make that Thursday afternoon."

Expected:
- Previous confirmation invalidated.
- Corrected details read back.
- New explicit confirmation required.

### Scenario 3 — Practice information

Caller: "What time does the practice open on Monday?"

Expected:
- Grounded answer from Qdrant ("On Monday, the practice opens at 8:00 AM...").
- Citation source internally validated.
- Spoken via TTS.

### Scenario 4 — Multiple requests

Caller: "I need a repeat prescription and I also want an appointment."

Expected:
- Two independent requests; no cross-submission.

### Scenario 5 — Cancellation

Caller: "Cancel my appointment request."

Expected:
- Targeted request cancelled; cannot be re-submitted.
- Other requests remain intact.

### Scenario 6 — Emergency

Caller: "I am having severe chest pain."

Expected:
- Fixed 999 script spoken immediately.
- Zero OpenAI calls, zero Gemini embedding calls, zero Qdrant calls, zero
  ordinary backend submissions.

---

## 4. What is NOT demonstrated

- Real telephone / Exotel / PSTN call (out of scope).
- Acoustic barge-in measured with a real microphone (requires browser + mic).
- End-to-end speech-end→first-audio latency (requires browser timing).
- Live provider calls (require credentials not available in this environment).

---

## 5. Shutdown

```bash
pkill -f "uvicorn app.main:app"
pkill -f "next dev"
```
