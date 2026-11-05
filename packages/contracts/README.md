# contracts package (placeholder — Day 2)

Shared, language-agnostic contracts for the EMMA monorepo. Today this is a
placeholder directory documenting the contract that already exists in code:

* Media stream protocol (Exotel AgentStream wire contract): see
  `docs/integrations/exotel-protocol.md` — verified `connected / start / media /
  dtmf(V) / mark(V) / stop` inbound events and `media / mark / clear` outbound
  events, with base64 linear PCM audio.
* Internal domain events (`apps/voice-gateway/app/domain/events.py`):
  `CallStarted`, `AudioReceived`, `CallEnded`, `ConnectionClosed`, `AudioToSend`
  — the typed boundary any provider adapter must map into.

Day 3 plan: move any cross-service DTOs (NestJS practice backend ↔ voice
gateway ↔ dashboard) here as JSON Schemas or typed model definitions so both
sides validate against one source of truth.
