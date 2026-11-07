# Day 3 — implementation architecture (streaming speech + practice backend)

Status: speech pipeline implemented & tested (207 automated tests green, lint clean);
practice backend scaffold in progress (see §12 for the honest verification state).

Scope today: **streaming speech pipeline (STT → FSM → deterministic response → TTS)**
on the existing Exotel gateway transport, plus the **NestJS practice backend +
MySQL persistence contract**, with the Python→NestJS typed client integrated.

This is the engineering companion to the Day 2 document; the Day 2 architecture and
all its tests are untouched.

---

## 1. What was built

```
apps/voice-gateway
├── app
│   ├── api/routes        /ws/exotel (echo + speech loops), /health/live|ready
│   ├── core              config (env), JSON logging w/ redaction, lifecycle/registry
│   ├── domain            provider-independent events, audio model, session book-keeping
│   │   ├── speech.py            normalized STT/TTS events (frozen)
│   │   ├── fsm.py              typed voice FSM + explicit transition table
│   │   └── speech_session.py  per-call speech session state (format lock, transcripts…)
│   ├── integrations/exotel  verified AgentStream adapter (schemas · codec · adapter)
│   ├── integrations/practice_api.py  typed async client → NestJS practice backend
│   ├── services          voice_pipeline (orchestration), response_handler, framer, metrics
│   └── speech            provider abstraction: base contracts, mock STT/TTS, audio_convert, azure adapters, factory
├── tests                 207 tests green (106 Day-2 + 101 new; unit + true-uvicorn integration)
├── pyproject.toml / uv.lock / Dockerfile
scripts/websocket_simulator.py

apps/practice-api        NestJS 11 + TypeORM backend (scaffolded; see §12)
infrastructure/docker    three-service compose (gateway + practice-api + MySQL)
docs/                    protocol verification · runbooks · this document
```

Design rules enforced and tested:

* **Provider-independent pipeline** — `SpeechToTextProvider` / `TextToSpeechProvider`
  protocols are implemented by `mock` and `azure` adapters; the pipeline, route and
  tests only ever see normalized events / chunk events.
* **Central factory, no silent fallback** — `SpeechProviderFactory` selects from
  validated settings; `STT_PROVIDER=azure`/`TTS_PROVIDER=azure` without
  `AZURE_SPEECH_KEY`/`AZURE_SPEECH_REGION` → `ProviderConfigurationError` → readiness
  degraded and session closed 1011. No silent mock fallback exists.
* **Deterministic FSM owns transitions** — every state change goes through
  `VoiceStateMachine.transition` against the explicit `VALID_TRANSITIONS` table;
  the LLM (Day 4) can only *choose the next target*, never mutate state.
* **Explicit audio handling** — formats are locked at session start; renegotiation
  after lock is rejected (1008 in speech mode); conversions only via the tested
  `app/speech/audio_convert.py` and never silent.
* **Bounded, honest persistence** — the voice agent never claims a request was
  persisted unless the practice API confirmed; failures return structured
  `SubmissionOutcome`s and response texts never make persistence claims.

## 2. The speech pipeline (data flow)

```
AudioReceived (Exotel media frame → decoded PCM16 mono @ negotiated rate)
      │
      ▼
VoicePipeline.submit_audio ─► convert_if_needed (explicit only)
      │
      ▼
STT provider stream  (mock: duration-driven deterministic script · azure: continuous recognition)
      │  events() yields Normalized SttEvents (Started / Partial / Final / Stopped / Error)
      ▼
SpeechSession + FSM (per-call state)
      │  FinalTranscript (final-only intent) → FSM PROCESSING
      ▼
DeterministicResponseHandler.respond (Day-3: keyword routing; LLM-replaceable seam)
      │  ConversationDecision {response_text, request_type, handoff}
      ▼
TTS provider synthesize (mock: deterministic tone chunks · azure: streaming capture)
      │  SynthesisStarted → SynthesisChunk… → SynthesisCompleted (last)
      ▼
OutboundAudioFramer (steady-state ≥ 3200 B, target 32000 B; turn-end flush)
      ▼
AudioToSend → Exotel outbound media envelope (base64 payload)
```

Key behaviours:

* The WebSocket route keeps ownership of transport only; the pipeline is a
  per-session object built when `start` is processed (`_build_speech_pipeline`).
* Outbound frames flow through a dedicated **pump task** (`stream_outbound` queue).
* Inbound audio never blocks on persistence: practice submissions are
  `asyncio.wait_for`-bounded (practice timeout) and best-effort; failures never
  break the speech stream.
* TTS latency instrumentation: `SessionMetrics` records STT start/partials/finals,
  processing turn duration, TTS first-chunk/completion/bytes, audio counters and
  provider errors — one compact JSON record per session (no transcript text, no
  audio payload, no secrets).

## 3. Azure Speech integration (verified facts — docs fetched 2026-11-06)

Verified via official Microsoft Learn documentation and the installed SDK
`azure-cognitiveservices-speech==1.53.0` (installed via `uv add`; importability
verified locally; **real-API behaviour not yet executed — no Azure credentials**):

* Streaming STT: `azure.audio.PushAudioInputStream` with explicit
  `AudioStreamFormat(samples_per_second, bits_per_sample=16, channels=1)`; continuous
  recognition via `SpeechRecognizer.start_continuous_recognition_async()` with
  `recognizing` / `recognized` / `canceled` event signals; stop via
  `stop_continuous_recognition_async()`. SDK callbacks run on the SDK thread →
  bridged to the asyncio loop via `loop.call_soon_threadsafe`.
