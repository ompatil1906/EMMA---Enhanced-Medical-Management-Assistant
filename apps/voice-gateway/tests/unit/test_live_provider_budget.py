"""Day 18: live-provider budget guard tests (offline).

The guard is what makes the Day 18 spend limits enforceable instead of
aspirational: it refuses a call *before* it is made. These tests pin the caps
from the brief (3 Deepgram sessions / 15 s audio, 3 ElevenLabs requests /
300 chars) and the "refuse, never silently degrade" behaviour.
"""

from __future__ import annotations

import pytest

from app.services.live_provider_budget import (
    LiveBudgetError,
    LiveProviderBudget,
    LiveProviderLimits,
)


def test_default_limits_match_the_day18_brief() -> None:
    limits = LiveProviderLimits()
    assert limits.max_deepgram_sessions == 3
    assert limits.max_deepgram_audio_seconds == 15.0
    assert limits.max_elevenlabs_requests == 3
    assert limits.max_elevenlabs_chars == 300


def test_invalid_limits_are_rejected() -> None:
    with pytest.raises(LiveBudgetError):
        LiveProviderLimits(max_deepgram_sessions=0)
    with pytest.raises(LiveBudgetError):
        LiveProviderLimits(max_deepgram_audio_seconds=0)
    with pytest.raises(LiveBudgetError):
        LiveProviderLimits(max_elevenlabs_chars=-1)


def test_deepgram_session_cap_is_enforced() -> None:
    budget = LiveProviderBudget(LiveProviderLimits(max_deepgram_sessions=2))
    for _ in range(2):
        budget.check_deepgram_session(3.0)
        budget.record_deepgram_session(3.0)

    with pytest.raises(LiveBudgetError, match="session cap"):
        budget.check_deepgram_session(3.0)

    assert budget.deepgram_sessions == 2
    assert len(budget.refused) == 1


def test_deepgram_audio_duration_cap_is_enforced() -> None:
    budget = LiveProviderBudget()
    with pytest.raises(LiveBudgetError, match="cap"):
        budget.check_deepgram_session(15.5)
    # Rejected calls are recorded so the report shows the attempt.
    assert budget.refused and "15.5s" in budget.refused[0]
    assert budget.deepgram_sessions == 0


@pytest.mark.parametrize("seconds", [0, -1.0, float("nan"), float("inf")])
def test_invalid_audio_durations_are_rejected(seconds: float) -> None:
    budget = LiveProviderBudget()
    with pytest.raises(LiveBudgetError):
        budget.check_deepgram_session(seconds)


def test_elevenlabs_char_and_request_caps_are_enforced() -> None:
    budget = LiveProviderBudget(LiveProviderLimits(max_elevenlabs_requests=2))
    text = "x" * 300
    for _ in range(2):
        budget.check_elevenlabs_request(text)
        budget.record_elevenlabs_request(text)

    with pytest.raises(LiveBudgetError, match="request cap"):
        budget.check_elevenlabs_request("short")

    with pytest.raises(LiveBudgetError, match="char cap"):
        LiveProviderBudget().check_elevenlabs_request("y" * 301)


def test_empty_text_is_rejected() -> None:
    with pytest.raises(LiveBudgetError, match="must not be empty"):
        LiveProviderBudget().check_elevenlabs_request("   ")


def test_report_counts_usage_and_refusals() -> None:
    budget = LiveProviderBudget()
    budget.check_deepgram_session(3.7)
    budget.record_deepgram_session(3.7)
    budget.check_elevenlabs_request("Good morning.")
    budget.record_elevenlabs_request("Good morning.")
    with pytest.raises(LiveBudgetError):
        budget.check_elevenlabs_request("z" * 400)

    report = budget.report()
    assert report["used"] == {
        "deepgram_sessions": 1,
        "deepgram_audio_seconds": 3.7,
        "elevenlabs_requests": 1,
        "elevenlabs_chars": len("Good morning."),
    }
    assert report["limits"]["max_elevenlabs_requests"] == 3
    assert len(report["refused"]) == 1
    assert "elevenlabs requests 1/3" in budget.summary_line()


def test_limits_can_be_overridden_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMMA_LIVE_MAX_DEEPGRAM_SESSIONS", "1")
    monkeypatch.setenv("EMMA_LIVE_MAX_ELEVENLABS_CHARS", "60")
    limits = LiveProviderLimits.from_env()
    assert limits.max_deepgram_sessions == 1
    assert limits.max_elevenlabs_chars == 60
