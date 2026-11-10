"""Regression: a new request stated while a confirmation is pending.

The failure this pins (seen on a live call, 2026-11-10):

    EMMA: Before I submit, please confirm … Is that correct?
    Caller: I need to move my appointment
    EMMA: I just need a clear yes or no before I submit…     ← repeated forever

Cause: with a confirmation pending, the interpretation comes back "unclear" for
anything that is not a confirmation, and the pipeline re-asked the same
clarification instead of capturing the new request.

These tests drive the real pipeline and assert that:

* a stated request wins over the pending confirmation;
* the loop cannot repeat indefinitely (it escalates to a human);
* the deterministic fallback still asks for genuinely missing details.
"""

from __future__ import annotations

import pytest

from app.domain.conversation import RequestStatus
from app.domain.llm import InterpretationResult, RequestDetail
from app.integrations.llm.base import LLMRequest, LLMResponse
from app.integrations.llm.mock_provider import MockLLMProvider
from tests.evaluation.day9 import close, make_pipeline, run_turn

APPOINTMENT = "I'd like to book an appointment for Tuesday morning"
MOVE = "I need to move my appointment"
PRESCRIPTION = "I need a prescription repeat"


def _appointment() -> InterpretationResult:
    return InterpretationResult(
        intent="appointment-request",
        requests=[
            RequestDetail(
                request_type="appointment-request",
                details={"preferred_date": "Tuesday", "preferred_time": "morning"},
                missing_fields=[],
                source_quotes=["Tuesday morning"],
            )
        ],
    )


def _unclear() -> InterpretationResult:
    """What providers actually return for a new request mid-confirmation."""
    return InterpretationResult(
        intent="unclear",
        requests=[],
        needs_clarification=True,
        clarification_reason="confirmation response",
    )


def _request_of(harness, request_type: str):
    for request in harness.state.captured_requests:
        if request.request_type == request_type:
            return request
    return None


def _last(harness) -> str:
    return harness.state.turns[-1].content.lower()


@pytest.mark.asyncio
async def test_new_request_mid_confirmation_is_captured_not_reasked() -> None:
    harness = await make_pipeline(responses={APPOINTMENT: _appointment(), MOVE: _unclear()})
    try:
        await run_turn(harness, APPOINTMENT)
        assert harness.state.confirmation_pending is True

        await run_turn(harness, MOVE)

        change = _request_of(harness, "appointment-change-request")
        assert change is not None, "the caller's new request must be captured"
        assert change.status in (
            RequestStatus.NEW,
            RequestStatus.COLLECTING_INFORMATION,
            RequestStatus.READY_FOR_CONFIRMATION,
        )
        assert "clear yes or no" not in _last(harness), "the loop must not repeat"
        assert "what date" in _last(harness) or "date" in _last(harness)
    finally:
        await close(harness)


@pytest.mark.asyncio
async def test_new_request_overrides_the_pending_confirmation() -> None:
    harness = await make_pipeline(responses={APPOINTMENT: _appointment(), PRESCRIPTION: _unclear()})
    try:
        await run_turn(harness, APPOINTMENT)
        assert harness.state.confirmation_pending is True

        await run_turn(harness, PRESCRIPTION)

        prescription = _request_of(harness, "prescription-request")
        assert prescription is not None
        # Both pending requests are read back together (the caller never
        # rejected the appointment; nothing is submitted without a clear yes).
        assert harness.state.confirmation_pending is True
        assert "prescription" in _last(harness)
        assert "clear yes or no" not in _last(harness)
    finally:
        await close(harness)


@pytest.mark.asyncio
async def test_repeated_unclear_turns_escalate_instead_of_looping() -> None:
    unclear = InterpretationResult(intent="unclear", requests=[], needs_clarification=True)
    harness = await make_pipeline(responses={"blah": unclear})
    try:
        await run_turn(harness, "blah")
        await run_turn(harness, "blah", sequence=2)
        await run_turn(harness, "blah", sequence=3)

        # After MAX_CLARIFICATIONS the controller escalates to a human.
        assert any(
            entry.tool == "request_human_handoff" for entry in harness.pipeline.tool_executor.audit
        ), "the third unclear turn must escalate rather than repeat the question"
        assert "clear yes or no" not in _last(harness)
    finally:
        await close(harness)


class _RecordingMockLLM:
    """The real MockLLMProvider, with the requests it received recorded."""

    provider_name = "mock-llm"

    def __init__(self) -> None:
        self._provider = MockLLMProvider()
        self.requests: list[LLMRequest] = []

    async def interpret(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return await self._provider.interpret(request)


class _Factory:
    def __init__(self, provider: _RecordingMockLLM) -> None:
        self._provider = provider

    def create(self) -> _RecordingMockLLM:
        return self._provider


@pytest.mark.asyncio
async def test_mock_provider_path_does_not_loop_on_a_new_request() -> None:
    """End-to-end on the demo provider used in the reported call."""
    harness = await make_pipeline(responses={})
    harness.pipeline._llm_factory = _Factory(_RecordingMockLLM())
    harness.pipeline._general_service_ready = True  # keep general chat out of the way
    try:
        await run_turn(harness, APPOINTMENT)
        assert harness.state.confirmation_pending is True

        await run_turn(harness, PRESCRIPTION, sequence=2)
        assert _request_of(harness, "prescription-request") is not None
        assert "clear yes or no" not in _last(harness)

        await run_turn(harness, MOVE, sequence=3)
        assert _request_of(harness, "appointment-change-request") is not None
    finally:
        await close(harness)