* Streaming TTS: `SpeechSynthesizer(speech_config, audio_config=None)` for capture;
  audio via `synthesizing` event (`event_args.result.audio_data`); end via
  `synthesis_completed` / `synthesis_canceled`; `speak_text_async(text).get()` resolves
  after delivery; raw output via `SpeechSynthesisOutputFormat.Raw{8,16,24}Khz16BitMonoPcm`.
* Default voice: `en-GB-SoniaNeural` (verified F0-eligible UK English voice).
* Confidence: SDK exposes no top-level `confidence` on results → defensive JSON
  extraction from `Confidence` / `NBest[0]` (documented in adapter).
* Concurrency: F0 for streaming STT = 1 concurrent stream per resource; TTS ≈ 20
  transactions per 60 s.

Sources: Microsoft Learn — Speech SDK for Python (overview / streaming audio input,
streaming audio output, speech synthesis concepts, free (F0) pricing & concurrency
limits. Full URLs recorded in the final engineering report.

## 4. Mock providers (deterministic, no credentials)

* **Mock STT** (`app/speech/mock_stt.py`): duration-driven recognition. Crossing
  `MOCK_STT_UTTERANCE_MS` of audio since the last finalized utterance schedules a
  deterministic `PartialTranscript` immediately and `FinalTranscript` after
  `MOCK_STT_FINALIZE_MS` (simulated provider latency). Script lines (comma-separated
  in `MOCK_STT_SCRIPT`) are consumed in order and cycle on exhaustion. Not
  audio-derived — documented honestly (mock proves pipeline/FSM/framing, not phonetics).
  Fault injection: `MOCK_STT_ERROR_AT_FRAME=N` → non-fatal `RecognitionError`.
* **Mock TTS** (`app/speech/mock_tts.py`): deterministic PCM16 tone chunks
  (100 ms per chunk; N×70 ms per char, capped at 4.8 s), `SynthesisCompleted` always
  last (`cancelled=True` on consumer cancellation), empty text → started+completed
  with 0 bytes.

## 5. FSM & session state

States: `IDLE → CONNECTING → LISTENING → PROCESSING → SPEAKING → HANDOFF → ENDED`,
plus `ERROR` (reachable from operational states; ERROR → ENDED only).

Explicit transition table in `app/domain/fsm.py` (`VALID_TRANSITIONS`), typed
`VoiceTransition` events with reasons. Design decisions:

* Every non-terminal state may end the call (cleanup invariant; `ENDED` terminal).
* `SPEAKING → LISTENING` is the only legal response-completion path; no barge-in
  (documented policy) — `SPEAKING → PROCESSING` invalid.
* `ERROR` reachable from `CONNECTING/LISTENING/PROCESSING/SPEAKING/HANDOFF`.
* Per-call `SpeechSession` holds format lock, provider states, bounded transcript
  history (last 20), pending utterance/response, lifecycle counters, cleanup marker.

## 6. Practice backend contract (NestJS — scaffold status in §12)

Five tables (`practices`, `calls`, `patient_requests`, `handoffs`, `audit_events`)
with migrations-only schema, FKs, indexes, UTC timestamps; DB-enforced idempotency on
`patient_requests` (`practice_id` + `idempotency_key` unique, payload hash conflict → 409).

## 7. Testing

* Day 2 regression: **106 tests** still green (integration conftest pins
  `VOICE_PIPELINE=echo` for existing tests; new speech tests pin `speech` via
  per-test env params).
* New unit suites: FSM, speech events, speech session, mock STT, mock TTS,
  audio conversion, response handler, framer, metrics — **64 new tests**.
* New integration suite `tests/integration/test_speech_pipeline.py` — real uvicorn +
  `websockets` client: greeting TTS chunk framing, end-to-end turn (audio → response →
  outbound → clean close 1000), renegotiation 1008, redundant same-rate start tolerated,
  injected STT error tolerated + clean close, abrupt disconnect cleanup, persistence
  disabled → speech still answers.
* **Final suite: 207 passed, 0 failed** (106 Day-2 + 101 net new this day); `ruff check`
  and `ruff format --check` clean on `app` + `tests`.
* Azure paths: unit-tested with stubbed `azure.cognitiveservices.speech` module (lazy
  import); **real Azure API tests not executed** (no credentials).

## 8. Out of scope today (Day 3 boundaries kept)

No LLM/Gemini/Groq/Azure OpenAI/RAG/triage/booking/NHS integration. Deterministic
response handler is the Day-3 seam; the emergency/human handoff route is a demo
keyword policy, not a clinical safety system (Day 4 evaluates it).

## 9. Operational notes

* Close-code policy table (Day 2) extended with speech-mode rows: provider
  misconfiguration → 1011; speech pipeline start failure → 1011; media
  renegotiation post-lock → 1008.
* `/health/ready` gained additive Day-3 checks: `voice_pipeline`, `stt_provider`,
  `tts_provider`, `speech_provider_configured`.
* Metrics log line: `emma.metrics` emits one compact JSON `voice_session_metrics`
  record per session.

## 10. Verification status (honest)

| Claim | Status |
|---|---|
| Day-2 suite green after Day-3 changes | ✅ verified locally (207 now) |
| Speech unit + integration tests green | ✅ verified locally |
| Ruff lint + format clean | ✅ verified locally |
| Azure SDK import + documented streaming API support | ✅ verified (docs + local import) |
| Real Azure speech round-trip | ❌ not executed (no credentials) |
| Real Exotel inbound→speech→outbound call | ❌ not executed (no account/call) |
| Docker Compose runtime | ❌ not executed (no Docker daemon) |
| NestJS practice-api build/tests | ❌ incomplete (see §12) |
