"""Bounded conversation context + deterministic digest (Day 20).

Pipecat's `LLMContextAggregatorPair` grows a message list and auto-summarises
when it gets large. EMMA never replays the transcript to the model (the
controller's state is the source of truth), which is safer — but it also meant a
long call had no compact "what has happened so far" for the model to resolve
references against.

This module adds that missing piece without weakening the rule:

* the context handed to the model is **bounded** (characters, turns, per-turn
  cap) so latency and token cost stay flat as a call gets long;
* older turns collapse into a **deterministic digest** (no model call, no extra
  cost, no hallucination risk) — an LLM summariser can be plugged in later
  behind the same interface;
* nothing here invents facts: the digest only restates utterances the caller or
  EMMA actually produced.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.conversation import ConversationState, Speaker

_CHARS_PER_TOKEN = 4  # conservative English estimate, no tokenizer dependency


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """Hard limits for the context handed to the model."""

    max_chars: int = 1400
    recent_turns: int = 8
    turn_char_cap: int = 180
    digest_after_turns: int = 12
    digest_char_cap: int = 320
    # OFF by default: Day 9 pinned "the full transcript is never replayed to the
    # model". Operators may opt in for long-call reference resolution.
    include_dialogue: bool = False


def estimate_tokens(text: str) -> int:
    """Cheap token estimate (chars/4) for budgeting and diagnostics."""
    return max(1, (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)


class ConversationContextManager:
    """Builds the bounded context string for one interpretation call."""

    def __init__(self, budget: ContextBudget | None = None) -> None:
        self._budget = budget or ContextBudget()

    @property
    def budget(self) -> ContextBudget:
        return self._budget

    def build(
        self,
        conversation: ConversationState,
        *,
        state_lines: list[str] | None = None,
    ) -> str | None:
        """Return the bounded context, or None when there is nothing to add."""
        budget = self._budget
        lines: list[str] = list(state_lines or [])

        if not budget.include_dialogue:
            # Default (Day 9 invariant): server state only — no transcript replay.
            context = "\n".join(lines)
            if not context:
                return None
            return context[: budget.max_chars]

        turns = [turn for turn in conversation.turns if turn.content.strip()]
        if len(turns) > budget.digest_after_turns:
            older = turns[: -budget.recent_turns]
            digest = self.digest(older)
            if digest:
                lines.append(f"earlier_digest: {digest}")

        recent = turns[-budget.recent_turns :]
        if recent:
            lines.append("recent_dialogue:")
            for turn in recent:
                speaker = "caller" if turn.speaker == Speaker.CALLER else "emma"
                text = " ".join(turn.content.split())[: budget.turn_char_cap]
                lines.append(f"- {speaker}: {text}")

        if not lines:
            return None
        context = "\n".join(lines)
        if len(context) > budget.max_chars:
            # Keep the tail (most recent information) and mark the truncation.
            context = "…" + context[-budget.max_chars :]
        return context

    def digest(self, turns: list[object]) -> str:
        """Deterministic one-line digest of older turns (no model call)."""
        parts: list[str] = []
        for turn in turns:
            speaker = getattr(turn, "speaker", None)
            content = " ".join(str(getattr(turn, "content", "")).split())
            if not content:
                continue
            label = "caller" if speaker == Speaker.CALLER else "emma"
            snippet = content[:60]
            parts.append(f"{label}:{snippet}")
        if not parts:
            return ""
        joined = " | ".join(parts)
        if len(joined) > self._budget.digest_char_cap:
            joined = joined[: self._budget.digest_char_cap - 1].rstrip() + "…"
        return joined


__all__ = ["ContextBudget", "ConversationContextManager", "estimate_tokens"]
