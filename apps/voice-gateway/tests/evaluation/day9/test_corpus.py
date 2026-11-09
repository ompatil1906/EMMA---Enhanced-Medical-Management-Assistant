"""Day 9 multi-turn conversation corpus.

Runs 33 synthetic scenarios through the real `VoicePipeline` with a scripted
LLM, fake practice backend, and fake retriever. Every assertion is about
deterministic behaviour: state transitions, slot filling, confirmation,
submission, cancellation, emergency ordering, duplicate-turn handling, and
grounded RAG follow-ups.

No real credentials, no network calls, no real patient data.
"""

from __future__ import annotations

import asyncio

import pytest

from app.domain.conversation import RequestStatus
from tests.evaluation.day9 import (
    DEFAULT_ANSWER,
    EVAL_PRACTICE_ID,
    OTHER_PRACTICE_ID,
    EvalCase,
    TurnSpec,
    appointment,
    appointment_change,
    assistant_messages,
    close,
    last_assistant_message,
    make_chunk,
    make_pipeline,
    multiple,
    practice_information,
    prescription,
    request_of,
    run_case,
    run_turn,
    unclear,
)

APPOINTMENT_UTTERANCE = "I'd like to book an appointment for Tuesday morning"
YES = "yes"
NO = "no"
MAYBE = "maybe"

EMERGENCY_SCRIPT = "This may be a medical emergency. Please hang up and call 999 now."

# Phrases that would be a false persistence claim if spoken by the assistant.
FALSE_CLAIM_PHRASES = (
    "appointment is booked",
    "appointment has been booked",
    "you are booked",
    "booked for you",
    "appointment is confirmed",
    "your appointment is confirmed",
    "prescription is ready",
    "prescription has been sent",
    "prescription has been fulfilled",
)


