"""Conversation state domain models (Day 9).

Typed, auditable models for multi-turn conversation state. The conversation
controller owns these; the LLM never changes conversation state directly.

Design rules:

* Each administrative request has its own independent lifecycle
  (NEW → COLLECTING_INFORMATION → READY_FOR_CONFIRMATION → SUBMITTING →
  SUBMITTED/FAILED/CANCELLED).
* Every turn has a stable identifier and sequence number.
* Confirmation applies only to the request or action being confirmed.
* Corrections modify the relevant active request; old values are superseded.
* Submitted requests cannot be silently rewritten; cancelled requests cannot
  be submitted; a request cannot be submitted twice.
* All timestamps are wall-clock deltas; no PII is stored in these models.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum


class RequestStatus(StrEnum):
    """Lifecycle of one administrative request."""

    NEW = "new"
    COLLECTING_INFORMATION = "collecting_information"
    READY_FOR_CONFIRMATION = "ready_for_confirmation"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Speaker(StrEnum):
    """Who produced a turn."""

    CALLER = "caller"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class TurnStatus(StrEnum):
    """Processing status of one turn."""

    RECEIVED = "received"
    PROCESSING = "processing"
    COMPLETED = "completed"
    REJECTED = "rejected"  # duplicate / stale / invalid


class ConfirmationTarget(StrEnum):
    """What a pending confirmation refers to."""

    NONE = "none"
    REQUEST = "request"  # confirm a specific administrative request
    CORRECTION = "correction"  # confirm a corrected value


@dataclass(frozen=True)
class ConversationTurn:
    """One turn in a conversation (caller utterance or assistant response)."""

    turn_id: str
    sequence: int
    speaker: Speaker
    content: str
    status: TurnStatus = TurnStatus.RECEIVED
    timestamp: float = field(default_factory=time.time)
    correlation_id: str | None = None  # links to backend submission or retrieval
    intent: str | None = None  # LLM-detected intent, if any


@dataclass(frozen=True)
class AdministrativeRequest:
    """One captured administrative request with its own lifecycle."""

    request_type: str
    details: dict[str, str | None]
    missing_fields: list[str]
    source_quotes: list[str]
    status: RequestStatus = RequestStatus.NEW
    turn_id: str | None = None  # turn that last modified this request
    confirmed_at: float | None = None
    submitted_at: float | None = None
    failure_reason: str | None = None
    idempotency_key: str | None = None

    def with_updated_details(
        self,
        *,
        details: dict[str, str | None],
        missing_fields: list[str],
        source_quotes: list[str],
        turn_id: str,
    ) -> AdministrativeRequest:
        """Return a copy with merged details and corrected fields (latest wins).

        The lifecycle status is derived from the *merged* missing fields, not
        from the latest utterance alone: filling one slot on a second turn must
        move the request to READY when nothing else is missing.
        """
        merged = dict(self.details)
        merged.update(details)
        missing = [f for f in missing_fields if f not in merged or merged[f] is None]
        return AdministrativeRequest(
            request_type=self.request_type,
            details=merged,
            missing_fields=missing,
            source_quotes=list(dict.fromkeys(self.source_quotes + source_quotes)),
            status=(
                RequestStatus.READY_FOR_CONFIRMATION
                if not missing
                else RequestStatus.COLLECTING_INFORMATION
            ),
            turn_id=turn_id,
        )

    def mark_submitted(self, *, turn_id: str, idempotency_key: str) -> AdministrativeRequest:
        """Return a copy marked as submitted (backend confirmed)."""
        return AdministrativeRequest(
            request_type=self.request_type,
            details=self.details,
            missing_fields=[],
            source_quotes=self.source_quotes,
            status=RequestStatus.SUBMITTED,
            turn_id=turn_id,
            confirmed_at=time.time(),
            submitted_at=time.time(),
            idempotency_key=idempotency_key,
        )

    def mark_failed(self, *, turn_id: str, reason: str) -> AdministrativeRequest:
        """Return a copy marked as failed."""
        return AdministrativeRequest(
            request_type=self.request_type,
            details=self.details,
            missing_fields=self.missing_fields,
            source_quotes=self.source_quotes,
            status=RequestStatus.FAILED,
            turn_id=turn_id,
            failure_reason=reason,
        )

    def mark_cancelled(self, *, turn_id: str) -> AdministrativeRequest:
        """Return a copy marked as cancelled."""
        return AdministrativeRequest(
            request_type=self.request_type,
            details=self.details,
            missing_fields=self.missing_fields,
            source_quotes=self.source_quotes,
            status=RequestStatus.CANCELLED,
            turn_id=turn_id,
        )

    def mark_ready_for_confirmation(self, *, turn_id: str) -> AdministrativeRequest:
        """Return a copy marked as ready for confirmation."""
        return AdministrativeRequest(
            request_type=self.request_type,
            details=self.details,
            missing_fields=[],
            source_quotes=self.source_quotes,
            status=RequestStatus.READY_FOR_CONFIRMATION,
            turn_id=turn_id,
        )

    def mark_submitting(self, *, turn_id: str, idempotency_key: str) -> AdministrativeRequest:
        """Return a copy marked as submitting to the backend."""
        return AdministrativeRequest(
            request_type=self.request_type,
            details=self.details,
            missing_fields=self.missing_fields,
            source_quotes=self.source_quotes,
            status=RequestStatus.SUBMITTING,
            turn_id=turn_id,
            idempotency_key=idempotency_key,
        )

    @property
    def is_active(self) -> bool:
        """True when the request can still be modified or confirmed."""
        return self.status in (
            RequestStatus.NEW,
            RequestStatus.COLLECTING_INFORMATION,
            RequestStatus.READY_FOR_CONFIRMATION,
        )


@dataclass
class ConversationState:
    """Mutable per-session conversation state (held by SpeechSession).

    Extends the Day-5 `ConversationState` with per-request lifecycle states,
    turn history, confirmation targeting, and multi-turn RAG context.
    """

    # --- turns ----------------------------------------------------------------
    turns: list[ConversationTurn] = field(default_factory=list)
    turn_sequence: int = 0

    # --- administrative requests ---------------------------------------------
    captured_requests: list[AdministrativeRequest] = field(default_factory=list)

    # --- confirmation ---------------------------------------------------------
    confirmation_pending: bool = False
    confirmation_target: ConfirmationTarget = ConfirmationTarget.NONE
    confirmation_target_request_type: str | None = None
    awaiting_field: str | None = None
    clarification_count: int = 0

    # --- submissions ----------------------------------------------------------
    confirmed_submissions: list[str] = field(default_factory=list)
    backend_errors: list[str] = field(default_factory=list)

    # --- multi-turn RAG context ----------------------------------------------
    last_grounded_answer: str | None = None
    last_grounded_topic: str | None = None
    last_grounded_citation_ids: list[str] = field(default_factory=list)
    last_grounded_turn_id: str | None = None

    # --- metadata -------------------------------------------------------------
    session_created_at: float = field(default_factory=time.time)
    last_activity_at: float = field(default_factory=time.time)

    # --- turn management ------------------------------------------------------
    MAX_CLARIFICATIONS: int = 2
    MAX_HISTORY_TURNS: int = 50  # bounded context

    # --- request lifecycle ----------------------------------------------------

    def add_or_update_request(self, request: AdministrativeRequest) -> AdministrativeRequest:
        """Add a new request or update an existing one of the same type.

        Latest explicit correction wins — merged details supersede old values.
        Returns the resulting request (possibly updated).
        """
        # Convert Day-5 CapturedRequest to AdministrativeRequest if needed.
        if not isinstance(request, AdministrativeRequest):
            request = AdministrativeRequest(
                request_type=request.request_type,
                details=dict(request.details),
                missing_fields=list(request.missing_fields),
                source_quotes=list(request.source_quotes),
                status=(
                    RequestStatus.READY_FOR_CONFIRMATION
                    if not request.missing_fields
                    else RequestStatus.COLLECTING_INFORMATION
                ),
            )
        else:
            # Ensure new AdministrativeRequest objects get the right status
            # based on their missing fields.
            request = AdministrativeRequest(
                request_type=request.request_type,
                details=dict(request.details),
                missing_fields=list(request.missing_fields),
                source_quotes=list(request.source_quotes),
                status=(
                    RequestStatus.READY_FOR_CONFIRMATION
                    if not request.missing_fields
                    else RequestStatus.COLLECTING_INFORMATION
                ),
                turn_id=request.turn_id,
            )

        for i, existing in enumerate(self.captured_requests):
            if existing.request_type == request.request_type and existing.is_active:
                merged = existing.with_updated_details(
                    details=request.details,
                    missing_fields=request.missing_fields,
                    source_quotes=request.source_quotes,
                    turn_id=request.turn_id or "",
                )
                self.captured_requests[i] = merged
                return merged
        self.captured_requests.append(request)
        return request

    def get_active_request(self, request_type: str) -> AdministrativeRequest | None:
        """Return the active request of the given type, if any."""
        for req in self.captured_requests:
            if req.request_type == request_type and req.is_active:
                return req
        return None

    def cancel_request(self, *, request_type: str, turn_id: str) -> bool:
        """Cancel the active request of the given type. Returns True if cancelled."""
        for i, req in enumerate(self.captured_requests):
            if req.request_type == request_type and req.is_active:
                self.captured_requests[i] = req.mark_cancelled(turn_id=turn_id)
                return True
        return False

    def cancel_all_pending(self, *, turn_id: str) -> int:
        """Cancel all active, non-submitted requests. Returns count cancelled."""
        cancelled = 0
        for i, req in enumerate(self.captured_requests):
            if req.is_active:
                self.captured_requests[i] = req.mark_cancelled(turn_id=turn_id)
                cancelled += 1
        return cancelled

    def ready_for_confirmation(self) -> bool:
        """True when at least one active request is ready for confirmation."""
        for req in self.captured_requests:
            if not req.is_active:
                continue
            # Day-9: status-based. Day-5 CapturedRequest has no status; treat
            # missing_fields == [] as ready.
            if isinstance(req, AdministrativeRequest):
                if req.status == RequestStatus.READY_FOR_CONFIRMATION:
                    return True
            else:
                if not req.missing_fields:
                    return True
        return False

    def collect_missing(self) -> list[str]:
        """Collect all missing fields across active requests, in order."""
        missing: list[str] = []
        seen: set[str] = set()
        for req in self.captured_requests:
            if not req.is_active:
                continue
            for missing_field in req.missing_fields:
                if missing_field not in seen:
                    seen.add(missing_field)
                    missing.append(missing_field)
        return missing

    # --- turn history ---------------------------------------------------------

    def add_turn(
        self,
        *,
        turn_id: str,
        speaker: Speaker,
        content: str,
        **kwargs,
    ) -> ConversationTurn:
        """Append a turn and return it. Bounded history: drops oldest turns."""
        sequence = self.turn_sequence + 1
        self.turn_sequence = sequence
        turn = ConversationTurn(
            turn_id=turn_id,
            sequence=sequence,
            speaker=speaker,
            content=content,
            **kwargs,
        )
        self.turns.append(turn)
        # Bound history to prevent unbounded memory growth.
        if len(self.turns) > self.MAX_HISTORY_TURNS:
            self.turns = self.turns[-self.MAX_HISTORY_TURNS :]
        self.touch()
        return turn

    def touch(self) -> None:
        self.last_activity_at = time.time()

    def recent_turns(self, *, count: int = 10) -> list[ConversationTurn]:
        """Return the most recent turns (for context building)."""
        return self.turns[-count:]

    def last_assistant_turn(self) -> ConversationTurn | None:
        """Return the most recent assistant turn, if any."""
        for turn in reversed(self.turns):
            if turn.speaker is Speaker.ASSISTANT:
                return turn
        return None

    def last_caller_turn(self) -> ConversationTurn | None:
        """Return the most recent caller turn, if any."""
        for turn in reversed(self.turns):
            if turn.speaker is Speaker.CALLER:
                return turn
        return None

    # --- introspection --------------------------------------------------------

    @property
    def active_request_count(self) -> int:
        return sum(1 for req in self.captured_requests if req.is_active)

    @property
    def submitted_count(self) -> int:
        return sum(1 for req in self.captured_requests if req.status is RequestStatus.SUBMITTED)

    @property
    def failed_count(self) -> int:
        return sum(1 for req in self.captured_requests if req.status is RequestStatus.FAILED)

    @property
    def cancelled_count(self) -> int:
        return sum(1 for req in self.captured_requests if req.status is RequestStatus.CANCELLED)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.session_created_at

    def snapshot(self) -> dict[str, object]:
        """Compact state dump for metrics (ids/counts/lengths only, no text)."""
        return {
            "turn_sequence": self.turn_sequence,
            "turn_count": len(self.turns),
            "active_requests": self.active_request_count,
            "submitted_requests": self.submitted_count,
            "failed_requests": self.failed_count,
            "cancelled_requests": self.cancelled_count,
            "confirmation_pending": self.confirmation_pending,
            "confirmation_target": self.confirmation_target.value,
            "awaiting_field": self.awaiting_field,
            "clarification_count": self.clarification_count,
            "backend_errors": len(self.backend_errors),
            "last_grounded_topic": self.last_grounded_topic,
            "age_seconds": round(self.age_seconds, 3),
        }


def new_turn_id() -> str:
    """Generate a stable turn identifier."""
    return f"turn-{uuid.uuid4().hex[:12]}"


__all__ = [
    "RequestStatus",
    "Speaker",
    "TurnStatus",
    "ConfirmationTarget",
    "ConversationTurn",
    "AdministrativeRequest",
    "ConversationState",
    "new_turn_id",
]
