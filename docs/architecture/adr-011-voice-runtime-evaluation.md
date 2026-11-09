# ADR-011 — Voice Runtime Evaluation (LiveKit alternative)

**Date:** 2026-11-08
**Status:** Proposed (evaluation only — no migration)
**Deciders:** EMMA engineering
**Context:** Day 11 considered whether to migrate EMMA's browser voice runtime
from the current FastAPI WebSocket + Azure Speech stack to a LiveKit-based
WebRTC stack. This document records the evaluation and recommendation. **No
migration is performed.**

---

## 1. Context

EMMA Day 10 implements a browser voice receptionist:

```
Next.js browser UI
      |
      v (WebSocket /ws/exotel)
FastAPI Voice Gateway
      |
      +-- Azure STT (push-stream PCM16 16 kHz)
      |
      +-- Gemini gemini-3.8-flash (LLM interpreter)
      |
      +-- Deterministic ConversationController (emergency-first)
      |
      +-- Gemini embeddings + Qdrant (practice-scoped RAG)
      |
      +-- Azure TTS (PCM_L16 16 kHz mono)
      |
      v (base64 PCM16 media + control events)
Browser audio playback (pcm-player.ts, Web Audio)
```

The browser transport captures mic audio via `getUserMedia` + AudioWorklet,
resamples to 16 kHz PCM16 mono (640 bytes / 20 ms frame), sends it over
WebSocket, and plays assistant audio via Web Audio. Barge-in is implemented at
two layers: browser playback cancellation + `barge-in` control event, and
server-side energy-gated TTS cancellation (PCM16 RMS threshold + 40 ms gate).

Day 11 could not execute real browser/microphone testing (no browser binary or
automation tooling in this environment), so browser audio reliability and
acoustic barge-in remain unverified.

---

## 2. Options compared

### Option A — Current approach (retained)

```
Next.js browser UI
      |
      v WebSocket
FastAPI Voice Gateway
      |
      +-- Azure STT
      |
      +-- Gemini + deterministic controller
      |
      +-- Qdrant RAG / NestJS backend
      |
      +-- Azure TTS
      |
      v Web Audio playback
```

**Properties:**
- Browser audio reliability: WebSocket + PCM16 framing; proven protocol but
  browser-side reliability (autoplay, AudioContext suspension, network jitter)
  unverified without a browser test.
- Turn detection: Azure STT end-of-utterance + silence; server-controlled.
- Interruption: browser playback stop + `barge-in` control event +
  server energy-gated TTS cancel; acoustic barge-in unverified.
- Streaming: real-time PCM16 20 ms frames both directions.
- Operational complexity: low — single FastAPI process, existing Azure/Gemini/
  Qdrant/NestJS providers, no new infrastructure.
- Migration work: none (already implemented).
- Cost: existing Azure F0-eligible speech + Gemini; no new paid plans.
- Provider flexibility: Azure STT/TTS + Gemini; swapping requires adapter work
  but the provider boundary is isolated (`app/speech/`, `app/integrations/llm`,
  `app/integrations/embeddings`, `app/integrations/vector_store`).
- Existing code reuse: 100% — the entire Day 1–11 stack.
- Telephone integration readiness: Exotel AgentStream-compatible WebSocket is
  already the transport; PSTN remains out of scope.

### Option B — LiveKit alternative (evaluated, not adopted)

```
Next.js browser UI
      |
      v WebRTC (LiveKit client)
LiveKit Server
      |
      +-- LiveKit Agents
      |
      +-- STT provider (e.g. Deepgram Flux, or Azure)
      |
      +-- LLM (Gemini or another)
      |
      +-- Existing controller, RAG, backend
      |
      +-- TTS provider (e.g. ElevenLabs, or Azure)
      |
      v WebRTC audio playback
```

**Properties (from public documentation and pricing, no API calls made):**
- Browser audio reliability: WebRTC is generally more resilient to jitter and
  handles audio routing natively; requires LiveKit server + client SDK.
- Turn detection: LiveKit Agents has built-in VAD + turn detection; reduces
  custom silence/endpointing logic.
- Interruption: native WebRTC client-side barge-in + agent interruption;
  acoustic barge-in more straightforward but still requires real testing.
- Streaming: WebRTC audio streams; lower-level frame management handled by
  LiveKit.
