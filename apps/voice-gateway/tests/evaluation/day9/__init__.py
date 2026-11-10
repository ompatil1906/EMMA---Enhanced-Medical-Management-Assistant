"""Day 9 multi-turn conversation evaluation harness (synthetic only).

The harness drives the *real* `VoicePipeline` and the deterministic
`ConversationController` with a scripted LLM, a fake practice backend, and a
fake retriever. No network calls, no credentials, no real patient data.

Why a scripted LLM instead of the mock heuristic provider?

* The controller — not the model — owns conversation state, confirmation,
  submission, and cancellation. Scripting each interpretation isolates those
  deterministic decisions from any provider's keyword heuristics.
* It makes the "model says X" scenarios explicit and auditable.

The context-aware `MockLLMProvider` is exercised separately in
`tests/unit/test_day9_dialogue.py` so its slot-filling behaviour is also
covered without pretending it is a live model.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings
from app.domain.audio import AudioFormat
from app.domain.conversation import RequestStatus
from app.domain.llm import InterpretationResult, RequestDetail
from app.domain.retrieval import RetrievalQuery, RetrievalResult, RetrievedChunk
from app.domain.speech import FinalTranscript
from app.domain.speech_session import SpeechSession
from app.integrations.llm.base import LLMRequest, LLMResponse
from app.integrations.practice_api import SubmissionOutcome
from app.services.caller_identity import CallerIdentity
from app.services.retrieval.citation_validator import CitationValidationResult
from app.services.retrieval.knowledge_answer_service import (
    GroundedAnswer,
    KnowledgeAnswerResult,
)
from app.services.voice_pipeline import VoicePipeline
from app.speech.mock_stt import MockSpeechToTextProvider
from app.speech.mock_tts import MockTextToSpeechProvider

EVAL_PRACTICE_ID = "practice-eval-001"
OTHER_PRACTICE_ID = "practice-eval-002"

DEFAULT_ANSWER = "The practice is open Monday to Friday, 8am to 6pm."

ScriptedValue = InterpretationResult | Exception


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class ScriptedLLM:
    """Return pre-scripted interpretations keyed by the caller utterance.

    A list value is consumed in order (for repeated utterances such as "yes").
    An unexpected utterance raises, and the harness also asserts the exact
    number of calls so a skipped/extra LLM call fails the case.
    """

    provider_name = "scripted-llm"

    def __init__(self, responses: dict[str, ScriptedValue | list[ScriptedValue]]) -> None:
        self._responses: dict[str, ScriptedValue | list[ScriptedValue]] = {}
        for transcript, value in responses.items():
            self._responses[transcript] = list(value) if isinstance(value, list) else value
        self.requests: list[LLMRequest] = []

    async def interpret(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if request.transcript not in self._responses:
            raise AssertionError(f"unexpected LLM call for {request.transcript!r}")
        value = self._responses[request.transcript]
        if isinstance(value, list):
            if not value:
                raise AssertionError(f"no scripted response left for {request.transcript!r}")
            value = value.pop(0) if len(value) > 1 else value[0]
        if isinstance(value, Exception):
            raise value
        return LLMResponse(
            raw_text=value.model_dump_json(),
            parsed=value,
            first_token_ms=0.0,
            total_ms=0.0,
            correlation_id=request.correlation_id,
        )


class SingleProviderFactory:
    """Factory returning one shared provider instance (one per harness)."""

    def __init__(self, provider: ScriptedLLM) -> None:
        self._provider = provider

    def create(self) -> ScriptedLLM:
        return self._provider


class FakePracticeClient:
    """Record submissions and return scripted backend outcomes.

    Day 19 additions record the call registration and end-of-call summary so the
    summary tests can assert exactly what the pipeline submits (and what it
    deliberately does not).
    """

    def __init__(self, outcomes: tuple[str, ...] = ()) -> None:
        self.calls: list[dict[str, Any]] = []
        self.call_records: list[dict[str, Any]] = []
        self.summaries: list[dict[str, Any]] = []
        self._outcomes = list(outcomes)
        self.summary_delay_seconds = 0.0

    async def submit_call(self, **kwargs: Any) -> SubmissionOutcome:
        self.call_records.append(dict(kwargs))
        return SubmissionOutcome(
            status="confirmed", detail="confirmed", record_id="call-eval-record"
        )

    async def submit_call_summary(self, **kwargs: Any) -> SubmissionOutcome:
        if self.summary_delay_seconds:
            await asyncio.sleep(self.summary_delay_seconds)
        self.summaries.append(dict(kwargs))
        return SubmissionOutcome(
            status="confirmed", detail="confirmed", record_id="summary-eval-record"
        )

    async def submit_request(self, **kwargs: Any) -> SubmissionOutcome:
        self.calls.append(dict(kwargs))
        status = self._outcomes.pop(0) if self._outcomes else "confirmed"
        return SubmissionOutcome(
            status=status,
            detail=status,
            record_id="record-eval" if status == "confirmed" else None,
        )

    async def submit_handoff(self, **kwargs: Any) -> SubmissionOutcome:
        self.calls.append(dict(kwargs))
        return SubmissionOutcome(status="confirmed", detail="confirmed", record_id="handoff-eval")


class FakeRetriever:
    """Record retrieval queries and return configured chunks."""

    def __init__(
        self,
        results: tuple[tuple[RetrievedChunk, ...], ...] = (),
        *,
        default_chunks: tuple[RetrievedChunk, ...] = (),
    ) -> None:
        self.queries: list[RetrievalQuery] = []
        self._results = list(results)
        self._default = default_chunks

    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        self.queries.append(query)
        chunks = list(self._results.pop(0)) if self._results else list(self._default)
        return RetrievalResult(
            query_text=query.query_text,
            practice_id=query.practice_id,
            chunks=chunks,
            total_retrieved=len(chunks),
            retrieval_latency_ms=1.0,
        )


class FakeAnswerService:
    """Return a fixed grounded answer with citations from the retrieved chunks."""

    def __init__(self, answer: str = DEFAULT_ANSWER) -> None:
        self.questions: list[str] = []
        self._answer = answer

    async def answer_question(
        self,
        *,
        question: str,
        practice_id: str,
        retrieval_result: RetrievalResult,
        correlation_id: str,
    ) -> KnowledgeAnswerResult:
        self.questions.append(question)
        citations = [
            {
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "source_title": chunk.source_title,
                "section": chunk.section,
                "quote": chunk.content[:80],
            }
            for chunk in retrieval_result.chunks[:1]
        ]
        grounded = GroundedAnswer(
            answer=self._answer,
            abstained=False,
            abstention_reason=None,
            citations=citations,
            generation_latency_ms=1.0,
        )
        validation = CitationValidationResult(
            valid_citations=[citation["chunk_id"] for citation in citations],
            invalid_citations=[],
            reasons={},
        )
        return KnowledgeAnswerResult(
            answer=grounded,
            retrieval_result=retrieval_result,
            citation_validation=validation,
        )


# ---------------------------------------------------------------------------
# Harness construction and driving
# ---------------------------------------------------------------------------


def eval_settings(**overrides: object) -> Settings:
    """Deterministic settings for the evaluation harness.

    The repository `.env` may select Azure or Gemini; every provider is pinned
    to a test double here so the corpus never touches the network.
    """
    values: dict[str, object] = {
        "VOICE_PIPELINE": "speech",
        "STT_PROVIDER": "mock",
        "TTS_PROVIDER": "mock",
        "LLM_PROVIDER": "mock",
        "EMBEDDING_PROVIDER": "mock",
        "VECTOR_STORE_PROVIDER": "mock",
        "PRACTICE_ID": EVAL_PRACTICE_ID,
        "PRACTICE_API_BASE_URL": "",
        "SPEECH_CONNECTION_TIMEOUT": 2.0,
        "SPEECH_REQUEST_TIMEOUT": 2.0,
        "PRACTICE_API_TIMEOUT": 1.0,
        "RETRIEVAL_TOP_K": 5,
        "RETRIEVAL_MIN_SCORE": 0.0,
        # Tests must never touch a live task platform from the developer's .env.
        "JIRA_MODE": "disabled",
        "CALL_RECORD_SINK": "practice_api",
    }
    values.update(overrides)
    return Settings(**values)


@dataclass
class Harness:
    """Everything a test needs to inspect after driving a conversation."""

    pipeline: VoicePipeline
    session: SpeechSession
    llm: ScriptedLLM
    practice: FakePracticeClient
    retriever: FakeRetriever
    answer_service: FakeAnswerService
    settings: Settings
    next_sequence: int = 1
    drained_chunks: int = field(default=0)

    @property
    def state(self):
        return self.session.conversation_state


async def make_pipeline(
    *,
    responses: dict[str, ScriptedValue | list[ScriptedValue]],
    practice_id: str = EVAL_PRACTICE_ID,
    practice_outcomes: tuple[str, ...] = (),
    retrieval_chunks: tuple[RetrievedChunk, ...] = (),
    retrieval_results: tuple[tuple[RetrievedChunk, ...], ...] = (),
    answer_text: str = DEFAULT_ANSWER,
    settings_overrides: dict[str, object] | None = None,
    session_id: str | None = None,
    caller: CallerIdentity | None = None,
) -> Harness:
    """Build and start one pipeline with deterministic test doubles."""
    settings = eval_settings(**(settings_overrides or {}))
    session = SpeechSession(
        session_id=session_id or f"eval-{uuid.uuid4().hex[:10]}",
        call_sid=f"call-{uuid.uuid4().hex[:10]}",
        practice_id=practice_id,
        audio_format=AudioFormat(sample_rate=8000, channels=1),
        caller=caller,
    )
    stt = MockSpeechToTextProvider(script=["unused"], finalize_ms=10, utterance_ms=1000)
    tts = MockTextToSpeechProvider()
    llm = ScriptedLLM(responses)
    practice = FakePracticeClient(practice_outcomes)
    retriever = FakeRetriever(retrieval_results, default_chunks=retrieval_chunks)
    answer_service = FakeAnswerService(answer_text)
    pipeline = VoicePipeline(
        session=session,
        stt=stt,
        tts=tts,
        practice_client=practice,
        practice_id=practice_id,
        settings=settings,
        llm_factory=SingleProviderFactory(llm),
        retriever=retriever,
        knowledge_answer_service=answer_service,
        caller=caller,
    )
    await pipeline.start()
    harness = Harness(
        pipeline=pipeline,
        session=session,
        llm=llm,
        practice=practice,
        retriever=retriever,
        answer_service=answer_service,
        settings=settings,
    )
    drain_outbound(harness)
    return harness


def drain_outbound(harness: Harness) -> int:
    """Discard queued outbound audio so tests never accumulate frames."""
    count = 0
    while True:
        try:
            harness.pipeline._outbound_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        count += 1
    harness.drained_chunks += count
    return count


async def run_turn(
    harness: Harness,
    text: str,
    *,
    sequence: int | None = None,
    confidence: float | None = 0.99,
) -> None:
    """Deliver one final transcript directly to the pipeline's turn handler."""
    if sequence is None:
        sequence = harness.next_sequence
    harness.next_sequence = max(harness.next_sequence, sequence + 1)
    event = FinalTranscript(
        session_id=harness.session.session_id,
        provider="eval-stt",
        sequence=sequence,
        text=text,
        confidence=confidence,
    )
    await harness.pipeline._handle_stt_event(event)
    # Day 20: finals are admitted to the conversation worker instead of being
    # processed inline, so tests must wait for the worker to drain before
    # asserting state (this is also the production shutdown contract).
    await harness.pipeline.wait_for_turn_idle()
    drain_outbound(harness)


