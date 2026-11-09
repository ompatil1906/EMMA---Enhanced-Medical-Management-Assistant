"""Live Day 9 smoke tests (opt-in; never part of ordinary CI).

These tests call the real Gemini LLM/embedding APIs and the local persistent
Qdrant store. They are skipped unless `EMMA_RUN_LIVE_TESTS=1`, so a clean
checkout with no credentials still runs the full offline suite.

Credentials are read from settings only. No key is printed, logged, or written
to a fixture. The live practice id is fictional (`demo-practice-001`).

Run:

    EMMA_RUN_LIVE_TESTS=1 uv run pytest tests/live/test_day9_live_smoke.py -q -s
"""

from __future__ import annotations

import os
import statistics
import time

import pytest

from app.core.config import Settings
from app.domain.audio import AudioFormat
from app.domain.conversation import RequestStatus
from app.domain.retrieval import RetrievalQuery
from app.domain.speech import FinalTranscript
from app.domain.speech_session import SpeechSession
from app.integrations.embeddings.gemini_provider import GeminiEmbeddingProvider
from app.integrations.llm.base import LLMRequest
from app.integrations.llm.gemini_provider import GeminiLLMProvider
from app.integrations.vector_store.qdrant_store import QdrantVectorStoreProvider
from app.services.retrieval import Retriever
from app.services.voice_pipeline import VoicePipeline
from app.speech.mock_stt import MockSpeechToTextProvider
from app.speech.mock_tts import MockTextToSpeechProvider

pytestmark = pytest.mark.live

LIVE_PRACTICE_ID = os.getenv("EMMA_LIVE_PRACTICE_ID", "demo-practice-001")
ISOLATION_PRACTICE_ID = "practice-live-isolation-check"


@pytest.fixture()
def live_settings() -> Settings:
    if os.getenv("EMMA_RUN_LIVE_TESTS") != "1":
        pytest.skip("live tests disabled (set EMMA_RUN_LIVE_TESTS=1 to run)")
    settings = Settings()
    if settings.LLM_PROVIDER != "gemini" or not settings.LLM_API_KEY:
        pytest.skip("Gemini LLM is not configured")
    if settings.EMBEDDING_PROVIDER != "gemini" or not settings.GEMINI_EMBEDDING_API_KEY:
        pytest.skip("Gemini embeddings are not configured")
    if settings.VECTOR_STORE_PROVIDER != "qdrant":
        pytest.skip("Qdrant is not configured")
    return settings


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def _print_latency(label: str, values: list[float]) -> None:
    if not values:
        return
    print(
        f"\n{label}: n={len(values)} "
        f"p50={statistics.median(values):.0f}ms "
        f"p95={_percentile(values, 0.95):.0f}ms "
        f"min={min(values):.0f}ms max={max(values):.0f}ms"
    )


@pytest.mark.asyncio
async def test_live_retrieval_practice_isolation_and_latency(live_settings: Settings) -> None:
    embedding = GeminiEmbeddingProvider(
        api_key=live_settings.GEMINI_EMBEDDING_API_KEY or "",
        model=live_settings.GEMINI_EMBEDDING_MODEL,
        dimension=int(live_settings.GEMINI_EMBEDDING_DIMENSION),
        timeout_seconds=live_settings.GEMINI_EMBEDDING_TIMEOUT_SECONDS,
        max_retries=live_settings.GEMINI_EMBEDDING_MAX_RETRIES,
    )
    store = QdrantVectorStoreProvider(
        collection=live_settings.QDRANT_COLLECTION,
        dimension=int(live_settings.GEMINI_EMBEDDING_DIMENSION),
        local_path=live_settings.QDRANT_LOCAL_PATH,
    )
    retriever = Retriever(
        embedding_provider=embedding,
        vector_store=store,
        default_top_k=live_settings.RETRIEVAL_TOP_K,
        default_min_score=live_settings.RETRIEVAL_MIN_SCORE,
    )
    queries = [
        "What time does the practice open on Monday?",
        "How can I get in touch with the practice?",
        "How do I request a repeat prescription?",
        "What are the opening hours?",
    ]
    latencies: list[float] = []
    try:
        for query_text in queries:
            result = await retriever.retrieve(
                RetrievalQuery(
                    query_text=query_text,
                    practice_id=LIVE_PRACTICE_ID,
                    top_k=live_settings.RETRIEVAL_TOP_K,
                    min_score=live_settings.RETRIEVAL_MIN_SCORE,
                )
            )
            assert result.chunks, f"no chunks returned for {query_text!r}"
            assert all(chunk.practice_id == LIVE_PRACTICE_ID for chunk in result.chunks)
            latencies.append(result.retrieval_latency_ms)

        isolated = await retriever.retrieve(
            RetrievalQuery(
                query_text=queries[0],
                practice_id=ISOLATION_PRACTICE_ID,
                top_k=live_settings.RETRIEVAL_TOP_K,
                min_score=live_settings.RETRIEVAL_MIN_SCORE,
            )
        )
        assert isolated.chunks == [], "cross-practice leakage detected"
        _print_latency("live retrieval (embedding + Qdrant)", latencies)
    finally:
        await store.aclose()
        await embedding.aclose()


