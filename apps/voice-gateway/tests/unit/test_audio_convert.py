"""Explicit audio conversion adapter tests (no silent resampling, §4.1)."""

from __future__ import annotations

import random
import struct

import pytest
from pydantic import ValidationError

from app.domain.audio import AudioFormat
from app.speech.audio_convert import AudioConversionError, convert_if_needed, convert_pcm16


def fmt(rate: int) -> AudioFormat:
    return AudioFormat(encoding="PCM_L16", sample_rate=rate, channels=1)


class TestIdentity:
    def test_same_rate_is_passthrough(self) -> None:
        payload = struct.pack("<4h", 0, 1000, -1000, 500)
        converted, flag = convert_if_needed(payload, fmt(8000), fmt(8000))
        assert flag is False
        assert converted == payload  # byte-identical, no conversion touched it

    def test_empty_payload_between_rates(self) -> None:
        converted, flag = convert_if_needed(b"", fmt(8000), fmt(16000))
        assert converted == b""
        assert flag is True  # rates differ ⇒ the conversion path was engaged

    def test_odd_byte_payload_aligned_before_conversion(self) -> None:
        payload = struct.pack("<2h", 5, -5) + b"\x00"  # trailing half-sample
        result = convert_pcm16(payload, source_rate=8000, target_rate=8000)
        assert result == struct.pack("<2h", 5, -5)


class TestResampling:
    def test_upsample_doubles_sample_count(self) -> None:
        payload = struct.pack("<4h", 0, 1000, 2000, 3000)
        out = convert_pcm16(payload, source_rate=8000, target_rate=16000)
        assert len(out) == 2 * 4 * 2  # 8 samples × 2 bytes
        samples = struct.unpack("<8h", out)
        assert samples[0] == 0
        assert samples[-1] == 3000

    def test_interpolated_values_between_samples(self) -> None:
        # 8 kHz → 16 kHz with endpoint fidelity (first-in→first-out, last-in→last-out).
        payload = struct.pack("<2h", 0, 1000)
        out = convert_pcm16(payload, source_rate=8000, target_rate=16000)
        samples = struct.unpack("<4h", out)
        assert samples[0] == 0
        assert samples[1] == 333  # 1/3 along the 0→1000 ramp
        assert samples[2] == 667  # 2/3 along the ramp
        assert samples[3] == 1000

    def test_downsample_applies_ratio(self) -> None:
        payload = struct.pack("<9h", *[i * 100 for i in range(9)])
        out = convert_pcm16(payload, source_rate=24000, target_rate=8000)
        assert len(out) == 6  # 3 output samples × 2 bytes
        samples = struct.unpack("<3h", out)
        assert samples[0] == 0
        assert samples[-1] == 800

    def test_output_is_always_valid_int16_mono_frames(self) -> None:
        rng = random.Random(7)
        payload = struct.pack("<512h", *[rng.randint(-32768, 32767) for _ in range(512)])
        out = convert_pcm16(payload, source_rate=16000, target_rate=8000)
        assert len(out) % 2 == 0
        struct.unpack(f"<{len(out) // 2}h", out)  # must not raise


class TestUnsupportedConversions:
    def test_channel_change_refused_by_model_then_adapter(self) -> None:
        # Gate 1: the canonical AudioFormat model itself refuses non-mono (Day-2).
        with pytest.raises(ValidationError):
            AudioFormat(encoding="PCM_L16", sample_rate=8000, channels=2)
        # Gate 2 (defense in depth): the converter refuses bypassed/malformed models.
        malformed = AudioFormat.model_construct(encoding="PCM_L16", sample_rate=8000, channels=2)
        with pytest.raises(AudioConversionError):
            convert_if_needed(b"\x00\x00", fmt(8000), malformed)

    def test_encoding_change_refused_by_model_then_adapter(self) -> None:
        with pytest.raises(ValidationError):
            AudioFormat(encoding="PCM_L8", sample_rate=8000, channels=1)
        malformed = AudioFormat.model_construct(encoding="PCM_L8", sample_rate=8000, channels=1)
        with pytest.raises(AudioConversionError):
            convert_if_needed(b"\x00\x00", malformed, fmt(8000))

    def test_zero_rate_refused(self) -> None:
        with pytest.raises(ValueError):
            convert_pcm16(b"\x00\x00", source_rate=0, target_rate=8000)
