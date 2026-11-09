# Test fixtures (Day 2)

All fixtures here are **synthetic and deterministic** — no microphone input, no
recordings of real calls, and nothing resembling patient data.

## Synthetic audio frames

`tests/conftest.py` provides `make_daily_audio_frame(payload: bytes | None = None) -> bytes`:

* default: 1,280 bytes (80 ms of 8 kHz mono 16-bit PCM, the Exotel default rate), or
* an explicit byte payload for exact round-trip assertions (echo must be
  byte-for-byte identical).

Frame values come from a fixed arithmetic pattern (`(i * 7 + j) % 256`), so every
test run is reproducible on every machine.

## Why no real audio files?

The Day 2 success criterion is transport/protocol correctness, not audio
content. Deterministic bytes make the "echoed == sent" assertion exact and
prevent accidentally shipping any real-world recording into the repository.

## Speech fixture (Day 18)

`speech_gp_appointment.wav` — "Hello, I would like to book a routine GP
appointment.", synthesised **locally** with the macOS speech synthesiser
(`say -v Daniel`) and converted with `afconvert` to WAV / PCM16 / mono / 16 kHz
(3.67 s, peak −1.6 dBFS, RMS −19.4 dBFS, 67 % voiced).

It is **machine-generated speech**, not a human recording, not a telephone
recording, and contains no patient data. It exists so a live provider smoke test
can compare a transcript against a known reference. It is not evidence of human
microphone recognition quality.

Regenerate (no network, no cost):

```bash
.venv/bin/python -m tools.make_speech_fixture --out tests/fixtures/speech_gp_appointment.wav
.venv/bin/python -m tools.make_speech_fixture --out /tmp/oneshot.wav --trailing-silence-ms 15000
```
