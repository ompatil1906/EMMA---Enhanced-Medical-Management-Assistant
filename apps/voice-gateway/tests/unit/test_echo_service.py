"""Echo service contract tests (provider-independent)."""

import pytest

from app.domain.audio import AudioFormat
from app.domain.events import AudioReceived


@pytest.mark.asyncio
async def test_echo_preserves_payload_byte_for_byte() -> None:
    from app.services.echo_service import EchoService

    payload = bytes(range(256))
    received = AudioReceived(
        session_id="s1", sequence=5, payload=payload, audio_format=AudioFormat(sample_rate=16000)
    )
    outbound = await EchoService().process(received)
    assert outbound.payload == payload
    assert outbound.session_id == "s1"
    assert outbound.sequence == 5
    assert outbound.audio_format.sample_rate == 16000


@pytest.mark.asyncio
async def test_echo_preserves_empty_payload() -> None:
    from app.services.echo_service import EchoService

    received = AudioReceived(session_id="s1", sequence=1, payload=b"", audio_format=AudioFormat())
    outbound = await EchoService().process(received)
    assert outbound.payload == b""


@pytest.mark.asyncio
async def test_echo_answers_each_frame_in_order() -> None:
    from app.services.echo_service import EchoService

    service = EchoService()
    received = [
        AudioReceived(session_id="s1", sequence=i, payload=bytes([i]), audio_format=AudioFormat())
        for i in (1, 2, 3)
    ]
    sent = [await service.process(frame) for frame in received]
    assert [frame.request_sequence for frame in sent] == [1, 2, 3]
    assert [frame.payload for frame in sent] == [b"\x01", b"\x02", b"\x03"]
