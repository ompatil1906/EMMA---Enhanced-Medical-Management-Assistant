"""Unit tests for the deterministic conversation controller (Day 5)."""

from __future__ import annotations

from app.domain.llm import InterpretationResult, RequestDetail
from app.services.conversation.conversation_controller import (
    CapturedRequest,
    ConversationController,
    ConversationState,
)


def make_interpretation(
    intent: str = "appointment-request",
    needs_clarification: bool = False,
    clarification_reason: str | None = None,
    details: dict | None = None,
    missing_fields: list[str] | None = None,
    source_quotes: list[str] | None = None,
) -> InterpretationResult:
    return InterpretationResult(
        intent=intent,
        needs_clarification=needs_clarification,
        clarification_reason=clarification_reason,
        requests=[
            RequestDetail(
                request_type=intent,
                details=details or {},
                missing_fields=missing_fields or [],
                source_quotes=source_quotes or [],
            )
        ],
    )


class TestConversationState:
    def test_add_or_update_request(self) -> None:
        state = ConversationState()
        state.add_or_update_request(
            CapturedRequest(
                request_type="appointment-request",
                details={"preferred_date": "tuesday"},
                missing_fields=["preferred_time"],
                source_quotes=["tuesday"],
            )
        )
        assert len(state.captured_requests) == 1
        assert state.captured_requests[0].request_type == "appointment-request"
        assert state.captured_requests[0].details["preferred_date"] == "tuesday"

    def test_update_existing_request(self) -> None:
        state = ConversationState()
        state.add_or_update_request(
            CapturedRequest(
                request_type="appointment-request",
                details={"preferred_date": "tuesday"},
                missing_fields=["preferred_time"],
                source_quotes=["tuesday"],
            )
        )
        state.add_or_update_request(
            CapturedRequest(
                request_type="appointment-request",
                details={"preferred_time": "afternoon"},
                missing_fields=[],
                source_quotes=["afternoon"],
            )
        )
        assert len(state.captured_requests) == 1
        req = state.captured_requests[0]
        assert req.details["preferred_date"] == "tuesday"
        assert req.details["preferred_time"] == "afternoon"
        assert req.missing_fields == []

    def test_ready_for_confirmation(self) -> None:
        state = ConversationState()
        assert state.ready_for_confirmation() is False
        state.add_or_update_request(
            CapturedRequest(
                request_type="appointment-request",
                details={"preferred_date": "tuesday", "preferred_time": "morning"},
                missing_fields=[],
                source_quotes=["tuesday", "morning"],
            )
        )
        assert state.ready_for_confirmation() is True


class TestConversationController:
    def test_unclear_interpretation_leads_to_clarification(self) -> None:
        controller = ConversationController()
        result = make_interpretation(intent="unclear", needs_clarification=True)
        decision = controller.handle_interpretation(result)
        assert decision.action == "clarify"
        assert decision.response_text is not None

    def test_missing_field_leads_to_clarification(self) -> None:
        controller = ConversationController()
        result = make_interpretation(
            intent="appointment-request",
            missing_fields=["preferred_date", "preferred_time"],
        )
        decision = controller.handle_interpretation(result)
        assert decision.action == "clarify"
        assert "date" in decision.response_text.lower() or "time" in decision.response_text.lower()

    def test_all_fields_leads_to_confirmation(self) -> None:
        controller = ConversationController()
        result = make_interpretation(
            intent="appointment-request",
            details={"preferred_date": "tuesday", "preferred_time": "morning"},
            missing_fields=[],
        )
        decision = controller.handle_interpretation(result)
        assert decision.action == "confirm"
        assert "confirm" in decision.response_text.lower()
        assert "tuesday" in decision.response_text

    def test_multiple_requests_preserved(self) -> None:
        controller = ConversationController()
        result = InterpretationResult(
            intent="appointment-request",
            requests=[
                RequestDetail(
                    request_type="appointment-request",
                    details={"preferred_date": "tuesday"},
                    missing_fields=["preferred_time"],
                    source_quotes=["tuesday"],
                ),
                RequestDetail(
                    request_type="prescription-request",
                    details={},
                    missing_fields=[],
                    source_quotes=[],
                ),
            ],
        )
        decision = controller.handle_interpretation(result)
        assert len(controller.state.captured_requests) == 2
        assert decision.action == "clarify"  # appointment has missing fields

    def test_caller_correction_updates_field(self) -> None:
        controller = ConversationController()
        # First turn: book appointment on tuesday
        result1 = make_interpretation(
            intent="appointment-request",
            details={"preferred_date": "tuesday"},
            missing_fields=["preferred_time"],
            source_quotes=["tuesday"],
        )
        controller.handle_interpretation(result1)
        # Second turn: correction to wednesday
        result2 = make_interpretation(
            intent="appointment-request",
            details={"preferred_date": "wednesday"},
            missing_fields=["preferred_time"],
            source_quotes=["wednesday"],
        )
        controller.handle_interpretation(result2)
        assert len(controller.state.captured_requests) == 1
        assert controller.state.captured_requests[0].details["preferred_date"] == "wednesday"
        quotes = controller.state.captured_requests[0].source_quotes
        assert "wednesday" in quotes

    def test_explicit_confirmation_allows_submit(self) -> None:
        controller = ConversationController()
        result = make_interpretation(
            intent="appointment-request",
            details={"preferred_date": "tuesday", "preferred_time": "morning"},
            missing_fields=[],
        )
        controller.handle_interpretation(result)
        assert controller.state.confirmation_pending is True

        decision = controller.handle_confirmation(confirmed=True)
        assert decision.action == "submit"
        assert decision.backend_action == "submit_patient_request"
        assert decision.backend_payload is not None
        assert "requests" in decision.backend_payload

    def test_denial_prevents_submit(self) -> None:
        controller = ConversationController()
        result = make_interpretation(
            intent="appointment-request",
            details={"preferred_date": "tuesday", "preferred_time": "morning"},
            missing_fields=[],
        )
        controller.handle_interpretation(result)

        decision = controller.handle_confirmation(confirmed=False)
        assert decision.action == "clarify"
        assert decision.backend_action is None

    def test_backend_failure_truthful(self) -> None:
        controller = ConversationController()
        decision = controller.handle_backend_failure("rejected by backend")
        assert decision.action == "error"
        assert (
            "registered with our clinic reception" in decision.response_text.lower()
            or "wasn't able to submit" in decision.response_text.lower()
        )
        assert "not a confirmed" not in decision.response_text.lower()


class TestCapturedRequest:
    def test_fields_preserved(self) -> None:
        req = CapturedRequest(
            request_type="prescription-request",
            details={"reason_as_stated": "repeat medication"},
            missing_fields=[],
            source_quotes=["repeat prescription"],
        )
        assert req.request_type == "prescription-request"
        assert req.details["reason_as_stated"] == "repeat medication"
