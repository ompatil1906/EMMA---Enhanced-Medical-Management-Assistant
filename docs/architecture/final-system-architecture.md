# EMMA — Final System Architecture

**Report date:** 2026-11-08 (Day 12 close-out).
**Git HEAD:** `3d40b9409b635623f22ce1cd0220f1f23f647572`
**Branch:** `main` (uncommitted).
**Status:** Prototype — not a production service, not a medical device, no
clinical advice, no real patient data.

---

## 1. Overview

EMMA is an AI voice-receptionist prototype for NHS GP practices. A caller
speaks through a browser; the voice gateway performs speech-to-text →
deterministic conversation control → LLM interpretation → optional RAG →
text-to-speech, streaming audio back to the browser. The LLM is an
**interpreter only** — it never makes emergency, booking, backend, tenant, or
access-control decisions.

Two transport paths exist:

* **Primary (Day 12):** LiveKit WebRTC → LiveKit Agents → Deepgram Flux STT →
  OpenAI GPT-6 Luna → Controller → Qdrant RAG → NestJS → ElevenLabs TTS →
  LiveKit WebRTC audio playback.
* **Fallback (Day 10/11, preserved):** FastAPI WebSocket `/ws/exotel` → Azure
  STT → Gemini LLM → Controller → Qdrant RAG → NestJS → Azure TTS → Web Audio
  playback.

The fallback path remains fully available via `VOICE_TRANSPORT=websocket`.

---

## 2. Component diagram — Primary path (LiveKit)

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Browser (Next.js UI)                             │
│  getUserMedia → LiveKit WebRTC client                               │
│  Microphone publishing → LiveKit room                               │
│  Assistant audio playback (LiveKit WebRTC)                          │
│  Live transcripts, assistant text, states rendered                  │
└──────────────┬──────────────────────────────────────────────────────┘
               │ WebRTC (LiveKit room)
               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 LiveKit Agent (apps/voice-gateway)                  │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  Room connection + participant lifecycle                      │  │
│  │  Mic subscription → Deepgram Flux STT                         │  │
│  │  Audio publishing ← ElevenLabs TTS                            │  │
│  │  Session lifecycle, reconnection, clean disconnect            │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                       │                             │
│                                       ▼                             │
│                            ┌──────────────────────┐                 │
│                            │  VoicePipeline       │                 │
│                            │  (per session)       │                 │
│                            │                      │                 │
│                            │  submit_audio()      │                 │
│                            │    → Deepgram Flux   │                 │
│                            │    (WebSocket        │                 │
│                            │     streaming)       │                 │
│                            │                      │                 │
│                            │  STT events:         │                 │
│                            │  • FinalTranscript   │                 │
│                            │    (intent; exactly once)│            │
│                            │  • PartialTranscript │                 │
│                            │  • RecognitionError  │                 │
│                            └──────────┬───────────┘             │
│                                       │                             │
│                            ┌──────────▼───────────┐                 │
│                            │  emergency_guard()   │◄── runs FIRST   │
│                            │  (deterministic)     │   before all    │
│                            └──────────┬───────────┘   providers/    │
│                                       │        backend              │
│                            ┌──────────▼───────────┐                 │
│                            │  OpenAI gpt-6-luna   │                 │
│                            │  (interpreter only   │                 │
│                            │   — LLMRequest)      │                 │
│                            └──────────┬───────────┘                 │
│                                       │                             │
│                            ┌──────────▼───────────┐                 │
│                            │  ConversationController│              │
│                            │  (deterministic):      │                 │
│                            │  • slot fill / missing │                 │
│                            │  • confirmation (lexical)│             │
│                            │  • correction + invalidate│           │
│                            │  • cancellation        │                 │
│                            │  • multi-request       │                 │
│                            │    independence        │                 │
│                            └──────────┬───────────┘                 │
│              ┌─────────────────────────┼─────────────────────────┐  │
│              │                         │                         │  │
│              ▼                         ▼                         ▼  │
│       ┌──────────────┐        ┌──────────────┐        ┌──────────┐  │
│       │ Gemini embed-│        │ Qdrant local │        │ NestJS   │  │
│       │ ings (768-d) │        │ persistent   │        │ practice │  │
│       │              │        │ (practice-   │        │ API      │  │
│       │              │        │ scoped RAG)  │        │ (optional)│ │
│       └──────────────┘        └──────────────┘        └──────────┘  │
│              │                         │                         │  │
│              └──────────┬─────────────┴─────────────┬─────────────┘  │
│                         │                             │              │
│                         ▼                             │              │
│              ┌──────────────────────┐                │              │
│              │  ElevenLabs Flash    │                │              │
│              │  v2.5 TTS (streaming)│                │              │
│              └──────────┬───────────┘                │              │
│                         │                             │              │
│              ┌──────────▼───────────┐                │              │
│              │  LiveKit Agent       │                │              │
│              │  publishes audio     │   control     │              │
│              │  to room (WebRTC)    │   events      │              │
│              └──────────────────────┘                               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Component diagram — Fallback path (WebSocket)

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Browser (Next.js UI)                           │
│  getUserMedia → AudioWorklet → 20 ms PCM16/16 kHz frames            │
│  pcm-player.ts: play base64 PCM16, stop() for barge-in              │
│  gateway-transport.ts: WS /ws/exotel?sample-rate=16000&control=1    │
│    • send connected/start/media                                     │
│    • render transcript / assistant / state control events           │
│    • browser barge-in: stop playback + send {event:"barge-in"}      │
└──────────────┬──────────────────────────────────────────────────────┘
               │ WebSocket (Exotel AgentStream-compatible)
               ▼
