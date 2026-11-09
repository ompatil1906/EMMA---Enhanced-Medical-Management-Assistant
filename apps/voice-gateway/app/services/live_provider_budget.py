"""Application-side spend guard for controlled live provider acceptance runs.

Day 18 requires every live call to be capped, counted and reported:

* Deepgram: at most 3 streaming sessions, each at most 15 s of audio.
* ElevenLabs: at most 3 synthesis requests, each at most 300 characters.
* No retries after auth/billing failures, no parallel live requests.

This guard is the code that makes those limits real rather than aspirational —
the same pattern as `app/services/openai_budget.py`: check *before* the network
call, record *after* it, and refuse loudly instead of degrading. It is a local
control only; it is not provider billing and never replaces it.

Two layers use the same limits:

* `LiveProviderBudget` — in-process counters for the standalone live harness.
* `LiveProviderLedger` (Day 18C) — a **file-backed, lock-protected** ledger used
  by the real runtime (`SpeechProviderFactory` → `BudgetedSTT`/`BudgetedTTS`),
  so concurrent requests and agent restarts cannot reset the budget.

Usage (runtime):

    ledger = LiveProviderLedger(path=..., limits=..., run_id=...)
    ledger.reserve_stt_session()                # before contacting the provider
    ledger.add_stt_audio(seconds)
    ledger.reserve_tts_request(text)
    ledger.snapshot()                           # for /health/ready

This is an **application-side** request/usage guard. It is not provider billing
and can never be described as a provider-enforced spending limit.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

try:  # POSIX only; the supported targets are macOS (dev) and Linux (Docker).
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    fcntl = None  # type: ignore[assignment]

Provider = Literal["deepgram", "elevenlabs"]


class LiveBudgetError(RuntimeError):
    """A live-provider call would exceed the configured Day 18 caps."""


@dataclass(frozen=True)
class LiveProviderLimits:
    """Hard caps for one acceptance run (defaults match the Day 18 brief)."""

    max_deepgram_sessions: int = 3
    max_deepgram_audio_seconds: float = 15.0
    max_elevenlabs_requests: int = 3
    max_elevenlabs_chars: int = 300

    def __post_init__(self) -> None:
        for name, value in (
            ("max_deepgram_sessions", self.max_deepgram_sessions),
            ("max_elevenlabs_requests", self.max_elevenlabs_requests),
            ("max_elevenlabs_chars", self.max_elevenlabs_chars),
        ):
            if value <= 0:
                raise LiveBudgetError(f"{name} must be positive (got {value})")
        if self.max_deepgram_audio_seconds <= 0:
            raise LiveBudgetError("max_deepgram_audio_seconds must be positive")

    @classmethod
    def from_env(cls) -> LiveProviderLimits:
        """Read optional overrides; the defaults are the approved Day 18 caps."""
        return cls(
            max_deepgram_sessions=int(os.getenv("EMMA_LIVE_MAX_DEEPGRAM_SESSIONS", "3")),
            max_deepgram_audio_seconds=float(
                os.getenv("EMMA_LIVE_MAX_DEEPGRAM_AUDIO_SECONDS", "15")
            ),
            max_elevenlabs_requests=int(os.getenv("EMMA_LIVE_MAX_ELEVENLABS_REQUESTS", "3")),
            max_elevenlabs_chars=int(os.getenv("EMMA_LIVE_MAX_ELEVENLABS_CHARS", "300")),
        )


@dataclass
class LiveProviderBudget:
    """Counts and caps live-provider usage for one acceptance run."""

    limits: LiveProviderLimits = field(default_factory=LiveProviderLimits)
    deepgram_sessions: int = 0
    deepgram_audio_seconds: float = 0.0
    elevenlabs_requests: int = 0
    elevenlabs_chars: int = 0
    refused: list[str] = field(default_factory=list)

    # --- checks (call BEFORE the network request) -----------------------------

    def check_deepgram_session(self, audio_seconds: float) -> None:
        if audio_seconds <= 0 or not math.isfinite(audio_seconds):
            raise LiveBudgetError(f"audio_seconds must be positive (got {audio_seconds})")
        if audio_seconds > self.limits.max_deepgram_audio_seconds:
            self.refused.append(
                f"deepgram session refused: {audio_seconds:.1f}s > "
                f"{self.limits.max_deepgram_audio_seconds:.1f}s cap"
            )
            raise LiveBudgetError(self.refused[-1])
        if self.deepgram_sessions + 1 > self.limits.max_deepgram_sessions:
            self.refused.append(
                f"deepgram session refused: {self.deepgram_sessions + 1} > "
                f"{self.limits.max_deepgram_sessions} session cap"
            )
            raise LiveBudgetError(self.refused[-1])

    def check_elevenlabs_request(self, text: str) -> None:
        chars = len(text.strip())
        if chars == 0:
            raise LiveBudgetError("elevenlabs text must not be empty")
        if chars > self.limits.max_elevenlabs_chars:
            self.refused.append(
                f"elevenlabs request refused: {chars} chars > "
                f"{self.limits.max_elevenlabs_chars} char cap"
            )
            raise LiveBudgetError(self.refused[-1])
        if self.elevenlabs_requests + 1 > self.limits.max_elevenlabs_requests:
            self.refused.append(
                f"elevenlabs request refused: {self.elevenlabs_requests + 1} > "
                f"{self.limits.max_elevenlabs_requests} request cap"
            )
            raise LiveBudgetError(self.refused[-1])

    # --- recording (call AFTER a successful claim) ----------------------------

    def record_deepgram_session(self, audio_seconds: float) -> None:
        self.deepgram_sessions += 1
        self.deepgram_audio_seconds += audio_seconds

    def record_elevenlabs_request(self, text: str) -> None:
        self.elevenlabs_requests += 1
        self.elevenlabs_chars += len(text.strip())

    # --- reporting -------------------------------------------------------------

    def report(self) -> dict[str, object]:
        """Usage summary for the acceptance report (no secrets involved)."""
        return {
            "limits": {
                "max_deepgram_sessions": self.limits.max_deepgram_sessions,
                "max_deepgram_audio_seconds": self.limits.max_deepgram_audio_seconds,
                "max_elevenlabs_requests": self.limits.max_elevenlabs_requests,
                "max_elevenlabs_chars": self.limits.max_elevenlabs_chars,
            },
            "used": {
                "deepgram_sessions": self.deepgram_sessions,
                "deepgram_audio_seconds": round(self.deepgram_audio_seconds, 2),
                "elevenlabs_requests": self.elevenlabs_requests,
                "elevenlabs_chars": self.elevenlabs_chars,
            },
            "refused": list(self.refused),
        }

    def summary_line(self) -> str:
        """Single-line usage string for logs/reports."""
        return (
            f"deepgram sessions {self.deepgram_sessions}/"
            f"{self.limits.max_deepgram_sessions} "
            f"({self.deepgram_audio_seconds:.1f}s audio), "
            f"elevenlabs requests {self.elevenlabs_requests}/"
            f"{self.limits.max_elevenlabs_requests} "
            f"({self.elevenlabs_chars} chars)"
        )


# --- Day 18C: persistent, concurrency-safe ledger ------------------------------

_LEDGER_VERSION = 1
# Fixed-schema counters only: the file can never grow without bound.
_MAX_LEDGER_BYTES = 64 * 1024
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class LiveLedgerError(LiveBudgetError):
    """Base class for ledger problems (unavailable, corrupt, unusable)."""


class LiveLedgerUnavailable(LiveLedgerError):
    """The ledger cannot be locked or written (e.g. no fcntl, bad directory)."""


class LiveLedgerCorrupt(LiveLedgerError):
    """The ledger exists but its contents are not a valid record.

    Deliberately fatal: silently resetting a corrupt ledger would hand the
    process an unlimited budget after a partial write.
    """


@dataclass
class LiveProviderLedger:
    """File-backed, run-scoped live-provider usage ledger.

    Concurrency: every mutation takes an exclusive `fcntl.flock` on a sidecar
    lock file, so threads *and* processes (gateway + agent worker) serialise.
    Durability: the record is written to a temporary file and published with
    `os.replace`, so an interrupted write leaves the previous record intact
    (a leftover `.tmp` file is never read).
    """

    path: Path
    limits: LiveProviderLimits = field(default_factory=LiveProviderLimits)
    run_id: str = "day18-live"

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if not _RUN_ID_RE.match(self.run_id or ""):
            raise LiveLedgerError(
                "ledger run id must match [A-Za-z0-9._-]{1,64} (no paths or secrets)"
            )

    # --- paths ---------------------------------------------------------------

    @property
    def lock_path(self) -> Path:
        return self.path.with_name(self.path.name + ".lock")

    @property
    def _tmp_path(self) -> Path:
        return self.path.with_name(f"{self.path.name}.tmp.{os.getpid()}")

    # --- locking -------------------------------------------------------------

    @contextmanager
    def _locked(self) -> Iterator[None]:
        if fcntl is None:  # pragma: no cover - non-POSIX
            raise LiveLedgerUnavailable(
                "live provider ledger requires POSIX file locking (fcntl); "
                "the guard refuses to run unprotected on this platform"
            )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            raise LiveLedgerUnavailable(f"cannot open ledger lock file: {exc.strerror}") from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    # --- record handling -----------------------------------------------------

    def _new_record(self) -> dict[str, Any]:
        now = round(time.time(), 3)
        return {
            "version": _LEDGER_VERSION,
            "run_id": self.run_id,
            "created_at": now,
            "updated_at": now,
            "deepgram": {"sessions": 0, "audio_seconds": 0.0},
            "elevenlabs": {"requests": 0, "chars": 0},
        }

    def _load(self) -> dict[str, Any]:
        """Read + validate the record. Never resets on malformed content."""
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return self._new_record()
        except OSError as exc:
            raise LiveLedgerUnavailable(f"cannot stat ledger: {exc.strerror}") from exc

        if size > _MAX_LEDGER_BYTES:
            raise LiveLedgerCorrupt(f"ledger is larger than {_MAX_LEDGER_BYTES} bytes")
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LiveLedgerUnavailable(f"cannot read ledger: {exc.strerror}") from exc
        if not raw.strip():
            raise LiveLedgerCorrupt("ledger is empty (interrupted write?)")

        try:
            record = json.loads(raw)
        except ValueError as exc:
            raise LiveLedgerCorrupt(f"ledger is not valid JSON: {exc}") from exc
        return self._validate(record)

    def _validate(self, record: Any) -> dict[str, Any]:
        if not isinstance(record, dict):
            raise LiveLedgerCorrupt("ledger root must be an object")
        if record.get("version") != _LEDGER_VERSION:
            raise LiveLedgerCorrupt(
                f"unsupported ledger version {record.get('version')!r} (expected {_LEDGER_VERSION})"
            )
        run_id = record.get("run_id")
        if not isinstance(run_id, str) or not _RUN_ID_RE.match(run_id):
            raise LiveLedgerCorrupt("ledger run_id is invalid")

        deepgram = record.get("deepgram")
        elevenlabs = record.get("elevenlabs")
        if not isinstance(deepgram, dict) or not isinstance(elevenlabs, dict):
            raise LiveLedgerCorrupt("ledger sections must be objects")

        sessions = deepgram.get("sessions")
        audio_seconds = deepgram.get("audio_seconds")
        requests = elevenlabs.get("requests")
        chars = elevenlabs.get("chars")

        for name, value in (("sessions", sessions), ("requests", requests), ("chars", chars)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise LiveLedgerCorrupt(f"ledger {name} must be a non-negative integer")
        if not isinstance(audio_seconds, (int, float)) or isinstance(audio_seconds, bool):
            raise LiveLedgerCorrupt("ledger audio_seconds must be numeric")
        if not math.isfinite(float(audio_seconds)) or float(audio_seconds) < 0:
            raise LiveLedgerCorrupt("ledger audio_seconds must be finite and non-negative")

        record["deepgram"]["audio_seconds"] = float(audio_seconds)
        return record

    def _save(self, record: dict[str, Any]) -> None:
        record["updated_at"] = round(time.time(), 3)
        payload = json.dumps(record, separators=(",", ":"), sort_keys=True)
        try:
            with open(self._tmp_path, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(self._tmp_path, self.path)
        except OSError as exc:
            try:
                self._tmp_path.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - best effort
                pass
            raise LiveLedgerUnavailable(f"cannot write ledger: {exc.strerror}") from exc

    @contextmanager
    def _mutate(self) -> Iterator[dict[str, Any]]:
        """Locked read-modify-write; commits on normal exit only."""
        with self._locked():
            record = self._load()
            if record.get("run_id") != self.run_id:
                # A different run id means a deliberate new acceptance run.
                record = self._new_record()
            yield record
            self._save(record)

    # --- public API ----------------------------------------------------------

    def ensure_ready(self) -> None:
        """Fail closed if the ledger cannot be locked/read right now."""
        with self._locked():
            self._load()

    def snapshot(self) -> dict[str, Any]:
        """Current usage + capacity, without mutating anything.

        A record written by a *different* run id is reported as a fresh budget
        (and only rewritten on the next reservation), so a read never mutates
        the file and a new run never inherits the previous run's usage.
        """
        with self._locked():
            record = self._load()
            if record.get("run_id") != self.run_id:
                record = self._new_record()
        usage = {
            "deepgram_sessions": int(record["deepgram"]["sessions"]),
            "deepgram_audio_seconds": round(float(record["deepgram"]["audio_seconds"]), 3),
            "elevenlabs_requests": int(record["elevenlabs"]["requests"]),
            "elevenlabs_chars": int(record["elevenlabs"]["chars"]),
        }
        limits = {
            "deepgram_sessions": self.limits.max_deepgram_sessions,
            "deepgram_audio_seconds": self.limits.max_deepgram_audio_seconds,
            "elevenlabs_requests": self.limits.max_elevenlabs_requests,
            "elevenlabs_chars": self.limits.max_elevenlabs_chars,
        }
        remaining = {
            "deepgram_sessions": max(0, limits["deepgram_sessions"] - usage["deepgram_sessions"]),
            "deepgram_audio_seconds": round(
                max(0.0, limits["deepgram_audio_seconds"] - usage["deepgram_audio_seconds"]), 3
            ),
            "elevenlabs_requests": max(
                0, limits["elevenlabs_requests"] - usage["elevenlabs_requests"]
            ),
            "elevenlabs_chars": max(0, limits["elevenlabs_chars"] - usage["elevenlabs_chars"]),
        }
        exhausted = (
            remaining["deepgram_sessions"] <= 0
            or remaining["deepgram_audio_seconds"] <= 0
            or remaining["elevenlabs_requests"] <= 0
            or remaining["elevenlabs_chars"] <= 0
        )
        return {
            "run_id": record["run_id"],
            "usage": usage,
            "limits": limits,
            "remaining": remaining,
            "exhausted": exhausted,
        }

    def reserve_stt_session(self) -> dict[str, Any]:
        """Reserve one Deepgram session (counted before the connection opens)."""
        with self._mutate() as record:
            sessions = int(record["deepgram"]["sessions"])
            if sessions + 1 > self.limits.max_deepgram_sessions:
                raise LiveBudgetError(
                    f"deepgram session refused: {sessions + 1} > "
                    f"{self.limits.max_deepgram_sessions} session cap (run "
                    f"{self.run_id!r})"
                )
            record["deepgram"]["sessions"] = sessions + 1
        return self.snapshot()

    def add_stt_audio(self, seconds: float) -> None:
        """Record streamed audio duration for the current run."""
        if seconds <= 0 or not math.isfinite(seconds):
            return
        with self._mutate() as record:
            record["deepgram"]["audio_seconds"] = round(
                float(record["deepgram"]["audio_seconds"]) + seconds, 6
            )

    def audio_seconds_used(self) -> float:
        return float(self.snapshot()["usage"]["deepgram_audio_seconds"])

    def reserve_tts_request(self, text: str) -> dict[str, Any]:
        """Reserve one ElevenLabs request after validating its size."""
        chars = len(text.strip())
        if chars == 0:
            raise LiveBudgetError("elevenlabs text must not be empty")
        if chars > self.limits.max_elevenlabs_chars:
            raise LiveBudgetError(
                f"elevenlabs request refused: {chars} chars > "
                f"{self.limits.max_elevenlabs_chars} char cap"
            )
        with self._mutate() as record:
            requests = int(record["elevenlabs"]["requests"])
            if requests + 1 > self.limits.max_elevenlabs_requests:
                raise LiveBudgetError(
                    f"elevenlabs request refused: {requests + 1} > "
                    f"{self.limits.max_elevenlabs_requests} request cap (run "
                    f"{self.run_id!r})"
                )
            record["elevenlabs"]["requests"] = requests + 1
            record["elevenlabs"]["chars"] = int(record["elevenlabs"]["chars"]) + chars
        return self.snapshot()


def build_runtime_ledger(settings: Any) -> LiveProviderLedger:
    """Construct the runtime ledger from settings (single construction point)."""
    return LiveProviderLedger(
        path=settings.live_ledger_path,
        limits=settings.live_provider_limits,
        run_id=settings.EMMA_LIVE_RUN_ID,
    )


def live_provider_selected(settings: Any) -> bool:
    """True when a *live* speech provider is configured (mock/Azure excluded)."""
    return str(settings.STT_PROVIDER) == "deepgram" or str(settings.TTS_PROVIDER) == "elevenlabs"


def live_guard_status(settings: Any) -> dict[str, Any]:
    """Secret-free guard status for `/health/ready` and operator logs.

    Never raises: a broken ledger is reported as `available: false` with a
    reason, which makes live-provider readiness fail closed.
    """
    enabled = bool(settings.live_guard_enabled)
    applicable = enabled and live_provider_selected(settings)
    attestation = {
        # USER-ATTESTED flags: acknowledgements, never evidence.
        "credentials_rotated": bool(settings.EMMA_LIVE_CREDENTIALS_ROTATED),
        "spend_approved": bool(settings.EMMA_LIVE_PROVIDER_SPEND_APPROVED),
    }
    attestation["ok"] = bool(attestation["credentials_rotated"] and attestation["spend_approved"])
    status: dict[str, Any] = {
        "enabled": enabled,
        "applicable": applicable,
        "attestation": attestation,
        "ledger_available": False,
        "ledger_file": None,
        "run_id": None,
        "usage": None,
        "limits": None,
        "remaining": None,
        "exhausted": None,
        "configuration_ready": not applicable or attestation["ok"],
        "error": None,
    }
    if applicable and not attestation["ok"]:
        missing = [
            name
            for name, present in (
                ("EMMA_LIVE_CREDENTIALS_ROTATED", attestation["credentials_rotated"]),
                ("EMMA_LIVE_PROVIDER_SPEND_APPROVED", attestation["spend_approved"]),
            )
            if not present
        ]
        status["error"] = (
            "live provider spend is not authorised (missing user attestation: "
            + ", ".join(missing)
            + ")"
        )
    if not applicable:
        # Guard off, or no live provider configured: nothing to protect, so
        # readiness is not degraded by a guard problem (mock dev keeps working).
        status["configuration_ready"] = True
        return status

    try:
        ledger = build_runtime_ledger(settings)
        snapshot = ledger.snapshot()
    except LiveBudgetError as exc:
        # Includes LedgerCorrupt: fail closed, never fall back to "no limit".
        status["error"] = f"{type(exc).__name__}: {exc}"
        status["ledger_file"] = Path(settings.live_ledger_path).name
        status["configuration_ready"] = False
        return status
    except Exception as exc:  # noqa: BLE001 — health must never raise
        status["error"] = f"{type(exc).__name__}"
        status["configuration_ready"] = False
        return status

    status.update(
        {
            "ledger_available": True,
            # Basename only: the operator needs to identify the ledger, not the
            # machine's directory layout.
            "ledger_file": ledger.path.name,
            "run_id": snapshot["run_id"],
            "usage": snapshot["usage"],
            # Prefix limits with `max_` so a status consumer cannot confuse a
            # ceiling with the usage counters next to it.
            "limits": {f"max_{name}": value for name, value in snapshot["limits"].items()},
            "remaining": snapshot["remaining"],
            "exhausted": snapshot["exhausted"],
            # A usable ledger is necessary but not sufficient: the operator
            # attestations must also be present.
            "configuration_ready": attestation["ok"],
        }
    )
    return status


def default_ledger_path() -> Path:
    """Ledger location: explicit override, else a per-user temp file.

    The default is deliberately outside the repository: the ledger is runtime
    state, and a committed ledger would hand the next run an exhausted budget.
    """
    override = os.getenv("EMMA_LIVE_LEDGER_PATH")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "emma-live-provider-ledger.json"


__all__ = [
    "LiveBudgetError",
    "live_provider_selected",
    "build_runtime_ledger",
    "live_guard_status",
    "LiveLedgerCorrupt",
    "LiveLedgerError",
    "LiveLedgerUnavailable",
    "LiveProviderBudget",
    "LiveProviderLedger",
    "LiveProviderLimits",
    "Provider",
    "default_ledger_path",
]
