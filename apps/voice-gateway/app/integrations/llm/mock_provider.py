"""Deterministic mock LLM provider (Day 5).

Scenario-based, zero network, zero randomness. Each configured scenario maps a
caller utterance pattern to a structured interpretation. Used for local
development and the regression suite — never a substitute for real-provider
testing.

Determinism rules:
* Same utterance → same result every time.
* Scenario order is stable (first match wins).
* Timeout/failure scenarios are opt-in via constructor flags.
"""

from __future__ import annotations

import asyncio
import time

from app.domain.llm import InterpretationResult, RequestDetail
from app.integrations.llm.base import LLMRequest, LLMResponse
from app.integrations.llm.exceptions import LLMConnectionError, LLMTimeoutError

# A caller utterance containing any of these is treated as an emergency by the
# guard layer before the LLM is ever called. The mock provider does not need to
# know about emergencies — the guard runs first — but it must never generate an
# interpretation for an emergency utterance.
_EMERGENCY_HINT = (
    "emergency",
    "999",
    "chest pain",
    "can't breathe",
    "cannot breathe",
    "unconscious",
    "stroke",
    "heart attack",
)


class MockLLMProvider:
    """Deterministic mock LLM provider implementing LLMProvider.

    Scenarios are matched by keyword inclusion (lowercased substring). The
    first matching scenario wins, so more specific patterns should be added
    before generic ones.
    """

    def __init__(
        self,
        *,
        simulate_timeout: bool = False,
        simulate_failure: bool = False,
        simulate_malformed: bool = False,
    ) -> None:
        self.simulate_timeout = simulate_timeout
        self.simulate_failure = simulate_failure
        self.simulate_malformed = simulate_malformed
        self.call_count = 0
        self.last_request: LLMRequest | None = None

    @property
    def provider_name(self) -> str:
        return "mock-llm"

    async def interpret(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1
        self.last_request = request

        if self.simulate_timeout:
            await asyncio.sleep(request.timeout_seconds + 0.1)
            raise LLMTimeoutError(f"mock provider timed out after {request.timeout_seconds}s")

        if self.simulate_failure:
            raise LLMConnectionError("mock provider connection failed")

        text = request.transcript.strip().lower()
        if not text:
            result = self._empty_interpretation()
        elif any(hint in text for hint in _EMERGENCY_HINT):
            # The guard runs before the LLM, so this path should be unreachable
            # in production. We return a safe clarification rather than an
            # administrative interpretation, just in case.
            result = self._clarification_interpretation(
                "unclear input — possible emergency phrase detected"
            )
        else:
            result = self._match_scenario(text)

        if self.simulate_malformed:
            raw = "{not valid json"
            parsed: InterpretationResult | None = None
        else:
            raw = result.model_dump_json()
            parsed = result

        t0 = time.monotonic()
        # Mock is instantaneous: first token == total.
        total_ms = (time.monotonic() - t0) * 1000.0

        return LLMResponse(
            raw_text=raw,
            parsed=parsed,
            first_token_ms=total_ms,
            total_ms=total_ms,
            correlation_id=request.correlation_id,
        )

    async def aclose(self) -> None:
        """No-op close for mock provider (no resources to release)."""
        return None

    # --- scenario matching ---------------------------------------------------

    def _match_scenario(self, text: str) -> InterpretationResult:
        """Match deterministic scenarios by keyword inclusion.

        Multiple independent requests in one utterance are preserved as
        separate entries. All categories are checked so multiple requests
        are captured in a single turn.
        """

        requests: list[RequestDetail] = []

        # --- appointment request ------------------------------------------
        appointment_keywords = (
            "appointment",
            "book",
            "schedule",
            "see the doctor",
            "see a doctor",
            "gp appointment",
        )
        if any(k in text for k in appointment_keywords):
            details: dict[str, str | None] = {}
            missing: list[str] = []
            quotes: list[str] = []

            if "tuesday" in text:
                details["preferred_date"] = "tuesday"
                quotes.append("tuesday")
            if "wednesday" in text:
                details["preferred_date"] = "wednesday"
                quotes.append("wednesday")
            if "morning" in text:
                details["preferred_time"] = "morning"
                quotes.append("morning")
            if "afternoon" in text:
                details["preferred_time"] = "afternoon"
                quotes.append("afternoon")
            if "evening" in text:
                details["preferred_time"] = "evening"
                quotes.append("evening")

            if details.get("preferred_date") is None:
                missing.append("preferred_date")
            if details.get("preferred_time") is None:
                missing.append("preferred_time")

            requests.append(
                RequestDetail(
                    request_type="appointment-request",
                    details=details,
                    missing_fields=missing,
                    source_quotes=quotes,
                )
            )

        # --- prescription request -----------------------------------------
        prescription_keywords = ("prescription", "repeat", "medication")
        if any(k in text for k in prescription_keywords):
            requests.append(
                RequestDetail(
                    request_type="prescription-request",
                    details={"reason_as_stated": None},
                    missing_fields=[],
                    source_quotes=[],
                )
            )

        # --- practice information -----------------------------------------
        info_keywords = ("opening", "hours", "address", "location", "phone", "contact")
        if any(k in text for k in info_keywords):
            requests.append(
                RequestDetail(
                    request_type="practice-information",
                    details={"topic": None},
                    missing_fields=[],
                    source_quotes=[],
                )
            )

        # --- appointment change -------------------------------------------
        change_keywords = ("change", "move", "reschedule", "cancel")
        if any(k in text for k in change_keywords) and "appointment" in text:
            requests.append(
                RequestDetail(
                    request_type="appointment-change-request",
                    details={},
                    missing_fields=["preferred_date", "preferred_time"],
                    source_quotes=[],
                )
            )

        # --- determine overall intent -------------------------------------
        if not requests:
            return InterpretationResult(
                intent="unclear",
                requests=[
                    RequestDetail(
                        request_type="unclear",
                        details={},
                        missing_fields=[],
                        source_quotes=[],
                    )
                ],
                needs_clarification=True,
                clarification_reason=(
                    "I'm not sure I understood. Are you asking about an appointment, "
                    "a prescription, or something else?"
                ),
            )

        # Primary intent is the first request type
        primary_intent = requests[0].request_type
        missing_any = any(req.missing_fields for req in requests)

        return InterpretationResult(
            intent=primary_intent,
            requests=requests,
            needs_clarification=missing_any,
            clarification_reason=(
                "Please tell me your preferred date and time." if missing_any else None
            ),
        )

    # --- helpers -------------------------------------------------------------

    def _empty_interpretation(self) -> InterpretationResult:
        return InterpretationResult(
            intent="unclear",
            requests=[
                RequestDetail(
                    request_type="unclear",
                    details={},
                    missing_fields=[],
                    source_quotes=[],
                )
            ],
            needs_clarification=True,
            clarification_reason="I didn't catch that. Could you repeat it?",
        )

    def _clarification_interpretation(self, reason: str) -> InterpretationResult:
        return InterpretationResult(
            intent="unclear",
            requests=[
                RequestDetail(
                    request_type="unclear",
                    details={},
                    missing_fields=[],
                    source_quotes=[],
                )
            ],
            needs_clarification=True,
            clarification_reason=reason,
        )


__all__ = ["MockLLMProvider"]
