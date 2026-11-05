"""Domain event model tests: immutability + byte fidelity."""

import pytest
from pydantic import ValidationError

from app.domain.audio import DEFAULT_SAMPLE_RATE, AudioFormat
from app.domain.events import AudioReceived, AudioToSend, CallEnded


class TestImmutability:
    def test_audio_received_is_frozen(self) -> None:
        event = AudioReceived(
            session_id="s1",
            sequence=1,
            payload=b"\x01\x02",
            audio_format=AudioFormat(),
        )
        with pytest.raises(ValidationError):
            event.payload = b"\x03"  # type: ignore[misc]

    def test_call_ended_is_frozen(self) -> None:
        ended = CallEnded(session_id="s1", reason="callended")
        with pytest.raises(ValidationError):
            ended.reason = "stopped"  # type: ignore[misc]


class TestByteFidelity:
    def test_audio_to_send_preserves_exact_bytes(self) -> None:
        payload = bytes(range(256))
        sent = AudioToSend(session_id="s1", sequence=7, payload=payload, audio_format=AudioFormat())
        assert sent.payload == payload

    def test_ordering_metadata_round_trips(self) -> None:
        received = AudioReceived(
            session_id="s1",
            sequence=42,
            payload=b"audio",
            audio_format=AudioFormat(sample_rate=DEFAULT_SAMPLE_RATE),
            provider_timestamp="1730000000",
        )
        assert received.provider_timestamp == "1730000000"
        assert received.sequence == 42


class TestIsolation:
    def test_domain_models_do_not_reference_exotel_impl(self) -> None:
        # Isolation invariant: the domain layer must not depend on the Exotel adapter.
        import inspect

        import app.domain.audio as audio_mod
        import app.domain.events as events_mod

        for module in (audio_mod, events_mod):
            source = inspect.getsource(module)
            assert "app.integrations" not in source
