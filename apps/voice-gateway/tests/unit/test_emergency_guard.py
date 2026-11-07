"""Unit tests for the emergency guard (Day 5).

The guard is deterministic and runs before any LLM call. Tests cover positive
triggers, natural-language variants, emergency + administrative requests,
near-misses, negation, empty/malformed input, and the fixed script.
"""

from __future__ import annotations

from app.services.safety.emergency_guard import (
    EMERGENCY_SCRIPT,
    emergency_guard,
    rule_ids,
)


class TestEmergencyGuard:
    def test_emergency_script_constant(self) -> None:
        assert EMERGENCY_SCRIPT == (
            "This may be a medical emergency. Please hang up and call 999 now."
        )

    def test_rule_ids_registered(self) -> None:
        ids = rule_ids()
        assert len(ids) > 0
        assert all(isinstance(rid, str) for rid in ids)
        assert "emergency-chest-pain" in ids
        assert "emergency-shortness-of-breath" in ids

    def test_chest_pain_trigger(self) -> None:
        result = emergency_guard("I have chest pain")
        assert result.matched is True
        assert result.rule_id == "emergency-chest-pain"
        assert result.response_text == EMERGENCY_SCRIPT

    def test_shortness_of_breath_trigger(self) -> None:
        result = emergency_guard("I can't breathe properly")
        assert result.matched is True
        assert result.rule_id == "emergency-shortness-of-breath"

    def test_serious_bleeding_trigger(self) -> None:
        result = emergency_guard("There's serious bleeding from my arm")
        assert result.matched is True
        assert result.rule_id == "emergency-serious-bleeding"

    def test_mental_health_crisis_trigger(self) -> None:
        result = emergency_guard("I'm having a mental health crisis")
        assert result.matched is True
        assert result.rule_id == "emergency-mental-health-crisis"

    def test_stroke_trigger(self) -> None:
        result = emergency_guard("I think I'm having a stroke")
        assert result.matched is True
        assert result.rule_id == "emergency-stroke"

    def test_heart_attack_trigger(self) -> None:
        result = emergency_guard("I'm having a heart attack")
        assert result.matched is True
        assert result.rule_id == "emergency-heart-attack"

    def test_call_999_trigger(self) -> None:
        result = emergency_guard("I need to call 999")
        assert result.matched is True
        assert result.rule_id == "emergency-call-999"

    def test_emergency_plus_administrative(self) -> None:
        """Emergency takes precedence over administrative requests."""
        result = emergency_guard("I have chest pain and I need an appointment")
        assert result.matched is True
        assert result.rule_id is not None

    def test_near_miss_no_match(self) -> None:
        """Chest tightness without 'pain' should not match chest-pain rule."""
        result = emergency_guard("my chest feels tight")
        assert result.matched is False

    def test_negation_still_matches_emergency(self) -> None:
        """Negation must not suppress a genuine emergency match."""
        result = emergency_guard("I don't have chest pain but I'm worried")
        # "chest pain" is still present as a substring, so it matches
        assert result.matched is True

    def test_empty_transcript(self) -> None:
        result = emergency_guard("")
        assert result.matched is False
        assert result.rule_id is None

    def test_whitespace_transcript(self) -> None:
        result = emergency_guard("   ")
        assert result.matched is False

    def test_normal_utterance_no_match(self) -> None:
        result = emergency_guard("I'd like to book an appointment")
        assert result.matched is False
        assert result.rule_id is None
        assert result.response_text is None

    def test_fixed_script_returned(self) -> None:
        result = emergency_guard("chest pain")
        assert result.response_text == EMERGENCY_SCRIPT
        assert "999" in result.response_text
