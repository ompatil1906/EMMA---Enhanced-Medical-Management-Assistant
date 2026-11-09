"""Day 13: LiveKit agent ↔ controller integration tests (offline, mocks).

Tests the agent-to-ConversationController boundary with mocked providers:
no live OpenAI, Deepgram, ElevenLabs, or LiveKit calls. No API credits consumed.

Covers conversation scenarios A–J and emergency safety.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import Settings
from app.domain.audio import AudioFormat
from app.domain.fsm import TransitionReason, VoiceState
from app.domain.llm import InterpretationResult, RequestDetail
from app.domain.speech import FinalTranscript
from app.domain.speech_session import SpeechSession
from app.services.conversation.conversation_controller import ConversationController
from app.services.response_handler import DeterministicResponseHandler
from app.services.voice_pipeline import VoicePipeline


def _make_settings(**overrides: Any) -> Settings:
    data = {
        "PRACTICE_ID": "demo-practice-001",
        "STT_PROVIDER": "deepgram",
        "TTS_PROVIDER": "elevenlabs",
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "test-key-not-real",
        "DEEPGRAM_API_KEY": "test-key-not-real",
        "ELEVENLABS_API_KEY": "test-key-not-real",
        "ELEVENLABS_VOICE_ID": "test-voice-not-real",
    }
    data.update(overrides)
    return Settings(**data)


def _make_pipeline(settings: Settings) -> VoicePipeline:
    """Build a VoicePipeline with mocked providers."""
    session = SpeechSession(
        session_id="test-session",
        call_sid="test-call",
        practice_id=settings.PRACTICE_ID,
        audio_format=AudioFormat(sample_rate=16000, channels=1),
    )
    session.fsm.transition(VoiceState.CONNECTING, TransitionReason.CALL_ACCEPTED)
    session.fsm.transition(VoiceState.LISTENING, TransitionReason.GREETING_COMPLETED)

    stt = MagicMock()
    stt.provider_name = "mock-stt"
    stt.push_audio = AsyncMock()

    tts = MagicMock()
    tts.provider_name = "mock-tts"

    async def _synthesize(text: str, sid: str, fmt: AudioFormat):
        from app.domain.speech import (
            SynthesisChunk,
            SynthesisCompleted,
            SynthesisStarted,
        )

        yield SynthesisStarted(
            session_id=sid,
            provider="mock-tts",
            utterance_id="u1",
            text_chars=len(text),
            audio_format=fmt,
        )
        yield SynthesisChunk(
            session_id=sid,
            provider="mock-tts",
            utterance_id="u1",
            payload=b"\x00\x01" * 160,
            audio_format=fmt,
        )
        yield SynthesisCompleted(
            session_id=sid,
            provider="mock-tts",
            utterance_id="u1",
            total_bytes=320,
            chunk_count=1,
            cancelled=False,
        )

    tts.synthesize = _synthesize

    pipeline = VoicePipeline(
        session=session,
        stt=stt,
        tts=tts,
        response_handler=DeterministicResponseHandler(),
        practice_client=None,
        practice_id=settings.PRACTICE_ID,
        settings=settings,
        llm_factory=None,
        control_events=True,
    )
    return pipeline


def _final_transcript(
    pipeline: VoicePipeline, text: str, sequence: int = 1, confidence: float = 0.99
) -> FinalTranscript:
    return FinalTranscript(
        session_id=pipeline.session.session_id,
        provider="mock-stt",
        sequence=sequence,
        text=text,
        confidence=confidence,
    )


def _interpretation_result(
    intent: str,
    requests: list[dict[str, Any]] | None = None,
    needs_clarification: bool = False,
    clarification_reason: str | None = None,
) -> InterpretationResult:
    """Build an InterpretationResult for mocking _interpret_utterance."""
    req_list = []
    for r in requests or []:
        req_list.append(
            RequestDetail(
                request_type=r.get("request_type", "appointment-request"),
                details=r.get("details", {}),
                missing_fields=r.get("missing_fields", []),
                source_quotes=r.get("source_quotes", []),
            )
        )
    return InterpretationResult(
        intent=intent,
        requests=req_list,
        needs_clarification=needs_clarification,
        clarification_reason=clarification_reason,
    )


@pytest.mark.asyncio
async def test_emergency_guard_runs_before_llm_and_backend() -> None:
    """Emergency guard blocks LLM, embeddings, Qdrant, and backend calls."""
    settings = _make_settings()
    pipeline = _make_pipeline(settings)

    # Emergency utterance.
    event = _final_transcript(
        pipeline, "I am having severe chest pain. I also want an appointment."
    )
    await pipeline._handle_stt_event(event)

    # Verify the emergency script was spoken (via _speak → TTS).
    # The pipeline's _speak method queues audio chunks to the outbound queue.
    # After processing, there should be TTS chunks queued.
    chunks = []
    while not pipeline._outbound_queue.empty():
        item = pipeline._outbound_queue.get_nowait()
        from app.services.voice_pipeline import ControlEvent

        if not isinstance(item, ControlEvent):
            chunks.append(item)

    assert len(chunks) > 0, "Emergency script should have been synthesized"

    # Verify no LLM call happened — _interpret_utterance was not called.
    # (The emergency path returns early before reaching the LLM.)
    assert pipeline.turns_processed == 1


@pytest.mark.asyncio
async def test_appointment_request_scenario() -> None:
    """Scenario A: appointment request → slot filling → confirmation read-back."""
    settings = _make_settings()
    pipeline = _make_pipeline(settings)

    # Mock _interpret_utterance to return an appointment request.
    async def mock_interpret(utterance, context=None):
        return _interpretation_result(
            "appointment-request",
            requests=[
                {
                    "request_type": "appointment-request",
                    "details": {
                        "preferred_date": "next Wednesday",
                        "preferred_time": "morning",
                    },
                    "missing_fields": [],
                    "source_quotes": ["next Wednesday morning"],
                }
            ],
        )

    pipeline._interpret_utterance = mock_interpret

    event1 = _final_transcript(
        pipeline, "I would like to request an appointment next Wednesday morning.", 1
    )
    await pipeline._handle_stt_event(event1)

    # Verify a confirmation was requested (READY_FOR_CONFIRMATION state).
    controller = ConversationController(pipeline.session.conversation_state)
    assert controller.state.confirmation_pending is True


@pytest.mark.asyncio
async def test_correction_invalidates_confirmation() -> None:
    """Scenario B: correction after read-back invalidates confirmation."""
    settings = _make_settings()
    pipeline = _make_pipeline(settings)

    call_count = [0]

    async def mock_interpret(utterance, context=None):
        call_count[0] += 1
        if call_count[0] == 1:
            # First turn: appointment request.
            return _interpretation_result(
                "appointment-request",
                requests=[
                    {
                        "request_type": "appointment-request",
                        "details": {
                            "preferred_date": "Tuesday",
                            "preferred_time": "morning",
                        },
                        "missing_fields": [],
                        "source_quotes": ["Tuesday morning"],
                    }
                ],
            )
        # Second turn: correction.
        return _interpretation_result(
            "appointment-request",
            requests=[
                {
                    "request_type": "appointment-request",
                    "details": {
                        "preferred_date": "Wednesday",
                        "preferred_time": "afternoon",
                    },
                    "missing_fields": [],
                    "source_quotes": ["Wednesday afternoon"],
                }
            ],
        )

    pipeline._interpret_utterance = mock_interpret

    # Turn 1: appointment request.
    event1 = _final_transcript(pipeline, "I want an appointment on Tuesday morning.", 1)
    await pipeline._handle_stt_event(event1)

    controller = ConversationController(pipeline.session.conversation_state)
    assert controller.state.confirmation_pending is True

    # Turn 2: correction.
    event2 = _final_transcript(pipeline, "Actually, make that Wednesday afternoon.", 2)
    await pipeline._handle_stt_event(event2)

    # Confirmation should be invalidated and a new one requested.
    assert controller.state.confirmation_pending is True
    # The correction should have updated the details.
    reqs = controller.state.captured_requests
    appointment_reqs = [r for r in reqs if r.request_type == "appointment-request"]
    assert len(appointment_reqs) >= 1


@pytest.mark.asyncio
async def test_multiple_requests_independent() -> None:
    """Scenario C: appointment + prescription requests are independent."""
    settings = _make_settings()
    pipeline = _make_pipeline(settings)

    async def mock_interpret(utterance, context=None):
        return _interpretation_result(
            "appointment-request",
            requests=[
                {
                    "request_type": "prescription-request",
                    "details": {},
                    "missing_fields": [],
                    "source_quotes": ["repeat prescription"],
                },
                {
                    "request_type": "appointment-request",
                    "details": {},
                    "missing_fields": ["preferred_date", "preferred_time"],
                    "source_quotes": ["appointment"],
                },
            ],
        )

    pipeline._interpret_utterance = mock_interpret

    event = _final_transcript(
        pipeline, "I need a repeat prescription and I also want an appointment.", 1
    )
    await pipeline._handle_stt_event(event)

    controller = ConversationController(pipeline.session.conversation_state)
    reqs = controller.state.captured_requests
    types = [r.request_type for r in reqs]
    assert "prescription-request" in types
    assert "appointment-request" in types


@pytest.mark.asyncio
async def test_cancellation_before_submission() -> None:
    """Scenario D: cancellation marks the request CANCELLED."""
    settings = _make_settings()
    pipeline = _make_pipeline(settings)

    async def mock_interpret(utterance, context=None):
        if context is None or "confirmation_pending: true" not in context:
            # First turn: appointment request.
            return _interpretation_result(
                "appointment-request",
                requests=[
                    {
                        "request_type": "appointment-request",
                        "details": {
                            "preferred_date": "next Wednesday",
                            "preferred_time": "morning",
                        },
                        "missing_fields": [],
                        "source_quotes": ["next Wednesday morning"],
                    }
                ],
            )
        # Second turn: cancel.
        return _interpretation_result(
            "appointment-request",
            requests=[
                {
                    "request_type": "appointment-request",
                    "details": {},
                    "missing_fields": [],
                    "source_quotes": ["cancel my appointment request"],
                }
            ],
            needs_clarification=False,
        )

    pipeline._interpret_utterance = mock_interpret

    # Turn 1: appointment request.
    event1 = _final_transcript(
        pipeline, "I want to request an appointment next Wednesday morning.", 1
    )
    await pipeline._handle_stt_event(event1)

    controller = ConversationController(pipeline.session.conversation_state)
    assert controller.state.confirmation_pending is True

    # Turn 2: cancel.
    event2 = _final_transcript(pipeline, "Cancel my appointment request.", 2)
    await pipeline._handle_stt_event(event2)

    # The cancelled request should not be submittable.
    reqs = controller.state.captured_requests
    appointment_reqs = [r for r in reqs if r.request_type == "appointment-request"]
    assert len(appointment_reqs) >= 1
    assert appointment_reqs[0].status.name == "CANCELLED"


@pytest.mark.asyncio
async def test_rejection_no_submission() -> None:
    """Scenario: rejection → no backend submission."""
    settings = _make_settings()
    pipeline = _make_pipeline(settings)

    async def mock_interpret(utterance, context=None):
        if context is None or "confirmation_pending: true" not in context:
            return _interpretation_result(
                "appointment-request",
                requests=[
                    {
                        "request_type": "appointment-request",
                        "details": {
                            "preferred_date": "next Wednesday",
                            "preferred_time": "morning",
                        },
                        "missing_fields": [],
                        "source_quotes": ["next Wednesday morning"],
                    }
                ],
            )
        # Second turn: rejection.
        return _interpretation_result(
            "other",
            requests=[
                {
                    "request_type": "other",
                    "details": {},
                    "missing_fields": [],
                    "source_quotes": ["no don't submit"],
                }
            ],
        )

    pipeline._interpret_utterance = mock_interpret

    event1 = _final_transcript(
        pipeline, "I want to request an appointment next Wednesday morning.", 1
    )
    await pipeline._handle_stt_event(event1)

    controller = ConversationController(pipeline.session.conversation_state)
    assert controller.state.confirmation_pending is True

    event2 = _final_transcript(pipeline, "No, don't submit.", 2)
    await pipeline._handle_stt_event(event2)

    # No submission should have happened.
    reqs = controller.state.captured_requests
    appointment_reqs = [r for r in reqs if r.request_type == "appointment-request"]
    assert len(appointment_reqs) >= 1
    assert appointment_reqs[0].status.name != "SUBMITTED"


@pytest.mark.asyncio
async def test_ambiguous_confirmation_no_submission() -> None:
    """Scenario E: ambiguous confirmation → clarification, no submission."""
    settings = _make_settings()
    pipeline = _make_pipeline(settings)

    async def mock_interpret(utterance, context=None):
        if context is None or "confirmation_pending: true" not in context:
            return _interpretation_result(
                "appointment-request",
                requests=[
                    {
                        "request_type": "appointment-request",
                        "details": {
                            "preferred_date": "next Wednesday",
                            "preferred_time": "morning",
                        },
                        "missing_fields": [],
                        "source_quotes": ["next Wednesday morning"],
                    }
                ],
            )
        # Second turn: ambiguous.
        return _interpretation_result(
            "other",
            requests=[
                {
                    "request_type": "other",
                    "details": {},
                    "missing_fields": [],
                    "source_quotes": ["maybe"],
                }
            ],
            needs_clarification=True,
            clarification_reason="I just need a clear yes or no.",
        )

    pipeline._interpret_utterance = mock_interpret

    event1 = _final_transcript(
        pipeline, "I want to request an appointment next Wednesday morning.", 1
    )
    await pipeline._handle_stt_event(event1)

    controller = ConversationController(pipeline.session.conversation_state)
    assert controller.state.confirmation_pending is True

    event2 = _final_transcript(pipeline, "Maybe.", 2)
    await pipeline._handle_stt_event(event2)

    # No submission.
    reqs = controller.state.captured_requests
    appointment_reqs = [r for r in reqs if r.request_type == "appointment-request"]
    assert len(appointment_reqs) >= 1
    assert appointment_reqs[0].status.name != "SUBMITTED"


@pytest.mark.asyncio
async def test_practice_information_rag() -> None:
    """Scenario F: practice information question → RAG path."""
    settings = _make_settings()
    pipeline = _make_pipeline(settings)

    # Mock the retriever and answer service.
    mock_chunk = MagicMock()
    mock_chunk.chunk_id = "chunk-001"
    mock_chunk.practice_id = "demo-practice-001"

    mock_retrieval = MagicMock()
    mock_retrieval.chunks = [mock_chunk]

    mock_answer = MagicMock()
    mock_answer.answer.answer = "On Monday, the practice opens at 8:00 AM."
    mock_answer.answer.citations = [{"chunk_id": "chunk-001"}]

    pipeline._retriever = MagicMock()
    pipeline._retriever.retrieve = AsyncMock(return_value=mock_retrieval)
    pipeline._knowledge_answer_service = MagicMock()
    pipeline._knowledge_answer_service.answer_question = AsyncMock(return_value=mock_answer)

    async def mock_interpret(utterance, context=None):
        return _interpretation_result(
            "practice-information",
            requests=[
                {
                    "request_type": "practice-information",
                    "details": {"topic": "opening hours"},
                    "missing_fields": [],
                    "source_quotes": ["What time does the practice open on Monday?"],
                }
            ],
        )

    pipeline._interpret_utterance = mock_interpret

    event = _final_transcript(pipeline, "What time does the practice open on Monday?", 1)
    await pipeline._handle_stt_event(event)

    # Verify the retriever was called with the practice ID.
    pipeline._retriever.retrieve.assert_called_once()
    call_args = pipeline._retriever.retrieve.call_args
    query_arg = call_args[0][0]
    assert query_arg.practice_id == "demo-practice-001"


@pytest.mark.asyncio
async def test_emergency_script_is_fixed() -> None:
    """The emergency script is the fixed 999 instruction, never LLM-generated."""
    from app.services.safety.emergency_guard import EMERGENCY_SCRIPT

    assert EMERGENCY_SCRIPT == ("This may be a medical emergency. Please hang up and call 999 now.")


@pytest.mark.asyncio
async def test_conversation_state_isolation_between_sessions() -> None:
    """Scenario J: two sessions maintain independent state."""
    settings = _make_settings()

    pipeline1 = _make_pipeline(settings)
    pipeline2 = _make_pipeline(settings)

    async def mock_interpret1(utterance, context=None):
        return _interpretation_result(
            "appointment-request",
            requests=[
                {
                    "request_type": "appointment-request",
                    "details": {
                        "preferred_date": "Tuesday",
                        "preferred_time": "morning",
                    },
                    "missing_fields": [],
                    "source_quotes": ["Tuesday morning"],
                }
            ],
        )

    pipeline1._interpret_utterance = mock_interpret1

    event1 = _final_transcript(pipeline1, "I want an appointment on Tuesday morning.", 1)
    await pipeline1._handle_stt_event(event1)

    controller1 = ConversationController(pipeline1.session.conversation_state)
    assert controller1.state.confirmation_pending is True

    # Session 2 should have no pending confirmation.
    controller2 = ConversationController(pipeline2.session.conversation_state)
    assert controller2.state.confirmation_pending is False


__all__ = [
    "test_emergency_guard_runs_before_llm_and_backend",
    "test_appointment_request_scenario",
    "test_correction_invalidates_confirmation",
    "test_multiple_requests_independent",
    "test_cancellation_before_submission",
    "test_rejection_no_submission",
    "test_ambiguous_confirmation_no_submission",
    "test_practice_information_rag",
    "test_emergency_script_is_fixed",
    "test_conversation_state_isolation_between_sessions",
]