@pytest.mark.asyncio
async def test_live_gemini_interpretation_full_utterance(live_settings: Settings) -> None:
    """A complete administrative utterance is parsed into one request.

    This deliberately uses a full utterance rather than a bare slot value:
    live models are reliable on complete sentences, while bare slot answers
    are covered by the deterministic controller backstop (offline tests). The
    test asserts only what the model is responsible for — extracting an
    appointment request with a date and a time.
    """
    provider = GeminiLLMProvider(
        api_key=live_settings.LLM_API_KEY or "",
        model=live_settings.LLM_MODEL,
        timeout_seconds=live_settings.LLM_TIMEOUT_SECONDS,
        max_output_tokens=live_settings.LLM_MAX_OUTPUT_TOKENS,
    )
    request = LLMRequest(
        transcript="I'd like to book an appointment for Tuesday morning please.",
        session_id="live-smoke",
        prompt_version=live_settings.LLM_PROMPT_VERSION,
        model=live_settings.LLM_MODEL,
        timeout_seconds=live_settings.LLM_TIMEOUT_SECONDS,
        max_output_tokens=live_settings.LLM_MAX_OUTPUT_TOKENS,
        correlation_id="live-smoke",
        context=("pending_confirmation: false\nactive_requests: none\nlast_grounded_topic: none"),
    )
    started = time.monotonic()
    try:
        response = await provider.interpret(request)
    finally:
        await provider.aclose()
    elapsed_ms = (time.monotonic() - started) * 1000.0

    assert response.parsed is not None, "Gemini returned no validated interpretation"
    parsed = response.parsed
    assert parsed.requests, f"no requests extracted: {parsed!r}"
    appointment = next(
        (r for r in parsed.requests if r.request_type == "appointment-request"),
        None,
    )
    assert appointment is not None, f"no appointment request: {parsed!r}"
    details = appointment.details
    assert details.get("preferred_date"), f"missing date: {details!r}"
    assert "tue" in str(details["preferred_date"]).lower()
    assert details.get("preferred_time"), f"missing time: {details!r}"
    assert "morning" in str(details["preferred_time"]).lower()
    print(f"\nlive Gemini interpretation: {elapsed_ms:.0f}ms")


