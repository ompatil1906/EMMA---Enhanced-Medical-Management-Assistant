"""Structured administrative-intent interpretation schema (Day 5).

The LLM is an interpreter, not an action executor. Every model response is
validated against this Pydantic schema before the conversation controller sees
it. Malformed or unsupported output is rejected — never silently coerced.

Design rules:

* Extraction only — never invent missing values, never infer dates/times, never
  invent appointment slots, never diagnose or triage.
* Multiple independent requests are preserved; caller corrections update the
  latest captured field.
* Source quotes are retained per field and validated against the transcript.
* Model confidence is not treated as evidence of correctness.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator


class RequestDetail(BaseModel):
    """One extracted administrative request detail block."""

    request_type: str = Field(
        ...,
        description=(
            "Administrative category: appointment-request, "
            "appointment-change-request, prescription-request, "
            "practice-information, practice-administration, other, unclear"
        ),
    )
    details: dict[str, str | None] = Field(
        default_factory=dict,
        description=(
            "Extracted fields (e.g. preferred_date, preferred_time, "
            "reason_as_stated). Missing values are null, never invented."
        ),
    )
    missing_fields: list[str] = Field(
        default_factory=list,
        description=(
            "Fields the caller did not provide and that are required for this request type."
        ),
    )
    source_quotes: list[str] = Field(
        default_factory=list,
        description=(
            "Caller-provided verbatim snippets supporting each extracted "
            "field. Every quote must occur in the transcript."
        ),
    )


class InterpretationResult(BaseModel):
    """One LLM interpretation of a final caller utterance.

    This is the contract between the LLM provider and the conversation
    controller. The controller decides what FSM transition or backend action
    follows — the model never does.
    """

    intent: str = Field(
        ...,
        description=(
            "Overall administrative intent: appointment-request, "
            "appointment-change-request, prescription-request, "
            "practice-information, practice-administration, other, unclear"
        ),
    )
    requests: list[RequestDetail] = Field(
        default_factory=list,
        description=(
            "One entry per distinct administrative request in the utterance. "
            "Unrelated requests are never merged."
        ),
    )
    needs_clarification: bool = Field(
        default=False,
        description=(
            "True when the utterance is ambiguous or missing required fields "
            "and the caller should be asked a controlled clarification."
        ),
    )
    clarification_reason: str | None = Field(
        default=None,
        description="Human-readable reason why clarification is needed.",
    )

    @field_validator("intent")
    @classmethod
    def _validate_intent(cls, value: str) -> str:
        allowed = {
            "appointment-request",
            "appointment-change-request",
            "prescription-request",
            "practice-information",
            "practice-administration",
            "other",
            "unclear",
        }
        if value not in allowed:
            raise ValueError(f"unsupported intent: {value!r}")
        return value

    @model_validator(mode="after")
    def _validate_source_quotes(self) -> InterpretationResult:
        """Quotes must be non-empty strings; nothing is validated against the
        transcript here (that happens in the interpreter layer with the
        transcript available)."""
        for request in self.requests:
            for quote in request.source_quotes:
                if not isinstance(quote, str) or not quote.strip():
                    raise ValueError("source_quotes must be non-empty strings")
        return self


__all__ = ["InterpretationResult", "RequestDetail"]
