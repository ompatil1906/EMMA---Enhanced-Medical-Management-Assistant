# ADR-012 — Voice Architecture: LiveKit + Deepgram + OpenAI + ElevenLabs

**Date:** 2026-11-08
**Status:** Proposed (implementation in progress — live verification pending credentials)
**Deciders:** EMMA engineering
**Supersedes:** ADR-011 (which recommended against migration; Day 12 proceeds with
migration per explicit user selection).

---

## 1. Context

EMMA Day 10/11 implemented a browser voice receptionist on the FastAPI WebSocket
+ Azure Speech + Gemini stack. The AI core was verified with real providers, but
browser audio reliability, acoustic barge-in, and live browser sessions remained
unverified (no browser binary or automation in the Day 11 environment).

The user has explicitly selected the advanced voice architecture for Day 12:

| Component | Primary technology | Fallback |
|---|---|---|
| Real-time voice runtime | LiveKit Agents | FastAPI WebSocket |
| Browser audio transport | LiveKit WebRTC | WebSocket `/ws/exotel` |
| Speech-to-text | Deepgram Flux | Azure Speech |
| LLM | OpenAI GPT-6 Luna | Gemini `gemini-3.8-flash` |
| Text-to-speech | ElevenLabs Flash v2.5 | Azure Speech |
| Embeddings | Gemini Embedding 2 | (unchanged) |
| Vector database | Qdrant local | (unchanged) |
| Conversation controller | Existing deterministic | (unchanged) |
| Backend | Existing NestJS | (unchanged) |
| Frontend | Existing Next.js | (unchanged) |

OpenAI budget: $2.50 total (dev $0.50, regression $0.50, demo reserve $0.75,
emergency reserve $0.75). GPT-6 Luna only — no Realtime, no transcription API,
no expensive fallback.

---

## 2. Target architecture

```
Next.js Browser
      |
      v (LiveKit WebRTC, if VOICE_TRANSPORT=livekit)
LiveKit Room
      |
      v
LiveKit Agent
      |
      +-- Deepgram Flux (STT)
      |
      +-- Emergency Guard (runs first)
      |
      +-- OpenAI GPT-6 Luna (LLM interpreter)
      |
      +-- Deterministic ConversationController
      |
      +-- Gemini Embeddings + Qdrant (RAG)
      |
      +-- NestJS Practice Backend
      |
      v
ElevenLabs Flash v2.5 (TTS)
      |
      v (LiveKit WebRTC audio)
Browser Speaker
```

**Fallback path (unchanged, always available):**

```
Next.js Browser
      |
      v (WebSocket /ws/exotel)
FastAPI Gateway
      |
      +-- Azure STT
      |
      +-- Gemini LLM
      |
      +-- Controller → Qdrant → NestJS
      |
      +-- Azure TTS
      |
      v Web Audio
Browser Speaker
```

---

## 3. Design rules

### 3.1 Preserved invariants

* The deterministic ConversationController is the single source of truth for
  administrative request state — no second state machine.
* The emergency guard runs before OpenAI, Gemini embeddings, Qdrant, and the
  backend.
* The LLM is an interpreter only — never makes clinical, booking, or backend
  decisions.
* Real providers fail fast on missing credentials — no silent mock fallback.
* Application-level OpenAI budget guard caps development spending.

### 3.2 LiveKit integration

* LiveKit credentials (API key/secret) never reach the browser.
* Room tokens are minted server-side with short expiry and trusted practice
  identity.
* The browser cannot choose another practice's identity.
* `VOICE_TRANSPORT=livekit` enables the WebRTC path; `websocket` keeps the
  existing path. Both are never active simultaneously.

---

## 4. Comparison with Day 11 baseline

| Criterion | Day 11 (WebSocket + Azure + Gemini) | Day 12 (LiveKit + Deepgram + OpenAI + ElevenLabs) |
|---|---|---|
| Browser audio | WebSocket PCM16, unverified | LiveKit WebRTC, unverified (no browser) |
| STT | Azure push-stream (verified) | Deepgram Flux (not verified — no credentials) |
| LLM | Gemini (verified) | OpenAI GPT-6 Luna (not verified — no credentials) |
| TTS | Azure SoniaNeural (verified) | ElevenLabs Flash v2.5 (not verified — no credentials) |
| Barge-in | Energy-gated (unverified acoustically) | LiveKit native (unverified — no browser) |
| OpenAI cost | N/A | $2.50 budget guard |
| Code reuse | 100% | Controller/RAG/backend reused; transport rewritten |

---

## 5. Consequences

* New provider adapters: OpenAI LLM, Deepgram STT, ElevenLabs TTS.
* New budget guard module for OpenAI spending control.
* New LiveKit integration module (lazy imports).
* Existing Azure fallback path preserved unchanged.
* All existing tests pass (383 passed, 4 skipped).
* Live provider verification is blocked pending credentials — cannot claim
  real Deepgram/OpenAI/ElevenLabs/LiveKit operation.
* Browser WebRTC testing is blocked pending a browser binary.

---

## 6. Open questions

1. Is `gpt-6-luna` a real, accessible OpenAI model? Cannot verify without
   web access or API credentials. Must be confirmed before live testing.
2. Are Deepgram Flux and ElevenLabs Flash v2.5 available under free tiers?
   Cannot verify without credentials or web access.
3. Can LiveKit run locally without Docker? Not evaluated (no Docker available).