CASES: tuple[EvalCase, ...] = (
    EvalCase(
        name="appointment request requires confirmation",
        turns=(TurnSpec(APPOINTMENT_UTTERANCE, appointment()),),
        expected_statuses=(("appointment-request", RequestStatus.READY_FOR_CONFIRMATION),),
        expected_llm_calls=1,
        expected_confirmation_pending=True,
        expected_last_assistant_contains=("please confirm", "tuesday", "morning"),
    ),
    EvalCase(
        name="explicit confirmation submits",
        turns=(
            TurnSpec(APPOINTMENT_UTTERANCE, appointment()),
            TurnSpec(YES, unclear("confirmation response")),
        ),
        expected_statuses=(("appointment-request", RequestStatus.SUBMITTED),),
        expected_backend_calls=1,
        expected_llm_calls=2,
        expected_submitted_types=("appointment-request",),
        expected_last_assistant_contains=("passed your request", "not a confirmed appointment"),
    ),
    EvalCase(
        name="explicit rejection does not submit",
        turns=(
            TurnSpec(APPOINTMENT_UTTERANCE, appointment()),
            TurnSpec(NO, unclear("confirmation response")),
        ),
        expected_statuses=(("appointment-request", RequestStatus.READY_FOR_CONFIRMATION),),
        expected_backend_calls=0,
        expected_llm_calls=2,
        expected_confirmation_pending=False,
        expected_last_assistant_contains=("no problem",),
    ),
    EvalCase(
        name="ambiguous yes does not submit",
        turns=(
            TurnSpec(APPOINTMENT_UTTERANCE, appointment()),
            TurnSpec(MAYBE, unclear("confirmation response")),
        ),
        expected_statuses=(("appointment-request", RequestStatus.READY_FOR_CONFIRMATION),),
        expected_backend_calls=0,
        expected_llm_calls=2,
        expected_confirmation_pending=True,
        expected_last_assistant_contains=("clear yes or no",),
    ),
    EvalCase(
        name="cancel before confirmation",
        turns=(
            TurnSpec(APPOINTMENT_UTTERANCE, appointment()),
            TurnSpec("cancel that", unclear("confirmation response")),
        ),
        expected_statuses=(("appointment-request", RequestStatus.CANCELLED),),
        expected_backend_calls=0,
        expected_llm_calls=2,
        expected_confirmation_pending=False,
        expected_cancelled_types=("appointment-request",),
        expected_last_assistant_contains=("cancelled",),
    ),
    EvalCase(
        name="correction after read-back triggers fresh confirmation",
        turns=(
            TurnSpec(APPOINTMENT_UTTERANCE, appointment()),
            TurnSpec("actually make it Thursday", appointment(date="thursday")),
        ),
        expected_statuses=(("appointment-request", RequestStatus.READY_FOR_CONFIRMATION),),
        expected_backend_calls=0,
        expected_llm_calls=2,
        expected_confirmation_pending=True,
        expected_last_assistant_contains=("please confirm", "thursday"),
    ),
    EvalCase(
        name="confirmed correction submits corrected value",
        turns=(
            TurnSpec(APPOINTMENT_UTTERANCE, appointment()),
            TurnSpec("actually make it Thursday", appointment(date="thursday")),
            TurnSpec(YES, unclear("confirmation response")),
        ),
        expected_statuses=(("appointment-request", RequestStatus.SUBMITTED),),
        expected_backend_calls=1,
        expected_llm_calls=3,
        expected_submitted_types=("appointment-request",),
        expected_backend_payload_subset=(("preferred_date", "thursday"),),
    ),
    EvalCase(
        name="missing date is requested",
        turns=(TurnSpec("I'd like an appointment in the morning", appointment(date=None)),),
        expected_statuses=(("appointment-request", RequestStatus.COLLECTING_INFORMATION),),
        expected_llm_calls=1,
        expected_awaiting_field="preferred_date",
        expected_confirmation_pending=False,
        expected_last_assistant_contains=("what date",),
    ),
    EvalCase(
        name="missing time is requested",
        turns=(TurnSpec("I'd like an appointment on Tuesday", appointment(time=None)),),
        expected_statuses=(("appointment-request", RequestStatus.COLLECTING_INFORMATION),),
        expected_llm_calls=1,
        expected_awaiting_field="preferred_time",
        expected_confirmation_pending=False,
        expected_last_assistant_contains=("what time of day",),
    ),
    EvalCase(
        name="slot filling then confirmation submits",
        turns=(
            TurnSpec("I'd like an appointment on Tuesday", appointment(time=None)),
            TurnSpec("in the morning", appointment(date=None)),
            TurnSpec(YES, unclear("confirmation response")),
        ),
        expected_statuses=(("appointment-request", RequestStatus.SUBMITTED),),
        expected_backend_calls=1,
        # The second turn is a bare slot value, filled deterministically.
        expected_llm_calls=2,
        expected_submitted_types=("appointment-request",),
        expected_backend_payload_subset=(
            ("preferred_date", "tuesday"),
            ("preferred_time", "morning"),
        ),
    ),
    EvalCase(
        name="prescription request submits after confirmation",
        turns=(
            TurnSpec("I need a repeat prescription", prescription()),
            TurnSpec(YES, unclear("confirmation response")),
        ),
        expected_statuses=(("prescription-request", RequestStatus.SUBMITTED),),
        expected_backend_calls=1,
        expected_llm_calls=2,
        expected_submitted_types=("prescription-request",),
    ),
    EvalCase(
        name="appointment change submits after confirmation",
        turns=(
            TurnSpec(
                "I need to move my appointment to Thursday afternoon",
                appointment_change(),
            ),
            TurnSpec(YES, unclear("confirmation response")),
        ),
        expected_statuses=(("appointment-change-request", RequestStatus.SUBMITTED),),
        expected_backend_calls=1,
        expected_llm_calls=2,
        expected_submitted_types=("appointment-change-request",),
        expected_backend_payload_subset=(
            ("preferred_date", "thursday"),
            ("preferred_time", "afternoon"),
        ),
    ),
    EvalCase(
        name="multiple requests preserved and submitted together",
        turns=(
            TurnSpec(
                "I need an appointment Tuesday morning and a repeat prescription",
                multiple(appointment(), prescription()),
            ),
            TurnSpec(YES, unclear("confirmation response")),
        ),
        expected_statuses=(
            ("appointment-request", RequestStatus.SUBMITTED),
            ("prescription-request", RequestStatus.SUBMITTED),
        ),
        expected_backend_calls=2,
        expected_llm_calls=2,
        expected_submitted_types=("appointment-request", "prescription-request"),
        expected_last_assistant_contains=("passed your requests",),
    ),
    EvalCase(
        name="multiple requests with independent missing fields",
        turns=(
            TurnSpec(
                "I need an appointment Tuesday and a repeat prescription",
                multiple(appointment(time=None), prescription()),
            ),
            TurnSpec("in the morning", appointment(date=None)),
            TurnSpec(YES, unclear("confirmation response")),
        ),
        expected_statuses=(
            ("appointment-request", RequestStatus.SUBMITTED),
            ("prescription-request", RequestStatus.SUBMITTED),
        ),
        expected_backend_calls=2,
        # The second turn is a bare slot value, filled deterministically.
        expected_llm_calls=2,
        expected_submitted_types=("appointment-request", "prescription-request"),
    ),
    EvalCase(
        name="cancel one of two pending requests",
        turns=(
            TurnSpec(
                "I need an appointment Tuesday morning and a repeat prescription",
                multiple(appointment(), prescription()),
            ),
            TurnSpec("cancel the prescription", unclear("confirmation response")),
        ),
        expected_statuses=(
            ("appointment-request", RequestStatus.READY_FOR_CONFIRMATION),
            ("prescription-request", RequestStatus.CANCELLED),
        ),
        expected_backend_calls=0,
        expected_llm_calls=2,
        expected_confirmation_pending=True,
        expected_cancelled_types=("prescription-request",),
        expected_last_assistant_contains=("cancelled your prescription", "please confirm"),
    ),
    EvalCase(
        name="confirm remaining request after targeted cancel",
        turns=(
            TurnSpec(
                "I need an appointment Tuesday morning and a repeat prescription",
                multiple(appointment(), prescription()),
            ),
            TurnSpec("cancel the prescription", unclear("confirmation response")),
            TurnSpec(YES, unclear("confirmation response")),
        ),
        expected_statuses=(
            ("appointment-request", RequestStatus.SUBMITTED),
            ("prescription-request", RequestStatus.CANCELLED),
        ),
        expected_backend_calls=1,
        expected_llm_calls=3,
        expected_submitted_types=("appointment-request",),
    ),
    EvalCase(
        name="practice information uses grounded answer",
        turns=(TurnSpec("What are your opening hours?", practice_information()),),
        expected_llm_calls=1,
        expected_retrieval_calls=1,
        expected_last_grounded_topic="opening hours",
        expected_last_assistant_contains=("8am to 6pm",),
        retrieval_chunks=(make_chunk(),),
    ),
    EvalCase(
        name="practice information follow-up uses previous topic",
        turns=(
            TurnSpec("What are your opening hours?", practice_information()),
            TurnSpec("What about Saturday?", practice_information()),
        ),
        expected_llm_calls=2,
        expected_retrieval_calls=2,
        expected_last_grounded_topic="opening hours",
        expected_last_assistant_contains=("8am to 6pm",),
        retrieval_chunks=(make_chunk(),),
    ),
    EvalCase(
        name="practice information abstains without evidence",
        turns=(TurnSpec("Do you have a specialist tomorrow?", practice_information("specialist")),),
        expected_llm_calls=1,
        expected_retrieval_calls=1,
        expected_last_assistant_contains=("don't have information",),
    ),
    EvalCase(
        name="emergency first turn blocks everything",
        turns=(TurnSpec("I have chest pain", None),),
        expected_llm_calls=0,
        expected_retrieval_calls=0,
        expected_backend_calls=0,
        expected_confirmation_pending=False,
        expected_last_assistant_contains=(EMERGENCY_SCRIPT,),
    ),
    EvalCase(
        name="emergency during slot filling cancels request",
        turns=(
            TurnSpec("I'd like an appointment on Tuesday", appointment(time=None)),
            TurnSpec("I can't breathe", None),
        ),
        expected_statuses=(("appointment-request", RequestStatus.CANCELLED),),
        expected_llm_calls=1,
        expected_backend_calls=0,
        expected_confirmation_pending=False,
        expected_cancelled_types=("appointment-request",),
        expected_last_assistant_contains=(EMERGENCY_SCRIPT,),
    ),
    EvalCase(
        name="emergency during confirmation cancels pending request",
        turns=(
            TurnSpec(APPOINTMENT_UTTERANCE, appointment()),
            TurnSpec("I think I'm having a stroke", None),
        ),
        expected_statuses=(("appointment-request", RequestStatus.CANCELLED),),
        expected_llm_calls=1,
        expected_backend_calls=0,
        expected_confirmation_pending=False,
        expected_cancelled_types=("appointment-request",),
        expected_last_assistant_contains=(EMERGENCY_SCRIPT,),
    ),
    EvalCase(
        name="emergency plus administrative request stays emergency",
        turns=(TurnSpec("I have chest pain and I need an appointment", None),),
        expected_llm_calls=0,
        expected_retrieval_calls=0,
        expected_backend_calls=0,
        expected_last_assistant_contains=(EMERGENCY_SCRIPT,),
    ),
    EvalCase(
        name="duplicate final turn is ignored",
        turns=(
            TurnSpec(APPOINTMENT_UTTERANCE, appointment(), sequence=1),
            TurnSpec(APPOINTMENT_UTTERANCE, appointment(date="wednesday"), sequence=1),
        ),
        expected_statuses=(("appointment-request", RequestStatus.READY_FOR_CONFIRMATION),),
        expected_llm_calls=1,
        expected_backend_calls=0,
        expected_confirmation_pending=True,
        expected_last_assistant_contains=("tuesday",),
    ),
    EvalCase(
        name="out-of-order final turn is ignored",
        turns=(
            TurnSpec(APPOINTMENT_UTTERANCE, appointment(), sequence=5),
            TurnSpec("I'd like an appointment on Friday", appointment(date="friday"), sequence=3),
        ),
        expected_statuses=(("appointment-request", RequestStatus.READY_FOR_CONFIRMATION),),
        expected_llm_calls=1,
        expected_backend_calls=0,
        expected_last_assistant_contains=("tuesday",),
    ),
    EvalCase(
        name="empty transcript is ignored",
        turns=(TurnSpec("   ", None),),
        expected_llm_calls=0,
        expected_backend_calls=0,
        expected_confirmation_pending=False,
    ),
    EvalCase(
        name="low-confidence transcript asks for repetition",
        turns=(
            TurnSpec(
                "I'd like an appointment on Tuesday morning",
                appointment(),
                confidence=0.2,
            ),
        ),
        expected_llm_calls=0,
        expected_backend_calls=0,
        expected_last_assistant_contains=("didn't hear that clearly",),
    ),
    EvalCase(
        name="repeated confirmation does not submit twice",
        turns=(
            TurnSpec(APPOINTMENT_UTTERANCE, appointment()),
            TurnSpec(YES, unclear("confirmation response")),
            TurnSpec(YES, unclear("confirmation response")),
        ),
        expected_statuses=(("appointment-request", RequestStatus.SUBMITTED),),
        expected_backend_calls=1,
        expected_llm_calls=3,
        expected_submitted_types=("appointment-request",),
        expected_last_assistant_contains=("could you tell me more",),
    ),
    EvalCase(
        name="backend rejection is truthful and marks failed",
        turns=(
            TurnSpec(APPOINTMENT_UTTERANCE, appointment()),
            TurnSpec(YES, unclear("confirmation response")),
        ),
        expected_statuses=(("appointment-request", RequestStatus.FAILED),),
        expected_backend_calls=1,
        expected_llm_calls=2,
        expected_failed_types=("appointment-request",),
        expected_last_assistant_contains=("wasn't able to submit",),
        practice_outcomes=("rejected",),
    ),
    EvalCase(
        name="backend timeout is truthful and marks failed",
        turns=(
            TurnSpec(APPOINTMENT_UTTERANCE, appointment()),
            TurnSpec(YES, unclear("confirmation response")),
        ),
        expected_statuses=(("appointment-request", RequestStatus.FAILED),),
        expected_backend_calls=1,
        expected_llm_calls=2,
        expected_failed_types=("appointment-request",),
        expected_last_assistant_contains=("wasn't able to submit",),
        practice_outcomes=("timeout",),
    ),
    EvalCase(
        name="partial multi-request backend failure is independent",
        turns=(
            TurnSpec(
                "I need an appointment Tuesday morning and a repeat prescription",
                multiple(appointment(), prescription()),
            ),
            TurnSpec(YES, unclear("confirmation response")),
        ),
        expected_statuses=(
            ("appointment-request", RequestStatus.SUBMITTED),
            ("prescription-request", RequestStatus.FAILED),
        ),
        expected_backend_calls=2,
        expected_llm_calls=2,
        expected_submitted_types=("appointment-request",),
        expected_failed_types=("prescription-request",),
        expected_last_assistant_contains=("wasn't able to submit",),
        practice_outcomes=("confirmed", "rejected"),
    ),
    EvalCase(
        name="model cannot mark incomplete request ready",
        turns=(TurnSpec("I'd like an appointment on Tuesday", appointment(time=None)),),
        expected_statuses=(("appointment-request", RequestStatus.COLLECTING_INFORMATION),),
        expected_llm_calls=1,
        expected_awaiting_field="preferred_time",
        expected_confirmation_pending=False,
        expected_last_assistant_contains=("what time of day",),
    ),
    EvalCase(
        name="model-supplied practice id is stripped from submission",
        turns=(
            TurnSpec(
                APPOINTMENT_UTTERANCE,
                appointment(extra={"practice_id": "evil-practice", "tenant_id": "evil-tenant"}),
            ),
            TurnSpec(YES, unclear("confirmation response")),
        ),
        expected_statuses=(("appointment-request", RequestStatus.SUBMITTED),),
        expected_backend_calls=1,
        expected_llm_calls=2,
        expected_submitted_types=("appointment-request",),
        forbidden_backend_payload_keys=("practice_id", "tenant_id"),
    ),
    EvalCase(
        name="retrieval uses trusted server practice id",
        turns=(
            TurnSpec(
                "What are your opening hours?",
                practice_information(extra={"practice_id": "evil-practice"}),
            ),
        ),
        expected_llm_calls=1,
        expected_retrieval_calls=1,
        expected_last_grounded_topic="opening hours",
        retrieval_chunks=(make_chunk(),),
    ),
)