@pytest.mark.asyncio
async def test_live_pipeline_reaches_confirmation_without_backend(
    live_settings: Settings,
) -> None:
    """One live turn fills slots and reaches confirmation; nothing is submitted.

    The practice client is `None`, so any attempt to submit would be visible as
    a different spoken response. The assertion is that the real Gemini model,
    through the real pipeline and deterministic controller, stops at
    READY_FOR_CONFIRMATION and asks the caller to confirm.
    """
    from app.integrations.llm.provider_factory import LLMProviderFactory

    session = SpeechSession(
        session_id="live-pipeline-smoke",
        call_sid="live-pipeline-call",
        practice_id=LIVE_PRACTICE_ID,
        audio_format=AudioFormat(sample_rate=8000, channels=1),
    )
    pipeline = VoicePipeline(
        session=session,
        stt=MockSpeechToTextProvider(script=["unused"], finalize_ms=10, utterance_ms=1000),
        tts=MockTextToSpeechProvider(),
        practice_client=None,
        practice_id=LIVE_PRACTICE_ID,
        settings=live_settings,
        llm_factory=LLMProviderFactory(live_settings),
    )
    await pipeline.start()
    try:
        event = FinalTranscript(
            session_id=session.session_id,
            provider="live-smoke-stt",
            sequence=1,
            text="I'd like to book an appointment for Tuesday morning please.",
            confidence=0.98,
        )
        await pipeline._handle_stt_event(event)

        state = session.conversation_state
        requests = [r for r in state.captured_requests if r.is_active]
        assert requests, f"no request captured; assistant said: {state.turns[-1].content!r}"
        appointment = requests[0]
        assert appointment.request_type == "appointment-request"
        assert appointment.status is RequestStatus.READY_FOR_CONFIRMATION, (
            f"status {appointment.status!r}; missing={appointment.missing_fields!r}"
        )
        assert state.confirmation_pending is True
        assert state.confirmed_submissions == []
        assistant_text = state.turns[-1].content.lower()
        assert "confirm" in assistant_text
        print(f"\nlive pipeline assistant: {state.turns[-1].content}")
    finally:
        await pipeline.aclose(reason="live_smoke_complete")


@pytest.mark.asyncio
async def test_live_grounded_answer(live_settings: Settings) -> None:
    from app.services.retrieval import KnowledgeAnswerService

    embedding = GeminiEmbeddingProvider(
        api_key=live_settings.GEMINI_EMBEDDING_API_KEY or "",
        model=live_settings.GEMINI_EMBEDDING_MODEL,
        dimension=int(live_settings.GEMINI_EMBEDDING_DIMENSION),
        timeout_seconds=live_settings.GEMINI_EMBEDDING_TIMEOUT_SECONDS,
        max_retries=live_settings.GEMINI_EMBEDDING_MAX_RETRIES,
    )
    store = QdrantVectorStoreProvider(
        collection=live_settings.QDRANT_COLLECTION,
        dimension=int(live_settings.GEMINI_EMBEDDING_DIMENSION),
        local_path=live_settings.QDRANT_LOCAL_PATH,
    )
    retriever = Retriever(
        embedding_provider=embedding,
        vector_store=store,
        default_top_k=live_settings.RETRIEVAL_TOP_K,
        default_min_score=live_settings.RETRIEVAL_MIN_SCORE,
    )
    llm = GeminiLLMProvider(
        api_key=live_settings.LLM_API_KEY or "",
        model=live_settings.LLM_MODEL,
        timeout_seconds=live_settings.LLM_TIMEOUT_SECONDS,
        max_output_tokens=live_settings.LLM_MAX_OUTPUT_TOKENS,
    )
    answer_service = KnowledgeAnswerService(
        llm_provider=llm,
        model=live_settings.LLM_MODEL,
        timeout_seconds=live_settings.LLM_TIMEOUT_SECONDS,
        max_output_tokens=live_settings.LLM_MAX_OUTPUT_TOKENS,
        prompt_version=live_settings.LLM_PROMPT_VERSION,
    )
    try:
        question = "What time does the practice open on Monday?"
        retrieval = await retriever.retrieve(
            RetrievalQuery(
                query_text=question,
                practice_id=LIVE_PRACTICE_ID,
                top_k=live_settings.RETRIEVAL_TOP_K,
                min_score=live_settings.RETRIEVAL_MIN_SCORE,
            )
        )
        result = await answer_service.answer_question(
            question=question,
            practice_id=LIVE_PRACTICE_ID,
            retrieval_result=retrieval,
            correlation_id="live-smoke",
        )
        assert result.answer.answer.strip()
        assert not result.answer.abstained
        assert result.answer.citations
        assert all(citation["chunk_id"] for citation in result.answer.citations)
        print(f"\nlive grounded answer: {result.answer.answer[:160]}")
    finally:
        await llm.aclose()
        await store.aclose()
        await embedding.aclose()
