"""User-turn assembly and interruption policy (Day 20).

Pipecat separates *user turn stop* into explicit strategies (speech timeout,
deferred finalisation, semantic turn analysers, eager matching). EMMA does not
run a frame bus, so this module implements the same decisions as one small,
deterministic and fully unit-testable component that sits between streaming STT
events and the conversation controller.

What it decides, per final transcript:

* **drop**   — filler-only or duplicate finals (STT double-finals are common);
* **hold**   — a final that still looks incomplete *and* arrived while partials
  were still streaming; it is merged with the next final instead of triggering a
  reply to half a sentence;
* **process** — everything else, immediately.

Design rules (deliberate, safety-first):

* Deterministic and local. No paid turn-classifier API: the signals are the
  caller's own words plus STT streaming evidence (partial-prefix + recency).
* Never holds a slot answer, a confirmation, or anything that is a complete
  utterance by shape — holding those would add latency for no benefit.
* Bounded: a hold is capped by ``max_hold_ms`` and released by the pipeline, so
  a caller who simply stops talking is answered without waiting forever.
* Nothing here changes conversation state; the controller and FSM stay the
  single authority for actions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

_SENTENCE_END = re.compile(r"[.!?…]$")
_TRAILING_PUNCT = re.compile(r"[\s,;:.\-–—]+$")
_MULTI_SPACE = re.compile(r"\s+")
_WORD = re.compile(r"[a-z0-9']+")

# Words that end a *clause*, not a turn. Kept short and literal: they only ever
# trigger a hold when STT partials show the caller was mid-stream.
_CONTINUATION_WORDS = frozenset(
    {
        "and",
        "but",
        "or",
        "so",
        "because",
        "also",
        "plus",
        "then",
        "if",
        "when",
        "while",
        "with",
        "for",
        "to",
        "of",
        "at",
        "on",
        "in",
        "my",
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "i",
        "im",
        "i'm",
        "um",
        "uh",
        "erm",
        "er",
        "hmm",
        "like",
        "just",
        "about",
        "that",
        "which",
    }
)

# Phrases that explicitly signal "I am still talking" even mid-sentence.
_CONTINUATION_PHRASES = (
    "i mean",
    "let me",
    "hold on",
    "wait a",
    "sorry i",
    "actually i",
    "and also",
    "one more",
    "the thing is",
)

# Fillers alone are not a turn.
_FILLERS = frozenset(
    {
        "um",
        "umm",
        "uh",
        "uhh",
        "erm",
        "er",
        "err",
        "hmm",
        "hm",
        "mm",
        "mhm",
        "mmhmm",
        "ah",
        "ahh",
        "oh",
        "eh",
        "huh",
        "like",
        "so",
    }
)

# Complete single-word turns: never held, never treated as fillers.
_COMPLETE_SINGLE_WORDS = frozenset(
    {
        "yes",
        "yeah",
        "yep",
        "yup",
        "no",
        "nope",
        "nah",
        "correct",
        "confirmed",
        "cancel",
        "sure",
        "ok",
        "okay",
        "right",
        "thanks",
        "hello",
        "hi",
        "bye",
        "goodbye",
        "morning",
        "afternoon",
        "evening",
    }
)

# Slot-shaped answers ("Tuesday", "morning", "3pm") complete a turn by themselves.
_TIME_OR_DATE_WORDS = frozenset(
    {
        "today",
        "tomorrow",
        "tonight",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "morning",
        "afternoon",
        "evening",
        "noon",
        "midday",
        "asap",
    }
)


class TurnAction(StrEnum):
    """What the pipeline should do with one final transcript."""

    PROCESS = "process"
    HOLD = "hold"
    DROP = "drop"


@dataclass(frozen=True, slots=True)
class TurnDecision:
    """One assembler decision plus the reason (logs/metrics only, never spoken)."""

    action: TurnAction
    text: str
    reason: str


@dataclass(frozen=True, slots=True)
class TurnManagerConfig:
    """Tunables (all local; no provider dependency)."""

    enabled: bool = True
    # How long an incomplete-looking final is held waiting for its continuation.
    hold_ms: int = 700
    # A held fragment is only merged into a final that arrives within this window.
    merge_window_ms: int = 2500
    # Absolute cap: total time a fragment may be held (hold + release).
    max_hold_ms: int = 4000
    # Two finals with the same normalised text closer than this are one final.
    dedupe_window_ms: int = 2000
    # Partials must have been seen this recently for a hold to be considered.
    partial_freshness_ms: int = 2000
    # Shortest utterance (in words) that may be held.
    min_words_for_hold: int = 2


def normalise_text(text: str) -> str:
    """Lower-cased, punctuation-collapsed form used for comparisons only."""
    lowered = text.strip().lower()
    lowered = _TRAILING_PUNCT.sub("", lowered)
    lowered = re.sub(r"[^a-z0-9' ]+", " ", lowered)
    return _MULTI_SPACE.sub(" ", lowered).strip()


def is_filler_only(text: str) -> bool:
    """True when the utterance carries no content ("um", "uh huh", "hmm")."""
    words = _WORD.findall(normalise_text(text))
    if not words:
        return True
    return all(word in _FILLERS for word in words)


def looks_incomplete(text: str, *, min_words: int = 2) -> bool:
    """Shape-based test for "the caller has not finished this sentence yet".

    Conservative by design: a complete sentence, a question, a confirmation, or a
    slot answer is never considered incomplete. The caller of this function
    additionally requires fresh STT partials before holding, so ordinary short
    utterances are answered immediately.
    """
    stripped = text.strip()
    if not stripped:
        return True
    lowered = normalise_text(stripped)
    words = lowered.split()
    if not words:
        return True

    # A finished sentence or a question is complete, always.
    if _SENTENCE_END.search(stripped) or stripped.endswith("?"):
        return False
    if len(words) == 1:
        word = words[0]
        if word in _COMPLETE_SINGLE_WORDS or word in _TIME_OR_DATE_WORDS:
            return False
        if re.fullmatch(r"\d{1,2}(?::\d{2})?\s*(?:am|pm)?", word):
            return False
        if len(word) < 3:
            return False
        return words[0] not in _COMPLETE_SINGLE_WORDS

    if len(words) < min_words:
        return False

    if any(phrase in lowered for phrase in _CONTINUATION_PHRASES):
        return True
    if stripped.endswith(",") or stripped.endswith("…") or stripped.endswith("..."):
        return True
    return words[-1] in _CONTINUATION_WORDS


class TurnAssembler:
    """Per-session turn assembly state (deterministic; time is injected)."""

    def __init__(self, config: TurnManagerConfig | None = None) -> None:
        self._config = config or TurnManagerConfig()
        self._partial_text: str = ""
        self._partial_at_ms: int | None = None
        self._held_text: str | None = None
        self._held_at_ms: int | None = None
        self._held_first_at_ms: int | None = None
        self._last_final_norm: str | None = None
        self._last_final_at_ms: int | None = None
        self._pending_release: str | None = None
        self.held = False

    @property
    def config(self) -> TurnManagerConfig:
        return self._config

    # --- streaming evidence -------------------------------------------------

    def note_partial(self, text: str, *, now_ms: int) -> None:
        """Record the latest partial (used as "the caller is still talking")."""
        if text.strip():
            self._partial_text = text.strip()
            self._partial_at_ms = now_ms

    def _partial_is_fresh(self, now_ms: int) -> bool:
        if self._partial_at_ms is None:
            return False
        return (now_ms - self._partial_at_ms) <= self._config.partial_freshness_ms

    def _partial_matches(self, text: str) -> bool:
        """True when the partial stream is consistent with this final."""
        if not self._partial_text:
            return False
        partial = normalise_text(self._partial_text)
        final = normalise_text(text)
        if not partial or not final:
            return True
        return final.startswith(partial[:24]) or partial.startswith(final[:24])

    # --- final decisions ----------------------------------------------------

    def note_final(
        self,
        text: str,
        *,
        now_ms: int,
        confidence: float | None = None,
        deduplicate: bool = True,
    ) -> TurnDecision:
        """Decide what to do with one final transcript.

        `deduplicate=False` is used when the assistant has replied since the
        previous final: an identical repeat then is a *new* caller turn ("yes"
        twice) rather than an STT double-final.
        """
        cleaned = _MULTI_SPACE.sub(" ", text.strip())
        norm = normalise_text(cleaned)

        if not self._config.enabled:
            return TurnDecision(TurnAction.PROCESS, cleaned, "disabled")

        if is_filler_only(cleaned):
            return TurnDecision(TurnAction.DROP, "", "filler_only")

        # Duplicate final (STT often emits the same final twice).
        if (
            deduplicate
            and self._last_final_norm is not None
            and norm == self._last_final_norm
            and self._last_final_at_ms is not None
            and (now_ms - self._last_final_at_ms) <= self._config.dedupe_window_ms
        ):
            return TurnDecision(TurnAction.DROP, "", "duplicate_final")

        # Merge with a held fragment (the caller continued the same sentence).
        if self._held_text is not None and self._held_at_ms is not None:
            within = (now_ms - self._held_at_ms) <= self._config.merge_window_ms
            if within and not self._looks_like_new_turn(cleaned):
                merged = f"{self._held_text.rstrip(',;: ')} {cleaned.lstrip(',;: ')}"
                self._clear_held()
                self._remember_final(merged, now_ms)
                return TurnDecision(TurnAction.PROCESS, merged, "merged_continuation")
            # Too late / clearly a new turn: release the fragment as its own turn.
            released = self._take_held()
            self._remember_final(cleaned, now_ms)
            if released:
                self._pending_release = released  # consumed by the pipeline
                return TurnDecision(TurnAction.PROCESS, cleaned, "released_hold_then_new_turn")
            return TurnDecision(TurnAction.PROCESS, cleaned, "hold_expired")

        # Hold a final that is still mid-sentence, only when STT shows a live
        # partial stream consistent with it. This is what keeps the corpus and
        # single-shot transports (which send no partials) latency-free.
        if (
            self._partial_is_fresh(now_ms)
            and self._partial_matches(cleaned)
            and looks_incomplete(cleaned, min_words=self._config.min_words_for_hold)
            and len(cleaned.split()) >= self._config.min_words_for_hold
        ):
            self._held_text = cleaned
            self._held_at_ms = now_ms
            self._held_first_at_ms = self._held_first_at_ms or now_ms
            self.held = True
            return TurnDecision(TurnAction.HOLD, cleaned, "incomplete_utterance")

        self._remember_final(cleaned, now_ms)
        return TurnDecision(TurnAction.PROCESS, cleaned, "complete")

    @staticmethod
    def _looks_like_new_turn(text: str) -> bool:
        """True when the text opens a new request rather than continuing one."""
        lowered = normalise_text(text)
        openers = (
            "i want",
            "i need",
            "i would like",
            "i'd like",
            "can i",
            "could i",
            "please",
            "book",
            "cancel",
            "reschedule",
            "hello",
            "hi ",
            "thanks",
            "one more thing",
            "another thing",
        )
        return any(lowered.startswith(opener) for opener in openers)

    # --- hold release -------------------------------------------------------

    def release(self, *, now_ms: int) -> str | None:
        """Return the held fragment if its hold window has elapsed (else None)."""
        if self._held_text is None or self._held_at_ms is None:
            return None
        waited = now_ms - self._held_at_ms
        total = now_ms - (self._held_first_at_ms or self._held_at_ms)
        if waited >= self._config.hold_ms or total >= self._config.max_hold_ms:
            text = self._take_held()
            if text:
                self._remember_final(text, now_ms)
            return text
        return None

    def pending_release(self) -> str | None:
        """A fragment released by the previous `note_final` call (one-shot)."""
        released = self._pending_release
        self._pending_release = None
        return released

    # --- internals ----------------------------------------------------------

    def _remember_final(self, text: str, now_ms: int) -> None:
        self._last_final_norm = normalise_text(text)
        self._last_final_at_ms = now_ms
        self._partial_text = ""
        self._partial_at_ms = None

    def _clear_held(self) -> None:
        self._held_text = None
        self._held_at_ms = None
        self._held_first_at_ms = None
        self.held = False

    def _take_held(self) -> str | None:
        text = self._held_text
        self._clear_held()
        return text


__all__ = [
    "TurnAction",
    "TurnAssembler",
    "TurnDecision",
    "TurnManagerConfig",
    "is_filler_only",
    "looks_incomplete",
    "normalise_text",
]
