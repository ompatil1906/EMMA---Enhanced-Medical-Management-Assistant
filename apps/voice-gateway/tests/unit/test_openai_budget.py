"""Day 12: OpenAI budget guard tests (offline, no API credits).

Tests the application-level spending control for GPT-6 Luna usage:

* Token usage recording and cumulative USD tracking.
* Dev budget cap enforcement (stops requests when dev allocation exhausted).
* Demonstration reserve preservation.
* Per-request and cumulative accounting.
* Emergency stop / unblock.
"""

from __future__ import annotations

import pytest

from app.services.openai_budget import (
    BudgetSnapshot,
    OpenAIBudgetExceededError,
    OpenAIBudgetGuard,
    UsageRecord,
)


def test_record_usage_tracks_tokens_and_usd() -> None:
    guard = OpenAIBudgetGuard(
        daily_budget_usd=2.50,
        dev_budget_usd=1.00,
        input_usd_per_1k_tokens=0.005,
        output_usd_per_1k_tokens=0.015,
        cached_usd_per_1k_tokens=0.00125,
    )
    record = guard.record_usage(
        model="gpt-6-luna",
        input_tokens=1000,
        output_tokens=500,
        cached_tokens=200,
    )
    assert isinstance(record, UsageRecord)
    assert record.model == "gpt-6-luna"
    assert record.input_tokens == 1000
    assert record.output_tokens == 500
    assert record.cached_tokens == 200
    # 1000/1000*0.005 + 500/1000*0.015 + 200/1000*0.00125
    expected = 0.005 + 0.0075 + 0.00025
    assert record.estimated_usd == pytest.approx(expected)


def test_snapshot_returns_cumulative_state() -> None:
    guard = OpenAIBudgetGuard(daily_budget_usd=2.50, dev_budget_usd=1.00)
    guard.record_usage(model="gpt-6-luna", input_tokens=1000, output_tokens=500)
    guard.record_usage(model="gpt-6-luna", input_tokens=500, output_tokens=250)

    snap = guard.snapshot()
    assert isinstance(snap, BudgetSnapshot)
    assert snap.input_tokens == 1500
    assert snap.output_tokens == 750
    assert snap.request_count == 2
    assert snap.total_usd > 0
    assert snap.remaining_usd == pytest.approx(1.00 - snap.total_usd)
    assert snap.can_spend is True


def test_dev_budget_cap_blocks_further_requests() -> None:
    guard = OpenAIBudgetGuard(daily_budget_usd=2.50, dev_budget_usd=0.01)
    # A small call that still exceeds the tiny dev cap.
    with pytest.raises(OpenAIBudgetExceededError):
        guard.record_usage(model="gpt-6-luna", input_tokens=1000, output_tokens=1000)

    # Guard is now blocked — all further calls raise.
    assert guard.snapshot().can_spend is False
    with pytest.raises(OpenAIBudgetExceededError):
        guard.record_usage(model="gpt-6-luna", input_tokens=10, output_tokens=10)


def test_daily_budget_not_exceeded_by_dev_cap() -> None:
    """The dev cap is a subset of the daily budget; demo reserve preserved."""
    guard = OpenAIBudgetGuard(daily_budget_usd=2.50, dev_budget_usd=1.00)
    # Record usage up to the dev cap.
    guard.record_usage(model="gpt-6-luna", input_tokens=50000, output_tokens=20000)
    snap = guard.snapshot()
    assert snap.remaining_usd >= 0
    # Daily budget ($2.50) is still intact beyond the dev cap ($1.00).
    assert snap.total_usd <= guard._daily_budget_usd  # noqa: SLF001


def test_emergency_stop_and_unblock() -> None:
    guard = OpenAIBudgetGuard(daily_budget_usd=2.50, dev_budget_usd=1.00)
    guard.block()
    assert guard.snapshot().can_spend is False
    with pytest.raises(OpenAIBudgetExceededError):
        guard.record_usage(model="gpt-6-luna", input_tokens=10, output_tokens=10)

    guard.unblock()
    assert guard.snapshot().can_spend is True
    guard.record_usage(model="gpt-6-luna", input_tokens=10, output_tokens=10)


def test_reset_clears_records() -> None:
    guard = OpenAIBudgetGuard(daily_budget_usd=2.50, dev_budget_usd=1.00)
    guard.record_usage(model="gpt-6-luna", input_tokens=100, output_tokens=50)
    assert guard.snapshot().request_count == 1
    guard.reset()
    assert guard.snapshot().request_count == 0
    assert guard.snapshot().total_usd == 0.0


def test_negative_token_counts_rejected() -> None:
    guard = OpenAIBudgetGuard(daily_budget_usd=2.50, dev_budget_usd=1.00)
    with pytest.raises(ValueError):
        guard.record_usage(model="gpt-6-luna", input_tokens=-1, output_tokens=10)
    with pytest.raises(ValueError):
        guard.record_usage(model="gpt-6-luna", input_tokens=10, output_tokens=-5)
