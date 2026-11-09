# Day 10 — Implementation

**Report date:** 2026-11-08 (Day 10 close-out).
**Git HEAD:** `3d40b9409b635623f22ce1cd0220f1f23f647572`
**Branch:** `main` (uncommitted; no commits or pushes made).
**Scope:** Close the remaining Day 10 integration gaps — real-time Azure STT
streaming, real Azure TTS, browser mic→STT→controller→TTS→playback loop, opt-in
UI control events, energy-gated barge-in — and validate end-to-end with real
providers. Preserves all Day 1–9 work; nothing is reset, discarded, or reverted.

---

## 1. What changed

### 1.1 `apps/voice-gateway/app/speech/azure_stt.py` (live-verified fix)

Root cause found in Day 9: the Azure SDK `SpeechRecognizer` was constructed with
the raw `PushAudioInputStream` as `audio_config`. The SDK accepts the stream
directly in the constructor but never delivers audio through it (0 partials,
0 finals). The recognizer must receive `AudioConfig(stream=push_stream)`.

Changes:

* `_build_stream_and_recognizer()` now wraps the push stream in
  `AudioConfig(stream=self._push_stream)` before constructing the recognizer
  (live-verified: exact transcript delivery).
* `stop()` closes the push stream **before** calling
  `stop_continuous_recognition_async()`, with a bounded grace window (~0.75s) so
  the SDK can flush the final transcript (live-verified: stop-immediately-after-
  audio still delivers the final).
* `_on_canceled()` treats `CancellationReason.EndOfStream` as normal teardown,
  not a fatal provider error (it fires when the push stream is closed during
  graceful `stop()`).
* Fatal cancellation now enqueues `RecognitionError` and `_STREAM_END` on the
  same loop turn via `loop.call_soon_threadsafe`, preserving event ordering.

### 1.2 `apps/voice-gateway/app/services/voice_pipeline.py` (Day 10 additions)

* New `ControlEvent` dataclass (transport-neutral UI control message carried on
  the outbound queue): `transcript` (partial/final), `assistant` text, `state`
  (thinking/speaking/listening), `barge-in`.
* `_emit_control(payload)` queues a control event only when the connection
  opted in (`control_events=True`); the verified Exotel `media` protocol is
  unchanged for provider traffic.
* `stream_outbound()` yields `AudioToSend | ControlEvent`, preserving queue
  position so assistant text precedes its audio.
* Partial/final transcripts emit control events; final text is delivered exactly
  once.
* `_speak()` emits `assistant` + `speaking` state before TTS, and `listening`
  state in the `finally` block.
* `cancel_tts()` emits a `barge-in` control event after discarding queued audio.

### 1.3 `apps/voice-gateway/app/api/routes/websocket.py` (Day 10 additions)

* New `?control-events=1` query option: when present, the speech pipeline emits
  JSON control events alongside `media` envelopes on the same WebSocket.
  Provider (Exotel) traffic never sets this, so the verified media protocol is
  byte-compatible.
