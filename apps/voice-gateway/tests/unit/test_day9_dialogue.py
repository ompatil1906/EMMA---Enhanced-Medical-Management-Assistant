"""Day 9 focused dialogue tests.

Covers the deterministic pieces that are easier to assert directly than
through the corpus: confirmation classification, targeted confirmation,
correction invalidation, duplicate-submission prevention, tenant-field
stripping, context-aware mock slot filling, and barge-in cancellation.
"""

from __future__ import annotations

import pytest

from app.domain.conversation import RequestStatus
from app.integrations.llm.base import LLMRequest
from app.integrations.llm.mock_provider import MockLLMProvider
from app.services.conversation.conversation_controller import (
    ConversationController,
    ConversationState,
)
from tests.evaluation.day9 import (
    appointment,
    close,
    make_pipeline,
    multiple,
    prescription,
)


def make_llm_request(transcript: str, *, context: str | None = None) -> LLMRequest:
    return LLMRequest(
        transcript=transcript,
        session_id="unit-session",
        prompt_version="v1",
        model="mock-model",
        timeout_seconds=1.0,
        max_output_tokens=256,
        correlation_id="unit-correlation",
        context=context,
    )


# ---------------------------------------------------------------------------
# Confirmation classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("yes", "confirm"),
        ("yeah, go ahead", "confirm"),
        ("that's right", "confirm"),
        ("no", "reject"),
        ("not now", "reject"),
        ("maybe", "ambiguous"),
        ("I'm not sure", "ambiguous"),
        ("cancel that", "cancel"),
        ("no, cancel it", "cancel"),
        ("what time is my appointment", "other"),
        ("", "other"),
    ],
)
def test_confirmation_classification(text: str, expected: str) -> None:
    assert ConversationController.classify_confirmation_act(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("cancel the prescription", "prescription-request"),
        ("cancel my appointment", "appointment-request"),
        ("move my appointment", "appointment-change-request"),
        ("cancel it", None),
    ],
)
def test_request_type_inference(text: str, expected: str | None) -> None:
    assert ConversationController.infer_request_type_from_text(text) == expected


# ---------------------------------------------------------------------------
# Confirmation and submission invariants
# ---------------------------------------------------------------------------


def test_no_submission_without_explicit_confirmation() -> None:
    controller = ConversationController(ConversationState())
    decision = controller.handle_interpretation(appointment())

    assert decision.action == "confirm"
    assert controller.state.confirmation_pending is True
    assert controller.state.confirmed_submissions == []
    assert controller.state.captured_requests[0].status is RequestStatus.READY_FOR_CONFIRMATION


def test_targeted_confirmation_submits_only_named_request() -> None:
    controller = ConversationController(ConversationState())
    controller.handle_interpretation(multiple(appointment(), prescription()))

    decision = controller.handle_confirmation(confirmed=True, request_type="prescription-request")

    assert decision.action == "submit"
    assert decision.backend_payload is not None
    requests = decision.backend_payload["requests"]
    assert [request["requestType"] for request in requests] == ["prescription-request"]
    appointment_request = controller.state.get_active_request("appointment-request")
    assert appointment_request is not None
    assert appointment_request.status is RequestStatus.READY_FOR_CONFIRMATION


def test_confirmation_does_not_submit_incomplete_request() -> None:
    controller = ConversationController(ConversationState())
    controller.handle_interpretation(appointment(time=None))

    decision = controller.handle_confirmation(confirmed=True)

    assert decision.action == "error"
    assert decision.backend_payload is None
    assert controller.state.confirmed_submissions == []


def test_correction_invalidates_pending_confirmation() -> None:
    controller = ConversationController(ConversationState())
    controller.handle_interpretation(appointment())
    assert controller.state.confirmation_pending is True

    correction = appointment(date="thursday")
    assert controller.interpretation_changes_state(correction) is True
    controller.invalidate_confirmation()
    assert controller.state.confirmation_pending is False

    decision = controller.handle_interpretation(correction)
    assert decision.action == "confirm"
    request = controller.state.get_active_request("appointment-request")
    assert request is not None
    assert request.details["preferred_date"] == "thursday"


