"""Day 18C: persistent live-provider ledger tests (offline, no provider calls).

What is pinned here:

* creation/validation of the on-disk record (fixed schema, no secrets),
* persistence across a simulated process restart,
* thread- and process-level concurrency (no cap bypass),
* refusal is not counted (commit only happens for accepted calls),
* malformed/empty/oversized ledgers fail closed instead of silently resetting,
* interrupted writes (stale `.tmp`) cannot corrupt the live record,
* deliberate new-run scoping vs accidental reset.
"""

from __future__ import annotations

import json
import multiprocessing
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.services.live_provider_budget import (
    LiveBudgetError,
    LiveLedgerCorrupt,
    LiveLedgerError,
    LiveProviderLedger,
    LiveProviderLimits,
)

_RECORD_KEYS = {"version", "run_id", "created_at", "updated_at", "deepgram", "elevenlabs"}


def _ledger(tmp_path: Path, *, run_id: str = "test-run", **limit_overrides) -> LiveProviderLedger:
    limits = LiveProviderLimits(**limit_overrides)
    return LiveProviderLedger(path=tmp_path / "ledger.json", limits=limits, run_id=run_id)


# --- 1. creation ---------------------------------------------------------------


def test_first_reservation_creates_a_valid_record(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    snapshot = ledger.reserve_stt_session()

    assert ledger.path.exists()
    record = json.loads(ledger.path.read_text())
    assert set(record) == _RECORD_KEYS
    assert record["version"] == 1
    assert record["run_id"] == "test-run"
    assert record["deepgram"] == {"sessions": 1, "audio_seconds": 0.0}
    assert record["elevenlabs"] == {"requests": 0, "chars": 0}
    assert snapshot["usage"]["deepgram_sessions"] == 1


def test_snapshot_does_not_create_or_mutate_the_file(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    snapshot = ledger.snapshot()
    assert snapshot["usage"]["deepgram_sessions"] == 0
    assert not ledger.path.exists()

    ledger.reserve_stt_session()
    before = ledger.path.read_bytes()
    ledger.snapshot()
    assert ledger.path.read_bytes() == before


def test_record_contains_no_secrets_or_personal_data(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.reserve_stt_session()
    ledger.add_stt_audio(1.5)
    ledger.reserve_tts_request("Good morning.")
    raw = ledger.path.read_text().lower()
    for forbidden in ("key", "secret", "token", "authorization", "bearer", "password"):
        assert forbidden not in raw


# --- 2. restart persistence ------------------------------------------------------


def test_usage_survives_a_simulated_worker_restart(tmp_path: Path) -> None:
    first = _ledger(tmp_path)
    first.reserve_stt_session()
    first.reserve_stt_session()

    # A restarted worker builds a brand-new ledger object over the same file.
    restarted = _ledger(tmp_path)
    assert restarted.snapshot()["usage"]["deepgram_sessions"] == 2

    restarted.reserve_stt_session()  # third and final session
    with pytest.raises(LiveBudgetError, match="session cap"):
        _ledger(tmp_path).reserve_stt_session()

    assert json.loads((tmp_path / "ledger.json").read_text())["deepgram"]["sessions"] == 3


def test_new_run_id_starts_a_fresh_budget(tmp_path: Path) -> None:
    previous = _ledger(tmp_path, run_id="run-one")
    for _ in range(3):
        previous.reserve_stt_session()

    next_run = _ledger(tmp_path, run_id="run-two")
    assert next_run.snapshot()["usage"]["deepgram_sessions"] == 0
    next_run.reserve_stt_session()
    assert next_run.snapshot()["usage"]["deepgram_sessions"] == 1


def test_run_id_rejects_paths_and_overlong_values(tmp_path: Path) -> None:
    for bad in ("../escape", "with space", "x" * 65, ""):
        with pytest.raises(LiveLedgerError):
            _ledger(tmp_path, run_id=bad)


# --- 3./4. concurrency -----------------------------------------------------------


def test_threaded_reservations_cannot_exceed_the_cap(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, max_deepgram_sessions=3)

    def attempt(_: int) -> bool:
        try:
            ledger.reserve_stt_session()
            return True
        except LiveBudgetError:
            return False

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(attempt, range(20)))

    assert sum(results) == 3
    assert ledger.snapshot()["usage"]["deepgram_sessions"] == 3


def _reserve_in_child(args: tuple[str, str]) -> bool:
    path, run_id = args
    ledger = LiveProviderLedger(
        path=Path(path),
        limits=LiveProviderLimits(max_deepgram_sessions=2),
        run_id=run_id,
    )
    try:
        ledger.reserve_stt_session()
        return True
    except LiveBudgetError:
        return False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX fork/lock semantics under test")
def test_inter_process_locking_serialises_reservations(tmp_path: Path) -> None:
    """Six processes race for two slots: exactly two may win."""
    path = str(tmp_path / "ledger.json")
    ctx = multiprocessing.get_context("fork")
    with ctx.Pool(processes=6) as pool:
        results = pool.map(_reserve_in_child, [(path, "ipc-run")] * 6)

    assert sum(results) == 2
    record = json.loads((tmp_path / "ledger.json").read_text())
    assert record["deepgram"]["sessions"] == 2


def test_refused_reservation_is_not_counted(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, max_elevenlabs_requests=1)
    ledger.reserve_tts_request("one")

    with pytest.raises(LiveBudgetError):
        ledger.reserve_tts_request("two")

    snapshot = ledger.snapshot()
    assert snapshot["usage"]["elevenlabs_requests"] == 1
    assert snapshot["usage"]["elevenlabs_chars"] == len("one")
    assert snapshot["exhausted"] is True


# --- 5. malformed ledger: fail closed ---------------------------------------------


def test_malformed_ledger_fails_closed_without_resetting(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.path.write_text('{"version": 1, "run_id": "test-run", "deepgram":')

    with pytest.raises(LiveLedgerCorrupt):
        ledger.snapshot()
    with pytest.raises(LiveLedgerCorrupt):
        ledger.reserve_stt_session()
    # The bad bytes are preserved for diagnosis: no silent reset to zero.
    assert "deepgram" in ledger.path.read_text()


def test_empty_ledger_is_treated_as_an_interrupted_write(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.path.write_text("")
    with pytest.raises(LiveLedgerCorrupt, match="empty"):
        ledger.snapshot()


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 99, "run_id": "test-run"},
        {"version": 1, "run_id": "bad id!"},
        {"version": 1, "run_id": "test-run", "deepgram": {"sessions": -1, "audio_seconds": 0}},
        {"version": 1, "run_id": "test-run", "deepgram": {"sessions": "3", "audio_seconds": 0}},
        {"version": 1, "run_id": "test-run", "deepgram": {"sessions": 1, "audio_seconds": "NaN"}},
        {"version": 1, "run_id": "test-run", "elevenlabs": {"requests": True, "chars": 0}},
        ["not", "an", "object"],
    ],
)
def test_invalid_records_are_rejected(tmp_path: Path, payload: object) -> None:
    ledger = _ledger(tmp_path)
    ledger.path.write_text(json.dumps(payload))
    with pytest.raises(LiveLedgerCorrupt):
        ledger.snapshot()


def test_oversized_ledger_is_rejected(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.path.write_text("{" + " " * (70 * 1024) + "}")
    with pytest.raises(LiveLedgerCorrupt, match="larger than"):
        ledger.snapshot()


# --- 6. atomic write recovery ------------------------------------------------------


def test_stale_tmp_file_never_corrupts_the_live_record(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.reserve_stt_session()

    # Simulate a write that was interrupted after the temp file was created.
    stale = ledger.path.with_name(f"{ledger.path.name}.tmp.999999")
    stale.write_text('{"version": 1, "run_id": "test-run", "deepgram": ')  # truncated

    snapshot = ledger.snapshot()  # reads the real file, never the temp
    assert snapshot["usage"]["deepgram_sessions"] == 1

    ledger.reserve_stt_session()
    assert ledger.snapshot()["usage"]["deepgram_sessions"] == 2
    assert json.loads(ledger.path.read_text())["deepgram"]["sessions"] == 2


def test_write_goes_through_a_temporary_file(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.reserve_stt_session()
    # No temp files are left behind after a successful publish.
    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp." in p.name]
    assert leftovers == []


def test_unusable_ledger_location_is_reported(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    ledger = LiveProviderLedger(
        path=blocker / "ledger.json",
        limits=LiveProviderLimits(),
        run_id="test-run",
    )
    with pytest.raises(LiveLedgerError):
        ledger.ensure_ready()


# --- reporting ---------------------------------------------------------------------


def test_snapshot_reports_remaining_capacity_and_exhaustion(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    snapshot = ledger.snapshot()
    assert snapshot["remaining"] == {
        "deepgram_sessions": 3,
        "deepgram_audio_seconds": 15.0,
        "elevenlabs_requests": 3,
        "elevenlabs_chars": 300,
    }
    assert snapshot["exhausted"] is False

    for _ in range(3):
        ledger.reserve_tts_request("abc")
    exhausted = ledger.snapshot()
    assert exhausted["remaining"]["elevenlabs_requests"] == 0
    assert exhausted["exhausted"] is True


def test_audio_seconds_accumulate_and_ignore_invalid_values(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.add_stt_audio(1.5)
    ledger.add_stt_audio(2.25)
    ledger.add_stt_audio(0)  # ignored
    ledger.add_stt_audio(-5)  # ignored
    ledger.add_stt_audio(float("nan"))  # ignored
    assert ledger.snapshot()["usage"]["deepgram_audio_seconds"] == 3.75
