# Deepgram Flux Setup — EMMA Runbook

**Purpose:** Configure and verify Deepgram Flux streaming STT for EMMA Day 12.
Credentials live in `.env` (git-ignored) and are never printed or committed.

**Status:** Not live-tested — no `DEEPGRAM_API_KEY` configured. This runbook
documents the configuration and startup procedure for when credentials become
available.

---

## 1. Required environment variables

Set in `apps/voice-gateway/.env` (values redacted):

| Variable | Purpose | Example |
|---|---|---|
| `DEEPGRAM_API_KEY` | Deepgram API key | present |
| `DEEPGRAM_MODEL` | Deepgram Flux model | `flux-general-en` |
| `STT_PROVIDER` | `deepgram` to enable | `deepgram` |

## 2. How the gateway uses Deepgram Flux

* When `STT_PROVIDER=deepgram`, the gateway constructs a
  `DeepgramFluxSTTProvider` per session.
* Audio (PCM16 16 kHz mono) is sent over WebSocket to
  `wss://api.deepgram.com/v1/listen`.
* Deepgram returns `Results` messages (partial + final transcripts) and
  `Error` messages.
* The adapter normalizes these into `PartialTranscript` / `FinalTranscript` /
  `RecognitionError` events.
* End-of-turn detection is native to Flux (`endpointing` parameter).

## 3. Verification (when credentials available)

```bash
cd apps/voice-gateway
# Confirm Deepgram module imports
uv run python -c "from app.speech.deepgram_stt import DeepgramFluxSTTProvider; print('OK')"

# Run offline tests (no API credits)
uv run pytest tests/unit/test_deepgram_stt.py -q

# Confirm settings load
uv run python -c "
from app.core.config import get_settings
s = get_settings()
print('STT_PROVIDER:', s.STT_PROVIDER)
print('DEEPGRAM_MODEL:', s.DEEPGRAM_MODEL)
print('Deepgram configured:', bool(s.DEEPGRAM_API_KEY))
"
```

## 4. Test phrases (for live verification)

When credentials become available, test these phrases:

1. "I would like to request an appointment next Wednesday morning."
2. "I need a repeat prescription and an appointment."
3. "Actually, I meant Thursday afternoon."
4. "What time does the practice open on Monday?"

Measure: first partial latency, final transcript latency, word error rate, date
extraction accuracy, time extraction accuracy, end-of-turn behaviour.

## 5. Security

* Never print, log, or commit `DEEPGRAM_API_KEY`.
* Real providers fail fast on missing credentials — no silent mock fallback.
