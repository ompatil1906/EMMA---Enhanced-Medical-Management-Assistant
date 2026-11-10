"""EMMA's built-in tools (Day 20).

Every capability the assistant may invoke is registered here as a typed tool.
The handlers wrap components EMMA already has — they do not re-implement RAG,
backend submission or escalation, so the existing safety behaviour (practice
scoping, confirmation, idempotency) is unchanged.

Permissions:

* `lookup_practice_information` — READ, may run any time.
* `create_appointment_request`  — WRITE, requires explicit confirmation.
* `request_human_handoff`       — ESCALATE, audited separately.

No handler receives a practice id from the model: `ToolContext` carries the
server-authoritative ids.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.retrieval import RetrievalQuery
from app.services.conversation.conversation_controller import ADMIN_REQUEST_TYPES
from app.services.tools.base import ToolContext, ToolPermission, ToolSpec
from app.services.tools.registry import ToolRegistry


class LookupKnowledgeArgs(BaseModel):
    """Arguments for a practice-information lookup."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        min_length=2,
        max_length=300,
        description="The caller's practice-information question, verbatim.",
    )
    top_k: int = Field(default=5, ge=1, le=10, description="Maximum passages to consider.")


class CreateRequestArgs(BaseModel):
    """Arguments for creating one practice request (appointment, prescription…)."""

    model_config = ConfigDict(extra="forbid")

    request_type: str = Field(
        description="One of: appointment-request, appointment-change-request, "
        "prescription-request, practice-administration.",
    )
    preferred_date: str | None = Field(
        default=None, max_length=60, description="Caller's own words for the date."
    )
    preferred_time: str | None = Field(
        default=None, max_length=60, description="Caller's own words for the time."
    )
    reason_as_stated: str | None = Field(
        default=None, max_length=200, description="Reason the caller gave, if any."
    )


