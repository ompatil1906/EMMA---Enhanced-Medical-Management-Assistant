"""Day 20: bounded conversation context and deterministic digest."""

from __future__ import annotations

from app.domain.conversation import (
    ConversationState,
    Speaker,
    TurnStatus,
)
from app.services.conversation.context_manager import (
    ContextBudget,
    ConversationContextManager,
    estimate_tokens,
)


def _state(turns: int) -> ConversationState:
    state = ConversationState()
    for i in range(turns):
        speaker = Speaker.CALLER if i % 2 == 0 else Speaker.ASSISTANT
        state.add_turn(
            turn_id=f"t{i}",
            speaker=speaker,
            content=f"turn {i} content that is reasonably long",
            status=TurnStatus.COMPLETED,
            correlation_id=i,
        )
    return state


def test_estimate_tokens_is_conservative() -> None:
    assert estimate_tokens("") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 400) == 100


def test_short_conversation_has_no_digest() -> None:
    manager = ConversationContextManager(
        ContextBudget(digest_after_turns=12, include_dialogue=True)
    )
    context = manager.build(_state(6))
    assert context is not None
    assert "earlier_digest" not in context
    assert "recent_dialogue:" in context


def test_long_conversation_digests_older_turns() -> None:
    manager = ConversationContextManager(
        ContextBudget(digest_after_turns=10, recent_turns=4, include_dialogue=True)
    )
    context = manager.build(_state(20))
    assert context is not None
    assert "earlier_digest:" in context
    # Only the newest turns are replayed verbatim.
    assert "recent_dialogue:" in context
    tail = context.split("recent_dialogue:", 1)[1]
    assert tail.count("- caller:") + tail.count("- emma:") == 4


def test_context_respects_the_character_budget() -> None:
    manager = ConversationContextManager(
        ContextBudget(max_chars=200, recent_turns=20, include_dialogue=True)
    )
    context = manager.build(_state(40), state_lines=["active_request: appointment-request"])
    assert context is not None
    assert len(context) <= 201  # 200 + the truncation marker
    assert context.endswith("turn 39 content that is reasonably long")


def test_dialogue_is_opt_in_by_default() -> None:
    """Day 9 invariant: the transcript is never replayed unless explicitly enabled."""
    manager = ConversationContextManager()
    context = manager.build(_state(20), state_lines=["awaiting_field: preferred_time"])
    assert context == "awaiting_field: preferred_time"
    assert "recent_dialogue" not in context


def test_empty_conversation_returns_state_only() -> None:
    manager = ConversationContextManager(ContextBudget(include_dialogue=True))
    assert manager.build(ConversationState()) is None
    assert manager.build(ConversationState(), state_lines=["a: b"]) == "a: b"


def test_digest_is_deterministic() -> None:
    manager = ConversationContextManager()
    first = _state(20).turns[:5]
    second = _state(20).turns[:5]
    assert manager.digest(first) == manager.digest(second)
    assert manager.digest(first) != ""
    assert manager.digest([]) == ""


def test_digest_is_capped() -> None:
    manager = ConversationContextManager(ContextBudget(digest_char_cap=40))
    digest = manager.digest(_state(10).turns)
    assert len(digest) <= 40
