"""Deterministic conversation controller (Day 5 + Day 9).

Owns conversation state and decides what happens after LLM interpretation:

* Ambiguous → controlled clarification
* Missing fields → ask for the required field
* Multiple requests → preserve independently
* Correction → latest explicit value wins
* Ready → read back captured details, require explicit confirmation
* Confirmed → typed backend submission
* Backend failure → truthful spoken response, no false confirmation
* Cancel → cancel pending request, do not submit
* Maybe → clarification, do not submit

Day 9 additions:

* Per-request lifecycle states (NEW → COLLECTING → READY_FOR_CONFIRMATION →
  SUBMITTING → SUBMITTED/FAILED/CANCELLED).
* Confirmation targets a specific request; correcting after read-back
  invalidates the pending confirmation.
* "Maybe" / ambiguous yes → clarification, never submission.
* Turn history and multi-turn RAG context (last grounded answer + topic).
* Session cleanup on emergency (invalidates pending confirmations).

The FSM remains the authority for call state — the controller only recommends
responses and backend actions. The LLM never changes conversation state.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.conversation import (
    AdministrativeRequest,
    ConfirmationTarget,
    ConversationState,
    ConversationTurn,
    RequestStatus,
    Speaker,
    TurnStatus,
    new_turn_id,
)
from app.domain.llm import InterpretationResult


@dataclass(frozen=True)
class CapturedRequest:
    """Backward-compatible alias for `AdministrativeRequest` (Day 5 tests)."""

    request_type: str
    details: dict[str, str | None]
    missing_fields: list[str]
    source_quotes: list[str]

    def __post_init__(self) -> None:
        # Ensure mutable defaults are not shared.
        object.__setattr__(self, "details", dict(self.details))
        object.__setattr__(self, "missing_fields", list(self.missing_fields))
        object.__setattr__(self, "source_quotes", list(self.source_quotes))

    @property
    def status(self) -> RequestStatus:
        return RequestStatus.NEW

    @property
    def is_active(self) -> bool:
        return True


@dataclass(frozen=True)
class ControllerDecision:
    """The controller's next step — returned to the pipeline."""

    action: str  # "clarify" | "confirm" | "submit" | "cancel" | "emergency" | "error"
    response_text: str
    backend_action: str | None = None  # "submit_patient_request" | "create_handoff" | None
    backend_payload: dict[str, object] | None = None
    idempotency_key: str | None = None
    # Which request the decision applies to (None = whole conversation).
    request_type: str | None = None


