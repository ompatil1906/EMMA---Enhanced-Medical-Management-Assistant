"""Generate a deterministic spoken WAV fixture locally (no network, no cost).

Day 18: the live provider acceptance tests need a *known* short speech sample
so a transcript can be compared against a reference. This tool synthesises the
phrase with the platform's speech synthesiser (macOS `say`) and converts it to
the pipeline's wire format with a local converter (`afconvert`, then `ffmpeg`
as a fallback). It never touches a provider API, never uploads anything, and
never uses a human recording.

Output format: WAV container, PCM16 little-endian, mono, 16 kHz — the format
the EMMA pipeline negotiates and what Deepgram receives as `linear16`.

Usage (from `apps/voice-gateway`):

    python -m tools.make_speech_fixture \
        --text "Hello, I would like to book a routine GP appointment." \
        --out tests/fixtures/speech_gp_appointment.wav

The tool prints measurements (duration, sample rate, channels, peak/RMS level)
so the fixture can be checked for *actually containing speech* rather than
silence or a synthetic tone.
"""

from __future__ import annotations

import argparse
import array
import math
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

_TARGET_RATE = 16_000
_TARGET_CHANNELS = 1
_TARGET_WIDTH = 2  # PCM16


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if path is None:
        raise SystemExit(
            f"{tool!r} not found on PATH. On macOS install the Xcode command line "
            "tools (say/afconvert) or ffmpeg."
        )
    return path


def _append_silence(path: Path, extra_seconds: float) -> None:
    """Append digital silence so the phrase is spoken once per loop.

    Chromium's `--use-file-for-fake-audio-capture` loops the file. For a
    single-utterance live test we want the phrase followed by a long pause, so
    the provider sees one end-of-turn instead of the phrase repeating under the
    test's feet.
    """
    if extra_seconds <= 0:
        return
    with wave.open(str(path), "rb") as handle:
        params = handle.getparams()
        frames = handle.readframes(handle.getnframes())

    silence = b"\x00\x00" * int(params.framerate * extra_seconds) * params.nchannels
    with wave.open(str(path), "wb") as handle:
        handle.setparams(params)
        handle.writeframes(frames + silence)


def synthesise(text: str, voice: str, out: Path) -> None:
    """Synthesise `text` to `out` as PCM16 mono 16 kHz WAV."""
    say = _require("say")
    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="emma-fixture-") as tmp:
        aiff = Path(tmp) / "phrase.aiff"
        subprocess.run([say, "-v", voice, "-o", str(aiff), text], check=True)

        afconvert = shutil.which("afconvert")
        if afconvert:
            subprocess.run(
                [
                    afconvert,
                    "-f",
                    "WAVE",
                    "-d",
                    f"LEI16@{_TARGET_RATE}",
                    "-c",
                    str(_TARGET_CHANNELS),
                    str(aiff),
                    str(out),
                ],
                check=True,
            )
            return

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise SystemExit("neither afconvert nor ffmpeg is available to convert the audio")
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(aiff),
                "-ac",
                str(_TARGET_CHANNELS),
                "-ar",
                str(_TARGET_RATE),
                "-acodec",
                "pcm_s16le",
                str(out),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def measure(path: Path) -> dict[str, float | int]:
    """Read back the WAV and report format + level measurements."""
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        frames = handle.getnframes()
        raw = handle.readframes(frames)

    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
    if sys.byteorder == "big":  # pragma: no cover - macOS/Linux CI are little-endian
        samples.byteswap()

    peak = max((abs(s) for s in samples), default=0) / 32768.0
    rms = math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768.0 if samples else 0.0
    voiced = sum(1 for s in samples if abs(s) > 0.01 * 32768)
    return {
        "sample_rate": rate,
        "channels": channels,
        "width": width,
        "frames": frames,
        "duration_s": round(frames / rate, 3) if rate else 0.0,
        "peak_dbfs": round(20 * math.log10(peak), 1) if peak > 0 else -999.0,
        "rms_dbfs": round(20 * math.log10(rms), 1) if rms > 0 else -999.0,
        "voiced_fraction": round(voiced / len(samples), 3) if samples else 0.0,
        "bytes": len(raw),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--text",
        default="Hello, I would like to book a routine GP appointment.",
        help="phrase to synthesise (keep short: live tests are duration-capped)",
    )
    parser.add_argument("--voice", default="Daniel", help="platform voice (en-GB)")
    parser.add_argument("--out", required=True, help="output .wav path")
    parser.add_argument(
        "--trailing-silence-ms",
        type=int,
        default=0,
        help="append silence after the phrase (browser fake-mic files loop)",
    )
    args = parser.parse_args()

    out = Path(args.out)
    synthesise(args.text, args.voice, out)
    _append_silence(out, args.trailing_silence_ms / 1000.0)

    stats = measure(out)
    print(f"wrote {out}")
    print(f"text      : {args.text!r} ({len(args.text)} chars)")
    for key, value in stats.items():
        print(f"{key:10}: {value}")

    if stats["sample_rate"] != _TARGET_RATE or stats["channels"] != _TARGET_CHANNELS:
        raise SystemExit(f"unexpected format: {stats['sample_rate']} Hz / {stats['channels']} ch")
    if stats["peak_dbfs"] < -40:
        raise SystemExit("fixture is effectively silent — refusing to ship it as a speech sample")
    if args.trailing_silence_ms <= 0 and stats["duration_s"] > 15:
        raise SystemExit("fixture exceeds the 15 s live-session cap — shorten the phrase")


if __name__ == "__main__":
    main()