class HandoffArgs(BaseModel):
    """Arguments for escalating the caller to a human."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        min_length=2,
        max_length=120,
        description="Short, non-clinical reason ('asked for a person', 'complex request').",
    )


def build_default_registry(
    *,
    retriever: Any = None,
    practice_client: Any = None,
    practice_id: str | None = None,
    jira: Any = None,
    on_issue: Any = None,
) -> ToolRegistry:
    """Register EMMA's built-in tools around the components it already uses.

    Live-product rule: when no practice backend is configured but Jira is, a
    confirmed request is recorded as a Jira issue (the practice's task board) and
    the outcome is reported truthfully. `on_issue` lets the caller remember the
    issue key so the end-of-call summary can be attached to the same issue.
    """
    registry = ToolRegistry()

    async def lookup_practice_information(
        args: LookupKnowledgeArgs, context: ToolContext
    ) -> dict[str, Any]:
        if retriever is None:
            return {"available": False, "passages": []}
        result = await retriever.retrieve(
            RetrievalQuery(
                query_text=args.question,
                practice_id=context.practice_id,
                top_k=args.top_k,
            )
        )
        passages = [
            {
                "chunk_id": chunk.chunk_id,
                "source_title": chunk.source_title,
                "content": chunk.content[:400],
            }
            for chunk in result.chunks[: args.top_k]
        ]
        return {"available": bool(passages), "passages": passages}

    registry.register(
        ToolSpec(
            name="lookup_practice_information",
            description=(
                "Search this practice's knowledge base for opening hours, services, "
                "contact details or policies. Never answers from general knowledge."
            ),
            parameters=LookupKnowledgeArgs,
            permission=ToolPermission.READ,
            timeout_seconds=4.0,
            idempotent=True,
            tags=("rag", "practice"),
        ),
        lookup_practice_information,
    )

    async def create_appointment_request(
        args: CreateRequestArgs, context: ToolContext
    ) -> dict[str, Any]:
        if args.request_type not in ADMIN_REQUEST_TYPES:
            raise ValueError("unsupported request_type")

        # Live product path: Jira is the system of record when no practice backend
        # is deployed. The issue is created immediately so staff see the request
        # while the call is still running.
        if practice_client is None or practice_id is None:
            if jira is None or getattr(jira, "mode", "disabled") == "disabled":
                return {"submitted": False, "detail": "no record sink configured"}
            summary = _request_summary(args, context)
            record = await jira.create_call_issue(
                summary,
                practice_id=context.practice_id or None,
                call_id=(context.call_id or context.session_id) + ":" + args.request_type,
                idempotency_key=f"emma:request:{context.session_id}:{args.request_type}",
            )
            if callable(on_issue) and record.issue_key:
                on_issue(record)
            return {
                "submitted": bool(record.ok),
                "status": record.status,
                "issue_key": record.issue_key,
                "issue_url": record.issue_url,
                "detail": record.error,
                "sink": "jira",
            }
        details = {
            key: value
            for key, value in {
                "preferred_date": args.preferred_date,
                "preferred_time": args.preferred_time,
                "reason_as_stated": args.reason_as_stated,
            }.items()
            if value
        }
        outcome = await practice_client.submit_request(
            practice_id=context.practice_id,
            idempotency_key=_key(context, args.request_type),
            request_type=args.request_type,
            structured_payload=details,
            call_id=context.call_id,
        )
        return {
            "submitted": bool(getattr(outcome, "confirmed", False)),
            "status": getattr(outcome, "status", "unknown"),
            "record_id": getattr(outcome, "record_id", None),
        }

    registry.register(
        ToolSpec(
            name="create_appointment_request",
            description=(
                "Send a confirmed appointment/prescription request to the practice "
                "team. Requires explicit caller confirmation; it does NOT book an "
                "appointment."
            ),
            parameters=CreateRequestArgs,
            permission=ToolPermission.WRITE,
            timeout_seconds=6.0,
            requires_confirmation=True,
            idempotent=True,
            tags=("practice", "write"),
        ),
        create_appointment_request,
    )

    async def request_human_handoff(args: HandoffArgs, context: ToolContext) -> dict[str, Any]:
        if practice_client is None or practice_id is None:
            return {"requested": False, "detail": "practice backend not configured"}
        outcome = await practice_client.submit_handoff(
            practice_id=context.practice_id,
            reason=args.reason,
            destination="practice-reception",
            call_id=context.call_id,
        )
        return {
            "requested": bool(getattr(outcome, "confirmed", False)),
            "status": getattr(outcome, "status", "unknown"),
        }

    registry.register(
        ToolSpec(
            name="request_human_handoff",
            description=(
                "Ask the practice reception team to call the patient back. Use when "
                "the caller asks for a person or the request is outside reception scope."
            ),
            parameters=HandoffArgs,
            permission=ToolPermission.ESCALATE,
            timeout_seconds=5.0,
            idempotent=False,
            tags=("escalation",),
        ),
        request_human_handoff,
    )

    return registry


def _request_summary(args: CreateRequestArgs, context: ToolContext) -> Any:
    """Build a one-task CallSummary for an immediate Jira record."""
    from app.services.call_summary import CallSummary

    request_label = args.request_type.replace("-", " ").title()
    details = "; ".join(
        part
        for part in (
            f"preferred date: {args.preferred_date}" if args.preferred_date else "",
            f"preferred time: {args.preferred_time}" if args.preferred_time else "",
            f"reason: {args.reason_as_stated}" if args.reason_as_stated else "",
        )
        if part
    )
    title = f"{request_label} — {details}" if details else request_label
    return CallSummary(
        headline=title,
        bullets=(
            f"Request captured during call {context.call_id or context.session_id}.",
            f"Request type: {args.request_type}.",
            details or "No additional details were captured.",
        ),
        tasks=(
            {
                "type": args.request_type.split("-")[0],
                "requestType": args.request_type,
                "title": title,
                "status": "captured",
                "details": {
                    key: value
                    for key, value in {
                        "preferred_date": args.preferred_date,
                        "preferred_time": args.preferred_time,
                        "reason_as_stated": args.reason_as_stated,
                    }.items()
                    if value
                },
            },
        ),
        outcome="completed",
        duration_seconds=0,
        turns=0,
        caller={},
    )


def _key(context: ToolContext, request_type: str) -> str:
    import hashlib

    digest = hashlib.sha256(
        f"{context.practice_id}|{context.call_id}|{request_type}".encode()
    ).hexdigest()[:32]
    return f"tool-{digest}"


__all__ = [
    "CreateRequestArgs",
    "HandoffArgs",
    "LookupKnowledgeArgs",
    "build_default_registry",
]
