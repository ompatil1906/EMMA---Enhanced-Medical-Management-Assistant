"""Audio primitives shared by adapters and services (provider-independent).

These are *our* canonical audio concepts. Provider-specific audio constraints
(e.g. Exotel's outbound chunk limits) are enforced inside the corresponding
integration module, not here.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

DEFAULT_SAMPLE_RATE = 8000  # Exotel AgentStream default (verified 2026-11-06)
DEFAULT_ENCODING = "PCM_L16"  # 16-bit signed little-endian linear PCM
DEFAULT_CHANNELS = 1
SUPPORTED_SAMPLE_RATES = (8000, 16000, 24000)  # verified Exotel support set
BYTES_PER_SAMPLE = 2


def bytes_to_duration_ms(payload: bytes, *, sample_rate: int = DEFAULT_SAMPLE_RATE) -> float:
    """Duration of a mono 16-bit PCM payload in milliseconds (diagnostics only)."""
    samples = len(payload) // BYTES_PER_SAMPLE
    return (samples / sample_rate) * 1000.0


class AudioFormat(BaseModel):
    """Normalized audio stream description negotiated for a session."""

    model_config = ConfigDict(frozen=True)

    encoding: str = DEFAULT_ENCODING
    sample_rate: int = DEFAULT_SAMPLE_RATE
    channels: int = DEFAULT_CHANNELS

    @field_validator("encoding", mode="before")
    @classmethod
    def _normalize_encoding(cls, value: object) -> str:
        text = str(value).strip().upper()
        aliases = {
            "PCM": DEFAULT_ENCODING,
            "LINEAR16": DEFAULT_ENCODING,
            "PCM_S16LE": DEFAULT_ENCODING,
            "S16LE": DEFAULT_ENCODING,
        }
        normalized = aliases.get(text, text)
        if normalized != DEFAULT_ENCODING:
            raise ValueError(f"unsupported audio encoding: {text!r}; expected {DEFAULT_ENCODING}")
        return normalized

    @field_validator("sample_rate")
    @classmethod
    def _validate_sample_rate(cls, value: int) -> int:
        if value not in SUPPORTED_SAMPLE_RATES:
            raise ValueError(
                f"unsupported sample rate {value}; "
                f"supported: {','.join(map(str, SUPPORTED_SAMPLE_RATES))}"
            )
        return value

    @field_validator("channels")
    @classmethod
    def _validate_channels(cls, value: int) -> int:
        if value != DEFAULT_CHANNELS:
            raise ValueError(
                f"multi-channel audio (channels={value}) is not supported; expected mono"
            )
        return value
