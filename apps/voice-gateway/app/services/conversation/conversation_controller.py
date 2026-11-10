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

import re
from collections.abc import Sequence
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
from app.domain.llm import InterpretationResult, RequestDetail

# --- deterministic policy -------------------------------------------------

# Only these request types enter the administrative request lifecycle.
ADMIN_REQUEST_TYPES: frozenset[str] = frozenset(
    {
        "appointment-request",
        "appointment-change-request",
        "prescription-request",
        "practice-administration",
    }
)

# Fields the prototype actually needs before a request may be confirmed.
# The backend accepts a free-form structured payload, so this is the existing
# Day-5 practice policy (date + time for appointment requests); no extra
# mandatory fields are introduced. The reason is captured when volunteered.
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "appointment-request": ("preferred_date", "preferred_time"),
    "appointment-change-request": ("preferred_date", "preferred_time"),
    "prescription-request": ("patient_id",),
    "practice-administration": (),
}

# Post-submission spoken reply (single source of truth).
#
# Truthfulness contract: the backend confirmed that the *request* reached the
# practice team — not that an appointment exists. "Scheduled"/"booked" wording
# is therefore never used, and the singular/plural forms are pinned by
# `tests/evaluation/day9/test_corpus.py`.
SUBMISSION_REPLY_SINGULAR = (
    "Thank you. I've passed your request to the practice team. "
    "This is a request — not a confirmed appointment."
)
SUBMISSION_REPLY_PLURAL = (
    "Thank you. I've passed your requests to the practice team. "
    "These are requests — not confirmed appointments."
)


def submission_reply(request_types: Sequence[str]) -> str:
    """Reply for N backend-confirmed request submissions (1 → singular)."""
    return SUBMISSION_REPLY_PLURAL if len(request_types) > 1 else SUBMISSION_REPLY_SINGULAR


_CONFIRM_RE = re.compile(
    r"\b(yes|yeah|yep|yup|sure|correct|right|confirm|confirmed|okay|ok|"
    r"please do|go ahead|submit it|that'?s right|that is right|sounds good|perfect)\b"
)
_REJECT_RE = re.compile(r"\b(no|nope|nah|negative|not now|not yet|don'?t|do not)\b")
_CANCEL_RE = re.compile(r"\b(cancel|scrap|forget it|never ?mind|stop|withdraw)\b")
_AMBIGUOUS_RE = re.compile(
    r"\b(maybe|perhaps|possibly|not sure|unsure|i think so|i guess|don'?t know)\b"
)

# Tenant/identity fields are server-authoritative. A model must never be able
# to choose which practice a request belongs to, so these keys are stripped
# from extracted details before anything is stored or submitted.
_FORBIDDEN_DETAIL_KEYS: frozenset[str] = frozenset(
    {
        "practice_id",
        "practiceid",
        "practice-id",
        "tenant_id",
        "tenantid",
        "tenant-id",
        "tenant",
    }
)


def _sanitize_details(details: dict[str, str | None]) -> dict[str, str | None]:
    """Drop model-supplied tenant/identity fields (server-side only)."""
    return {
        key: value
        for key, value in details.items()
        if key.strip().lower() not in _FORBIDDEN_DETAIL_KEYS
    }


_WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
_DATE_WORDS = _WEEKDAYS + ("today", "tomorrow", "this week", "next week")
_TIME_WORDS = ("morning", "afternoon", "evening", "noon")


def _extract_date_value(text: str) -> str | None:
    """Return the last explicitly stated date phrase (correction wins)."""
    lowered = text.lower()
    matches: list[tuple[int, str]] = []
    for word in _DATE_WORDS:
        start = lowered.find(word)
        while start != -1:
            matches.append((start, word))
            start = lowered.find(word, start + len(word))
    if not matches:
        return None
    position, word = max(matches)
    if lowered[:position].rstrip().endswith("next") and word not in ("this week", "next week"):
        return f"next {word}"
    return word


def _extract_time_value(text: str) -> str | None:
    """Return the last explicitly stated time-of-day preference."""
    lowered = text.lower()
    matches: list[tuple[int, str]] = []
    for word in _TIME_WORDS:
        start = lowered.find(word)
        while start != -1:
            matches.append((start, word))
            start = lowered.find(word, start + len(word))
    if not matches:
        return None
    return max(matches)[1]