┌─────────────────────────────────────────────────────────────────────┐
│              FastAPI Voice Gateway (apps/voice-gateway)             │
│  ┌─────────────┐  authenticate  ┌───────────────────────────────┐  │
│  │ /ws/exotel  │───────────────►│  session registry + state     │  │
│  │             │                │  (voice_pipeline=speech|echo)  │  │
│  └─────────────┘                └───────────────────────────────┘  │
│                                       │                             │
│                                       ▼                             │
│                            ┌──────────────────────┐                 │
│                            │  VoicePipeline       │                 │
│                            │  (per session)       │                 │
│                            │                      │                 │
│                            │  submit_audio()      │                 │
│                            │    → Azure STT       │                 │
│                            │      (push-stream,   │                 │
│                            │       AudioConfig)   │                 │
│                            │                      │                 │
│                            │  STT events:         │                 │
│                            │  • FinalTranscript   │                 │
│                            │    (intent; exactly once)│            │
│                            │  • PartialTranscript │                 │
│                            │  • RecognitionError  │                 │
│                            └──────────┬───────────┘             │
│                                       │                             │
│                            ┌──────────▼───────────┐                 │
│                            │  emergency_guard()   │◄── runs FIRST   │
│                            │  (deterministic)     │   before all    │
│                            └──────────┬───────────┘   providers/    │
│                                       │        backend              │
│                            ┌──────────▼───────────┐                 │
│                            │  Gemini gemini-3.8   │                 │
│                            │  -flash (interpreter │                 │
│                            │   only — LLMRequest) │                 │
│                            └──────────┬───────────┘                 │
│                                       │                             │
│                            ┌──────────▼───────────┐                 │
│                            │  ConversationController│              │
│                            │  (deterministic):      │                 │
│                            │  • slot fill / missing │                 │
│                            │  • confirmation (lexical)│             │
│                            │  • correction + invalidate│           │
│                            │  • cancellation        │                 │
│                            │  • multi-request       │                 │
│                            │    independence        │                 │
│                            └──────────┬───────────┘                 │
│              ┌─────────────────────────┼─────────────────────────┐  │
│              │                         │                         │  │
│              ▼                         ▼                         ▼  │
│       ┌──────────────┐        ┌──────────────┐        ┌──────────┐  │
│       │ Gemini embed-│        │ Qdrant local │        │ NestJS   │  │
│       │ ings (768-d) │        │ persistent   │        │ practice │  │
│       │              │        │ (practice-   │        │ API      │  │
│       │              │        │ scoped RAG)  │        │ (optional)│ │
│       └──────────────┘        └──────────────┘        └──────────┘  │
│              │                         │                         │  │
│              └──────────┬─────────────┴─────────────┬─────────────┘  │
│                         │                             │              │
│                         ▼                             │              │
│              ┌──────────────────────┐                │              │
│              │  Azure TTS           │                │              │
│              │  (PCM_L16 16k mono)  │                │              │
│              └──────────┬───────────┘                │              │
│                         │                             │              │
│              ┌──────────▼───────────┐                │              │
│              │  WebSocket pump      │                │              │
│              │  → media envelopes   │                │              │
│              │  → JSON ctrl events  │                │              │
│              └──────────────────────┘                               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. Provider bindings

