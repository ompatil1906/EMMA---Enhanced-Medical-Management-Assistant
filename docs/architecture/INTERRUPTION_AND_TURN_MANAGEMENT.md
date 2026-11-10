# Interruption and turn management

## 1. Two independent interruption detectors

| Detector | Signal | Latency | Module |
|---|---|---|---|
| **VAD** | caller loudness above an adaptive threshold | ~180 ms of voiced audio | `app/services/audio/vad.py` |
| **Transcript** | a new final (or a barge-in data message from the browser) | STT endpointing | `app/services/voice_pipeline.py` |

Either detector calls the same `_interrupt_in_flight()` path, so behaviour is
identical regardless of which fires first.

## 2. `_interrupt_in_flight()` order (matters)

1. If a synthesis is in flight → `cancel_tts()` (flushes queued audio, asks the
   provider to cancel, emits a `barge-in` control event).
   Otherwise → `_discard_pending_audio()`: drops queued **audio** frames only,
   preserving transcript/state control events.
2. Cancel the current turn task (`TURN_INTERRUPT_ENABLED`).
3. Repair the FSM: `SPEAKING|PROCESSING → LISTENING`.
4. Record interruption latency (`vad.barge_in_latency_ms` for VAD-triggered,
   `turns.interruption_latency_ms` for transcript-triggered).

Step 1 before step 3 is what guarantees "stale audio cannot continue after an
interruption": the framer is discarded and the transport queue holds no audio
for the cancelled reply.

## 3. Race protection

| Race | Guard |
|---|---|
| interruption during `_speak`'s exit transition | `_speak` only transitions if still `SPEAKING`; otherwise records `interrupted_speech` |
| two finals for one utterance | duplicate-final drop (same normalised text, within 2 s, no reply between) |
| out-of-order finals | original STT sequence guard at admission + dispatch sequence guard in `_process_turn` |
| held fragment + new request | `pending_release` emits the fragment as its own turn **before** the new one |
| cancellation while holding `_turn_lock` | `async with` releases the lock; state repaired by the interrupter |
| teardown during a turn | `aclose()` cancels hold task → worker → in-flight turn, bounded |

## 4. Turn-completion heuristics (`turn_manager.py`)

`looks_incomplete()` returns true only for shapes that are clearly unfinished:

* trailing conjunction/preposition ("…for", "…and", "…the")
* explicit continuation phrases ("I mean", "hold on", "let me")
* trailing comma or ellipsis

It returns **false** for any terminal punctuation, questions, single-word
confirmations (`yes`, `no`), slot answers (`Tuesday`, `morning`, `3pm`), and
words shorter than three characters. Holding additionally requires fresh STT
partials consistent with the final, which is what keeps the evaluation corpus
(no partials) latency-free.

## 5. Echo-safety

While the session is `SPEAKING`, the VAD start threshold is raised by
`VAD_ECHO_GUARD_DB` (6 dB default). Browser echo cancellation is never perfect;
this keeps EMMA from interrupting itself while still allowing a clearly louder
human voice through.

## 6. Verified

* `tests/unit/test_turn_manager.py` — 38 cases (hold, merge, release, filler,
  duplicate, stale, slot answers).
* `tests/unit/test_vad.py` — 14 cases (hysteresis, adaptive floor, echo guard,
  click rejection, natural pause).
* `tests/unit/test_day10_control_events.py` — audio flush vs control-event
  preservation.
* `tests/evaluation/day9` — confirmation/cancellation/multi-request flows still
  behave identically.

**Not verified:** acoustic (real-microphone, real-speaker) barge-in. The
instrumentation exists (`vad.barge_in_latency_ms`); the measurement requires a
physical room.
