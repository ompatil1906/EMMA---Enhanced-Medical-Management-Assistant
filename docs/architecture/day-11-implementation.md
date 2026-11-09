# Day 11 — Implementation

**Report date:** 2026-11-08 (Day 11 close-out).
**Git branch:** `main`
**Git HEAD:** `3d40b9409b635623f22ce1cd0220f1f23f647572`
**Status:** All work uncommitted; no commits or pushes made.
**Scope:** Day 11 is an acceptance-testing and reliability phase, not an
architecture rewrite. No new frontend, gateway, conversation controller, LLM
provider, embedding provider, vector store, or speech provider was created.
The existing Day 10 stack is preserved unchanged.

---

## 1. What changed

### 1.1 Configuration fix: `PRACTICE_ID` (`.env`)

`PRACTICE_ID` was empty in the root `.env`. The speech pipeline passes
`settings.PRACTICE_ID or ""` to Qdrant retrieval, so an empty value meant
practice-scoped retrieval returned no chunks and grounded practice-information
answers could not be produced.

Change: set `PRACTICE_ID=demo-practice-001` in the root `.env`, matching the
ingested knowledge base (`apps/voice-gateway/data/knowledge_base/demo-practice/`,
18 vectors for `demo-practice-001` in local Qdrant).

This is a non-secret practice identifier, not a credential.

### 1.2 No code changes to the speech stack

The Day 10 implementation (`azure_stt.py`, `voice_pipeline.py`,
`websocket.py`, `gateway-transport.ts`, `pcm-player.ts`) was preserved exactly.
No provider was replaced; no new architecture was introduced.

---

## 2. What was verified (real providers, local gateway)

The voice gateway was started locally on `http://127.0.0.1:8000` and the
speech pipeline was exercised end-to-end over WebSocket with real providers
(Azure STT → emergency guard → Gemini → Qdrant → controller → Azure TTS).

### 2.1 Local startup

| Check | Result |
|---|---|
| `uvicorn app.main:app --host 127.0.0.1 --port 8000` | Started successfully |
| `/health/live` | `{"status":"alive", ...}` |
| `/health/ready` | `{"status":"ready", ...}` — `speech_provider_configured: true`, `voice_pipeline: speech`, `stt_provider: azure`, `tts_provider: azure`, `rag_configured: true` |
| Provider readiness | Azure speech credentials configured; `EXOTEL_MODE=simulation` (no Exotel credentials needed locally) |

### 2.2 Azure STT (real, live)

- Exact final transcript: `"I would like to request an appointment next Wednesday morning."` — matched spoken input, delivered exactly once
- First final: ~2.7–3.2 s; first partial: ~1.9–2.4 s
- Multi-utterance, stop/flush, `EndOfStream` non-fatal (carried forward from Day 10 live verification)

### 2.3 Azure TTS (real, live)

- PCM_L16, 16 kHz, mono, `en-GB-SoniaNeural`
- First chunk ~0.9–1.7 s; synthesis totals match chunk/byte counts; `cancelled=False`

### 2.4 Gemini + Qdrant grounded answer (real, live) — newly verified with `PRACTICE_ID` set

Utterance: `"What time does the practice open on Monday?"`

- Final transcript: `"What time does the practise open on Monday?"`
- Grounded assistant answer:
  > "On Monday, the practice opens at 8:00 AM [1].\n\nSources:\n[1] Riverbend Medical Practice — Opening Hours, Section: Riverbend Medical Practice — Opening Hours"
- Real Gemini embedding + Qdrant retrieval (practice-scoped, `demo-practice-001`), citation `[1]` included
- Spoken via Azure TTS

---

## 3. What remains deliberately unverified (no browser/microphone access)

The coding environment has **no browser binary** (Chrome, Chromium, Firefox) and
**no browser automation tooling** (Playwright, Puppeteer, WebDriver). Therefore
the following could NOT be executed and are reported as unverified, not as passes:

| Item | Status | Reason |
|---|---|---|
| Real microphone capture | UNVERIFIED | No browser, no microphone access |
| Live browser session | UNVERIFIED | No browser binary |
| Browser audio playback | UNVERIFIED | No browser to render AudioContext |
| Acoustic barge-in | UNVERIFIED | No real microphone / assistant audio |
| Synthetic browser microphone input | UNVERIFIED | No browser |
| Full multi-turn browser session | UNVERIFIED | No browser |
| End-to-end speech-end→first-audio latency | UNVERIFIED | Requires client-side playback timestamping in a browser |

The browser UI code (`gateway-transport.ts`, `pcm-player.ts`, `voice-screen.tsx`)
compiles, type-checks, and builds cleanly, but that does **not** prove browser
or microphone operation.

---

## 4. Deliverables

| Deliverable | Status |
|---|---|
| `docs/architecture/day-11-implementation.md` | this file |
| `docs/architecture/day-11-engineering-report.md` | written (sections A–O) |
| `docs/runbooks/browser-voice-demo.md` | written (manual demo procedure) |
| `docs/architecture/adr-011-voice-runtime-evaluation.md` | written (LiveKit evaluation, no migration) |
