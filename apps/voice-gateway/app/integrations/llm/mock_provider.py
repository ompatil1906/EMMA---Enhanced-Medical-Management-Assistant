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
import re
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

_DATE_WORDS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    "today",
    "tomorrow",
    "next week",
    "this week",
)
_TIME_WORDS = ("morning", "afternoon", "evening", "noon")


def _extract_date(text: str) -> str | None:
    """Extract an explicitly stated date phrase (never infer one).

    When several dates appear ("change Wednesday to Thursday"), the last one
    is the correction and wins.
    """
    lowered = text.lower()
    matches: list[tuple[int, str]] = []
    for word in _DATE_WORDS:
        for match in re.finditer(rf"\b{re.escape(word)}\b", lowered):
            matches.append((match.start(), word))
    if not matches:
        return None
    position, word = max(matches)
    if lowered[:position].rstrip().endswith("next") and word not in ("next week", "this week"):
        return f"next {word}"
    return word


def _extract_time(text: str) -> str | None:
    """Extract an explicitly stated time-of-day preference (last one wins)."""
    lowered = text.lower()
    matches: list[tuple[int, str]] = []
    for word in _TIME_WORDS:
        for match in re.finditer(rf"\b{re.escape(word)}\b", lowered):
            matches.append((match.start(), word))
    if not matches:
        return None
    return max(matches)[1]


def _parse_context(context: str | None) -> dict[str, object]:
    """Parse the bounded server-built context used by the mock provider."""
    result: dict[str, object] = {
        "active_request_type": None,
        "active_request_details": {},
        "awaiting_field": None,
        "pending_confirmation": False,
        "last_grounded_topic": None,
    }
    if not context:
        return result
    for raw_line in context.splitlines():
        key, separator, value = raw_line.partition(":")
        if not separator:
            continue
        key = key.strip().lower()
        value = value.strip()
        if key == "active_request" and value:
            parts = [part.strip() for part in value.split(";") if part.strip()]
            if parts:
                result["active_request_type"] = parts[0]
                details: dict[str, str] = {}
                for part in parts[1:]:
                    if part.startswith("missing="):
                        continue
                    field, _, field_value = part.partition("=")
                    if field and field_value:
                        details[field.strip()] = field_value.strip()
                result["active_request_details"] = details
        elif key == "awaiting_field":
            result["awaiting_field"] = value or None
        elif key == "pending_confirmation":
            result["pending_confirmation"] = value.lower() == "true"
        elif key == "last_grounded_topic":
            result["last_grounded_topic"] = value or None
    return result


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
        context = _parse_context(request.context)
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
            result = self._match_scenario(text, context=context)

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

    def _match_scenario(
        self, text: str, *, context: dict[str, object] | None = None
    ) -> InterpretationResult:
        """Match deterministic scenarios by keyword inclusion.

        Multiple independent requests in one utterance are preserved as
        separate entries. All categories are checked so multiple requests
        are captured in a single turn.

        Day 9: when a bounded conversation context is supplied, a bare slot
        value ("Wednesday", "afternoon") fills the active request, and a short
        follow-up ("what about Saturday?") stays on the previous grounded
        topic. This mirrors the real Gemini prompt contract without changing
        the no-context behaviour used by the Day 5/6 corpora.
        """
        ctx = context or {}
        pending_confirmation = bool(ctx.get("pending_confirmation"))
        active_type = ctx.get("active_request_type")
        awaiting_field = ctx.get("awaiting_field")
        last_topic = ctx.get("last_grounded_topic")

        # Confirmation words are deliberately left to the deterministic
        # controller. The model may still extract a correction ("yes, make it
        # Thursday") because that contains a new slot value.
        if pending_confirmation and not self._contains_slot_value(text):
            return self._clarification_interpretation("confirmation response")

        if isinstance(active_type, str) and self._looks_like_slot_value(
            text, active_type=active_type, awaiting_field=awaiting_field
        ):
            return self._slot_fill_interpretation(active_type, text)

        if (
            isinstance(last_topic, str)
            and last_topic
            and not self._has_admin_keyword(text)
            and self._looks_like_follow_up(text)
        ):
            return InterpretationResult(
                intent="practice-information",
                requests=[
                    RequestDetail(
                        request_type="practice-information",
                        details={"topic": last_topic},
                        missing_fields=[],
                        source_quotes=[text],
                    )
                ],
            )

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

    # --- Day 9 context helpers ----------------------------------------------

    @staticmethod
    def _contains_slot_value(text: str) -> bool:
        return bool(
            _extract_date(text)
            or _extract_time(text)
            or any(
                marker in text for marker in ("actually", "change", "make it", "i meant", "instead")
            )
        )

    @staticmethod
    def _has_admin_keyword(text: str) -> bool:
        return any(
            keyword in text
            for keyword in (
                "appointment",
                "book",
                "schedule",
                "prescription",
                "repeat",
                "medication",
                "reschedule",
                "move",
                "cancel",
            )
        )

    @staticmethod
    def _looks_like_follow_up(text: str) -> bool:
        starters = (
            "what about",
            "how about",
            "and ",
            "also ",
            "what time",
            "what are",
            "when ",
            "where ",
            "do you",
            "does the",
            "is there",
            "are there",
        )
        return len(text.split()) <= 8 and (text.startswith(starters) or text.endswith("?"))

    @staticmethod
    def _looks_like_slot_value(text: str, *, active_type: str, awaiting_field: object) -> bool:
        if awaiting_field == "preferred_date":
            return _extract_date(text) is not None
        if awaiting_field == "preferred_time":
            return _extract_time(text) is not None
        if awaiting_field in ("reason_as_stated", "reason_for_appointment"):
            return bool(text.strip())
        if active_type in ("appointment-request", "appointment-change-request"):
            return bool(_extract_date(text) or _extract_time(text))
        return False

    def _slot_fill_interpretation(self, active_type: str, text: str) -> InterpretationResult:
        details: dict[str, str | None] = {}
        date = _extract_date(text)
        if date is not None:
            details["preferred_date"] = date
        time_pref = _extract_time(text)
        if time_pref is not None:
            details["preferred_time"] = time_pref
        if not details:
            details["reason_as_stated"] = text
        return InterpretationResult(
            intent=active_type,
            requests=[
                RequestDetail(
                    request_type=active_type,
                    details=details,
                    missing_fields=[],
                    source_quotes=[text],
                )
            ],
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
