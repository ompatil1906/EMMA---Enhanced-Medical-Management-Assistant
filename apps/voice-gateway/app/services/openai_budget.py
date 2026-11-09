"""OpenAI API budget guard (Day 12).

Application-level spending control for the GPT-6 Luna LLM provider.

Design rules (assignment §2 — strict OpenAI budget):

* The user has a total remaining OpenAI balance of $2.50 for development,
  testing and the live judge demonstration.
* A configurable application-level cap stops OpenAI requests when the
  development allocation is exhausted, preserving the final demonstration
  reserve ($0.75) and emergency reserve ($0.75).
* Tracks input, output and cached token usage per request and cumulatively.
* Records estimated USD usage per request and cumulatively.
* Application-side accounting is NOT a replacement for the provider's billing
  controls — the user should also configure a usage limit in the OpenAI
  platform. This guard does NOT claim a technical hard cap.
* No silent model fallback. No automatic credit addition. No OpenAI Realtime
  audio API. No OpenAI transcription API.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """One LLM call's token usage and estimated cost."""

    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    estimated_usd: float
    timestamp: float = field(default_factory=time.time)


@dataclass(slots=True)
class BudgetSnapshot:
    """Current budget state."""

    total_usd: float
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    request_count: int
    can_spend: bool
    remaining_usd: float


class OpenAIBudgetExceededError(RuntimeError):
    """Raised when the application-level OpenAI budget cap is reached."""


class OpenAIBudgetGuard:
    """Tracks cumulative OpenAI token usage and estimated USD spend.

    Parameters:
        daily_budget_usd: hard total budget (default $2.50 per assignment §2).
        dev_budget_usd: initial conservative development cap (default $1.00).
        input_usd_per_1k_tokens: estimated input price per 1k tokens.
        output_usd_per_1k_tokens: estimated output price per 1k tokens.
        cached_usd_per_1k_tokens: estimated cached-input price per 1k tokens.

    Estimated prices are conservative placeholders until the actual provider
    usage response is available; they are never presented as authoritative.
    """

    def __init__(
        self,
        *,
        daily_budget_usd: float = 2.50,
        dev_budget_usd: float = 1.00,
        input_usd_per_1k_tokens: float = 0.005,
        output_usd_per_1k_tokens: float = 0.015,
        cached_usd_per_1k_tokens: float = 0.00125,
    ) -> None:
        if daily_budget_usd <= 0:
            raise ValueError("daily_budget_usd must be positive")
        if dev_budget_usd < 0 or dev_budget_usd > daily_budget_usd:
            raise ValueError("dev_budget_usd must be between 0 and daily_budget_usd")
        self._daily_budget_usd = daily_budget_usd
        self._dev_budget_usd = dev_budget_usd
        self._input_usd_per_1k = input_usd_per_1k_tokens
        self._output_usd_per_1k = output_usd_per_1k_tokens
        self._cached_usd_per_1k = cached_usd_per_1k_tokens
        self._records: list[UsageRecord] = []
        self._blocked = False

    # --- recording -----------------------------------------------------------

    def record_usage(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
        estimated_usd: float | None = None,
    ) -> UsageRecord:
        """Record one LLM call's usage and return the record.

        Raises `OpenAIBudgetExceededError` if the call would exceed the
        configured application cap — the caller must not proceed with the
        request.
        """
        if self._blocked:
            raise OpenAIBudgetExceededError(
                "OpenAI budget guard is blocked (cap reached); refusing further requests"
            )
        if input_tokens < 0 or output_tokens < 0 or cached_tokens < 0:
            raise ValueError("token counts must be non-negative")

        if estimated_usd is None:
            estimated_usd = self._estimate_usd(input_tokens, output_tokens, cached_tokens)

        # Enforce the dev budget cap before recording: if this call would push
        # cumulative spend above the dev allocation, block further calls so the
        # demonstration reserve is preserved.
        projected_total = self._total_usd() + estimated_usd
        if projected_total > self._dev_budget_usd:
            self._blocked = True
            raise OpenAIBudgetExceededError(
                f"OpenAI dev budget would be exceeded "
                f"(projected ${projected_total:.4f} > cap ${self._dev_budget_usd:.2f}); "
                f"development allocation exhausted — demonstration reserve preserved"
            )

        record = UsageRecord(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            estimated_usd=estimated_usd,
        )
        self._records.append(record)
        return record

    # --- queries -------------------------------------------------------------

    def can_spend(self, estimated_usd: float) -> bool:
        """Return True if one more call of `estimated_usd` would stay in budget."""
        if self._blocked:
            return False
        return self._total_usd() + estimated_usd <= self._dev_budget_usd

    def snapshot(self) -> BudgetSnapshot:
        """Current budget state (never exposes credentials)."""
        return BudgetSnapshot(
            total_usd=round(self._total_usd(), 6),
            input_tokens=sum(r.input_tokens for r in self._records),
            output_tokens=sum(r.output_tokens for r in self._records),
            cached_tokens=sum(r.cached_tokens for r in self._records),
            request_count=len(self._records),
            can_spend=self.can_spend(0.0),
            remaining_usd=round(max(0.0, self._dev_budget_usd - self._total_usd()), 6),
        )

    def records(self) -> list[UsageRecord]:
        return list(self._records)

    def reset(self) -> None:
        """Clear all usage records (test-only helper)."""
        self._records = []
        self._blocked = False

    def block(self) -> None:
        """Manually block all further OpenAI requests (emergency stop)."""
        self._blocked = True

    def unblock(self) -> None:
        """Unblock requests (only meaningful if under budget)."""
        self._blocked = False

    # --- internals -----------------------------------------------------------

    def _total_usd(self) -> float:
        return sum(r.estimated_usd for r in self._records)

    def _estimate_usd(self, input_tokens: int, output_tokens: int, cached_tokens: int) -> float:
        return (
            input_tokens / 1000.0 * self._input_usd_per_1k
            + output_tokens / 1000.0 * self._output_usd_per_1k
            + cached_tokens / 1000.0 * self._cached_usd_per_1k
        )


__all__ = [
    "OpenAIBudgetGuard",
    "OpenAIBudgetExceededError",
    "UsageRecord",
    "BudgetSnapshot",
]
