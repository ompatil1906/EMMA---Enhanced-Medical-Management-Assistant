"""Audio format contract tests (provider-independent domain)."""

import pytest
from pydantic import ValidationError

from app.domain.audio import (
    DEFAULT_ENCODING,
    DEFAULT_SAMPLE_RATE,
    SUPPORTED_SAMPLE_RATES,
    AudioFormat,
    bytes_to_duration_ms,
)


class TestSupportedFormats:
    @pytest.mark.parametrize("sample_rate", [8000, 16000, 24000])
    def test_supported_sample_rates_accepted(self, sample_rate: int) -> None:
        assert (
            AudioFormat(encoding="PCM_L16", sample_rate=sample_rate, channels=1).sample_rate
            == sample_rate
        )

    @pytest.mark.parametrize("sample_rate", [44100, 22050, 0, -8000])
    def test_unsupported_sample_rates_rejected(self, sample_rate: int) -> None:
        with pytest.raises(ValidationError, match="unsupported sample rate"):
            AudioFormat(encoding="PCM_L16", sample_rate=sample_rate, channels=1)

    def test_default_matches_exotel_audio_default(self) -> None:
        assert DEFAULT_ENCODING == "PCM_L16"
        assert DEFAULT_SAMPLE_RATE == 8000
        assert 8000 in SUPPORTED_SAMPLE_RATES

    def test_encoding_aliases_normalized(self) -> None:
        for alias in ("PCM", " pcm ", "Linear16", "PCM_S16LE", "s16le"):
            assert AudioFormat(encoding=alias).encoding == "PCM_L16"

    def test_unsupported_encoding_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unsupported audio encoding"):
            AudioFormat(encoding="MULAW")

    def test_channels_must_be_mono(self) -> None:
        assert AudioFormat(channels=1).channels == 1
        with pytest.raises(ValidationError, match="multi-channel"):
            AudioFormat(channels=2)


class TestDurations:
    def test_eight_khz_frame_duration(self) -> None:
        # 1,280 bytes = 640 samples @ 8 kHz = 80 ms
        assert bytes_to_duration_ms(b"\x00" * 1280, sample_rate=8000) == 80.0

    def test_empty_and_partial_frames(self) -> None:
        assert bytes_to_duration_ms(b"", sample_rate=8000) == 0.0
        # 3 bytes floor to 1 sample (FIFO truncation of a dangling half-sample).
        assert bytes_to_duration_ms(b"\x00" * 3, sample_rate=8000) == 0.125
