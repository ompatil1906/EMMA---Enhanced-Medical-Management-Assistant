"""Deterministic call summary (Day 19).

Built at call end from server-authoritative state — the intake form, the
ConversationController's captured requests, and the assistant's own turns. It is
then submitted to the practice backend, which stores it and mirrors it to the
external task platform (Jira).

Honesty and safety rules:

* **Deterministic by default.** No LLM, no transcript text beyond an optional
  short excerpt (`CALL_SUMMARY_INCLUDE_TRANSCRIPT`). Nothing here can invent a
  booking, a time, or a clinical fact.
* **Emergency calls produce no administrative tasks and are never submitted.**
  `VoicePipeline` skips submission entirely when the emergency guard matched, so
  the "zero administrative actions on emergency" invariant holds.
* **The transcript is not summarised by a model** unless the operator opts in
  (`CALL_SUMMARY_PROVIDER=llm`); the deterministic text is derived from state and
  request statuses only.
* Personal data stays in the practice record. Masking for the external task
  platform is applied server-side (`JIRA_PII_MODE`) and is not this module's job.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.domain.conversation import ConversationState, RequestStatus, Speaker
from app.services.caller_identity import CallerIdentity

_MAX_TRANSCRIPT_EXCERPT_CHARS = 1200

_REQUEST_LABELS: dict[str, str] = {
    "appointment-request": "Appointment request",
    "appointment-change-request": "Appointment change",
    "prescription-request": "Repeat prescription request",
    "practice-administration": "Practice administration request",
    "practice-information": "Practice information question",
    "handoff": "Reception callback",
    "other": "Other request",
}

_TASK_ISSUE_TYPES: dict[str, str] = {
    "appointment-request": "appointment",
    "appointment-change-request": "appointment",
    "prescription-request": "prescription",
    "practice-administration": "administration",
    "handoff": "callback",
}

_STATUS_LABELS: dict[RequestStatus, str] = {
    RequestStatus.NEW: "captured",
    RequestStatus.COLLECTING_INFORMATION: "collecting details",
    RequestStatus.READY_FOR_CONFIRMATION: "awaiting caller confirmation",
    RequestStatus.SUBMITTING: "submitting",
    RequestStatus.SUBMITTED: "submitted",
    RequestStatus.FAILED: "failed",
    RequestStatus.CANCELLED: "cancelled",
}


def _label(request_type: str) -> str:
    return _REQUEST_LABELS.get(request_type, request_type.replace("-", " ").title())


def _details_phrase(details: dict[str, str | None]) -> str:
    parts = [f"{key.replace('_', ' ')}: {value}" for key, value in details.items() if value]
    return "; ".join(parts)


@dataclass(frozen=True, slots=True)
class CallSummary:
    """One call's narrative summary plus the tasks it produced."""

    headline: str
    bullets: tuple[str, ...]
    tasks: tuple[dict[str, Any], ...]
    outcome: str
    duration_seconds: int
    turns: int
    caller: dict[str, Any]
    transcript_excerpt: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """JSON-safe payload for `POST /api/v1/calls/:id/summary`."""
        return {
            "headline": self.headline,
            "summary": "\n".join(self.bullets),
            "bullets": list(self.bullets),
            "tasks": list(self.tasks),
            "outcome": self.outcome,
            "durationSeconds": self.duration_seconds,
            "turns": self.turns,
            "caller": self.caller,
            "transcriptExcerpt": self.transcript_excerpt,
        }


def _caller_payload(caller: CallerIdentity | None) -> dict[str, Any]:
    """Full normalised intake record (masking happens in the practice backend)."""
    if caller is None:
        return {}
    payload: dict[str, Any] = {}
    if caller.first_name:
        payload["firstName"] = caller.first_name
    if caller.last_name:
        payload["lastName"] = caller.last_name
    if caller.email:
        payload["email"] = caller.email
    if caller.mobile:
        payload["mobile"] = caller.mobile
    if caller.reason:
        payload["reason"] = caller.reason
    return payload


