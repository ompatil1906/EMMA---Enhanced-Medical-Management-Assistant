# ElevenLabs Setup — EMMA Runbook

**Purpose:** Configure and verify ElevenLabs Flash v2.5 TTS for EMMA Day 12.
Credentials live in `.env` (git-ignored) and are never printed or committed.

**Status:** Not live-tested — no `ELEVENLABS_API_KEY` or `ELEVENLABS_VOICE_ID`
configured. This runbook documents the configuration and startup procedure for
when credentials become available.

---

## 1. Required environment variables

Set in `apps/voice-gateway/.env` (values redacted):

| Variable | Purpose | Example |
|---|---|---|
| `ELEVENLABS_API_KEY` | ElevenLabs API key | present |
| `ELEVENLABS_VOICE_ID` | Voice ID (British English) | present |
| `ELEVENLABS_MODEL` | ElevenLabs model | `eleven_flash_v2_5` |
| `TTS_PROVIDER` | `elevenlabs` to enable | `elevenlabs` |

## 2. How the gateway uses ElevenLabs

* When `TTS_PROVIDER=elevenlabs`, the gateway constructs an
  `ElevenLabsFlashTTSProvider` per session.
* Audio is streamed via WebSocket to
  `wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream/input`.
* The adapter emits `SynthesisStarted` / `SynthesisChunk` /
  `SynthesisCompleted` events.
* Output is PCM audio at the negotiated sample rate.
* `cancel_turn()` supports barge-in interruption.

## 3. Voice selection

* Use a British English-compatible voice suitable for an NHS GP receptionist.
* Read the voice ID from `ELEVENLABS_VOICE_ID` — do not invent a voice ID or
  use an unverified account resource.
* Compare the result with Azure `en-GB-SoniaNeural` (fallback path).
* Do not claim better naturalness without actual listening evaluation.

## 4. Verification (when credentials available)

```bash
cd apps/voice-gateway
# Confirm ElevenLabs module imports
uv run python -c "from app.speech.elevenlabs_tts import ElevenLabsFlashTTSProvider; print('OK')"

# Run offline tests (no API credits)
uv run pytest tests/unit/test_elevenlabs_tts.py -q

# Confirm settings load
uv run python -c "
from app.core.config import get_settings
s = get_settings()
print('TTS_PROVIDER:', s.TTS_PROVIDER)
print('ELEVENLABS_MODEL:', s.ELEVENLABS_MODEL)
print('ElevenLabs configured:', bool(s.ELEVENLABS_API_KEY and s.ELEVENLABS_VOICE_ID))
"
```

## 5. Security

* Never print, log, or commit `ELEVENLABS_API_KEY`.
* Real providers fail fast on missing credentials — no silent mock fallback.
