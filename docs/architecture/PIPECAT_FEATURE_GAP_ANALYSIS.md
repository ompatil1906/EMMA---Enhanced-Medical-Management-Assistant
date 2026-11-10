# Pipecat ↔ EMMA feature gap analysis

**Date:** 2026-11-10 · **Scope:** EMMA voice assistant (`apps/voice-gateway`, `apps/ui`,
`apps/practice-api`).

**Method.** Upstream Pipecat and its ecosystem (pipeline/processors, service
abstractions, turn stop strategies, interruption frames, context aggregators,
function calling, observers/metrics, transports) were reviewed against the actual
EMMA tree — not against a README or an earlier milestone's claims. Every row was
classified by reading EMMA's code; anything unverified is marked as such.

Legend: **A** already implemented · **P** partial · **M** missing, implemented
now · **N/A** not applicable · **F** future / external dependency.

| # | Capability | Pipecat reference | EMMA before | Status | What EMMA now does |
|---|---|---|---|---|---|
| 1 | Frame/processor pipeline with explicit boundaries | `Pipeline`, `FrameProcessor` | One orchestrator with an outbound queue | **P** | Kept the orchestrator (rewriting the hot path for a frame bus was not justified), but split the boundaries explicitly: STT consumer → turn assembler → conversation worker → TTS framer. Documented in `VOICE_PIPELINE.md`. |
| 2 | VAD (audio-level speech start/stop) | `SileroVADAnalyzer`, `VADProcessor` | None (turn end came only from STT finals) | **M** | `app/services/audio/vad.py` — dependency-free energy VAD with adaptive noise floor, hysteresis, min-speech and end-silence windows, and an echo guard. Emits `speech_started`/`speech_ended`. |
| 3 | Immediate barge-in (stop playback on speech, not on transcript) | `InterruptionFrame` | Energy-gated cancel on the WS route + transcript-driven cancel | **M/P** | VAD `speech_started` now triggers `_interrupt_in_flight()` in the background; audio frames are flushed, control events preserved, in-flight turn cancelled, FSM repaired. |
| 4 | Turn stop strategies (deferred / smart-turn / speech timeout) | `turns/user_stop/*` | Transcript processing was immediate | **M** | `app/services/conversation/turn_manager.py`: filler drop, duplicate-final drop, hold+merge for mid-sentence finals, bounded hold release, stale-sequence guard. Deterministic — no paid turn classifier. |
| 5 | Interruption during LLM/processing (not just TTS) | Speculative turns + interruption frames | A second final was queued behind the running turn | **M** | Conversation worker owns a cancellable task per turn; a newer utterance cancels it (`TURN_INTERRUPT_ENABLED`), after flushing audio. |
| 6 | Streaming STT contract (partials/finals, provider-independent) | `STTService` | Already: `SpeechToTextProvider` + Azure/Deepgram/mock | **A** | Unchanged. Partials now also feed turn assembly. |
| 7 | Streaming TTS | `TTSService` + sentence aggregation | Providers stream chunks; sentence-level framing in the outbound framer | **A/P** | Providers stream; chunk framing/backpressure unchanged (32 kB target, 3.2 kB floor). Text normalisation *not* added (see #14). |
| 8 | Function/tool calling | `FunctionSchema`, `@tool`, `FunctionCallResultFrame` | None (three hard-coded backend calls in the pipeline) | **M** | `app/services/tools/`: `ToolSpec`/`ToolRegistry`/`ToolExecutor` with Pydantic validation (`extra="forbid"`), permissions (read/write/escalate), confirmation gate, per-call timeout, idempotent replay, audit trail, and LLM-facing JSON declarations. Escalation now runs through it. |
| 9 | Context aggregation + summarisation | `LLMContextAggregatorPair`, auto-summarisation | State-only context (transcript never replayed) | **M (opt-in)** | `app/services/conversation/context_manager.py`: hard char/turn budget, deterministic digest of older turns. **Off by default** because a Day-9 test pins "no transcript replay". |
| 10 | Observability (TTFB/TTFA, per-service) | `UserBotLatencyObserver`, `enable_metrics` | Per-session metrics, no aggregation | **M** | `SessionMetrics` now records final→processing, processing→first-audio, LLM, TTS, turn total, interruption and VAD barge-in latency, exposed as p50/p95 via `GET /api/v1/diagnostics`. No OTel dependency added (see `VOICE_OBSERVABILITY.md`). |
| 11 | Diagnostics/tool catalog surface | — | `/health`, `/health/ready` only | **M** | `GET /api/v1/tools` (catalog + function declarations, no handlers) and `GET /api/v1/diagnostics` (providers, tunables, latency percentiles, recent sessions). |
| 12 | LiveKit transport | `LiveKitTransport` | Custom LiveKit agent bridge (Day 14/17) | **A** | Preserved. Browser path verified by the Day-17 acceptance suite; no PSTN work. |
| 13 | Multi-agent / worker handoff | `LLMWorker`, `LLMWithBackend`, job bus | Single orchestrator | **F** | Deliberately not implemented: for a receptionist the deterministic controller is cheaper and lower-latency. Escalation is modelled as a tool (`request_human_handoff`). |
| 14 | Expressive speech / emotion perception | expression & perception modules | None | **F** | Not implemented: needs either a paid model or an unvalidated heuristic; would risk latency and could compromise STT accuracy. Documented as out of scope. |
| 15 | Pipecat Flows (declarative dialogue) | `FlowManager` | Deterministic controller FSM | **N/A** | The request lifecycle in `ConversationController` is already an explicit FSM with valid transitions, confirmation and cancellation. Adding a second flow engine would duplicate state. |
| 16 | Evaluation harness | `evals/` scenarios | Day 5/6/9 corpora + browser acceptance | **A/P** | 34-case Day-9 dialogue corpus, Day-5/6 RAG corpora, WebSocket integration suite, Playwright WebRTC acceptance. See `VOICE_EVALUATION.md` for measured latencies. |
| 17 | PSTN / Exotel / Twilio | `Transports` | Exotel-compatible WS fallback | **N/A (this milestone)** | Explicitly out of scope; the browser path is the verification target. |

## Cost / dependency impact

No new runtime dependency was introduced. VAD is arithmetic on PCM frames;
turn assembly, tools and context management are pure Python; observability is an
in-process bounded registry. Optional paid capabilities (emotion, turn classifier,
hosted metrics backend) were rejected for this milestone and are listed as future
work with their trade-offs.
