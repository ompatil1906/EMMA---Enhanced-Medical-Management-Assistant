"""Outbound audio framer tests (verified Exotel chunk guidance; §Audio)."""

from __future__ import annotations

from app.services.audio_framer import (
    MIN_OUTBOUND_CHUNK_BYTES,
    TARGET_OUTBOUND_CHUNK_BYTES,
    OutboundAudioFramer,
)


def framer() -> OutboundAudioFramer:
    return OutboundAudioFramer(session_id="sess-1")


class TestChunking:
    def test_small_payloads_buffer_until_min_chunk(self) -> None:
        f = framer()
        f.push(b"\x00" * 1000)
        assert f.ready_chunks() == []
        assert f.pending_bytes == 1000

    def test_min_chunk_emitted_when_crossed(self) -> None:
        f = framer()
        f.push(b"\x01" * 3200)
        chunks = f.ready_chunks()
        assert chunks == [b"\x01" * 3200]
        assert f.pending_bytes == 0

    def test_target_chunk_splits_large_payloads(self) -> None:
        f = framer()
        f.push(b"\x02" * (TARGET_OUTBOUND_CHUNK_BYTES + 5000))
        chunks = f.ready_chunks()
        assert [len(chunk) for chunk in chunks] == [TARGET_OUTBOUND_CHUNK_BYTES, 5000]
        assert f.pending_bytes == 0

    def test_mid_traffic_emits_whole_buffer_within_target(self) -> None:
        f = framer()
        f.push(b"\x03" * 12000)  # < TARGET, ≥ MIN
        chunks = f.ready_chunks()
        assert [len(chunk) for chunk in chunks] == [12000]
        assert f.pending_bytes == 0

    def test_remainder_between_min_and_target_emits_in_same_call(self) -> None:
        f = framer()
        f.push(b"\x0a" * (TARGET_OUTBOUND_CHUNK_BYTES + 4000))
        chunks = f.ready_chunks()
        # one target chunk, and the 4000-byte remainder (≥ MIN) right after
        assert [len(chunk) for chunk in chunks] == [
            TARGET_OUTBOUND_CHUNK_BYTES,
            4000,
        ]


class TestFlush:
    def test_flush_emits_remaining_turn_tail(self) -> None:
        f = framer()
        f.push(b"\x05" * 4000)
        assert f.flush() == [b"\x05" * 4000]
        assert f.pending_bytes == 0

    def test_flush_sub_min_chunk_allowed_at_turn_end(self) -> None:
        f = framer()
        f.push(b"\x06" * 777)  # odd byte count → realigned to a whole PCM16 frame
        chunk = f.flush()[0]
        assert len(chunk) == 776  # turn tails may be smaller than the steady-state min
        assert f.trailing_bytes_dropped == 1

        f2 = framer()
        f2.push(b"\x06" * 778)  # even → nothing dropped
        assert len(f2.flush()[0]) == 778
        assert f2.trailing_bytes_dropped == 0

    def test_flush_empty_is_noop(self) -> None:
        assert framer().flush() == []

    def test_flush_drops_misaligned_trailing_byte(self) -> None:
        f = framer()
        f.push(b"\x07" * 11)  # odd count (11 bytes = 5.5 samples)
        (chunk,) = f.flush()
        assert len(chunk) == 10
        assert f.trailing_bytes_dropped == 1

    def test_discard_clears_without_yielding(self) -> None:
        f = framer()
        f.push(b"\x08" * 5000)
        f.discard()
        assert f.flush() == []
        assert f.pending_bytes == 0


class TestVerifiedLimits:
    def test_limits_track_verified_exotel_guidance(self) -> None:
        assert MIN_OUTBOUND_CHUNK_BYTES == 3200  # verified provider floor
        assert MIN_OUTBOUND_CHUNK_BYTES < TARGET_OUTBOUND_CHUNK_BYTES
        assert TARGET_OUTBOUND_CHUNK_BYTES <= 100_000  # verified provider max

    def test_tally_counters(self) -> None:
        f = framer()
        f.push(b"\x09" * (TARGET_OUTBOUND_CHUNK_BYTES + 200))
        f.ready_chunks()
        f.flush()
        assert f.yielded_chunks == 2
        assert f.yielded_bytes == TARGET_OUTBOUND_CHUNK_BYTES + 200