async def close(harness: Harness) -> None:
    await harness.pipeline.aclose(reason="evaluation_complete")


def assistant_messages(harness: Harness) -> list[str]:
    return [turn.content for turn in harness.state.turns if turn.speaker.value == "assistant"]


def last_assistant_message(harness: Harness) -> str:
    messages = assistant_messages(harness)
    return messages[-1] if messages else ""


def request_of(harness: Harness, request_type: str):
    for request in harness.state.captured_requests:
        if request.request_type == request_type:
            return request
    return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_chunk(
    *,
    chunk_id: str = "chunk-opening-hours-1",
    document_id: str = "doc-opening-hours",
    practice_id: str = EVAL_PRACTICE_ID,
    content: str = "The practice is open Monday to Friday, 8am to 6pm.",
    source_title: str = "Opening Hours",
    section: str | None = "Opening hours",
    score: float = 0.91,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id=document_id,
        practice_id=practice_id,
        source_title=source_title,
        source_filename="opening-hours.txt",
        section=section,
        page_number=1,
        chunk_index=0,
        content=content,
        score=score,
        metadata={"status": "active"},
    )


# ---------------------------------------------------------------------------
# Interpretation builders
# ---------------------------------------------------------------------------


def appointment(
    *,
    date: str | None = "tuesday",
    time: str | None = "morning",
    reason: str | None = None,
    extra: dict[str, str | None] | None = None,
) -> InterpretationResult:
    details: dict[str, str | None] = {}
    if date is not None:
        details["preferred_date"] = date
    if time is not None:
        details["preferred_time"] = time
    if reason is not None:
        details["reason_as_stated"] = reason
    if extra:
        details.update(extra)
    return InterpretationResult(
        intent="appointment-request",
        requests=[
            RequestDetail(
                request_type="appointment-request",
                details=details,
                missing_fields=[],
                source_quotes=["synthetic appointment utterance"],
            )
        ],
    )


