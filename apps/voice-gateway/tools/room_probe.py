"""Room probe — a non-product LiveKit participant for browser acceptance tests.

Day 17 test instrumentation. It is NOT part of the voice gateway's runtime:
it connects to a self-hosted LiveKit room like a browser participant would and
records observed events as JSON lines so browser acceptance tests (Day 17,
`docs/BROWSER_WEBRTC_ACCEPTANCE.md`) can assert server-side ground truth:

* participant connections (browser caller + EMMA agent in the same room),
* control data messages (transcripts / assistant / state / barge-in),
* subscribed remote audio frame observations (frame count + RMS level),
* `--auto-barge-in`: when the agent reports `speaking`, the probe sends the
  same `{"event": "barge-in"}` data message the browser's audio-level monitor
  sends — a **simulated, data-level** interruption (never acoustic).

Usage (from `apps/voice-gateway`):

    uv run python -m tools.room_probe --room emma-voice --seconds 60 \
        --out /tmp/probe.jsonl [--auto-barge-in]

Credentials come from the environment / `.env` (LIVEKIT_URL/API_KEY/SECRET).
Probe identity is `emma-probe-…` so the agent ignores its audio — the agent's
input loop only binds `emma-caller-…` participants (minted server-side).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from typing import Any

from app.core.config import get_settings
from app.integrations.livekit import LiveKitTokenMinter, _import_rtc
from app.integrations.livekit.agent import _frame_to_mono_pcm16

_TRACK_KIND_AUDIO = 1  # livekit.rtc._proto.track_pb2.TrackKind.KIND_AUDIO


def _log(handle: Any, event: dict[str, Any]) -> None:
    """Append one JSON line to the output file and mirror to stdout."""
    event = {"ts": round(time.time(), 3), **event}
    line = json.dumps(event, default=str)
    handle.write(line + "\n")
    handle.flush()
    print(line, flush=True)


def _rms(payload: bytes) -> float:
    """Root-mean-square of a PCM16 mono payload, as a 0…1 float level."""
    samples = len(payload) // 2
    if samples == 0:
        return 0.0
    import struct

    total = 0
    for value in struct.unpack(f"<{samples}h", payload):
        total += value * value
    return math.sqrt(total / samples) / 32768.0


class RoomProbe:
    """Minimal rtc.Room participant with JSONL event logging."""

    def __init__(self, room_name: str, identity: str, auto_barge_in: bool) -> None:
        self._room_name = room_name
        self._identity = identity
        self._auto_barge_in = auto_barge_in
        self._rtc = _import_rtc()
        self._room: Any = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._audio_frames = 0
        self._nonzero_frames = 0
        self._last_rms = 0.0
        self._peak_rms = 0.0
        self._agent_identity: str | None = None
        self._barge_in_sent = False
        self._barge_in_pending = False
        self._barge_in_echoed = False
        self.disconnected = asyncio.Event()

    async def run(self, seconds: int, handle: Any) -> dict[str, Any]:
        settings = get_settings()
        minter = LiveKitTokenMinter(settings=settings)
        token = minter.mint_participant_token(
            room_name=self._room_name,
            participant_identity=self._identity,
            practice_id=settings.PRACTICE_ID or "demo-practice-001",
            ttl_seconds=3600,
        )

        room = self._rtc.Room()
        self._room = room
        room.on("participant_connected")(self._on_participant)
        room.on("participant_disconnected")(self._on_participant)
        room.on("track_published")(self._on_track_publication)
        room.on("track_subscribed")(self._on_track_subscribed)
        room.on("data_received")(self._on_data_received)
        room.on("disconnected")(self._on_disconnected)

        await room.connect(settings.LIVEKIT_URL, token)
        _log(handle, {"event": "probe_connected", "room": self._room_name})
        # Register any participants already in the room.
        for participant in list(room.remote_participants.values()):
            self._on_participant(participant)
        # Consume any remote audio tracks already published before we joined
        # (e.g. the agent's greeting track, published while the probe was
        # connecting). With auto-subscribe they are already subscribed; we just
        # need to start observing them.
        for participant in list(room.remote_participants.values()):
            for publication in participant.track_publications.values():
                track = getattr(publication, "track", None)
                if track is not None and int(track.kind) == _TRACK_KIND_AUDIO:
                    task = asyncio.ensure_future(self._consume_audio(track))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)

        try:
            await asyncio.wait_for(self.disconnected.wait(), timeout=seconds)
        except TimeoutError:
            pass
        summary = {
            "event": "probe_summary",
            "audio_frames": self._audio_frames,
            "nonzero_audio_frames": self._nonzero_frames,
            "agent_identity": self._agent_identity,
        }
        _log(handle, summary)
        try:
            await room.disconnect()
        except Exception:  # noqa: BLE001 — probe shutdown is best-effort
            pass
        return summary

    # --- event handlers ---------------------------------------------------

    def _on_participant(self, participant: Any) -> None:
        _log(_OUT, {"event": "participant", "identity": participant.identity})

    def _on_track_publication(self, publication: Any, participant: Any) -> None:
        _log(
            _OUT,
            {
                "event": "track_published",
                "participant": participant.identity,
                "kind": str(getattr(publication, "kind", "")),
                "track_name": getattr(publication, "track_name", ""),
            },
        )

    def _on_track_subscribed(self, track: Any, publication: Any, participant: Any) -> None:
        identity = getattr(participant, "identity", "")
        kind = int(track.kind)
        _log(
            _OUT,
            {
                "event": "track_subscribed",
                "participant": identity,
                "kind": str(track.kind),
                "isAudio": kind == _TRACK_KIND_AUDIO,
            },
        )
        if int(track.kind) == _TRACK_KIND_AUDIO:
            if identity.startswith("emma-agent"):
                self._agent_identity = identity
            task = asyncio.ensure_future(self._consume_audio(track))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    def _on_data_received(self, event: Any) -> None:
        # LiveKit 1.2.0 emits DataPacket with .data (not .payload).
        payload = getattr(event, "data", None) or getattr(event, "payload", None) or b""
        try:
            if isinstance(payload, (bytes, bytearray, memoryview)):
                decoded = bytes(payload).decode("utf-8", errors="replace")
            else:
                decoded = str(payload)
            message = json.loads(decoded)
        except (ValueError, TypeError):
            return
        event_name = message.get("event") if isinstance(message, dict) else None
        text = message.get("text") if isinstance(message, dict) else None
        entry: dict[str, Any] = {"event": "data", "data_event": event_name}
        if text:
            entry["text"] = str(text)[:400]
        if isinstance(message, dict) and "state" in message:
            entry["state"] = message.get("state")
        _log(_OUT, entry)
        if event_name == "barge-in":
            # The agent echoed our barge-in: cancellation was accepted.
            self._barge_in_echoed = True
            return
        # Auto barge-in: send the same control message the browser's audio-level
        # monitor sends, and retry on each subsequent "speaking" report until
        # the agent echoes it back. The first send can race the agent's own
        # SPEAKING entry (the pipeline ignores barge-in when not speaking), so
        # a single best-effort send would be flaky by construction.
        if (
            self._auto_barge_in
            and event_name == "state"
            and message.get("state") == "speaking"
            and not self._barge_in_echoed
            and not self._barge_in_pending
        ):
            self._barge_in_sent = True
            self._barge_in_pending = True
            task = asyncio.ensure_future(self._send_barge_in())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    def _on_disconnected(self, *_: Any) -> None:
        self.disconnected.set()

    # --- audio observation / actions --------------------------------------

    async def _consume_audio(self, track: Any) -> None:
        """Tally subscribed audio frames + RMS so tests can assert flow."""
        try:
            stream = self._rtc.AudioStream(track)
            async for frame_event in stream:
                frame = frame_event.frame
                payload, rate = _frame_to_mono_pcm16(frame, self._rtc)
                self._audio_frames += 1
                level = _rms(payload)
                first_audible = self._nonzero_frames == 0
                if level > 0.01:
                    self._nonzero_frames += 1
                self._last_rms = level
                self._peak_rms = max(self._peak_rms, level)
                # Periodic progress plus an immediate line the first time any
                # audible frame arrives, so a test never has to sample exactly
                # on a 100-frame boundary (the cadence is shared across tracks).
                if self._audio_frames % 100 == 0 or (first_audible and level > 0.01):
                    _log(
                        _OUT,
                        {
                            "event": "audio_progress",
                            "frames": self._audio_frames,
                            "nonzero_frames": self._nonzero_frames,
                            "rms": round(level, 4),
                            "peak_rms": round(self._peak_rms, 4),
                            "rate": rate,
                        },
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — observation only
            _log(_OUT, {"event": "audio_consume_error", "detail": type(exc).__name__})

    async def _send_barge_in(self) -> None:
        try:
            assert self._room is not None
            await self._room.local_participant.publish_data(
                json.dumps({"event": "barge-in"}).encode("utf-8"),
            )
            _log(_OUT, {"event": "barge_in_sent"})
        except Exception as exc:  # noqa: BLE001
            _log(_OUT, {"event": "barge_in_send_error", "detail": type(exc).__name__})
        finally:
            self._barge_in_pending = False


_OUT: Any = None


def main() -> None:
    parser = argparse.ArgumentParser(description="Room probe for browser acceptance tests")
    parser.add_argument("--room", required=True)
    parser.add_argument("--identity", default=None)
    parser.add_argument("--seconds", type=int, default=90)
    parser.add_argument("--out", required=True)
    parser.add_argument("--auto-barge-in", action="store_true")
    args = parser.parse_args()

    global _OUT
    _OUT = open(args.out, "w", encoding="utf-8")  # noqa: SIM115 - tool's own lifetime

    identity = args.identity or f"emma-probe-{time.time_ns() % 100000:05d}"
    settings = get_settings()
    if not settings.LIVEKIT_URL or not settings.LIVEKIT_API_KEY or not settings.LIVEKIT_API_SECRET:
        print("probe: LIVEKIT_* credentials missing", file=sys.stderr)
        raise SystemExit(2)

    probe = RoomProbe(args.room, identity, args.auto_barge_in)
    summary = asyncio.run(probe.run(args.seconds, _OUT))
    _OUT.close()
    print("PROBE_DONE " + json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