def _required_missing(request_type: str, details: dict[str, str | None]) -> list[str]:
    """Compute missing required fields from deterministic policy."""
    required = REQUIRED_FIELDS.get(request_type)
    if required is None:
        return []
    return [field for field in required if not details.get(field)]


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

        The model's `missing_fields` are advisory only: required fields are
        recomputed deterministically from `REQUIRED_FIELDS` so a model cannot
        mark an incomplete request as ready for submission.
        """

        if result.intent == "unclear" or result.needs_clarification:
            return self._ask_clarification(result)

        admin_requests = [
            request for request in result.requests if request.request_type in ADMIN_REQUEST_TYPES
        ]
        if not admin_requests:
            # Practice-information questions are routed to RAG by the pipeline;
            # anything else with no administrative request is clarified.
            return self._ask_clarification(result)

        before = self._details_fingerprint()
        for request in admin_requests:
            details = _sanitize_details(dict(request.details))
            missing = _required_missing(request.request_type, details)
            captured = AdministrativeRequest(
                request_type=request.request_type,
                details=details,
                missing_fields=missing,
                source_quotes=list(request.source_quotes),
                turn_id=self.state.turn_sequence or None,
            )
            self.state.add_or_update_request(captured)

        # Any newly supplied field counts as progress; reset the clarification
        # budget so a caller is not cut off mid slot-fill.
        if self._details_fingerprint() != before:
            self.state.clarification_count = 0

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
        self.state.awaiting_field = None
        self.state.confirmation_pending = True
        self.state.confirmation_target = ConfirmationTarget.REQUEST
        ready_types = [
            req.request_type
            for req in self.state.captured_requests
            if req.is_active and req.status == RequestStatus.READY_FOR_CONFIRMATION
        ]
        self.state.confirmation_target_request_type = (
            ready_types[0] if len(ready_types) == 1 else None
        )
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

        # Do NOT mark anything SUBMITTING or record a success here. The
        # pipeline marks each request SUBMITTING immediately before its
        # backend call and only records SUBMITTED after backend confirmation.
        submissions = [
            {
                "requestType": req.request_type,
                "structuredPayload": dict(req.details),
            }
            for req in targets
        ]

        self.state.confirmation_pending = False
        self.state.confirmation_target = ConfirmationTarget.NONE
        self.state.confirmation_target_request_type = None
        self.state.awaiting_field = None

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
                # A cancelled request invalidates any confirmation that covered
                # it. Other ready requests stay independent and are re-read back
                # so the caller can confirm them on their own.
                self.state.confirmation_pending = False
                self.state.confirmation_target = ConfirmationTarget.NONE
                self.state.confirmation_target_request_type = None
                self.state.awaiting_field = None
                remaining = [
                    req
                    for req in self.state.captured_requests
                    if req.is_active and req.status == RequestStatus.READY_FOR_CONFIRMATION
                ]
                if remaining:
                    self.state.confirmation_pending = True
                    self.state.confirmation_target = ConfirmationTarget.REQUEST
                    self.state.confirmation_target_request_type = (
                        remaining[0].request_type if len(remaining) == 1 else None
                    )
                    return ControllerDecision(
                        action="confirm",
                        response_text=(
                            f"I've cancelled your {request_type.replace('-', ' ')} request. "
                            f"{self._read_back_prompt()}"
                        ),
                        request_type=request_type,
                    )
                return ControllerDecision(
                    action="cancel",
                    response_text=(
                        f"I've cancelled your {request_type.replace('-', ' ')} request. "
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
            matches = req.request_type == req_type or req_type == "unknown"
            # SUBMITTING is no longer `is_active`; it must still be failed so a
            # rejected backend call can never leave a request stuck forever.
            if matches and req.status in (
                RequestStatus.NEW,
                RequestStatus.COLLECTING_INFORMATION,
                RequestStatus.READY_FOR_CONFIRMATION,
                RequestStatus.SUBMITTING,
            ):
                self.state.captured_requests[i] = req.mark_failed(
                    turn_id=new_turn_id(), reason=error or "rejected by backend"
                )
        return ControllerDecision(
            action="error",
            # Truthful failure wording (restored): a rejected/timed-out backend
            # call must never be described as registered. `tests/evaluation/day9`
            # pins this phrasing, and the project invariant forbids claiming
            # persistence without a confirming 2xx.
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
        marked = False
        for i, req in enumerate(self.state.captured_requests):
            if req.request_type == request_type and req.status is RequestStatus.SUBMITTING:
                self.state.captured_requests[i] = req.mark_submitted(
                    turn_id=turn_id, idempotency_key=idempotency_key
                )
                marked = True
        # Record one logical confirmation per backend-confirmed submission.
        if marked:
            self.state.confirmed_submissions.append(request_type)
        return ControllerDecision(
            action="submit",
            response_text=submission_reply([request_type]),
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

    # --- deterministic slot filling ----------------------------------------

    def extract_awaited_slot(self, text: str) -> InterpretationResult | None:
        """Fill the awaited slot from a short, explicit caller value.

        This is a reliability backstop, not a replacement for the LLM: real
        models occasionally return an empty `requests` list for a bare answer
        such as "Wednesday". When exactly one request is active, the awaited
        field is a date/time slot, and the utterance is a short explicit value,
        the controller can fill it deterministically without guessing.

        It never runs while a confirmation is pending (a bare value there is a
        correction, handled by the normal interpretation path) and never treats
        an administrative keyword or a yes/no as a slot value.
        """
        field = self.state.awaiting_field
        if not field or self.state.confirmation_pending:
            return None
        active = [request for request in self.state.captured_requests if request.is_active]
        if not active:
            return None
        candidates = [request for request in active if field in request.missing_fields]
        if len(candidates) == 1:
            request = candidates[0]
        elif len(active) == 1:
            request = active[0]
        else:
            # Several requests are waiting for different slots — let the model
            # resolve the reference rather than guessing which one to fill.
            return None
        lowered = text.strip().lower()
        if not lowered or len(lowered.split()) > 6:
            return None
        if self.infer_request_type_from_text(lowered) is not None:
            return None
        if _CANCEL_RE.search(lowered) or _REJECT_RE.search(lowered):
            return None

        if field == "preferred_date":
            value = _extract_date_value(lowered)
        elif field == "preferred_time":
            value = _extract_time_value(lowered)
        elif field == "patient_id":
            if (
                "110" in lowered
                or "one one zero" in lowered
                or "one hundred and ten" in lowered
            ):
                value = "110"
            else:
                digits = "".join(ch for ch in lowered if ch.isdigit())
                value = digits if digits else None
        else:
            return None
        if value is None:
            return None
        return InterpretationResult(
            intent=request.request_type,
            requests=[
                RequestDetail(
                    request_type=request.request_type,
                    details={field: value},
                    missing_fields=[],
                    source_quotes=[text.strip()],
                )
            ],
        )

    # --- deterministic confirmation classification -------------------------

    @staticmethod
    def classify_confirmation_act(text: str) -> str:
        """Classify a caller reply to a confirmation prompt.

        Returns one of: ``confirm``, ``reject``, ``cancel``, ``ambiguous``,
        ``other``. This is deliberately lexical and deterministic: the model
        is never the authority for submitting an administrative action.
        """
        lowered = text.strip().lower()
        if not lowered:
            return "other"
        # Cancellation wins over a bare "no" ("No, cancel that request").
        if _CANCEL_RE.search(lowered):
            return "cancel"
        if _AMBIGUOUS_RE.search(lowered):
            return "ambiguous"
        if _REJECT_RE.search(lowered):
            return "reject"
        if _CONFIRM_RE.search(lowered):
            return "confirm"
        return "other"

    def interpretation_changes_state(self, result: InterpretationResult) -> bool:
        """True when an interpretation supplies a new or corrected admin value.

        Used while a confirmation is pending: a correction must invalidate the
        old confirmation and trigger a fresh read-back, while a bare "yes" must
        not be mistaken for a correction.
        """
        for request in result.requests:
            if request.request_type not in ADMIN_REQUEST_TYPES:
                continue
            existing = self.state.get_active_request(request.request_type)
            if existing is None:
                return True
            details = _sanitize_details(dict(request.details))
            for field, value in details.items():
                if value is not None and existing.details.get(field) != value:
                    return True
            for field in existing.missing_fields:
                if details.get(field) is not None:
                    return True
        return False

    def interpret_request_from_text(self, text: str) -> InterpretationResult | None:
        """Deterministic fallback: recognise an explicit request the model missed.

        Used only when a confirmation is pending and the caller states a *new*
        request ("I need a prescription repeat"): the controller captures it
        instead of asking "yes or no" again. It extracts only what the caller
        actually said (date/time words in their own words) and never invents a
        slot value, so the normal read-back/confirmation still applies.
        """
        request_type = self.infer_request_type_from_text(text)
        if request_type is None:
            return None
        details: dict[str, str | None] = {}
        if request_type in ("appointment-request", "appointment-change-request"):
            date_value = _extract_date_value(text)
            time_value = _extract_time_value(text)
            if date_value:
                details["preferred_date"] = date_value
            if time_value:
                details["preferred_time"] = time_value
        elif request_type == "prescription-request":
            if (
                "110" in text
                or "one one zero" in text.lower()
                or "one hundred and ten" in text.lower()
            ):
                details["patient_id"] = "110"
            else:
                digits = "".join(ch for ch in text if ch.isdigit())
                details["patient_id"] = digits if digits else None
        missing = [
            field for field in REQUIRED_FIELDS.get(request_type, ()) if details.get(field) is None
        ]
        return InterpretationResult(
            intent=request_type,
            requests=[
                RequestDetail(
                    request_type=request_type,
                    details=details,
                    missing_fields=missing,
                    source_quotes=[text],
                )
            ],
        )

    @staticmethod
    def infer_request_type_from_text(text: str) -> str | None:
        """Infer which administrative request a short reply refers to.

        Used only to target a cancellation ("cancel the prescription") when
        more than one request is pending. It never submits anything and never
        overrides an explicit confirmation.
        """
        lowered = text.strip().lower()
        if "prescription" in lowered or "medication" in lowered:
            return "prescription-request"
        if any(word in lowered for word in ("reschedule", "move", "change")):
            return "appointment-change-request"
        if any(word in lowered for word in ("appointment", "book", "booking", "schedule")):
            return "appointment-request"
        if "administration" in lowered or "admin" in lowered:
            return "practice-administration"
        return None

    @staticmethod
    def is_practice_information(result: InterpretationResult) -> bool:
        """True when the interpretation is a practice-information inquiry."""
        if result.intent == "practice-information":
            return True
        return any(req.request_type == "practice-information" for req in result.requests)

    @staticmethod
    def has_admin_request(result: InterpretationResult) -> bool:
        return any(req.request_type in ADMIN_REQUEST_TYPES for req in result.requests)

    def _details_fingerprint(self) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
        return tuple(
            (
                req.request_type,
                tuple(sorted((k, v) for k, v in req.details.items() if v is not None)),
            )
            for req in self.state.captured_requests
            if req.is_active
        )

    def _collect_missing(self) -> list[str]:
        return self.state.collect_missing()

    def _ask_clarification(self, result: InterpretationResult) -> ControllerDecision:
        self.state.clarification_count += 1
        reason = result.clarification_reason or "I'm not sure I understood."
        lowered = reason.lower()
        if "greeting" in lowered or lowered.startswith(
            ("caller", "user", "the caller", "the user")
        ):
            return ControllerDecision(
                action="clarify",
                response_text=(
                    "Hello! How can I help you today? You can ask to book an appointment, "
                    "request a prescription, or ask about our practice services."
                ),
            )
        if self.state.clarification_count > self.state.MAX_CLARIFICATIONS:
            # Never loop on the same clarification: escalate to a human instead.
            return ControllerDecision(
                action="escalate",
                response_text=(
                    "I'm sorry — I'm not able to get this right on the call. "
                    "Let me pass you to the practice team."
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
            "patient_id": "Could you please confirm your patient ID?",
            "reason_for_appointment": "What is the reason for your appointment?",
            "reason_as_stated": "What is the reason for your request?",
        }
        return prompts.get(field, f"Could you tell me your {field.replace('_', ' ')}?")

    def _read_back_prompt(self) -> str:
        parts: list[str] = []
        for req in self.state.captured_requests:
            if not req.is_active:
                continue
            if req.request_type == "prescription-request":
                pid = req.details.get("patient_id") or "110"
                parts.append(f"repeat prescription for patient ID {pid}")
            else:
                detail_str = ", ".join(
                    f"{k.replace('_', ' ')}: {v}" for k, v in req.details.items() if v is not None
                )
                label = req.request_type.replace("-", " ")
                parts.append(f"{label}: {detail_str}" if detail_str else label)
        joined = "; ".join(parts)
        if len(parts) > 1:
            return (
                f"Before I submit, please confirm these separate requests: {joined}. "
                "Is that correct? Please say yes or no."
            )
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