def test_duplicate_submission_is_prevented() -> None:
    controller = ConversationController(ConversationState())
    controller.handle_interpretation(appointment())
    controller.handle_confirmation(confirmed=True, request_type="appointment-request")
    controller.mark_submitting(
        "appointment-request", turn_id="turn-test", idempotency_key="key-test"
    )
    controller.handle_backend_success(
        request_type="appointment-request", idempotency_key="key-test"
    )
    controller.handle_backend_success(
        request_type="appointment-request", idempotency_key="key-test"
    )

    assert controller.state.confirmed_submissions == ["appointment-request"]
    request = controller.state.get_active_request("appointment-request")
    assert request is None  # SUBMITTED is no longer active


def test_model_practice_and_tenant_fields_are_stripped() -> None:
    controller = ConversationController(ConversationState())
    controller.handle_interpretation(
        appointment(extra={"practice_id": "evil-practice", "tenant_id": "evil-tenant"})
    )

    request = controller.state.captured_requests[0]
    assert "practice_id" not in request.details
    assert "tenant_id" not in request.details
    assert request.details["preferred_date"] == "tuesday"


# ---------------------------------------------------------------------------
# Context-aware mock provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_provider_fills_awaited_date_from_context() -> None:
    provider = MockLLMProvider()
    response = await provider.interpret(
        make_llm_request(
            "Wednesday",
            context=(
                "active_request: appointment-request; preferred_time=morning\n"
                "awaiting_field: preferred_date\n"
                "pending_confirmation: false"
            ),
        )
    )

    assert response.parsed is not None
    request = response.parsed.requests[0]
    assert request.request_type == "appointment-request"
    assert request.details["preferred_date"] == "wednesday"
    # The model returns only what the caller said; the controller merges the
    # existing preferred_time from server state.
    assert "preferred_time" not in request.details


@pytest.mark.asyncio
async def test_mock_provider_last_mentioned_date_wins() -> None:
    provider = MockLLMProvider()
    response = await provider.interpret(
        make_llm_request(
            "change Wednesday to Thursday",
            context=(
                "active_request: appointment-request; preferred_time=morning\n"
                "awaiting_field: preferred_date\n"
                "pending_confirmation: false"
            ),
        )
    )

    assert response.parsed is not None
    assert response.parsed.requests[0].details["preferred_date"] == "thursday"


@pytest.mark.asyncio
async def test_mock_provider_resolves_follow_up_topic() -> None:
    provider = MockLLMProvider()
    response = await provider.interpret(
        make_llm_request(
            "What about Saturday?",
            context=("pending_confirmation: false\nlast_grounded_topic: opening hours"),
        )
    )

    assert response.parsed is not None
    assert response.parsed.intent == "practice-information"
    assert response.parsed.requests[0].details["topic"] == "opening hours"


@pytest.mark.asyncio
async def test_mock_provider_without_context_keeps_day5_behaviour() -> None:
    provider = MockLLMProvider()
    appointment_response = await provider.interpret(
        make_llm_request("I would like to book an appointment next Tuesday")
    )
    information_response = await provider.interpret(
        make_llm_request("What are your opening hours?")
    )

    assert appointment_response.parsed is not None
    assert appointment_response.parsed.intent == "appointment-request"
    assert information_response.parsed is not None
    assert information_response.parsed.intent == "practice-information"


# ---------------------------------------------------------------------------
# Barge-in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_barge_in_cancels_in_flight_tts_and_queued_audio() -> None:
    harness = await make_pipeline(responses={})
    try:
        pipeline = harness.pipeline
        pipeline._current_tts_utterance_id = "utterance-test"
        pipeline._outbound_queue.put_nowait(b"\x00\x01" * 160)

        assert pipeline.should_cancel_for_barge_in is True

        await pipeline.cancel_tts()

        assert pipeline._current_tts_utterance_id is None
        assert pipeline._outbound_queue.empty()
        assert "utterance-test" in pipeline._tts._cancelled
        assert pipeline.should_cancel_for_barge_in is False
    finally:
        await close(harness)


@pytest.mark.asyncio
async def test_barge_in_clears_queued_audio_without_in_flight_synthesis() -> None:
    harness = await make_pipeline(responses={})
    try:
        pipeline = harness.pipeline
        pipeline._current_tts_utterance_id = None
        pipeline._outbound_queue.put_nowait(b"\x00\x01" * 160)

        assert pipeline.should_cancel_for_barge_in is True
        await pipeline.cancel_tts()
        assert pipeline._outbound_queue.empty()
    finally:
        await close(harness)
