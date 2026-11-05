"""Exotel adapter tests: verified-envelope translation + negotiation strictness."""

import json

import pytest
from pydantic import ValidationError

from app.domain.audio import AudioFormat
from app.domain.events import AudioReceived
from app.integrations.exotel.adapter import ExotelAdapter, build_outbound_media_envelope
from app.integrations.exotel.codec import encode_media_payload
from app.integrations.exotel.errors import ExotelProtocolError
from app.integrations.exotel.schemas import (
    ExotelStart,
    ExotelUnknownEvent,
)

ADAPTER = ExotelAdapter()


class TestParseInbound:
    def test_known_events_parse(self) -> None:
        assert ADAPTER.parse_inbound('{"event":"connected"}').event == "connected"
        stop = ADAPTER.parse_inbound('{"event":"stop","reason":"callended"}')
        assert stop.reason == "callended"

    def test_unknown_event_is_tolerated_object(self) -> None:
        event = ADAPTER.parse_inbound('{"event":"symphony","anything":1}')
        assert isinstance(event, ExotelUnknownEvent)
        assert event.event == "symphony"

    def test_media_with_extra_fields_tolerated(self) -> None:
        event = ADAPTER.parse_inbound(
            '{"event":"media","chunk":"' + encode_media_payload(b"x") + '","future_field":true}'
        )
        assert event.event == "media"

    @pytest.mark.parametrize(
        "raw",
        [
            "not-json-at-all",
            "[1,2,3]",  # valid JSON but not an object
            '{"no_event_field": true}',
            '{"event": 42}',  # event must be a string
            '{"event":"start","media_format":{"sample_rate":"NaN-rate"}}',  # bad coercion
        ],
    )
    def test_protocol_errors_raise(self, raw: str) -> None:
        with pytest.raises(ExotelProtocolError):
            ADAPTER.parse_inbound(raw)


class TestHandleStart:
    def test_full_metadata_translated(self) -> None:
        event = ExotelStart.model_validate(
            {
                "event": "start",
                "stream_sid": "s1",
                "call_sid": "c1",
                "account_sid": "acct",
                "from": "+441234000001",
                "to": "+441234000999",
                "custom_parameters": {"practice": "p1"},
                "media_format": {"encoding": "PCM", "sample_rate": 16000, "bit_rate": 128000},
            }
        )
        started = ADAPTER.handle_start(event)
        assert started.provider == "exotel"
        assert started.stream_sid == "s1"
        assert started.from_number == "+441234000001"
        assert started.audio_format == AudioFormat(encoding="PCM_L16", sample_rate=16000)

    def test_missing_media_format_leaves_format_to_route_default(self) -> None:
        # Adapter returns None → route keeps its `?sample-rate=`/default (8 kHz) format.
        event = ExotelStart.model_validate({"event": "start", "stream_sid": "s1"})
        started = ADAPTER.handle_start(event)
        assert started.audio_format is None

    def test_unsupported_sample_rate_blocked_at_negotiation(self) -> None:
        event = ExotelStart.model_validate(
            {"event": "start", "stream_sid": "s1", "media_format": {"sample_rate": 44100}}
        )
        with pytest.raises(ValidationError, match="unsupported sample rate"):
            ADAPTER.handle_start(event)


class TestHandleMedia:
    async def test_media_translated_to_audio_received(self) -> None:
        payload = b"\x01\x02\x03\x04"
        event = ADAPTER.parse_inbound(
            json.dumps({"event": "media", "chunk": encode_media_payload(payload), "timestamp": "7"})
        )
        received = ADAPTER.handle_media(
            event, session_id="s1", audio_format=AudioFormat(), sequence=1, max_chunk_bytes=100
        )
        assert isinstance(received, AudioReceived)
        assert received.payload == payload
        assert received.sequence == 1
        assert received.provider_timestamp == "7"

    async def test_oversized_chunk_rejected_as_protocol_error(self) -> None:
        event = ADAPTER.parse_inbound(
            json.dumps({"event": "media", "chunk": encode_media_payload(b"x" * 11)})
        )
        with pytest.raises(ExotelProtocolError, match="exceeds limit"):
            ADAPTER.handle_media(
                event, session_id="s1", audio_format=AudioFormat(), sequence=1, max_chunk_bytes=10
            )

    async def test_empty_chunk_allowed(self) -> None:
        event = ADAPTER.parse_inbound(json.dumps({"event": "media", "chunk": ""}))
        received = ADAPTER.handle_media(
            event, session_id="s1", audio_format=AudioFormat(), sequence=1, max_chunk_bytes=10
        )
        assert received.payload == b""


class TestOutboundEnvelope:
    def test_envelope_is_exactly_three_top_level_fields(self) -> None:
        # Verified outbound shape: {event, stream_sid, media{payload}}
        envelope = json.loads(build_outbound_media_envelope(_audio_to_send(), stream_sid="s1"))
        assert sorted(envelope.keys()) == ["event", "media", "stream_sid"]
        assert envelope["event"] == "media"
        assert envelope["stream_sid"] == "s1"
        assert sorted(envelope["media"].keys()) == ["payload"]
        assert envelope["media"]["payload"] == encode_media_payload(b"audio!")

    def test_stream_sid_omitted_when_unknown(self) -> None:
        envelope = json.loads(build_outbound_media_envelope(_audio_to_send(), stream_sid=None))
        assert sorted(envelope.keys()) == ["event", "media"]

    def test_compact_json_frame(self) -> None:
        raw = build_outbound_media_envelope(_audio_to_send(), stream_sid="s1")
        assert "\n" not in raw and "\r" not in raw
        assert not raw.startswith(" ") and not raw.endswith(" ")


def _audio_to_send():
    from app.domain.audio import AudioFormat
    from app.domain.events import AudioToSend

    return AudioToSend(session_id="s1", sequence=1, payload=b"audio!", audio_format=AudioFormat())