def appointment_change(
    *,
    date: str | None = "thursday",
    time: str | None = "afternoon",
) -> InterpretationResult:
    details: dict[str, str | None] = {}
    if date is not None:
        details["preferred_date"] = date
    if time is not None:
        details["preferred_time"] = time
    return InterpretationResult(
        intent="appointment-change-request",
        requests=[
            RequestDetail(
                request_type="appointment-change-request",
                details=details,
                missing_fields=[],
                source_quotes=["synthetic change utterance"],
            )
        ],
    )


def prescription() -> InterpretationResult:
    return InterpretationResult(
        intent="prescription-request",
        requests=[
            RequestDetail(
                request_type="prescription-request",
                details={"medication_query": "repeat prescription"},
                missing_fields=[],
                source_quotes=["synthetic prescription utterance"],
            )
        ],
    )


def practice_information(
    topic: str = "opening hours",
    *,
    extra: dict[str, str | None] | None = None,
) -> InterpretationResult:
    details: dict[str, str | None] = {"topic": topic}
    if extra:
        details.update(extra)
    return InterpretationResult(
        intent="practice-information",
        requests=[
            RequestDetail(
                request_type="practice-information",
                details=details,
                missing_fields=[],
                source_quotes=["synthetic information question"],
            )
        ],
    )


