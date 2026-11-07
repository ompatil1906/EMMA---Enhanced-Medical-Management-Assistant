"""Deterministic voice-session finite state machine (typed transitions).

The FSM OWNS the transition rules: every pass goes through `transition()`,
which validates against the explicit table below and raises
`InvalidTransitionError` otherwise. Nothing (including a future LLM) may set
`state` directly.

Verified-driven table (Day 3; TRY/FAIL via unit tests):

    IDLE ──────► CONNECTING ──► LISTENING ──► PROCESSING ──► SPEAKING ─┐
                   │              ▲            │   │        ▲            │
                   │ greeting allowed via SPEAKING │   └ HANDOFF│         │
                   ▼              │            ▼   ▼           │          ▼
                 SPEAKING ◄───────┘           HANDOFF       LISTENING ◄───┘
                   │
                   ▼
                 LISTENING / ERROR / ENDED

    · every non-terminal state may end the call (ENDED) — cleanup must never
      be refused;
    · ERROR is reachable from CONNECTING/LISTENING/PROCESSING/SPEAKING/HANDOFF,
      and ERROR only continues to ENDED;
    · ENDED is terminal (no outbound edges);
    · no barge-in today: SPEAKING only ever returns to LISTENING.
"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Final

from pydantic import Field

from app.domain.events import GatewayEvent


class VoiceState(StrEnum):
    IDLE = "idle"
    CONNECTING = "connecting"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    HANDOFF = "handoff"
    ENDED = "ended"
    ERROR = "error"


class TransitionReason(StrEnum):
    """Typed, closed-set transition reasons (keeps logs/metrics greppable)."""

    CALL_ACCEPTED = "call_accepted"  # transport accepted → CONNECTING
    CALL_STARTED = "call_started"  # provider `start` processed
    LISTENING_STARTED = "listening_started"  # recognition stream live
    GREETING_STARTED = "greeting_started"  # direct greeting on connect
    GREETING_COMPLETED = "greeting_completed"
    UTTERANCE_RECEIVED = "utterance_received"  # final transcript → PROCESSING
    RESPONSE_STARTED = "response_started"  # TTS begins → SPEAKING
    RESPONSE_COMPLETED = "response_completed"  # back to LISTENING
    HANDOFF_INITIATED = "handoff_initiated"  # deterministic handoff decision
    PROVIDER_ERROR = "provider_error"  # STT/TTS provider failure
    INTERNAL_ERROR = "internal_error"
    CALL_ENDED = "call_ended"  # provider stop / peer disconnect
    CLEANUP = "cleanup"  # guaranteed teardown path
    SHUTDOWN = "shutdown"


class InvalidTransitionError(Exception):
    """Raised for any transition not present in the explicit valid table."""

    def __init__(self, session_id: str, current: VoiceState, target: VoiceState) -> None:
        allowed = ", ".join(sorted(state.value for state in VALID_TRANSITIONS[current]))
        super().__init__(
            f"invalid voice transition {current.value!r} → {target.value!r} "
            f"(allowed: [{allowed} or none]) for session {session_id!r}"
        )
        self.session_id = session_id
        self.current = current
        self.target = target


# The explicit, therefore auditable, transition table.
VALID_TRANSITIONS: Final[dict[VoiceState, frozenset[VoiceState]]] = {
    VoiceState.IDLE: frozenset({VoiceState.CONNECTING, VoiceState.ENDED}),
    VoiceState.CONNECTING: frozenset(
        {VoiceState.LISTENING, VoiceState.SPEAKING, VoiceState.ERROR, VoiceState.ENDED}
    ),
    VoiceState.LISTENING: frozenset({VoiceState.PROCESSING, VoiceState.ERROR, VoiceState.ENDED}),
    VoiceState.PROCESSING: frozenset(
        {
            VoiceState.SPEAKING,
            VoiceState.LISTENING,
            VoiceState.HANDOFF,
            VoiceState.ERROR,
            VoiceState.ENDED,
        }
    ),
    VoiceState.SPEAKING: frozenset({VoiceState.LISTENING, VoiceState.ERROR, VoiceState.ENDED}),
    VoiceState.HANDOFF: frozenset(
        {VoiceState.SPEAKING, VoiceState.LISTENING, VoiceState.ERROR, VoiceState.ENDED}
    ),
    VoiceState.ERROR: frozenset({VoiceState.ENDED}),
    VoiceState.ENDED: frozenset(),  # terminal
}


class VoiceTransition(GatewayEvent):
    """One validated state change (typed event; immutable)."""

    session_id: str
    from_state: VoiceState
    to_state: VoiceState
    reason: TransitionReason
    sequence: int
    timestamp: float = Field(default_factory=time.time)


class VoiceStateMachine:
    """Owns transition rules for one voice session (single task orchestrates)."""

    __slots__ = ("_session_id", "_state", "_previous", "_history", "_sequence")

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self._state = VoiceState.IDLE
        self._previous: VoiceState | None = None
        self._history: list[VoiceTransition] = []
        self._sequence = 0

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def state(self) -> VoiceState:
        return self._state

    @property
    def previous_state(self) -> VoiceState | None:
        return self._previous

    @property
    def is_terminal(self) -> bool:
        return self._state is VoiceState.ENDED

    def allowed_targets(self) -> frozenset[VoiceState]:
        return VALID_TRANSITIONS[self._state]

    def transition(self, target: VoiceState, reason: TransitionReason) -> VoiceTransition:
        """Validate & apply one transition; raises InvalidTransitionError."""
        if target not in VALID_TRANSITIONS[self._state]:
            raise InvalidTransitionError(self._session_id, self._state, target)
        self._previous = self._state
        self._state = target
        self._sequence += 1
        record = VoiceTransition(
            session_id=self._session_id,
            from_state=self._previous,
            to_state=target,
            reason=reason,
            sequence=self._sequence,
        )
        self._history.append(record)
        return record

    def try_transition(
        self, target: VoiceState, reason: TransitionReason
    ) -> VoiceTransition | None:
        """Transition that returns None instead of raising (teardown paths only)."""
        try:
            return self.transition(target, reason)
        except InvalidTransitionError:
            return None

    def fail(self, reason: TransitionReason) -> VoiceTransition | None:
        """Move to ERROR when valid; no-op returns None (e.g. already ENDED/ERROR)."""
        return self.try_transition(VoiceState.ERROR, reason)

    def end(self, reason: TransitionReason) -> VoiceTransition | None:
        """Guaranteed path to ENDED from any non-terminal state (cleanup invariant)."""
        if self._state is VoiceState.ENDED:
            return None
        return self.transition(VoiceState.ENDED, reason)

    @property
    def history(self) -> tuple[VoiceTransition, ...]:
        return tuple(self._history)

    def snapshot(self) -> dict[str, object]:
        """Compact dict for metrics/logs (state names only — never payload data)."""
        return {
            "session_id": self._session_id,
            "state": self._state.value,
            "previous_state": self._previous.value if self._previous else None,
            "transitions": self._sequence,
        }