def _assert_no_false_claims(harness) -> None:
    for message in assistant_messages(harness):
        lowered = message.lower()
        for phrase in FALSE_CLAIM_PHRASES:
            assert phrase not in lowered, f"false persistence claim: {phrase!r} in {message!r}"


def _assert_case(case: EvalCase, harness) -> None:
    state = harness.state

    for request_type, expected_status in case.expected_statuses:
        request = request_of(harness, request_type)
        assert request is not None, f"{case.name}: missing {request_type} request"
        assert request.status is expected_status, (
            f"{case.name}: {request_type} status {request.status!r} != {expected_status!r}"
        )

    assert len(harness.practice.calls) == case.expected_backend_calls, (
        f"{case.name}: backend calls {len(harness.practice.calls)} != {case.expected_backend_calls}"
    )
    if case.expected_llm_calls is not None:
        assert len(harness.llm.requests) == case.expected_llm_calls, (
            f"{case.name}: LLM calls {len(harness.llm.requests)} != {case.expected_llm_calls}"
        )
    assert len(harness.retriever.queries) == case.expected_retrieval_calls, (
        f"{case.name}: retrieval calls {len(harness.retriever.queries)} != "
        f"{case.expected_retrieval_calls}"
    )

    if case.expected_awaiting_field is not None:
        assert state.awaiting_field == case.expected_awaiting_field, (
            f"{case.name}: awaiting {state.awaiting_field!r} != {case.expected_awaiting_field!r}"
        )
    if case.expected_confirmation_pending is not None:
        assert state.confirmation_pending is case.expected_confirmation_pending, (
            f"{case.name}: confirmation_pending {state.confirmation_pending} != "
            f"{case.expected_confirmation_pending}"
        )

    for request_type in case.expected_submitted_types:
        assert request_type in state.confirmed_submissions, (
            f"{case.name}: {request_type} not in confirmed submissions"
        )
    for request_type in case.expected_failed_types:
        request = request_of(harness, request_type)
        assert request is not None and request.status is RequestStatus.FAILED, (
            f"{case.name}: {request_type} was not marked failed"
        )
    for request_type in case.expected_cancelled_types:
        request = request_of(harness, request_type)
        assert request is not None and request.status is RequestStatus.CANCELLED, (
            f"{case.name}: {request_type} was not marked cancelled"
        )

    if case.expected_last_grounded_topic is not None:
        assert state.last_grounded_topic == case.expected_last_grounded_topic, (
            f"{case.name}: grounded topic {state.last_grounded_topic!r} != "
            f"{case.expected_last_grounded_topic!r}"
        )

    last_message = last_assistant_message(harness).lower()
    for expected in case.expected_last_assistant_contains:
        assert expected.lower() in last_message, (
            f"{case.name}: expected {expected!r} in last assistant message "
            f"{last_assistant_message(harness)!r}"
        )
    for forbidden in case.forbidden_assistant_contains:
        assert forbidden.lower() not in last_message, (
            f"{case.name}: forbidden {forbidden!r} in last assistant message"
        )

    if case.expected_backend_payload_subset:
        assert harness.practice.calls, f"{case.name}: no backend call to inspect"
        payload = harness.practice.calls[-1]["structured_payload"]
        for key, value in case.expected_backend_payload_subset:
            assert payload.get(key) == value, (
                f"{case.name}: payload {key}={payload.get(key)!r} != {value!r}"
            )
    for key in case.forbidden_backend_payload_keys:
        assert harness.practice.calls, f"{case.name}: no backend call to inspect"
        payload = harness.practice.calls[-1]["structured_payload"]
        assert key not in payload, f"{case.name}: forbidden payload key {key!r} present"

    _assert_no_false_claims(harness)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