* **Bug fix:** `_build_speech_pipeline()` now passes
  `llm_factory=state.llm_factory` to `VoicePipeline`. Without this the pipeline
  could not interpret utterances (every turn fell back to "trouble
  understanding"); live-verified fixed.
* **Energy-gated barge-in:** a continuously streaming browser mic sends silence
  too, so "any inbound frame while speaking" would cut off every reply. Inbound
  PCM16 RMS is measured (`_pcm16_rms`, threshold ~250, ~40 ms energy gate);
  genuine caller energy while the assistant is speaking cancels TTS. Silence and
  low-level noise do not interrupt a reply.
* `ExotelUnknownEvent` with `event == "barge-in"` triggers browser-initiated
  barge-in cancel (Exotel never sends this; it is browser-only).

### 1.4 New unit tests

* `tests/unit/test_azure_stt_lifecycle.py` — 6 fake-SDK tests: AudioConfig
  wrapping, final delivered once, EndOfStream non-fatal, real cancellation
  fatal, stop closes stream before stopping recognition, post-stop push
  ignored.
* `tests/unit/test_day10_control_events.py` — 5 tests: control events disabled
  by default, assistant text precedes audio, partial+final emitted exactly once,
  barge-in emits control event after discarding audio, PCM16 RMS separates
  silence from speech.
* `tests/integration/test_speech_pipeline.py` — added control-events opt-in
  integration test (`?control-events=1` surfaces assistant/state events without
  changing media envelopes).

### 1.5 `apps/ui/src/lib/voice/pcm-player.ts` (new)

Web Audio PCM16 mono player: base64 decode → AudioBuffer → scheduled
back-to-back on one AudioContext timeline. `playBase64()` queues a chunk,
`stop()` cancels all scheduled and audible audio immediately (used by barge-in
and teardown), `level()` returns smoothed output RMS.

### 1.6 `apps/ui/src/lib/voice/gateway-transport.ts` (rewritten)

The live transport now:

* Connects with `?sample-rate=16000&control-events=1`.
* Sends `connected` → `start{media_format}` → 20 ms PCM16 `media` frames.
* Decodes base64 PCM16 `media` envelopes and plays them via `pcm-player`.
* Renders gateway `transcript` (partial/final), `assistant`, and `state`
  control events into the transcript UI.
* Browser-side barge-in: when mic level ≥ threshold for 2 consecutive ticks
  while audio is playing, stops local playback and sends `{event:"barge-in"}`.
* Emits `output-level` for the orb waveform during playback.

### 1.7 Supporting UI changes

* `apps/ui/src/lib/voice/types.ts`: docstring updated (live transport now
  renders agent speech/thinking/speaking); `output-level` no longer
  demo-only; `framesOut` doc clarified as assistant audio frames.
* `apps/ui/src/components/voice/voice-screen.tsx`: metrics notice text updated
  for live audio frames.

---

## 2. What was verified live (real providers)

| Provider | Path | Result |
|---|---|---|
| Azure STT | push-stream continuous, 16 kHz PCM16 mono | Exact final transcript, partials + finals, multi-utterance, stop flush, EndOfStream non-fatal |
| Azure TTS | 4 response types (short/grounded/clarification/emergency) | Valid PCM_L16 16 kHz mono; first-chunk 1.5–2.3 s; totals match chunks/bytes; cancelled=False |
| Gemini LLM | `gemini-3.8-flash` interpretation | Appointment extraction + confirmation read-back (~2.9 s) |
| Gemini embeddings + Qdrant | `gemini-embedding-2` 768-d, local Qdrant | Practice-scoped retrieval, grounded answer |
| E2E loop | mic PCM → STT → guard → Gemini → controller → TTS → playback | Turn 1 exact transcript + confirmation; Turn 2 practice-info grounded answer; clean close 1000 |

No credentials are printed, logged, or committed.

---

## 3. What remains deliberately unverified

* **Real microphone / acoustic barge-in:** the browser UI implements playback
  cancellation + a `barge-in` control event, and the server implements
  energy-gated server-side barge-in, but no real microphone capture or acoustic
  interruption was tested (no browser automation/microphone access in this
  environment). Reported as protocol-level + browser-playback cancellation only.
* **Browser end-to-end:** the UI code compiles and type-checks, but no live
  browser session was executed against the gateway.
* **Exotel/PSTN:** out of scope; not implemented or tested.
* **Docker/MySQL:** runtime unavailable; Qdrant local persistent mode used.

---

## 4. Bugs fixed (Day 10)

1. Azure STT recognizer received raw `PushAudioInputStream` instead of
   `AudioConfig(stream=...)` → no transcripts. Fixed (live-verified).
2. Azure STT `stop()` stopped recognition before closing the push stream →
   flushed final was dropped. Fixed: close stream first, bounded grace window.
3. `CancellationReason.EndOfStream` was surfaced as a fatal provider error
   during graceful teardown. Fixed: treated as normal teardown.
4. Fatal `RecognitionError` and `_STREAM_END` could be dequeued out of order.
   Fixed: enqueued on the same loop turn.
5. `_build_speech_pipeline()` did not pass `llm_factory`, so every interpreted
   turn failed with "trouble understanding". Fixed (live-verified).
6. Barge-in triggered on any inbound frame (including silence from a streaming
   mic). Fixed: energy-gated PCM16 RMS threshold.

---

## 5. Deliverables status

| Deliverable | Status |
|---|---|
| `docs/architecture/day-10-implementation.md` | this file |
| `docs/architecture/day-10-engineering-report.md` | written (A–J sections) |
| `docs/runbooks/azure-speech-setup.md` | written |
| `docs/runbooks/final-local-acceptance-testing.md` | written |
| `docs/architecture/final-system-architecture.md` | written |
| `README.md` | updated |