def multiple(*results: InterpretationResult) -> InterpretationResult:
    """Combine independent requests into one interpretation (never merged)."""
    requests = [request for result in results for request in result.requests]
    intent = requests[0].request_type if requests else "unclear"
    return InterpretationResult(intent=intent, requests=requests)


def unclear(reason: str = "I'm not sure I understood.") -> InterpretationResult:
    return InterpretationResult(
        intent="unclear",
        needs_clarification=True,
        clarification_reason=reason,
    )


# ---------------------------------------------------------------------------
# Case model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TurnSpec:
    """One synthetic caller turn."""

    utterance: str
    interpretation: InterpretationResult | None = None
    sequence: int | None = None
    confidence: float | None = 0.99


@dataclass(frozen=True)
class EvalCase:
    """One end-to-end multi-turn scenario and its expected outcomes."""

    name: str
    turns: tuple[TurnSpec, ...]
    expected_statuses: tuple[tuple[str, RequestStatus], ...] = ()
    expected_backend_calls: int = 0
    expected_llm_calls: int | None = None
    expected_retrieval_calls: int = 0
    expected_awaiting_field: str | None = None
    expected_confirmation_pending: bool | None = None
    expected_last_assistant_contains: tuple[str, ...] = ()
    forbidden_assistant_contains: tuple[str, ...] = ()
    expected_submitted_types: tuple[str, ...] = ()
    expected_failed_types: tuple[str, ...] = ()
    expected_cancelled_types: tuple[str, ...] = ()
    expected_last_grounded_topic: str | None = None
    expected_backend_payload_subset: tuple[tuple[str, object], ...] = ()
    forbidden_backend_payload_keys: tuple[str, ...] = ()
    practice_outcomes: tuple[str, ...] = ()
    retrieval_chunks: tuple[RetrievedChunk, ...] = ()
    retrieval_results: tuple[tuple[RetrievedChunk, ...], ...] = ()
    answer_text: str = DEFAULT_ANSWER
    practice_id: str = EVAL_PRACTICE_ID

    def responses(self) -> dict[str, ScriptedValue | list[ScriptedValue]]:
        """Build the scripted LLM response map, preserving repeated turns."""
        built: dict[str, ScriptedValue | list[ScriptedValue]] = {}
        for turn in self.turns:
            if turn.interpretation is None:
                continue
            existing = built.get(turn.utterance)
            if existing is None:
                built[turn.utterance] = turn.interpretation
            elif isinstance(existing, list):
                existing.append(turn.interpretation)
            else:
                built[turn.utterance] = [existing, turn.interpretation]
        return built


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_case(case: EvalCase) -> Harness:
    """Run one scenario end to end and return the inspectable harness.

    Assertions live in the caller so failures show the case name and the
    observed state rather than an opaque helper traceback.
    """
    harness = await make_pipeline(
        responses=case.responses(),
        practice_id=case.practice_id,
        practice_outcomes=case.practice_outcomes,
        retrieval_chunks=case.retrieval_chunks,
        retrieval_results=case.retrieval_results,
        answer_text=case.answer_text,
    )
    try:
        for turn in case.turns:
            await run_turn(
                harness,
                turn.utterance,
                sequence=turn.sequence,
                confidence=turn.confidence,
            )
    finally:
        await close(harness)
    return harness
