# Azure Speech Setup — EMMA Runbook

**Purpose:** Configure and verify Azure AI Speech (STT + TTS) for the EMMA voice
gateway. Credentials live in `.env` (ignored by git) and are never printed or
committed.

**Scope:** Azure Speech only. No Exotel/PSTN, no paid tier changes, no Azure
subscription changes. Uses existing F0-eligible configuration.

---

## 1. Required environment variables

Set in `apps/voice-gateway/.env` (values redacted here — never record secrets):

| Variable | Purpose | Example shape |
|---|---|---|
| `AZURE_SPEECH_KEY` | Azure Speech subscription key | present (84 chars) |
| `AZURE_SPEECH_REGION` | Azure Speech region | `eastasia` |
| `AZURE_SPEECH_LANGUAGE` | STT recognition language | `en-GB` |
| `AZURE_SPEECH_VOICE` | TTS voice | `en-GB-SoniaNeural` |
| `STT_PROVIDER` | `azure` (or `mock` for tests) | `azure` |
| `TTS_PROVIDER` | `azure` (or `mock` for tests) | `azure` |
| `VOICE_PIPELINE` | `speech` (full pipeline) or `echo` | `speech` |

Optional runtime:
* `SPEECH_CONNECTION_TIMEOUT` (default 10s)
* `SPEECH_REQUEST_TIMEOUT` (default 15s)

## 2. How the gateway uses Azure Speech

* **STT:** one `SpeechRecognizer` per session, push-stream input, continuous
  recognition. Audio must be PCM16 mono at the negotiated sample rate
  (default 16 kHz). The SDK requires `AudioConfig(stream=push_stream)` — passing
  the raw `PushAudioInputStream` constructs but never delivers audio.
* **TTS:** `SpeechSynthesizer` streaming PCM_L16 output at 16 kHz mono. Chunks
  are framed by `OutboundAudioFramer` (≥ 3,200 bytes, ≤ 32,000 bytes per chunk)
  and sent as base64 in Exotel `media` envelopes.
* **Provider selection:** `SpeechProviderFactory(settings)` chooses `azure` or
  `mock`. Real providers fail fast on missing credentials — there is **no silent
  mock fallback**.

## 3. Local verification (no credentials printed)

Run the fake-SDK lifecycle tests (no network, no credentials):

```bash
cd apps/voice-gateway
uv run pytest tests/unit/test_azure_stt_lifecycle.py -q
```

Run real Azure TTS synthesis (uses live credentials from `.env`):

```bash
cd apps/voice-gateway
EMMA_RUN_LIVE_TESTS=1 uv run python - <<'EOF'
import asyncio, time
from app.core.config import Settings
from app.domain.audio import AudioFormat
from app.speech.factory import SpeechProviderFactory

async def main():
    s = Settings(); fmt = AudioFormat(sample_rate=16000, channels=1)
    tts = SpeechProviderFactory(s).create_tts(fmt)
    for phrase in ("I would like to request an appointment next Wednesday morning.",
                   "What time does the practice open on Monday?",
                   "This may be a medical emergency. Please hang up and call 999 now."):
        t0 = time.monotonic(); chunks = 0; total = 0; first = None
        async for ev in tts.synthesize(phrase, "runbook-check", fmt):
            name = ev.__class__.__name__
            if name == "SynthesisChunk":
                if first is None: first = (time.monotonic()-t0)*1000
                chunks += 1; total += len(ev.payload)
        print(f"{phrase[:40]}... first={first:.0f}ms total={(time.monotonic()-t0)*1000:.0f}ms chunks={chunks} bytes={total}")

asyncio.run(main())
EOF
```

Expected: first chunk ~1.5–2.3 s, valid PCM_L16 16 kHz mono, `cancelled=False`.

## 4. Known Azure STT behaviours (from live testing)

| Behaviour | Notes |
|---|---|
| Partial + final transcripts | Delivered; final exactly once per utterance |
| End-of-utterance | Trailing silence flushes the final |
| Stop immediately after audio | Final still flushed when push stream is closed before `stop_continuous_recognition_async()` |
| `CancellationReason.EndOfStream` | Normal teardown during graceful `stop()`, not a fatal error |
| Fatal cancellation | `RecognitionError` + `_STREAM_END` enqueued on the same loop turn |
| Transient timeout | Possible under load; retry succeeded in testing |

## 5. Troubleshooting

* **0 partials, 0 finals:** recognizer was likely constructed with the raw
  `PushAudioInputStream` instead of `AudioConfig(stream=...)`. Verify
  `app/speech/azure_stt.py` `_build_stream_and_recognizer()`.
* **Final transcript dropped on stop:** push stream must be closed **before**
  `stop_continuous_recognition_async()`, with a brief grace window to let the
  SDK flush.
* **`EndOfStream` treated as error:** expected during graceful teardown; only
  `CancellationReason.Error` (not EndOfStream) is fatal.
* **TTS timeout (RTF > 2):** transient; retry synthesis. Do not disable retry
  silently.

## 6. Security

* Never print, log, or commit `AZURE_SPEECH_KEY` or any other secret.
* `.env` is git-ignored; confirm with `git status --short` before committing
  (no commits are made for Day 10).
* Metrics records carry counts/latencies only — no transcript text, no audio
  payloads, no secrets.