async def test_day9_corpus_case(case: EvalCase) -> None:
    harness = await run_case(case)
    _assert_case(case, harness)


@pytest.mark.asyncio
async def test_day9_corpus_summary() -> None:
    """Run every case once and report aggregate outcomes."""
    results: list[tuple[str, bool, str | None]] = []
    for case in CASES:
        harness = await run_case(case)
        try:
            _assert_case(case, harness)
        except AssertionError as exc:  # pragma: no cover - only on regression
            results.append((case.name, False, str(exc)))
        else:
            results.append((case.name, True, None))

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    failures = [(name, detail) for name, ok, detail in results if not ok]

    print(f"\nDay 9 multi-turn corpus: {passed}/{total} cases passed")
    if failures:
        for name, detail in failures:
            print(f"  FAIL {name}: {detail}")
    assert passed == total, f"{len(failures)} Day 9 cases failed"


@pytest.mark.asyncio
async def test_day9_session_isolation() -> None:
    """Two concurrent sessions never share requests, context, or submissions."""
    first = await make_pipeline(
        responses={
            "I'd like to book an appointment for Tuesday morning": appointment(),
            "yes": unclear("confirmation response"),
        },
        practice_id=EVAL_PRACTICE_ID,
        retrieval_chunks=(make_chunk(practice_id=EVAL_PRACTICE_ID),),
    )
    second = await make_pipeline(
        responses={
            "What are your opening hours?": practice_information(),
            "I need a repeat prescription": prescription(),
        },
        practice_id=OTHER_PRACTICE_ID,
        retrieval_chunks=(make_chunk(practice_id=OTHER_PRACTICE_ID),),
    )
    try:
        # Sessions run concurrently; turns within one session stay ordered.
        await asyncio.gather(
            run_turn(first, "I'd like to book an appointment for Tuesday morning"),
            run_turn(second, "What are your opening hours?"),
        )
        await asyncio.gather(
            run_turn(first, "yes"),
            run_turn(second, "I need a repeat prescription"),
        )
    finally:
        await close(first)
        await close(second)

    assert first.session.session_id != second.session.session_id
    assert request_of(first, "appointment-request") is not None
    assert request_of(first, "prescription-request") is None
    assert request_of(second, "prescription-request") is not None
    assert request_of(second, "appointment-request") is None
    assert first.practice.calls and first.practice.calls[0]["practice_id"] == EVAL_PRACTICE_ID
    assert second.retriever.queries[0].practice_id == OTHER_PRACTICE_ID
    assert first.state.last_grounded_topic is None
    assert second.state.last_grounded_topic == "opening hours"


