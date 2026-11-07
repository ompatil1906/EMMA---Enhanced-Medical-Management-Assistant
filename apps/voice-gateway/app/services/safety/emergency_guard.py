"""Deterministic pre-LLM emergency guard (Day 5).

Runs **before** every LLM interpretation call. Phrase-based, auditable, and
independent of the LLM. No LLM is consulted for emergency detection.

Design rules (from Day 1 product research):

* The guard is the outermost ring at every turn — emergency always wins.
* On match: no LLM call, no administrative submission, no symptom questions,
  no diagnosis/triage. Deliver the fixed emergency script.
* The fixed script is centrally managed — never generated or paraphrased by
  the LLM.
* Script: "This may be a medical emergency. Please hang up and call 999 now."
* Recall is measured only against the defined synthetic test corpus — not
  claimed as 100% universal or clinically validated.
* This is NOT clinical triage — it is a deterministic safety stop.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EmergencyRule:
    """One auditable emergency detection rule."""

    rule_id: str
    description: str
    trigger_patterns: tuple[str, ...]
    documentation_reference: str
    response_action: str = "deliver_fixed_emergency_script"


@dataclass(frozen=True)
class EmergencyGuardResult:
    """Result of running the emergency guard on one transcript."""

    matched: bool
    rule_id: str | None = None
    rule_description: str | None = None
    matched_pattern: str | None = None
    response_text: str | None = None


# Fixed emergency script — centrally managed, never LLM-generated.
EMERGENCY_SCRIPT = "This may be a medical emergency. Please hang up and call 999 now."

# Documented emergency red-flag triggers (Day 1 product research, W8 section).
# Patterns are lowercased substrings matched against the lowercased transcript.
EMERGENCY_RULES: tuple[EmergencyRule, ...] = (
    EmergencyRule(
        rule_id="emergency-chest-pain",
        description="Caller reports chest pain — possible cardiac emergency.",
        trigger_patterns=("chest pain", "chest pains", "pain in my chest"),
        documentation_reference="EMMA_PRODUCT_ANALYSIS.md W8 Emergency red-flag",
    ),
    EmergencyRule(
        rule_id="emergency-shortness-of-breath",
        description="Caller reports difficulty breathing — possible respiratory emergency.",
        trigger_patterns=(
            "shortness of breath",
            "can't breathe",
            "cannot breathe",
            "difficulty breathing",
            "struggling to breathe",
        ),
        documentation_reference="EMMA_PRODUCT_ANALYSIS.md W8 Emergency red-flag",
    ),
    EmergencyRule(
        rule_id="emergency-serious-bleeding",
        description="Caller reports serious bleeding — possible haemorrhage.",
        trigger_patterns=("serious bleeding", "heavy bleeding", "bleeding heavily"),
        documentation_reference="EMMA_PRODUCT_ANALYSIS.md W8 Emergency red-flag",
    ),
    EmergencyRule(
        rule_id="emergency-mental-health-crisis",
        description="Caller reports a mental-health crisis — possible psychiatric emergency.",
        trigger_patterns=("mental health crisis", "mental-health crisis", "suicidal"),
        documentation_reference="EMMA_PRODUCT_ANALYSIS.md W8 Emergency red-flag",
    ),
    EmergencyRule(
        rule_id="emergency-stroke",
        description="Caller reports stroke symptoms — possible neurological emergency.",
        trigger_patterns=("stroke", "having a stroke", "think i'm having a stroke"),
        documentation_reference="EMMA_PRODUCT_ANALYSIS.md W8 Emergency red-flag",
    ),
    EmergencyRule(
        rule_id="emergency-heart-attack",
        description="Caller reports heart attack symptoms — possible cardiac emergency.",
        trigger_patterns=(
            "heart attack",
            "having a heart attack",
            "think i'm having a heart attack",
        ),
        documentation_reference="EMMA_PRODUCT_ANALYSIS.md W8 Emergency red-flag",
    ),
    EmergencyRule(
        rule_id="emergency-call-999",
        description="Caller explicitly references 999 or emergency services.",
        trigger_patterns=("call 999", "ring 999", "dial 999", "emergency services"),
        documentation_reference="REQUIREMENTS.md F9 Emergency red-flag handling",
    ),
)

_RULES_BY_ID = {rule.rule_id: rule for rule in EMERGENCY_RULES}


def emergency_guard(transcript: str) -> EmergencyGuardResult:
    """Run deterministic emergency detection on one transcript.

    Returns a match result. When matched, the caller receives EMERGENCY_SCRIPT
    and the LLM must NOT be called.
    """
    if not transcript or not transcript.strip():
        return EmergencyGuardResult(matched=False)

    text = transcript.strip().lower()
    for rule in EMERGENCY_RULES:
        for pattern in rule.trigger_patterns:
            if pattern in text:
                return EmergencyGuardResult(
                    matched=True,
                    rule_id=rule.rule_id,
                    rule_description=rule.description,
                    matched_pattern=pattern,
                    response_text=EMERGENCY_SCRIPT,
                )
    return EmergencyGuardResult(matched=False)


def rule_ids() -> tuple[str, ...]:
    """Return all registered emergency rule IDs (for auditing/tests)."""
    return tuple(rule.rule_id for rule in EMERGENCY_RULES)


__all__ = [
    "EmergencyRule",
    "EmergencyGuardResult",
    "EMERGENCY_SCRIPT",
    "EMERGENCY_RULES",
    "emergency_guard",
    "rule_ids",
]