def _transcript_excerpt(conversation: ConversationState) -> str | None:
    """Short, capped excerpt of the actual dialogue (off by default)."""
    lines: list[str] = []
    for turn in conversation.turns:
        speaker = "Caller" if turn.speaker == Speaker.CALLER else "EMMA"
        text = " ".join(turn.content.split())
        if not text:
            continue
        lines.append(f"{speaker}: {text}")
    if not lines:
        return None
    excerpt = "\n".join(lines)
    if len(excerpt) > _MAX_TRANSCRIPT_EXCERPT_CHARS:
        excerpt = excerpt[: _MAX_TRANSCRIPT_EXCERPT_CHARS - 3].rstrip() + "..."
    return excerpt


def build_call_summary(
    *,
    conversation: ConversationState,
    caller: CallerIdentity | None,
    outcome: str = "completed",
    started_at: float | None = None,
    include_transcript: bool = False,
    now: float | None = None,
) -> CallSummary:
    """Derive the deterministic call summary from server-side state.

    `outcome` is one of `completed`, `emergency`, `failed`, `abandoned` — supplied
    by the pipeline, never guessed here.
    """
    ended = now if now is not None else time.monotonic()
    duration = 0 if started_at is None else max(0, int(round(ended - started_at)))

    bullets: list[str] = []
    tasks: list[dict[str, Any]] = []

    display_name = caller.display_name if caller else None
    if display_name:
        bullets.append(f"Caller: {display_name}{_contact_note(caller)}.")
    else:
        bullets.append("Caller: name not captured on the intake form.")

    if caller and caller.reason:
        bullets.append(f"Reason given on the intake form: {caller.reason}.")

    active_requests = [
        req for req in conversation.captured_requests if req.status != RequestStatus.CANCELLED
    ]
    if active_requests:
        described = ", ".join(
            f"{_label(req.request_type)} ({_STATUS_LABELS.get(req.status, str(req.status))})"
            for req in active_requests
        )
        bullets.append(f"Requests captured: {described}.")
    else:
        bullets.append("No administrative request was captured.")

    for req in active_requests:
        task: dict[str, Any] = {
            "type": _TASK_ISSUE_TYPES.get(req.request_type, "task"),
            "requestType": req.request_type,
            "title": _task_title(req.request_type, req.details),
            "status": _STATUS_LABELS.get(req.status, str(req.status)),
            "details": {k: v for k, v in req.details.items() if v},
        }
        if req.failure_reason:
            task["failureReason"] = req.failure_reason
        tasks.append(task)

    if conversation.confirmed_submissions:
        bullets.append(
            f"Confirmed by the practice system: {len(conversation.confirmed_submissions)}."
        )
    if conversation.backend_errors:
        bullets.append(
            f"The practice system did not confirm {len(conversation.backend_errors)} request(s)."
        )
    if conversation.clarification_count:
        bullets.append(f"Clarifications needed: {conversation.clarification_count}.")
    if conversation.last_grounded_topic:
        bullets.append(f"Last practice topic answered: {conversation.last_grounded_topic}.")
    if outcome == "emergency":
        # Recorded for the audit trail only; no submission happens on this path.
        bullets.append(
            "Emergency guard matched: the fixed 999 script was spoken, no actions taken."
        )

    bullets.append(f"Call duration: {duration}s over {conversation.turn_sequence} turn(s).")

    headline = _headline(active_requests, display_name)
    return CallSummary(
        headline=headline,
        bullets=tuple(bullets),
        tasks=tuple(tasks),
        outcome=outcome,
        duration_seconds=duration,
        turns=conversation.turn_sequence,
        caller=_caller_payload(caller),
        transcript_excerpt=_transcript_excerpt(conversation) if include_transcript else None,
    )


def _contact_note(caller: CallerIdentity | None) -> str:
    if caller is None or not caller.mobile:
        return ""
    digits = [ch for ch in caller.mobile if ch.isdigit()]
    return f" (mobile ending {''.join(digits[-3:])})" if len(digits) >= 3 else ""


def _task_title(request_type: str, details: dict[str, str | None]) -> str:
    label = _label(request_type)
    phrase = _details_phrase(details)
    return f"{label} — {phrase}" if phrase else label


def _headline(active_requests: list[Any], display_name: str | None) -> str:
    who = display_name or "Unnamed caller"
    if not active_requests:
        return f"General enquiry — {who}"
    primary = _label(active_requests[0].request_type)
    return f"{primary} — {who}"


__all__ = ["CallSummary", "build_call_summary"]
