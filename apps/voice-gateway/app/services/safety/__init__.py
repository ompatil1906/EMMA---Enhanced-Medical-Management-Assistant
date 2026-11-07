"""Emergency safety services (Day 5).

Deterministic pre-LLM emergency guard — runs before any LLM interpretation.
"""

from app.services.safety.emergency_guard import (
    EMERGENCY_RULES,
    EMERGENCY_SCRIPT,
    EmergencyGuardResult,
    EmergencyRule,
    emergency_guard,
    rule_ids,
)

__all__ = [
    "EMERGENCY_SCRIPT",
    "EMERGENCY_RULES",
    "EmergencyGuardResult",
    "EmergencyRule",
    "emergency_guard",
    "rule_ids",
]