class ConversationController:
    """Deterministic controller bridging LLM interpretation to FSM/backend."""

    MAX_CLARIFICATIONS = 2

    def __init__(self, state: ConversationState | None = None) -> None:
        self.state = state or ConversationState()

    # --- entry point -------------------------------------------------------

    def handle_interpretation(self, result: InterpretationResult) -> ControllerDecision:
        """Decide the next step from a validated LLM interpretation.

        Returns a `ControllerDecision`. The pipeline speaks `response_text`
        and, when `action == "submit"`, performs the backend submission.
        """

        if result.intent == "unclear" or result.needs_clarification:
            return self._ask_clarification(result)

        # Preserve all extracted requests independently.
        for request in result.requests:
            captured = AdministrativeRequest(
                request_type=request.request_type,
                details=request.details,
                missing_fields=list(request.missing_fields),
                source_quotes=list(request.source_quotes),
                turn_id=self.state.turn_sequence or None,
            )
            self.state.add_or_update_request(captured)

        # Check for missing required fields across active requests.
        missing = self.state.collect_missing()
        if missing:
            self.state.awaiting_field = missing[0]
            self.state.clarification_count += 1
            if self.state.clarification_count > self.state.MAX_CLARIFICATIONS:
                return ControllerDecision(
                    action="clarify",
                    response_text=(
                        "I'm still not sure about the details. "
                        "Could you tell me what you'd like, and I'll pass it "
                        "to the practice team?"
                    ),
                )
            return ControllerDecision(
                action="clarify",
                response_text=self._clarification_prompt(missing[0]),
            )

        # All active fields captured → require explicit confirmation.
        self.state.confirmation_pending = True
        self.state.confirmation_target = ConfirmationTarget.REQUEST
        return ControllerDecision(
            action="confirm",
            response_text=self._read_back_prompt(),
            request_type=None,  # confirmation covers all pending requests
        )

    # --- confirmation ------------------------------------------------------

    def handle_confirmation(
        self, *, confirmed: bool, request_type: str | None = None
    ) -> ControllerDecision:
        """Process the caller's explicit confirmation (or denial).

        When `confirmed` is True and a specific `request_type` is given, only
        that request is submitted. When False, the pending confirmation is
        cleared without submitting.
        """

        if not confirmed:
            self.state.confirmation_pending = False
            self.state.confirmation_target = ConfirmationTarget.NONE
            self.state.confirmation_target_request_type = None
            self.state.awaiting_field = None
            return ControllerDecision(
                action="clarify",
                response_text=(
                    "No problem — please tell me again what you'd like, "
                    "and I'll make sure it's correct this time."
                ),
            )

        # Explicit confirmation → submit the confirmed request(s).
        if not self.state.ready_for_confirmation():
            return ControllerDecision(
                action="error",
                response_text=(
                    "I'm missing some details. Could you tell me what you'd like again?"
                ),
            )

        # Determine which requests to submit.
        if request_type is not None:
            req = self.state.get_active_request(request_type)
            if req is None or req.status != RequestStatus.READY_FOR_CONFIRMATION:
                return ControllerDecision(
                    action="error",
                    response_text=(
                        "I don't have a pending request to confirm right now. "
                        "Could you tell me what you'd like?"
                    ),
                )
            targets = [req]
        else:
            targets = [
                req
                for req in self.state.captured_requests
                if req.is_active and req.status == RequestStatus.READY_FOR_CONFIRMATION
            ]

        if not targets:
            return ControllerDecision(
                action="error",
                response_text=(
                    "There's nothing pending to submit. Could you tell me what you'd like?"
                ),
            )

        submissions = []
        turn_id = new_turn_id()
        for req in targets:
            submissions.append(
                {
                    "requestType": req.request_type,
                    "structuredPayload": req.details,
                }
            )
            self.state.confirmed_submissions.append(req.request_type)
            # Mark as submitting; the pipeline moves to SUBMITTED on backend
            # confirmation or FAILED on backend rejection.
            for i, existing in enumerate(self.state.captured_requests):
                if existing.request_type == req.request_type and existing.is_active:
                    self.state.captured_requests[i] = existing.mark_submitting(
                        turn_id=turn_id,
                        idempotency_key=f"{req.request_type}:{turn_id}",
                    )

        self.state.confirmation_pending = False
        self.state.confirmation_target = ConfirmationTarget.NONE
        self.state.confirmation_target_request_type = None

        return ControllerDecision(
            action="submit",
            response_text=(
                "Thank you. I'll pass your request to the practice team. "
                "This is a request — not a confirmed appointment."
            ),
            backend_action="submit_patient_request",
            backend_payload={"requests": submissions},
            idempotency_key=None,  # set by pipeline per request
            request_type=targets[0].request_type if len(targets) == 1 else None,
        )

    # --- cancellation -------------------------------------------------------

    def handle_cancel(self, *, request_type: str | None = None) -> ControllerDecision:
        """Cancel a pending request (or all pending requests)."""

        if request_type is not None:
            cancelled = self.state.cancel_request(request_type=request_type, turn_id=new_turn_id())
            if cancelled:
                return ControllerDecision(
                    action="cancel",
                    response_text=(
                        f"I've cancelled your {request_type} request. "
                        "Is there anything else I can help with?"
                    ),
                    request_type=request_type,
                )
            return ControllerDecision(
                action="error",
                response_text=(
                    "I don't have a pending request to cancel. Could you tell me what you'd like?"
                ),
            )

        cancelled = self.state.cancel_all_pending(turn_id=new_turn_id())
        self.state.confirmation_pending = False
        self.state.confirmation_target = ConfirmationTarget.NONE
        if cancelled:
            return ControllerDecision(
                action="cancel",
                response_text=(
                    "I've cancelled your pending requests. Is there anything else I can help with?"
                ),
            )
        return ControllerDecision(
            action="clarify",
            response_text=("There's nothing to cancel. Could you tell me what you'd like?"),
        )

    # --- backend submission outcome ----------------------------------------

    def handle_backend_failure(
        self, error: str = "", *, request_type: str | None = None
    ) -> ControllerDecision:
        """Truthful response when the backend rejects a submission for one request.

        Backward-compatible: accepts `handle_backend_failure(error)` (Day 5)
        or `handle_backend_failure(request_type=..., error=...)` (Day 9).
        """
        req_type = request_type or "unknown"
        self.state.backend_errors.append(error or "rejected by backend")
        for i, req in enumerate(self.state.captured_requests):
            if (req.request_type == req_type or req_type == "unknown") and req.is_active:
                self.state.captured_requests[i] = req.mark_failed(
                    turn_id=new_turn_id(), reason=error or "rejected by backend"
                )
        return ControllerDecision(
            action="error",
            response_text=(
                "I'm sorry — I wasn't able to submit your request just now. "
                "The practice team may be able to help you directly."
            ),
            request_type=req_type,
        )

    def handle_backend_success(
        self, *, request_type: str, idempotency_key: str
    ) -> ControllerDecision:
        """Mark a request as submitted after backend confirmation."""

        turn_id = new_turn_id()
        for i, req in enumerate(self.state.captured_requests):
            if req.request_type == request_type and req.status is RequestStatus.SUBMITTING:
                self.state.captured_requests[i] = req.mark_submitted(
                    turn_id=turn_id, idempotency_key=idempotency_key
                )
        self.state.confirmed_submissions.append(request_type)
        return ControllerDecision(
            action="submit",
            response_text=(
                "Thank you. I've passed your request to the practice team. "
                "This is a request — not a confirmed appointment."
            ),
            request_type=request_type,
        )

    # --- emergency ----------------------------------------------------------

    def handle_emergency(self) -> ControllerDecision:
        """Invalidate all pending ordinary actions on emergency detection."""

        self.state.cancel_all_pending(turn_id=new_turn_id())
        self.state.confirmation_pending = False
        self.state.confirmation_target = ConfirmationTarget.NONE
        self.state.confirmation_target_request_type = None
        self.state.awaiting_field = None
        return ControllerDecision(
            action="emergency",
            response_text=("This may be a medical emergency. Please hang up and call 999 now."),
        )

    # --- helpers ------------------------------------------------------------

    def mark_submitting(self, request_type: str, *, turn_id: str, idempotency_key: str) -> None:
        """Mark a request as SUBMITTING (called by pipeline before backend call)."""
        for i, req in enumerate(self.state.captured_requests):
            if req.request_type == request_type and req.is_active:
                self.state.captured_requests[i] = req.mark_submitting(
                    turn_id=turn_id, idempotency_key=idempotency_key
                )

    def invalidate_confirmation(self) -> None:
        """Invalidate a pending confirmation (e.g. after a correction)."""
        self.state.confirmation_pending = False
        self.state.confirmation_target = ConfirmationTarget.NONE
        self.state.confirmation_target_request_type = None

    def _collect_missing(self) -> list[str]:
        return self.state.collect_missing()

    def _ask_clarification(self, result: InterpretationResult) -> ControllerDecision:
        self.state.clarification_count += 1
        reason = result.clarification_reason or "I'm not sure I understood."
        if self.state.clarification_count > self.state.MAX_CLARIFICATIONS:
            return ControllerDecision(
                action="clarify",
                response_text=(
                    "I'm having trouble understanding. Could you tell me "
                    "what you need, and I'll pass it to the practice team?"
                ),
            )
        return ControllerDecision(
            action="clarify",
            response_text=f"{reason} Could you tell me more?",
        )

    def _clarification_prompt(self, field: str) -> str:
        prompts = {
            "preferred_date": "What date would you like?",
            "preferred_time": "What time of day would you prefer?",
            "reason_for_appointment": "What is the reason for your appointment?",
        }
        return prompts.get(field, f"Could you tell me your {field}?")

    def _read_back_prompt(self) -> str:
        parts: list[str] = []
        for req in self.state.captured_requests:
            if not req.is_active:
                continue
            detail_str = ", ".join(f"{k}: {v}" for k, v in req.details.items() if v is not None)
            parts.append(f"{req.request_type}: {detail_str}")
        joined = "; ".join(parts)
        return f"Before I submit, please confirm: {joined}. Is that correct? Please say yes or no."


# --- backward-compatible state alias ----------------------------------------

# `ConversationState` is imported from `app.domain.conversation`; `CapturedRequest`
# above is a frozen alias of `AdministrativeRequest` for Day-5 test compatibility.


__all__ = [
    "CapturedRequest",
    "ConversationState",
    "ControllerDecision",
    "ConversationController",
    "AdministrativeRequest",
    "RequestStatus",
    "ConfirmationTarget",
    "ConversationTurn",
    "Speaker",
    "TurnStatus",
    "new_turn_id",
]