@pytest.mark.asyncio
async def test_day9_llm_context_is_bounded() -> None:
    """The server-built context stays small and contains no raw transcript dump."""
    harness = await make_pipeline(
        responses={
            "I'd like an appointment on Tuesday": appointment(time=None),
            "I'm not sure, can you help?": unclear("I need a time."),
        }
    )
    try:
        await run_turn(harness, "I'd like an appointment on Tuesday")
        await run_turn(harness, "I'm not sure, can you help?")
    finally:
        await close(harness)

    contexts = [request.context for request in harness.llm.requests]
    assert contexts[1] is not None
    assert "awaiting_field: preferred_time" in contexts[1]
    assert len(contexts[1]) < 1000
    # The full first transcript is not replayed verbatim.
    assert "I'd like an appointment on Tuesday" not in contexts[1]


@pytest.mark.asyncio
async def test_day9_answer_text_is_not_a_persistence_claim() -> None:
    """RAG answers are spoken verbatim and never claim an admin action."""
    harness = await make_pipeline(
        responses={"What are your opening hours?": practice_information()},
        retrieval_chunks=(make_chunk(),),
        answer_text=DEFAULT_ANSWER,
    )
    try:
        await run_turn(harness, "What are your opening hours?")
    finally:
        await close(harness)

    assert last_assistant_message(harness) == DEFAULT_ANSWER
    assert harness.state.confirmed_submissions == []