- Operational complexity: **high** — LiveKit server (self-hosted or cloud),
  Docker/MySQL for persistence, agent workers, client SDK integration.
- Migration work: **high** — replace WebSocket transport + pcm-player with
  LiveKit client, re-frame audio for WebRTC, wire LiveKit Agents to the
  existing controller/RAG/backend, potentially new STT/TTS providers.
- Cost: LiveKit cloud or self-hosted infra; Deepgram Flux / ElevenLabs are paid
  providers (no sign-ups, no API calls made for Day 11).
- Provider flexibility: LiveKit is agnostic but integrating a new STT/TTS/LLM
  is agent-worker work; Azure/Gemini can be retained but adds abstraction.
- Existing code reuse: controller, RAG, NestJS backend, emergency-first guard
  reusable; transport + playback layer rewritten.
- Telephone integration readiness: LiveKit has SIP ingress/egress for PSTN;
  better long-term fit if telephone integration becomes a hard requirement.

---

## 3. Comparison table

| Criterion | Current (FastAPI WS + Azure) | LiveKit alternative | Winner |
|---|---|---|---|
| Browser audio reliability | Unverified (no browser test) | Likely better (WebRTC) | LiveKit* |
| Turn detection | Custom STT end-of-utterance | Built-in VAD + turn detection | LiveKit |
| Interruption / barge-in | 2-layer (browser + server); acoustic unverified | Native client + agent; still needs testing | LiveKit* |
| Streaming | PCM16 20 ms frames over WS | WebRTC audio streams | Comparable |
| Operational complexity | Low | High (LiveKit server + agents) | Current |
| Migration work | None | High | Current |
| Cost | Existing Azure + Gemini | LiveKit + possible Deepgram/ElevenLabs | Current |
| Provider flexibility | Good (isolated adapters) | Good (but new integration work) | Comparable |
| Existing code reuse | 100% | Transport layer rewritten | Current |
| Telephone (PSTN) readiness | Exotel-compatible WS; PSTN out of scope | LiveKit SIP | LiveKit* |

\*LiveKit advantages are theoretical for this prototype — acoustic barge-in and
browser reliability remain unverified for both stacks until tested in a real
browser.

---

## 4. Deepgram Flux and ElevenLabs (optional providers)

Evaluated as **candidate STT/TTS providers only**; no sign-ups, no API calls,
no paid plans.

- **Deepgram Flux:** streaming STT with endpointing; would replace Azure STT
  behind the existing `app/speech` provider boundary. Pricing from public docs;
  not adopted.
- **ElevenLabs:** low-latency TTS; would replace Azure TTS behind the existing
  `app/speech` provider boundary. Pricing from public docs; not adopted.

Both are viable provider swaps within the current architecture — no LiveKit
migration required to use them.

---

## 5. Recommendation

**Do not migrate EMMA to LiveKit during Day 11 (or without a separate
decision).** Keep the existing Gemini + Qdrant + NestJS + emergency-first
controller architecture with the FastAPI WebSocket transport.

Rationale:

1. The current stack is fully implemented and its AI core is verified with real
   providers. Migration would discard working, verified code.
2. The alleged LiveKit advantages (better browser audio, native barge-in, VAD)
   are **unverifiable in this environment** (no browser) and would still require
   real browser testing after migration — so they do not de-risk Day 11.
3. Migration cost is high (LiveKit server, agent workers, client SDK,
   possible new paid STT/TTS providers, Docker/MySQL) with no compensating
   benefit for a prototype whose telephone integration is out of scope.
4. The provider boundary is already isolated; Deepgram Flux / ElevenLabs can be
   evaluated as provider swaps without changing the runtime.

**Reassess LiveKit only if** telephone (PSTN/SIP) integration becomes a hard
requirement, at which point a dedicated spike with real browser testing should
be run first.

---

## 6. Consequences

- EMMA remains on FastAPI WebSocket + Azure Speech + Gemini + Qdrant + NestJS.
- Browser audio reliability and acoustic barge-in remain **unverified** pending
  a browser-capable environment (see `docs/runbooks/browser-voice-demo.md`).
- No new paid providers or subscriptions are introduced.
- The LiveKit option is documented here for future consideration, not adopted.
