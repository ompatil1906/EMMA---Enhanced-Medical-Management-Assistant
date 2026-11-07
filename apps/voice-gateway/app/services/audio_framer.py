"""Outbound audio framing for the verified Exotel AgentStream chunk guidance.

Verified facts (protocol doc §3): media chunks ride base64 inside `media`
envelopes and provider-side guidance is 3,200–100,000 bytes PER CHUNK decoded.

Our outbound policy (explicit, tested):
  * accumulate synthesized PCM and emit chunks of ≥ `_MIN_OUTBOUND_CHUNK_BYTES`
    (3,200 — the verified floor), up to `_TARGET_OUTBOUND_CHUNK_BYTES`
    (32,000 — our own latency/latency-aware choice, below the provider max);
  * a turn-end flush may emit one smaller final chunk (exception documented —
    the provider floor applies to steady-state chunks, not turn tails);
  * PCM16 alignment is enforced: a trailing odd byte is dropped with a
    recorded count (never a whole sample's worth of payload loss).
"""

from __future__ import annotations

from dataclasses import dataclass, field

MIN_OUTBOUND_CHUNK_BYTES = 3200  # verified provider minimum
TARGET_OUTBOUND_CHUNK_BYTES = 32000  # outbound target chunk (32 kB ≈ 137 ms @ 24 k)


@dataclass
class OutboundAudioFramer:
    """Accumulates PCM bytes and yields provider-shaped chunks."""

    session_id: str
    buffer: bytearray = field(init=False, default_factory=bytearray)
    trailing_bytes_dropped: int = 0
    yielded_chunks: int = 0
    yielded_bytes: int = 0

    def push(self, payload: bytes) -> None:
        """Buffer one synthesized chunk (no events until a chunk is formable)."""
        self.buffer += payload

    def ready_chunks(self) -> list[bytes]:
        """Yield formable chunks (≥ MIN, ≤ TARGET); keep the remainder buffered."""
        chunks: list[bytes] = []
        while len(self.buffer) >= TARGET_OUTBOUND_CHUNK_BYTES:
            chunks.append(bytes(self.buffer[:TARGET_OUTBOUND_CHUNK_BYTES]))
            del self.buffer[:TARGET_OUTBOUND_CHUNK_BYTES]
        if len(self.buffer) >= MIN_OUTBOUND_CHUNK_BYTES:
            chunks.append(bytes(self.buffer))
            del self.buffer[:]
        self._tally(chunks)
        return chunks

    def flush(self) -> list[bytes]:
        """Turn-end flush: emit any remaining bytes as one final (possibly small) chunk."""
        if not self.buffer:
            return []
        chunk = self._flush_aligned()
        self._tally([chunk])
        return [chunk]

    def discard(self) -> None:
        """Drop buffered bytes without yielding (teardown path)."""
        self.buffer.clear()

    @property
    def pending_bytes(self) -> int:
        return len(self.buffer)

    # --- internals -----------------------------------------------------------------

    def _flush_aligned(self) -> bytes:
        payload = bytes(self.buffer)
        self.buffer.clear()
        if len(payload) % 2:
            payload = payload[:-1]  # keep PCM16 alignment (half-sample is invalid)
            self.trailing_bytes_dropped += 1
        return payload

    def _tally(self, chunks: list[bytes]) -> None:
        self.yielded_chunks += len(chunks)
        self.yielded_bytes += sum(len(chunk) for chunk in chunks)
