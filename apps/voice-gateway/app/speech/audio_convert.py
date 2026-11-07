"""Explicit, tested PCM16 audio conversion adapter (assignment §4.1 Audio handling).

Policy: the pipeline NEVER resamples silently.

* Provider input/output formats are configured to match the negotiated Exotel
  format by default (Azure exposes raw PCM output at 8/16/24 kHz — all rates
  the gateway negotiates — so conversions should never trigger in this design).
* If a mismatch ever occurs (future provider with a fixed output format, e.g.
  opus/µ-law adapter), conversion MUST go through `convert_pcm16` here, which
  is loud, unit-tested, and surfaced in session metrics as an explicit event.
* Channel-count changes are refused outright (mono is a verified invariant of
  the Exotel transport and of Azure's raw mono formats).
"""

from __future__ import annotations

import struct

from app.domain.audio import AudioFormat

_AUDIO_CONVERSION_NOTES = (
    "Linear interpolation resampler for PCM16 LE mono. Deterministic; verified "
    "edge cases: identity passthrough, exact 2x upsample, exact 3x downsample, "
    "odd-byte payloads (truncated for alignment), non-divisor ratios."
)


class AudioConversionError(Exception):
    """Conversion requested for unsupported shapes (channels/encoding)."""


def convert_pcm16(payload: bytes, *, source_rate: int, target_rate: int) -> bytes:
    """Resample 16-bit signed LE mono PCM from source_rate to target_rate.

    Linear interpolation between neighbour samples; identity passthrough when
    rates match exactly. Odd trailing bytes are dropped for 16-bit alignment
    (a half-sample can never be a valid PCM16 frame).
    """
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError(f"sample rates must be positive ({source_rate=} {target_rate=})")
    if len(payload) % 2:
        payload = payload[: len(payload) - 1]  # force 16-bit frame alignment
    if source_rate == target_rate or not payload:
        return payload

    source_samples = len(payload) // 2
    out_samples = max(1, round(source_samples * target_rate / source_rate))
    source = struct.unpack(f"<{source_samples}h", payload)
    out = bytearray()
    scale = (source_samples - 1) / max(1, out_samples - 1)
    for j in range(out_samples):
        position = j * scale
        index = int(position)
        frac = position - index
        left = source[index]
        right = source[min(index + 1, source_samples - 1)]
        value = int(round(left + (right - left) * frac))
        out += struct.pack("<h", max(-32768, min(32767, value)))
    return bytes(out)


def convert_if_needed(
    payload: bytes, source: AudioFormat, target: AudioFormat
) -> tuple[bytes, bool]:
    """Convert payload from `source` to `target` format (explicit & observable).

    Returns (payload, converted). Raises AudioConversionError for unsupported
    changes (channel count, non-PCM16 encodings) — callers must not fall back
    to silent behaviour.
    """
    if source.channels != 1 or target.channels != 1:
        raise AudioConversionError("only mono (channels=1) audio is supported")
    if source.encoding != "PCM_L16" or target.encoding != "PCM_L16":
        raise AudioConversionError(
            f"unsupported encoding conversion {source.encoding!r} → {target.encoding!r}"
        )
    if source.sample_rate == target.sample_rate:
        return payload, False
    return (
        convert_pcm16(payload, source_rate=source.sample_rate, target_rate=target.sample_rate),
        True,
    )


__all__ = [
    "AudioConversionError",
    "convert_if_needed",
    "convert_pcm16",
    "_AUDIO_CONVERSION_NOTES",
]
