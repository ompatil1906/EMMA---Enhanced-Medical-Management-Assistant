#!/usr/bin/env python3
"""EMMA voice gateway — local Exotel protocol WebSocket simulator.

Validates the Day-2 echo path WITHOUT a real telephone call or Exotel account:
connects to `/ws/exotel`, plays the provider role (start → media frames → stop),
and verifies every echoed payload byte-for-byte.

Two modes (assignment §8 requirement — never confuse them):

* `simulation` (default) — local test flow, authenticated via `X-WS-Token`.
  ✔ proves: gateway transport + adapter + echo loop work.
  ✘ does NOT prove: a real PSTN call, Exotel-side behaviour, or the exact
    inbound frames a configured live Exotel call flow emits.

* `exotel` — the verified AgentStream provider flow: `Authorization: Basic`
  header, `?sample-rate=` query parameter, and NO synthetic `connected` frame
  (that event belongs to the Exotel platform). Use it only against a gateway
  started with matching EXOTEL_MODE=live credentials.

Exit codes: 0 success · 2 usage/invariant violation · 3 payload/protocol mismatch.

Run (from apps/voice-gateway so the project venv is used):
    uv run python ../../scripts/websocket_simulator.py
    uv run python ../../scripts/websocket_simulator.py --mode exotel \
        --url wss://<public-gateway>/ws/exotel --live-key KEY --live-token TOKEN
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import os
import struct
import sys
import time

DEFAULT_WS_URL = "ws://127.0.0.1:8000/ws/exotel"
SUPPORTED_SAMPLE_RATES = (8000, 16000, 24000)  # verified AgentStream set
FRAME_MS = 20  # 20 ms cadence → 160/320-sample chunks at 8/16/24 kHz


def synth_chunk(index: int, sample_rate: int, tone_hz: float = 440.0) -> bytes:
    """One deterministic sine frame (int16 LE mono) — synthetic, never real audio."""
    samples = FRAME_MS * sample_rate // 1000
    t0 = index * FRAME_MS / 1000.0
    return struct.pack(
        f"<{samples}h",
        *(
            int(8000 * math.sin(2 * math.pi * tone_hz * (t0 + i / sample_rate)))
            for i in range(samples)
        ),
    )


def connect_media_start(stream_sid: str, call_sid: str, sample_rate: int) -> list[str]:
    """Verified `start` envelope (metadata + media_format)."""
    _ = call_sid  # reserved for future report output
    start = json.dumps(
        {
            "event": "start",
            "stream_sid": stream_sid,
            "call_sid": call_sid,
            "account_sid": "acct-simulator",
            "from": "+441234000001",
            "to": "+441234000999",
            "custom_parameters": {"practice": "sim-practice"},
            "media_format": {"encoding": "PCM", "sample_rate": sample_rate, "bit_rate": 128000},
        },
        separators=(",", ":"),
    )
    return [start]


def media_message(chunk: str, timestamp: str) -> str:
    """Verified inbound `media` envelope: exactly {event, chunk, timestamp} in both modes."""
    return json.dumps(
        {"event": "media", "chunk": chunk, "timestamp": timestamp}, separators=(",", ":")
    )


def stop_message(reason: str = "stopped") -> str:
    """Verified `stop` envelope: reason ∈ {stopped, callended}."""
    return json.dumps({"event": "stop", "reason": reason}, separators=(",", ":"))


def build_url(base_url: str, sample_rate: int) -> str:
    return f"{base_url}?sample-rate={sample_rate}"


async def run_simulator(args: argparse.Namespace) -> int:
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    # Authentication: separate mechanisms per mode (assignment §9).
    headers: dict[str, str] = {}
    if args.mode == "simulation":
        if args.ws_token:
            headers["X-WS-Token"] = args.ws_token
    elif not (args.live_key and args.live_token):
        print("error: --mode exotel requires --live-key and --live-token", file=sys.stderr)
        return 2
    else:
        token = base64.b64encode(f"{args.live_key}:{args.live_token}".encode()).decode("ascii")
        headers["Authorization"] = f"Basic {token}"

    url = build_url(args.url, args.sample_rate)
    frame_bytes = FRAME_MS * args.sample_rate // 1000 * 2
    stream_sid = f"sim-stream-{os.getpid():x}"
    call_sid = f"sim-call-{os.getpid():x}"
    label = "exotel" if args.mode == "exotel" else "simulation"
    print(f"[{label}] connecting → {url}")

    sent_frames = 0
    rtts: list[float] = []
    close_code: int | None = None
    failure: str | None = None

    async with connect(url, additional_headers=headers, open_timeout=5) as ws:
        if args.mode == "simulation":
            # `connected` exists in the verified event family; the simulator only
            # synthesizes it in simulation mode (in exotel mode the platform sends it).
            await ws.send(json.dumps({"event": "connected"}, separators=(",", ":")))
        for message in connect_media_start(stream_sid, call_sid, args.sample_rate):
            await ws.send(message)

        try:
            for index in range(args.frames):
                chunk = synth_chunk(index, args.sample_rate)
                chunk_b64 = base64.b64encode(chunk).decode("ascii")
                await ws.send(media_message(chunk_b64, str(index * FRAME_MS)))
                started = time.perf_counter()
                reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                rtts.append((time.perf_counter() - started) * 1000.0)
                if reply.get("event") != "media":
                    failure = f"unexpected non-media reply: {json.dumps(reply)[:120]}"
                    break
                if reply.get("stream_sid") not in (stream_sid, None):
                    failure = f"echo stream_sid mismatch: {reply.get('stream_sid')!r}"
                    break
                echoed = base64.b64decode(reply["media"]["payload"])
                if echoed != chunk:
                    failure = (
                        f"frame {index}: payload mismatch "
                        f"(echoed {len(echoed)} bytes ≠ sent {len(chunk)} bytes)"
                    )
                    break
                sent_frames += 1
                if args.interval > 0:
                    await asyncio.sleep(args.interval)

            if failure is None:
                await ws.send(stop_message("stopped"))
                # Drain echoes (if any remain) until the gateway's close frame.
                while True:
                    await asyncio.wait_for(ws.recv(), timeout=5.0)
        except ConnectionClosed as exc:
            if exc.rcvd is None:
                failure = failure or "gateway vanished without a close frame"
            else:
                close_code = exc.rcvd.code
        except TimeoutError:
            failure = failure or "no reply/close within 5.0s timeout"

    summary = {
        "mode": label,
        "url": args.url,
        "sample_rate": args.sample_rate,
        "frame_bytes": frame_bytes,
        "frames_expected": args.frames,
        "frames_matched_byte_for_byte": sent_frames,
        "close_code": close_code,
        "mismatch": failure,
        "rtt_ms_avg": round(sum(rtts) / len(rtts), 2) if rtts else None,
        "rtt_ms_max": round(max(rtts), 2) if rtts else None,
    }

    if failure is not None:
        print(json.dumps({"status": "fail", **summary}, indent=2))
        return 3
    if close_code != 1000:
        print(json.dumps({"status": "fail", "error": "expected close 1000", **summary}, indent=2))
        return 3
    if args.json:
        print(json.dumps({"status": "ok", **summary}, indent=2))
    else:
        print(
            f"[{label}] audio: frame={frame_bytes}B rate={args.sample_rate} "
            f"frames={sent_frames}/{args.frames} matched byte-for-byte"
        )
        if summary["rtt_ms_avg"] is not None:
            print(f"[{label}] round-trip: avg={summary['rtt_ms_avg']}ms max={summary['rtt_ms_max']}ms")
        print(f"[{label}] closed cleanly (1000)")
        print("NOTICE: simulation success ≠ a real Exotel PSTN call working.")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--url", default=DEFAULT_WS_URL, help="WebSocket URL (default local gateway)")
    parser.add_argument(
        "--mode", choices=["simulation", "exotel"], default="simulation",
        help="simulation = local flow simulated envelope · exotel = verified provider flow",
    )
    parser.add_argument(
        "--sample-rate", type=int, choices=SUPPORTED_SAMPLE_RATES, default=8000,
        help="negotiated `?sample-rate=` (verified Exotel set)",
    )
    parser.add_argument("--frames", type=int, default=10, help="audio frames to echo-test")
    parser.add_argument(
        "--interval", type=float, default=0.01, help="seconds between frames (0 → max rate)"
    )
    parser.add_argument("--ws-token", default="", help="X-WS-Token (simulation-mode auth)")
    parser.add_argument("--live-key", default="", help="EXOTEL_API_KEY (exotel-mode auth)")
    parser.add_argument("--live-token", default="", help="EXOTEL_API_TOKEN (exotel-mode auth)")
    parser.add_argument("--json", action="store_true", help="JSON summary output")
    parsed = parser.parse_args(argv)
    if parsed.frames < 1:
        parser.error("--frames must be ≥ 1")
    return parsed


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_simulator(parse_args())))
