# Voice pipeline

## 1. Inbound path

```
transport frame → submit_audio()
   ├─ convert_if_needed()        app/speech/audio_convert.py  (explicit, never silent)
   ├─ EnergyVad.process()        app/services/audio/vad.py    (20 ms RMS, adaptive)
   └─ stt.push_audio()           app/speech/{deepgram,azure,mock}_stt.py
```

`submit_audio()` never awaits a provider decision beyond the push itself, so a
slow STT connection cannot stall the transport. VAD runs inline (one RMS pass);
its `speech_started` transition schedules barge-in as a background task.

## 2. Turn admission (the Pipecat "turn stop" equivalent)

`VoicePipeline._admit_final()` → `TurnAssembler.note_final()` returns one of:

| decision | when | effect |
|---|---|---|
| `drop` | filler-only, duplicate within 2 s (and EMMA has not spoken since), stale sequence | metric only |
| `hold` | final looks mid-sentence **and** STT partials show the caller still streaming | released after `TURN_HOLD_MS` (capped) or merged into the next final |
| `process` | everything else | queued with a monotonic dispatch sequence |

Holding is deliberately conservative: with no partial evidence (single-shot
transports, the evaluation corpus) every final is processed immediately, so no
latency is added where it cannot help.

## 3. Conversation worker

```
turn queue (bounded, 4) ─► _turn_loop()
      └─ task = create_task(_process_turn(utterance))   # cancellable
```

`_process_turn()` order:

1. FSM `LISTENING → PROCESSING`, record the turn
2. greeting / identity fast paths
3. low-confidence fallback
4. `emergency_guard()` — always before anything with a side effect
5. deterministic clinical refusal (no model call)
6. deterministic small talk
7. slot fill → LLM interpretation (bounded context)
8. controller decision → RAG / general knowledge / clarify / confirm
9. TTS → outbound framer → transport queue

Everything after step 4 is inside a cancellable task, which is what makes
interruption-during-processing possible.

## 4. Outbound path

`OutboundAudioFramer` accumulates PCM16 and emits 32 kB chunks (3.2 kB floor,
odd-byte tail dropped with a counter). Control events (`transcript`,
`assistant`, `state`, `barge-in`) share the queue so text always precedes its
audio.

## 5. Backpressure and limits

| Queue | Bound | On overflow |
|---|---|---|
| turn queue | 4 | oldest pending turn is dropped (counted) |
| outbound queue | unbounded by design (framed, cancelled on interruption) | flushed on barge-in |
| tool calls | per-call timeout | `timeout` result, conversation continues |
| hold release | `TURN_HOLD_MS` + `TURN_MAX_HOLD_MS` | released as its own turn |

## 6. Verified behaviour

`tests/unit/test_turn_manager.py`, `tests/unit/test_vad.py`,
`tests/unit/test_day10_control_events.py`,
`tests/integration/test_speech_pipeline.py`,
`tests/evaluation/day9/*` (34 dialogue cases) and the Day-17 Playwright WebRTC
acceptance suite. Live-provider latency is **not** claimed (mock providers only).
