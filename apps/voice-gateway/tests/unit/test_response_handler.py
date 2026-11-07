"""Deterministic response-handler tests (Day-3 pipeline responses, no LLM)."""

from __future__ import annotations

import pytest

from app.services.response_handler import DeterministicResponseHandler


@pytest.fixture()
def handler() -> DeterministicResponseHandler:
    return DeterministicResponseHandler()


class TestGreeting:
    async def test_greeting_fixed(self, handler: DeterministicResponseHandler) -> None:
        decision = await handler.greeting()
        assert "practice" in decision.response_text
        assert decision.request_type is None
        assert decision.handoff is False


class TestDeterministicRouting:
    async def test_booking_request_routed(self, handler: DeterministicResponseHandler) -> None:
        decision = await handler.respond(utterance="Hello, I would like to book an appointment")
        assert decision.request_type == "appointment-request"
        assert decision.handoff is False
        # never claims persistence on Day 3
        assert "submitted" not in decision.response_text
        assert "record" not in decision.response_text

    async def test_cancellation_routed(self, handler: DeterministicResponseHandler) -> None:
        decision = await handler.respond(utterance="I need to cancel my appointment")
        assert decision.request_type == "cancellation"

    async def test_reschedule_routed(self, handler: DeterministicResponseHandler) -> None:
        decision = await handler.respond(utterance="I want to reschedule my appointment")
        assert decision.request_type == "reschedule"

    async def test_prescription_routed(self, handler: DeterministicResponseHandler) -> None:
        decision = await handler.respond(utterance="I need a prescription repeat")
        assert decision.request_type == "prescription-query"

    async def test_unknown_asks_for_clarification(
        self, handler: DeterministicResponseHandler
    ) -> None:
        decision = await handler.respond(utterance="what is the meaning of life")
        assert decision.request_type is None
        assert "catch" in decision.response_text or "repeat" in decision.response_text

    async def test_empty_utterance_clarification(
        self, handler: DeterministicResponseHandler
    ) -> None:
        decision = await handler.respond(utterance="   ")
        assert decision.request_type is None

    async def test_test_response_phrase_fixed(self, handler: DeterministicResponseHandler) -> None:
        decision = await handler.respond(utterance="please respond with a test response")
        assert "pipeline test response" in decision.response_text
        assert decision.request_type is None  # no persistence for synthetic turns

    async def test_handoff_on_help_request(self, handler: DeterministicResponseHandler) -> None:
        decision = await handler.respond(utterance="get me someone please help")
        assert decision.handoff is True
        assert decision.handoff_destination == "practice-reception"
        assert decision.request_type is None  # handoffs are not admin requests


class TestDeterminismAndSafety:
    async def test_repeated_calls_identical(self, handler: DeterministicResponseHandler) -> None:
        first = await handler.respond(utterance="book an appointment")
        second = await handler.respond(utterance="book an appointment")
        assert first == second

    async def test_no_clinical_advice_language(self, handler: DeterministicResponseHandler) -> None:
        for utterance in (
            "I have chest pains",
            "I feel faint",
            "book an appointment now, please",
        ):
            decision = await handler.respond(utterance=utterance)
            for banned in ("diagnos", "symptoms mean", "dr says", "tablet dose"):
                assert banned not in decision.response_text.lower()