| Layer | Primary (Day 12) | Fallback (Day 10/11) |
|---|---|---|
| Voice runtime | LiveKit Agents | FastAPI WebSocket |
| Browser transport | LiveKit WebRTC | WebSocket `/ws/exotel` |
| STT | Deepgram Flux (`flux-general-en`) | Azure Speech (push-stream) |
| LLM | OpenAI `gpt-6-luna` | Gemini `gemini-3.8-flash` |
| TTS | ElevenLabs Flash v2.5 | Azure Speech (`en-GB-SoniaNeural`) |
| Embeddings | Gemini `gemini-embedding-2` | Gemini `gemini-embedding-2` |
| Vector store | Qdrant local persistent | Qdrant local persistent |
| Backend | NestJS practice API | NestJS practice API |

Real providers fail fast on missing credentials — no silent mock fallback.

---

## 5. Key invariants (safety)

1. **Emergency guard ordering:** `emergency_guard()` runs before the LLM,
   embeddings, Qdrant, and the backend. On match: fixed 999 script spoken,
   pending requests/confirmations invalidated, zero admin/backend calls.
2. **Explicit confirmation:** no backend submission without a lexical `confirm`;
   request marked `SUBMITTED` only after backend confirmation.
3. **LLM = interpreter only:** never an authority for emergency, booking,
   backend execution, lifecycle, tenant identity, or practice access control.
4. **Per-session turn lock + duplicate/out-of-order rejection:** one intent at
   a time; stale turns discarded.
5. **Practice-scoped retrieval:** `practice_id` is server-side; caller-supplied
   tenant fields are stripped.
6. **No silent fallback:** provider misconfiguration closes the session rather
   than silently substituting mocks.
7. **OpenAI budget guard:** application-level cap on GPT-6 Luna spending
   ($2.50 total; $1.00 dev cap; $0.75 demo reserve preserved).
8. **No clinical claims:** EMMA is not a triage system, not a medical device,
   and must never process real patient data.

---

## 6. Token endpoint (LiveKit only)

```
Browser
  |
  v POST /api/voice/token  {room_name: "..."}
FastAPI Gateway
  |
  v LiveKitTokenMinter (server-side)
  |
  v JWT + room + practice_id
  |
  v Browser connects to LiveKit room with JWT
```

* `LiveKitTokenMinter` mints short-lived JWTs using `livekit.api.AccessToken`.
* `practice_id` is bound server-side — the browser cannot choose another
  practice's identity.
* Returns 503 when `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` are not configured.
* Returns 400 for invalid room names, 422 for missing body.

---

## 7. What is NOT part of this architecture

* Exotel / PSTN calling (out of scope; not implemented).
* Docker / MySQL runtime (unavailable; SQLite dev/test, Qdrant local mode).
* Real microphone / acoustic barge-in testing (unverified; implemented at
  protocol + browser-playback level only).
* OpenAI Realtime audio API (not used).
* OpenAI transcription API (not used).
* Production-tier Azure resources or paid services (no subscription changes).
