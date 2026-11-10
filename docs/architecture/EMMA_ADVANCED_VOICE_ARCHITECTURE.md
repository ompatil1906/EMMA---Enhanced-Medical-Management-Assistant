# EMMA advanced voice architecture

**Status:** implemented and tested offline. Live-provider tuning is not verified
(no paid credentials were used in this milestone).

## 1. Layers

```
Browser (Next.js) ── LiveKit WebRTC ──┐
Browser (Next.js) ── WS /ws/exotel ───┤
                                      ▼
                        ┌──────────────────────────────┐
                        │  Transport (token, room, WS) │
                        └──────────────┬───────────────┘
                                       ▼
   ┌───────────────────────────────────────────────────────────────────┐
   │ VoicePipeline (one per session)                                   │
   │                                                                   │
   │  inbound audio ─► resample/normalise ─► EnergyVad ─► STT provider │
   │                                            │            │         │
   │                                    speech_started   partials/     │
   │                                            │         finals       │
   │                                            ▼            ▼         │
   │                                  early barge-in   TurnAssembler   │
   │                                  (flush + cancel) (drop/hold/merge)│
   │                                                         │         │
   │                                                         ▼         │
   │                              turn queue ─► conversation worker    │
   │                                                         │         │
   │   emergency guard ─► clinical refusal ─► small talk ─► controller │
   │                                 ─► LLM interpret ─► RAG / general │
   │                                 ─► tool executor (write/escalate) │
   │                                                         │         │
   │                                            TTS ─► framer ─► queue │
   └───────────────────────────────────────────────────────────────────┘
                                       │
                         Practice API (calls, summaries, Jira mirror)
```

## 2. What each boundary owns

| Boundary | Module | Owns | Never does |
|---|---|---|---|
| Transport | `app/api/routes/*`, `app/integrations/livekit/*` | session setup, token minting, frame I/O | conversation decisions |
| Audio | `app/services/audio/vad.py`, `app/speech/audio_convert.py`, `app/services/audio_framer.py` | loudness, speech detection, resampling, chunk framing | provider calls |
| Turn | `app/services/conversation/turn_manager.py` | what counts as one caller turn | intent, actions |
| Conversation | `app/services/voice_pipeline.py` (worker), `conversation_controller.py` | ordering, FSM, confirmation, cancellation | clinical judgement |
| Intelligence | `app/services/conversation/*`, `app/services/retrieval/*` | interpretation, grounded answers, everyday conversation | persistence |
| Tools | `app/services/tools/*` | validated, permissioned side effects | speaking |
| Speech output | `app/speech/*` | synthesis + cancellation | text decisions |
| Observability | `app/services/metrics.py`, `observability.py` | latencies, counters | storing content |

## 3. Invariants preserved (unchanged from previous milestones)

1. `emergency_guard()` runs before the LLM, embeddings, Qdrant, tools and the
   backend; a match speaks the fixed 999 script and performs zero actions —
   including no end-of-call summary submission.
2. No backend persistence is claimed without a confirming 2xx; the spoken
   submission reply is `"I've passed your request to the practice team. This is a
   request — not a confirmed appointment."`
3. `practice_id` is server-authoritative; tool arguments cannot carry tenant ids
   (`extra="forbid"` rejects them).
4. One turn at a time per session; stale/duplicate finals are dropped.
5. Provider misconfiguration fails fast — no silent mock fallback.

## 4. Session lifecycle

```
start():      CONNECTING → SPEAKING(greeting) → LISTENING
turn:         LISTENING → PROCESSING → SPEAKING → LISTENING
interruption: SPEAKING|PROCESSING → LISTENING (audio flushed, turn cancelled)
teardown:     any → ENDED (idempotent; bounded summary submission)
```

## 5. Tunables (all optional, safe defaults)

| Setting | Default | Effect |
|---|---|---|
| `TURN_ASSEMBLY_ENABLED` | true | filler/duplicate drop, hold+merge |
| `TURN_HOLD_MS` / `TURN_MAX_HOLD_MS` | 700 / 4000 | how long a mid-sentence final waits |
| `TURN_INTERRUPT_ENABLED` | true | a new utterance cancels in-flight work |
| `VAD_ENABLED` / `VAD_BARGE_IN_ENABLED` | true | audio-level speech detection / early barge-in |
| `VAD_START_ENERGY_DB` / `VAD_END_ENERGY_DB` | -35 / -45 | speech thresholds (dBFS) |
| `VAD_END_SILENCE_MS` | 450 | silence before "speech ended" |
| `VAD_ECHO_GUARD_DB` | 6 | extra loudness required while EMMA speaks |
| `LLM_CONTEXT_MAX_CHARS` / `RECENT_TURNS` | 1400 / 8 | model context budget |
| `LLM_CONTEXT_INCLUDE_DIALOGUE` | false | opt-in transcript slice |
| `GENERAL_CHAT_ENABLED` | true | everyday-knowledge answers |
| `CALL_SUMMARY_ENABLED` | true | end-of-call summary + Jira mirror |
