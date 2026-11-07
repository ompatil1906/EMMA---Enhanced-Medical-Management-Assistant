"""Voice FSM unit tests — explicit transition table (assignment §7)."""

from __future__ import annotations

import pytest

from app.domain.fsm import (
    InvalidTransitionError,
    TransitionReason,
    VoiceState,
    VoiceStateMachine,
)

_REASONS = TransitionReason


class TestHappyPath:
    def test_full_conversation_lifecycle(self) -> None:
        fsm = VoiceStateMachine("sess-1")
        assert fsm.state is VoiceState.IDLE
        fsm.transition(VoiceState.CONNECTING, _REASONS.CALL_ACCEPTED)
        fsm.transition(VoiceState.SPEAKING, _REASONS.GREETING_STARTED)
        fsm.transition(VoiceState.LISTENING, _REASONS.GREETING_COMPLETED)
        fsm.transition(VoiceState.PROCESSING, _REASONS.UTTERANCE_RECEIVED)
        fsm.transition(VoiceState.SPEAKING, _REASONS.RESPONSE_STARTED)
        fsm.transition(VoiceState.LISTENING, _REASONS.RESPONSE_COMPLETED)
        fsm.end(_REASONS.CALL_ENDED)
        assert fsm.state is VoiceState.ENDED
        assert fsm.is_terminal
        assert len(fsm.history) == 7

    def test_transitions_are_typed_events(self) -> None:
        fsm = VoiceStateMachine("sess-2")
        record = fsm.transition(VoiceState.CONNECTING, _REASONS.CALL_ACCEPTED)
        assert record.session_id == "sess-2"
        assert record.from_state is VoiceState.IDLE
        assert record.to_state is VoiceState.CONNECTING
        assert record.reason is _REASONS.CALL_ACCEPTED
        assert record.sequence == 1
        assert record.timestamp > 0

    def test_previous_state_tracked(self) -> None:
        fsm = VoiceStateMachine("sess-3")
        fsm.transition(VoiceState.CONNECTING, _REASONS.CALL_ACCEPTED)
        assert fsm.previous_state is VoiceState.IDLE
        fsm.transition(VoiceState.SPEAKING, _REASONS.GREETING_STARTED)
        assert fsm.previous_state is VoiceState.CONNECTING


class TestInvalidTransitions:
    def test_listening_from_idle_rejected(self) -> None:
        fsm = VoiceStateMachine("x")
        with pytest.raises(InvalidTransitionError) as excinfo:
            fsm.transition(VoiceState.LISTENING, _REASONS.LISTENING_STARTED)
        message = str(excinfo.value)
        assert "idle" in message and "listening" in message
        assert fsm.state is VoiceState.IDLE  # unchanged after rejection

    def test_speaking_to_processing_rejected(self) -> None:
        fsm = VoiceStateMachine("x")
        fsm.transition(VoiceState.CONNECTING, _REASONS.CALL_ACCEPTED)
        fsm.transition(VoiceState.SPEAKING, _REASONS.GREETING_STARTED)
        with pytest.raises(InvalidTransitionError):
            fsm.transition(VoiceState.PROCESSING, _REASONS.UTTERANCE_RECEIVED)

    def test_ended_is_terminal(self) -> None:
        fsm = VoiceStateMachine("x")
        fsm.transition(VoiceState.CONNECTING, _REASONS.CALL_ACCEPTED)
        fsm.end(_REASONS.CALL_ENDED)
        with pytest.raises(InvalidTransitionError):
            fsm.transition(VoiceState.LISTENING, _REASONS.LISTENING_STARTED)
        assert fsm.end(_REASONS.CLEANUP) is None  # idempotent terminal state

    def test_barge_in_absent_by_design(self) -> None:
        # No SPEAKING→PROCESSING edge: no barge-in on Day 3 (documented policy).
        fsm = VoiceStateMachine("x")
        fsm.transition(VoiceState.CONNECTING, _REASONS.CALL_ACCEPTED)
        fsm.transition(VoiceState.SPEAKING, _REASONS.GREETING_STARTED)
        with pytest.raises(InvalidTransitionError):
            fsm.transition(VoiceState.PROCESSING, _REASONS.UTTERANCE_RECEIVED)


class TestErrorAndCleanup:
    def test_provider_failure_moves_to_error_then_ended(self) -> None:
        fsm = VoiceStateMachine("x")
        fsm.transition(VoiceState.CONNECTING, _REASONS.CALL_ACCEPTED)
        fsm.transition(VoiceState.LISTENING, _REASONS.LISTENING_STARTED)
        record = fsm.fail(_REASONS.PROVIDER_ERROR)
        assert record is not None and record.to_state is VoiceState.ERROR
        fsm.end(_REASONS.CLEANUP)
        assert fsm.state is VoiceState.ENDED

    def test_fail_from_terminal_is_noop(self) -> None:
        fsm = VoiceStateMachine("x")
        fsm.transition(VoiceState.CONNECTING, _REASONS.CALL_ACCEPTED)
        fsm.end(_REASONS.CALL_ENDED)
        assert fsm.fail(_REASONS.PROVIDER_ERROR) is None

    def test_fail_from_speaking_allowed(self) -> None:
        fsm = VoiceStateMachine("x")
        fsm.transition(VoiceState.CONNECTING, _REASONS.CALL_ACCEPTED)
        fsm.transition(VoiceState.SPEAKING, _REASONS.GREETING_STARTED)
        assert fsm.fail(_REASONS.INTERNAL_ERROR) is not None
        assert fsm.state is VoiceState.ERROR

    def test_end_reachable_from_every_operational_state(self) -> None:
        for entry in ("CONNECTING", "LISTENING", "PROCESSING", "SPEAKING", "HANDOFF"):
            fsm = VoiceStateMachine("x")
            # any of these states is reachable via a short valid path
            for target in pipeline_to(entry):  # helper below
                fsm.transition(target, _REASONS.UTTERANCE_RECEIVED)
            assert fsm.end(_REASONS.CLEANUP) is not None
            assert fsm.state is VoiceState.ENDED

    def test_try_transition_returns_none_silently(self) -> None:
        fsm = VoiceStateMachine("x")
        assert fsm.try_transition(VoiceState.ERROR, _REASONS.SHUTDOWN) is None


def pipeline_to(state_name: str) -> list[VoiceState]:
    """Shortest valid state sequences reaching the requested state (excluding IDLE)."""
    paths = {
        "CONNECTING": [VoiceState.CONNECTING],
        "LISTENING": [VoiceState.CONNECTING, VoiceState.LISTENING],
        "PROCESSING": [VoiceState.CONNECTING, VoiceState.LISTENING, VoiceState.PROCESSING],
        "SPEAKING": [VoiceState.CONNECTING, VoiceState.SPEAKING],
        "HANDOFF": [
            VoiceState.CONNECTING,
            VoiceState.LISTENING,
            VoiceState.PROCESSING,
            VoiceState.HANDOFF,
        ],
    }
    return paths[state_name]
