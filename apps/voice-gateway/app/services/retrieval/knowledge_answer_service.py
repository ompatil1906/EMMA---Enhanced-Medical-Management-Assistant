"""Grounded answer service (Day 6).

Generates answers to practice-information questions using only retrieved
context. The LLM is an interpreter only — it never executes actions, never
invents facts, and never answers from general knowledge.

All answers are validated against retrieved chunks via citation validation.
Unsupported questions are abstained with a controlled response.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from pydantic import BaseModel

from app.domain.retrieval import RetrievalResult
from app.integrations.llm.base import LLMProvider, LLMRequest, LLMResponse
from app.integrations.llm.exceptions import LLMError
from app.services.retrieval.citation_validator import (
    CitationValidationResult,
    validate_citations,
)


class GroundedAnswerSchema(BaseModel):
    """Pydantic schema for validating grounded answer LLM output."""

    answer: str
    abstained: bool
    abstention_reason: str | None = None
    citations: list[str] = []


@dataclass(frozen=True)
class GroundedAnswer:
    """A grounded answer with citations."""

    answer: str
    abstained: bool
    abstention_reason: str | None
    citations: list[dict]  # list of {chunk_id, document_id, source_title, section, quote}
    generation_latency_ms: float


@dataclass(frozen=True)
class KnowledgeAnswerResult:
    """Result of generating a grounded answer."""

    answer: GroundedAnswer
    retrieval_result: RetrievalResult
    citation_validation: CitationValidationResult


class KnowledgeAnswerService:
    """Generates grounded answers from retrieved context using an LLM provider.

    The service:

    * Receives retrieved chunks and a caller question.
    * Sends only the retrieved context + question to the LLM.
    * Validates all citations against retrieved chunks.
    * Abstains when evidence is missing or conflicting.
    * Never invents facts, opening hours, contact details, or medical advice.
    """

    def __init__(
        self,
        *,
        llm_provider: LLMProvider,
        model: str,
        timeout_seconds: float,
        max_output_tokens: int,
        prompt_version: str,
    ) -> None:
        self._llm_provider = llm_provider
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_output_tokens = max_output_tokens
        self._prompt_version = prompt_version

    async def answer_question(
        self,
        *,
        question: str,
        practice_id: str,
        retrieval_result: RetrievalResult,
        correlation_id: str,
    ) -> KnowledgeAnswerResult:
        """Generate a grounded answer to a practice-information question.

        Only uses retrieved context — never general knowledge. Abstains when
        evidence is missing or conflicting.
        """
        chunks = retrieval_result.chunks

        # Build the context from retrieved chunks
        context_parts: list[str] = []
        for i, chunk in enumerate(chunks):
            citation_marker = f"[{i + 1}]"
            section_info = f" (Section: {chunk.section})" if chunk.section else ""
            page_info = f" (Page: {chunk.page_number})" if chunk.page_number else ""
            context_parts.append(f"{citation_marker} {chunk.content}{section_info}{page_info}")
        context_text = "\n\n".join(context_parts)

        # Generate the answer using the LLM
        t0 = time.monotonic()
        try:
            llm_response = await self._generate_answer(
                question=question,
                context=context_text,
                correlation_id=correlation_id,
            )
        except LLMError as exc:
            # Provider failure → abstain with controlled response
            return KnowledgeAnswerResult(
                answer=GroundedAnswer(
                    answer=(
                        "I'm unable to access the practice information just now. "
                        "Please contact the practice directly."
                    ),
                    abstained=True,
                    abstention_reason=f"provider failure: {exc}",
                    citations=[],
                    generation_latency_ms=(time.monotonic() - t0) * 1000.0,
                ),
                retrieval_result=retrieval_result,
                citation_validation=CitationValidationResult(
                    valid_citations=[],
                    invalid_citations=[],
                    reasons={},
                ),
            )

        generation_latency_ms = (time.monotonic() - t0) * 1000.0

        # Parse the LLM's raw JSON output
        raw_text = llm_response.raw_text or ""
        answer_text: str = ""
        abstained: bool = True
        abstention_reason: str | None = None
        cited_chunk_ids: list[str] = []

        try:
            parsed = json.loads(raw_text)
            answer_text = str(parsed.get("answer", ""))
            abstained = bool(parsed.get("abstained", True))
            abstention_reason = parsed.get("abstention_reason")
            cited_chunk_ids = list(parsed.get("citations", []) or [])
        except (ValueError, TypeError):
            # Malformed JSON → abstain
            return KnowledgeAnswerResult(
                answer=GroundedAnswer(
                    answer=(
                        "I'm unable to answer that question from the practice "
                        "information available to me. Please contact the practice "
                        "directly."
                    ),
                    abstained=True,
                    abstention_reason="LLM output failed validation",
                    citations=[],
                    generation_latency_ms=generation_latency_ms,
                ),
                retrieval_result=retrieval_result,
                citation_validation=CitationValidationResult(
                    valid_citations=[],
                    invalid_citations=[],
                    reasons={},
                ),
            )

        # Validate citations against retrieved chunks
        citation_validation = validate_citations(
            cited_chunk_ids=cited_chunk_ids,
            retrieved_chunks=chunks,
            practice_id=practice_id,
        )

        # Build citation objects for valid citations only
        valid_citation_map = {c.chunk_id: c for c in chunks}
        citation_objects: list[dict] = []
        for chunk_id in citation_validation.valid_citations:
            chunk = valid_citation_map.get(chunk_id)
            if chunk:
                citation_objects.append(
                    {
                        "chunk_id": chunk.chunk_id,
                        "document_id": chunk.document_id,
                        "source_title": chunk.source_title,
                        "section": chunk.section,
                        "page_number": chunk.page_number,
                        "quote": chunk.content[:200],  # truncate long quotes
                    }
                )

        # If citations are invalid or answer lacks citations, abstain
        if citation_validation.invalid_citations:
            return KnowledgeAnswerResult(
                answer=GroundedAnswer(
                    answer=(
                        "I'm unable to verify that information from the practice "
                        "documents available to me. Please contact the practice "
                        "directly."
                    ),
                    abstained=True,
                    abstention_reason="invalid or unsupported citations",
                    citations=[],
                    generation_latency_ms=generation_latency_ms,
                ),
                retrieval_result=retrieval_result,
                citation_validation=citation_validation,
            )

        # Build final answer text with citations
        final_answer = self._format_answer_with_citations(answer_text, citation_objects)

        return KnowledgeAnswerResult(
            answer=GroundedAnswer(
                answer=final_answer,
                abstained=abstained,
                abstention_reason=abstention_reason,
                citations=citation_objects,
                generation_latency_ms=generation_latency_ms,
            ),
            retrieval_result=retrieval_result,
            citation_validation=citation_validation,
        )

    # --- internal helpers ---------------------------------------------------

    async def _generate_answer(
        self,
        *,
        question: str,
        context: str,
        correlation_id: str,
    ) -> LLMResponse:
        """Call the LLM provider to generate a grounded answer."""
        prompt = self._build_prompt(question=question, context=context)

        request = LLMRequest(
            transcript=prompt,
            session_id=correlation_id,
            prompt_version=self._prompt_version,
            model=self._model,
            timeout_seconds=self._timeout_seconds,
            max_output_tokens=self._max_output_tokens,
            correlation_id=correlation_id,
            response_schema=GroundedAnswerSchema,
        )
        return await self._llm_provider.interpret(request)

    def _build_prompt(self, *, question: str, context: str) -> str:
        """Build the grounded-answer prompt."""
        return (
            "You are an administrative assistant for an NHS GP practice receptionist. "
            "Answer the caller's question using ONLY the retrieved practice "
            "documents below. "
            "Rules: "
            "1) Answer only using information in the retrieved context — never "
            "invent opening hours, contact details, or medical facts. "
            "2) Never claim an appointment is booked or a prescription is "
            "processed. "
            "3) Never diagnose, triage, or recommend treatment. "
            "4) If the answer is not supported by the context, say so explicitly "
            "and abstain — do not guess. "
            "5) Return a JSON object with fields: answer (string), "
            "abstained (boolean), abstention_reason (string or null), "
            "citations (array of chunk IDs referenced). "
            "6) Include citation markers like [1], [2] in your answer text "
            "that correspond to the retrieved chunks. "
            "\n\n"
            f"Retrieved practice documents:\n{context}\n\n"
            f"Caller question: {question}\n"
        )

    def _format_answer_with_citations(self, answer_text: str, citations: list[dict]) -> str:
        """Format the answer text with citation references."""
        if not citations:
            return answer_text

        citation_refs: list[str] = []
        for i, cite in enumerate(citations, 1):
            source = cite.get("source_title", "Unknown source")
            section = cite.get("section")
            section_str = f", Section: {section}" if section else ""
            citation_refs.append(f"[{i}] {source}{section_str}")

        return f"{answer_text}\n\nSources:\n" + "\n".join(citation_refs)


__all__ = ["KnowledgeAnswerService", "GroundedAnswer", "KnowledgeAnswerResult"]
