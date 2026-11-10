# Pipecat porting changelog

**Milestone:** advanced real-time voice upgrade. **Date:** 2026-11-10.
**Licensing:** no Pipecat source was copied. Every item below is an independent
implementation of a documented architectural pattern; EMMA remains
QuantumLoopAI's own receptionist, not a Pipecat application. Pipecat itself is
BSD-2-Clause (`Copyright (c) 2024–2026, Daily`); nothing from it is vendored here.

## Added

| File | What | Pattern source | Status |
|---|---|---|---|
| `app/services/conversation/turn_manager.py` | Δturn stop: filler/duplicate drop, hold+merge, bounded release, stale guard | `turns/user_stop/*`, deferred finalisation | implemented + tested (38) |
| `app/services/audio/vad.py` | Energy VAD: adaptive floor, hysteresis, min-speech, end-silence, echo guard | `VADProcessor` / Silero analyzer | implemented + tested (14) |
| `app/services/tools/{base,registry,executor,builtin}.py` | Typed tool contract, permission/confirmation gate, timeout, idempotent replay, audit, LLM declarations | function-calling (`FunctionSchema`, `@tool`) | implemented + tested (15) |
| `app/services/observability.py` | Bounded per-session latency registry + percentiles | `UserBotLatencyObserver` | implemented + tested (7) |
| `app/services/conversation/context_manager.py` | Hard context budget + deterministic digest (opt-in) | `LLMContextAggregatorPair` summarisation | implemented + tested (7) |
| `app/api/routes/diagnostics.py` | `GET /api/v1/tools`, `GET /api/v1/diagnostics` | observer/metrics surface | implemented + tested |
| `app/services/caller_identity.py` | Sanitised intake identity → "Hello, {FirstName}" | — (product requirement) | implemented + tested |
| `app/services/call_summary.py` | Deterministic end-of-call summary + tasks | — (product requirement) | implemented + tested |
| `app/services/conversation/general_conversation.py` | Everyday small talk + bounded general knowledge with clinical refusal | — (product requirement) | implemented + tested |

## Changed

| File | Change |
|---|---|
| `app/services/voice_pipeline.py` | STT consumer → turn assembler → bounded turn queue → cancellable conversation worker; VAD early barge-in; audio-only flush preserving control events; FSM-safe `_speak` exit; latency stamps; registry publication at teardown |
| `app/services/metrics.py` | Added LLM/TTS/first-audio/interruption/VAD/turn latency series |
| `app/services/conversation/conversation_controller.py` | Single-source submission reply (`submission_reply()`), truthful failure wording restored |
| `app/core/config.py` | `TURN_*`, `VAD_*`, `LLM_CONTEXT_*` settings with validated bounds |
| `app/main.py` | Diagnostics router |
| `tests/evaluation/day9/__init__.py`, `tests/unit/test_livekit_integration.py`, `tests/unit/test_day10_control_events.py` | Tests synchronise on the conversation worker (`wait_for_turn_idle`) |
| `docs/*` | Nine architecture/testing/operations/implementation documents |

## Deliberately not ported

| Pipecat capability | Why |
|---|---|
| Frame bus / `FrameProcessor` graph | EMMA's orchestrator + queue is simpler and already testable; a frame bus would rewrite the hot path without a measurable win |
| Multi-agent workers / job bus | one deterministic controller is cheaper and lower latency for a receptionist |
| LLM-based turn classifier ("smart turn") | paid/latency cost; deterministic heuristics + VAD cover the observed patterns |
| Emotive TTS / perception | requires paid models or unvalidated heuristics; risks latency and STT quality |
| Pipecat Flows | the request FSM in `ConversationController` already provides explicit states/transitions |
| OpenTelemetry | exporter + collector infra with no current consumer; snapshot shape is OTel-ready |

## Behaviour changes worth knowing

1. A new caller utterance now **cancels** in-flight work instead of queueing
   behind it (`TURN_INTERRUPT_ENABLED=true`).
2. Assistant playback stops on **VAD speech start**, not on the STT final.
3. The spoken submission reply no longer claims an appointment was scheduled.
4. Escalation (`request_human_handoff`) runs through the tool executor
   (arrival-ordered audit, timeout, permission record).
