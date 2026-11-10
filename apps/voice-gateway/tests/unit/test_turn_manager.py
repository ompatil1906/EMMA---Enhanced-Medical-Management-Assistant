"""Day 20: user-turn assembly (hold / merge / drop / release).

These tests pin the deterministic turn-stop policy that sits between streaming
STT and the conversation controller. The two failure modes they exist to prevent
are (a) answering half a sentence, and (b) adding latency to complete utterances.
"""

from __future__ import annotations

import pytest

from app.services.conversation.turn_manager import (
    TurnAction,
    TurnAssembler,
    TurnManagerConfig,
    is_filler_only,
    looks_incomplete,
    normalise_text,
)

NOW = 10_000


def _assembler(**overrides: object) -> TurnAssembler:
    return TurnAssembler(TurnManagerConfig(**overrides))  # type: ignore[arg-type]


# --- pure shape helpers ---------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["um", "uh", "er", "hmm", "uh huh", "mm hmm", "   ", ""],
)
def test_filler_only_is_dropped(text: str) -> None:
    assert is_filler_only(text)


@pytest.mark.parametrize("text", ["yes", "Tuesday", "I need an appointment", "thanks"])
def test_content_is_not_filler(text: str) -> None:
    assert not is_filler_only(text)


@pytest.mark.parametrize(
    "text",
    [
        "I would like to book an appointment for",
        "I need a repeat prescription and",
        "Can you tell me about the",
        "I want to change my appointment, ",
        "so my appointment is on Tuesday but",
        "hold on",
    ],
)
def test_incomplete_shapes_are_recognised(text: str) -> None:
    assert looks_incomplete(text)


@pytest.mark.parametrize(
    "text",
    [
        "yes",
        "no",
        "Tuesday",
        "morning",
        "I need an appointment on Tuesday morning.",
        "What are your opening hours?",
        "Cancel my appointment",
        "3pm",
    ],
)
def test_complete_utterances_are_not_held(text: str) -> None:
    assert not looks_incomplete(text)


def test_normalise_text_is_comparison_only() -> None:
    assert normalise_text("  Hello, World!  ") == "hello world"
    assert normalise_text("I'd like it.") == "i'd like it"


# --- assembler decisions ---------------------------------------------------


def test_disabled_assembler_passes_everything_through() -> None:
    assembler = _assembler(enabled=False)
    decision = assembler.note_final("um", now_ms=NOW)
    assert decision.action is TurnAction.PROCESS


def test_filler_final_is_dropped() -> None:
    assembler = _assembler()
    decision = assembler.note_final("um", now_ms=NOW)
    assert decision.action is TurnAction.DROP
    assert decision.reason == "filler_only"


def test_duplicate_final_is_dropped_inside_the_window() -> None:
    assembler = _assembler()
    first = assembler.note_final("I need an appointment on Tuesday", now_ms=NOW)
    assert first.action is TurnAction.PROCESS
    duplicate = assembler.note_final("I need an appointment on Tuesday.", now_ms=NOW + 400)
    assert duplicate.action is TurnAction.DROP
    assert duplicate.reason == "duplicate_final"


def test_duplicate_final_is_kept_after_the_window() -> None:
    assembler = _assembler()
    assembler.note_final("I need an appointment on Tuesday", now_ms=NOW)
    later = assembler.note_final("I need an appointment on Tuesday", now_ms=NOW + 5000)
    assert later.action is TurnAction.PROCESS


def test_complete_final_without_partials_is_never_held() -> None:
    assembler = _assembler()
    decision = assembler.note_final("I need an appointment on Tuesday morning", now_ms=NOW)
    assert decision.action is TurnAction.PROCESS
    assert decision.reason == "complete"


def test_incomplete_final_with_live_partials_is_held_then_merged() -> None:
    assembler = _assembler()
    assembler.note_partial("I would like to book an appointment for", now_ms=NOW - 200)
    held = assembler.note_final("I would like to book an appointment for", now_ms=NOW)
    assert held.action is TurnAction.HOLD
    assert assembler.held is True

    merged = assembler.note_final("Tuesday morning", now_ms=NOW + 500)
    assert merged.action is TurnAction.PROCESS
    assert merged.reason == "merged_continuation"
    assert merged.text == "I would like to book an appointment for Tuesday morning"
    assert assembler.held is False


def test_hold_is_released_when_the_caller_stops_talking() -> None:
    assembler = _assembler(hold_ms=600)
    assembler.note_partial("I need to change my appointment", now_ms=NOW - 100)
    assert assembler.note_final("I need to change my appointment and", now_ms=NOW).action is (
        TurnAction.HOLD
    )
    assert assembler.release(now_ms=NOW + 200) is None
    released = assembler.release(now_ms=NOW + 700)
    assert released == "I need to change my appointment and"
    assert assembler.held is False


def test_hold_is_capped_by_max_hold() -> None:
    assembler = _assembler(hold_ms=5_000, max_hold_ms=1_000)
    assembler.note_partial("I need a repeat prescription", now_ms=NOW)
    assembler.note_final("I need a repeat prescription and", now_ms=NOW)
    assert assembler.release(now_ms=NOW + 900) is None
    assert assembler.release(now_ms=NOW + 1_050) is not None


def test_new_request_does_not_merge_with_a_held_fragment() -> None:
    assembler = _assembler()
    assembler.note_partial("I need to change my appointment", now_ms=NOW - 100)
    assembler.note_final("I need to change my appointment and", now_ms=NOW)
    decision = assembler.note_final("I need a repeat prescription", now_ms=NOW + 300)
    assert decision.action is TurnAction.PROCESS
    assert decision.reason == "released_hold_then_new_turn"
    assert assembler.pending_release() == "I need to change my appointment and"
    assert decision.text == "I need a repeat prescription"


def test_stale_partials_do_not_cause_a_hold() -> None:
    assembler = _assembler(partial_freshness_ms=500)
    assembler.note_partial("I would like to book for", now_ms=NOW - 5_000)
    decision = assembler.note_final("I would like to book for", now_ms=NOW)
    assert decision.action is TurnAction.PROCESS


def test_slot_answer_is_not_held_even_with_partials() -> None:
    assembler = _assembler()
    assembler.note_partial("tuesday", now_ms=NOW - 100)
    decision = assembler.note_final("Tuesday", now_ms=NOW)
    assert decision.action is TurnAction.PROCESS
