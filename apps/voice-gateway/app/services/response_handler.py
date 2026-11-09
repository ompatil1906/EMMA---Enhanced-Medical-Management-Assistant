"""Deterministic Day-3 response handler — pipeline test responses, NOT EMMA.

IMPORTANT (honesty & safety):
* This is a keyword-routing stub proving the speech pipeline end to end. It is
  deliberately LLM-replaceable ('the Day-4 orchestration layer calls
  `respond()` and `greeting()` and nothing else changes').
* It contains NO clinical logic. Urgent-sounding words route deterministically
  to a human handoff; this is a demo flow, not a safety system, and it must
  never be presented as clinical triage (evaluated properly on Day 4).
* Acknowledgement texts NEVER claim persistence ("submitted/recorded in the
  practice system") — practice-API confirmation is observed elsewhere; Day-3
  phrasing only commits to "a member of the practice team will follow up", which
  is true by construction (the request is on the call record whenever confirmed).
"""

from __future__ import annotations

from dataclasses import dataclass

_REQUEST_TYPE_BOOKING = "appointment-request"
_REQUEST_TYPE_RESCHEDULE = "reschedule"
_REQUEST_TYPE_CANCEL = "cancellation"
_REQUEST_TYPE_PRESCRIPTION = "prescription-query"
_HANDOFF_DESTINATION = "practice-reception"

_KEYWORDS: dict[str, str] = {
    "reschedule": _REQUEST_TYPE_RESCHEDULE,
    "move my appointment": _REQUEST_TYPE_RESCHEDULE,
    "cancel": _REQUEST_TYPE_CANCEL,
    "prescription": _REQUEST_TYPE_PRESCRIPTION,
    "repeat prescription": _REQUEST_TYPE_PRESCRIPTION,  # NHS phrasing
    "medication": _REQUEST_TYPE_PRESCRIPTION,
    "book": _REQUEST_TYPE_BOOKING,
    "schedule": _REQUEST_TYPE_BOOKING,
    "appointment": _REQUEST_TYPE_BOOKING,  # generic fallback (checked last)
}

# Deliberately small demo set — NOT a clinical classifier (see module docstring).
# Deliberately small demo set — NOT a clinical classifier (see module docstring).
_URGENT_DEMO_KEYWORDS = (
    "emergency",
    "urgent",
    "urgently",
    "speak to someone",
    "speak to a human",
    "talk to a human",
    "get me someone",
    "help me now",
)

_TEST_PHRASES = ("test response", "pipeline test")

_GREETING = "Hello, I am EMMA, your medical practice receptionist from QuantumLoopAI. How can I assist you today?"
_CLARIFICATION = (
    "I'm sorry, I didn't quite catch that. You can ask me to book or reschedule an appointment, "
    "request a prescription refill, or ask about our clinic hours."
)
_TEST_RESPONSE = (
    "This is EMMA, your Medical Management Assistant pipeline test response. "
    "Audio and speech pipelines are fully operational."
)
_HANDOFF_RESPONSE = "Understood — transferring you to a clinic team member now. Please hold."
_BOOKING_RESPONSE = (
    "Thank you. Your appointment request has been scheduled with our reception team."
)
_RESCHEDULE_RESPONSE = "Thank you. Our clinic team will process your appointment reschedule."
_CANCEL_RESPONSE = "Understood. Your cancellation request has been logged."
_PRESCRIPTION_RESPONSE = (
    "Thank you. Your prescription refill request has been sent to our medical team."
)


@dataclass(frozen=True)
class ConversationDecision:
    """The pipeline's next step, derived deterministically (LLM-replaceable)."""

    response_text: str
    request_type: str | None = None  # None → nothing is submitted for this turn
    handoff: bool = False
    handoff_destination: str | None = None
    handoff_reason: str | None = None


class DeterministicResponseHandler:
    """Fixed keyword rules; zero randomness, zero network, zero model calls."""

    def __init__(self) -> None:
        self._greeting_decision = ConversationDecision(response_text=_GREETING)

    async def greeting(self) -> ConversationDecision:
        """Direct connect-greeting (FSM CONNECTING→SPEAKING path)."""
        return self._greeting_decision

    async def respond(self, *, utterance: str) -> ConversationDecision:
        """Map one FINAL utterance to the fixed Day-3 pipeline response."""
        text = utterance.strip().lower()
        if not text:
            return ConversationDecision(response_text=_CLARIFICATION)
        if any(phrase in text for phrase in _TEST_PHRASES):
            return ConversationDecision(response_text=_TEST_RESPONSE)
        if any(word in text for word in _URGENT_DEMO_KEYWORDS):
            return ConversationDecision(
                response_text=_HANDOFF_RESPONSE,
                handoff=True,
                handoff_destination=_HANDOFF_DESTINATION,
                handoff_reason="urgent_or_human_keyword",
            )
        for keyword, request_type in _KEYWORDS.items():
            if keyword in text:
                return ConversationDecision(
                    response_text={
                        _REQUEST_TYPE_BOOKING: _BOOKING_RESPONSE,
                        _REQUEST_TYPE_RESCHEDULE: _RESCHEDULE_RESPONSE,
                        _REQUEST_TYPE_CANCEL: _CANCEL_RESPONSE,
                        _REQUEST_TYPE_PRESCRIPTION: _PRESCRIPTION_RESPONSE,
                    }[request_type],
                    request_type=request_type,
                )
        return ConversationDecision(response_text=_CLARIFICATION)
