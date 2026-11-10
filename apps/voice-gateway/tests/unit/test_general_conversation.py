"""Day 19: everyday conversation (small talk + general knowledge).

EMMA must be able to hold a normal short conversation, but it must never:

* answer a clinical question (no diagnosis, dosage, triage),
* answer a practice-specific question from world knowledge (opening hours,
  services, contact details come from the RAG knowledge base only),
* take an administrative action from a conversational turn,
* run before the emergency guard.

These tests pin all four, plus the deterministic fallbacks that keep a call
alive when the model is unavailable.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.domain.llm import InterpretationResult
from app.integrations.llm.base import LLMRequest, LLMResponse
from app.services.conversation.general_conversation import (
    CLINICAL_REFUSAL,
    GENERAL_FALLBACK,
    GeneralAnswerSchema,
    GeneralConversationService,
    detect_small_talk,
    is_clinical_question,
    looks_like_general_question,
    looks_practice_specific,
)
from app.services.safety.emergency_guard import EMERGENCY_SCRIPT
from tests.evaluation.day9 import (
    ScriptedLLM,
    close,
    drain_outbound,
    make_pipeline,
    run_turn,
)

# --- deterministic detectors ----------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("thanks", "thanks"),
        ("Thank you very much", "thanks"),
        ("bye", "goodbye"),
        ("how are you", "how_are_you"),
        ("are you a robot", "identity"),
        ("what can you do", "capability"),
        ("what time is it", "time"),
        ("what day is it", "date"),
        ("tell me a joke", "joke"),
        ("can you sing", "sing"),
        ("sorry", "apology"),
    ],
)
def test_small_talk_is_deterministic(text: str, kind: str) -> None:
    turn = detect_small_talk(text)
    assert turn is not None
    assert turn.kind == kind
    assert turn.response_text


@pytest.mark.parametrize(
    "text",
    [
        "thanks, can you also book Tuesday morning",
        "sorry I need an appointment",
        "hello I would like a repeat prescription",
        "what can you do about my repeat prescription",
        "I need to cancel my appointment",
    ],
)
def test_small_talk_never_hijacks_a_real_request(text: str) -> None:
    assert detect_small_talk(text) is None


def test_clinical_questions_are_detected_deterministically() -> None:
    assert is_clinical_question("What dose of paracetamol should I take?")
    assert is_clinical_question("Do I have diabetes?")
    assert is_clinical_question("Are there side effects?")
    # Administrative phrasing must never be mistaken for a clinical question.
    assert not is_clinical_question("I need a repeat prescription")
    assert not is_clinical_question("Can I book an appointment for Tuesday?")


def test_practice_scope_vocabulary() -> None:
    assert looks_practice_specific("What are your opening hours?")
    assert looks_practice_specific("Do you have a specialist tomorrow?")
    assert looks_practice_specific("Where is the surgery car park?")
    assert not looks_practice_specific("What is the capital of France?")
    assert not looks_practice_specific("How many days are in a leap year?")
    assert looks_like_general_question("What is the capital of France?")


# --- service-level behaviour ----------------------------------------------


class _Provider:
    provider_name = "fake-general"

    def __init__(
        self, *, answer: str = "Paris is the capital of France.", error: Exception | None = None
    ) -> None:
        self.requests: list[LLMRequest] = []
        self._answer = answer
        self._error = error

    async def interpret(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        payload = {"answer": self._answer, "in_scope": True, "refused": False}
        return LLMResponse(
            raw_text=json.dumps(payload),
            parsed=None,
            first_token_ms=0.0,
            total_ms=0.0,
            correlation_id=request.correlation_id,
        )


def _service(provider: _Provider) -> GeneralConversationService:
    return GeneralConversationService(
        llm_provider=provider,  # type: ignore[arg-type]
        model="test-model",
        timeout_seconds=1.0,
        max_output_tokens=100,
        prompt_version="v1",
    )


@pytest.mark.asyncio
async def test_general_question_is_answered_and_cleaned() -> None:
    provider = _Provider(answer="**Paris** is the capital of France. It is in Europe. Extra.")
    answer = await _service(provider).answer(
        question="What is the capital of France?", correlation_id="test"
    )
    assert answer.source == "llm"
    assert "**" not in answer.text
    assert "Extra." not in answer.text  # two-sentence cap
    assert provider.requests[0].response_schema is GeneralAnswerSchema


@pytest.mark.asyncio
async def test_clinical_question_is_refused_without_calling_the_model() -> None:
    provider = _Provider()
    answer = await _service(provider).answer(
        question="What dose of paracetamol should I take?", correlation_id="test"
    )
    assert answer.source == "refusal"
    assert answer.text == CLINICAL_REFUSAL
    assert provider.requests == []


@pytest.mark.asyncio
async def test_practice_question_is_not_answered_from_world_knowledge() -> None:
    provider = _Provider()
    answer = await _service(provider).answer(
        question="What are your opening hours on Saturday?", correlation_id="test"
    )
    assert answer.text == ""
    assert answer.source == "out_of_scope"
    assert provider.requests == []


@pytest.mark.asyncio
async def test_provider_failure_degrades_to_the_fallback() -> None:
    provider = _Provider(error=RuntimeError("boom"))
    answer = await _service(provider).answer(
        question="How many days are in a leap year?", correlation_id="test"
    )
    assert answer.text == GENERAL_FALLBACK
    assert answer.source == "fallback"


@pytest.mark.asyncio
async def test_malformed_output_degrades_to_the_fallback() -> None:
    provider = _Provider(answer="")
    service = _service(provider)
    answer = await service.answer(question="Is the sky blue?", correlation_id="test")
    assert answer.source == "fallback"
    assert answer.text == GENERAL_FALLBACK


# --- pipeline routing ------------------------------------------------------


class _GeneralAwareLLM(ScriptedLLM):
    """Scripted interpreter that also serves the general-knowledge schema."""

    def __init__(
        self,
        responses: dict[str, Any],
        *,
        general_answer: str = "Paris is the capital of France.",
        general_error: Exception | None = None,
    ) -> None:
        super().__init__(responses)
        self.general_requests: list[LLMRequest] = []
        self._general_answer = general_answer
        self._general_error = general_error

    async def interpret(self, request: LLMRequest) -> LLMResponse:
        if request.response_schema is GeneralAnswerSchema:
            self.general_requests.append(request)
            if self._general_error is not None:
                raise self._general_error
            payload = {"answer": self._general_answer, "in_scope": True, "refused": False}
            return LLMResponse(
                raw_text=json.dumps(payload),
                parsed=None,
                first_token_ms=0.0,
                total_ms=0.0,
                correlation_id=request.correlation_id,
            )
        return await super().interpret(request)


def _other_intent() -> InterpretationResult:
    return InterpretationResult(intent="other", requests=[])


@pytest.mark.asyncio
async def test_pipeline_small_talk_costs_no_model_call_and_no_action() -> None:
    harness = await make_pipeline(responses={})
    try:
        await run_turn(harness, "thanks")
        assert harness.state.turns[-1].content.startswith("You're very welcome")
        assert harness.llm.requests == []
        assert harness.practice.calls == []
    finally:
        await close(harness)


@pytest.mark.asyncio
async def test_pipeline_answers_a_general_question() -> None:
    harness = await make_pipeline(responses={})
    harness.pipeline._llm_factory = _Factory(
        _GeneralAwareLLM({"What is the capital of France?": _other_intent()})
    )
    try:
        await run_turn(harness, "What is the capital of France?")
        assert "Paris" in harness.state.turns[-1].content
        assert harness.practice.calls == []
        assert harness.state.confirmed_submissions == []
    finally:
        await close(harness)


@pytest.mark.asyncio
async def test_pipeline_refuses_clinical_questions_mid_conversation() -> None:
    harness = await make_pipeline(responses={})
    harness.pipeline._llm_factory = _Factory(_GeneralAwareLLM({}))
    try:
        drain_outbound(harness)
        await run_turn(harness, "What dose of paracetamol should I take?")
        # The emergency guard did not match, and no model call was made for the
        # clinical refusal (the small-talk/refusal path is deterministic).
        spoken = harness.state.turns[-1].content
        assert spoken.startswith("I can't give medical advice")
        assert harness.practice.calls == []
    finally:
        await close(harness)


@pytest.mark.asyncio
async def test_pipeline_emergency_still_wins_over_general_conversation() -> None:
    harness = await make_pipeline(responses={})
    harness.pipeline._llm_factory = _Factory(_GeneralAwareLLM({}))
    try:
        drain_outbound(harness)
        await run_turn(harness, "I have chest pain and I can't breathe")
        assert harness.state.turns[-1].content == EMERGENCY_SCRIPT
        assert harness.llm.requests == []
        assert harness.practice.calls == []
    finally:
        await close(harness)


@pytest.mark.asyncio
async def test_pipeline_does_not_hijack_pending_confirmation() -> None:
    """Small talk must not replace a confirmation prompt the caller must answer."""
    harness = await make_pipeline(
        responses={
            "I'd like an appointment for Tuesday morning": _appointment(),
            "yes": _other_intent(),
            "how are you": InterpretationResult(
                intent="unclear", requests=[], needs_clarification=True
            ),
        }
    )
    try:
        await run_turn(harness, "I'd like an appointment for Tuesday morning")
        assert harness.state.confirmation_pending is True
        await run_turn(harness, "how are you")
        # The small-talk template must not answer while a confirmation is
        # pending, and nothing may be submitted from a conversational turn.
        assert not harness.state.turns[-1].content.startswith("I'm doing well")
        assert harness.practice.calls == []
    finally:
        await close(harness)


class _Factory:
    def __init__(self, provider: Any) -> None:
        self._provider = provider

    def create(self) -> Any:
        return self._provider


def _appointment() -> InterpretationResult:
    from app.domain.llm import RequestDetail

    return InterpretationResult(
        intent="appointment-request",
        requests=[
            RequestDetail(
                request_type="appointment-request",
                details={
                    "preferred_date": "Tuesday",
                    "preferred_time": "morning",
                    "reason_as_stated": "check-up",
                },
                missing_fields=[],
                source_quotes=["Tuesday morning"],
            )
        ],
    )
