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
