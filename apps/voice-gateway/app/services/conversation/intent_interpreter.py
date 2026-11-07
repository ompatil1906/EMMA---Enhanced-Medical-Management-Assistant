"""Intent interpretation service (Day 5).

Orchestrates: emergency guard → LLM provider → structured-output validation.
The LLM is an interpreter only — this service never lets it change FSM state
or execute backend actions.
"""

from __future__ import annotations

from app.domain.llm import InterpretationResult
from app.integrations.llm.base import LLMProvider, LLMRequest
from app.services.safety.emergency_guard import EMERGENCY_SCRIPT, emergency_guard


class IntentInterpretationError(Exception):
    """Raised when interpretation cannot produce a usable result."""

    def __init__(
        self,
        message: str,
        *,
        emergency: bool = False,
        emergency_script: str | None = None,
    ) -> None:
        super().__init__(message)
        self.emergency = emergency
        self.emergency_script = emergency_script


class IntentInterpreter:
    """Runs emergency guard, then LLM interpretation, then Pydantic validation."""

    def __init__(
        self,
        llm_provider: LLMProvider,
        *,
        model: str,
        timeout_seconds: float,
        max_output_tokens: int,
        prompt_version: str,
        correlation_id: str,
    ) -> None:
        self._provider = llm_provider
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_output_tokens = max_output_tokens
        self._prompt_version = prompt_version
        self._correlation_id = correlation_id

    async def interpret(self, transcript: str, session_id: str) -> InterpretationResult:
        """Interpret one final transcript.

        Raises `IntentInterpretationError` with `emergency=True` when the
        emergency guard matches — the caller must never reach the LLM.
        """
        # --- emergency guard (pre-LLM, always) -----------------------------
        guard = emergency_guard(transcript)
        if guard.matched:
            raise IntentInterpretationError(
                f"emergency guard matched: {guard.rule_id}",
                emergency=True,
                emergency_script=guard.response_text or EMERGENCY_SCRIPT,
            )

        # --- LLM interpretation --------------------------------------------
        request = LLMRequest(
            transcript=transcript,
            session_id=session_id,
            prompt_version=self._prompt_version,
            model=self._model,
            timeout_seconds=self._timeout_seconds,
            max_output_tokens=self._max_output_tokens,
            correlation_id=self._correlation_id,
        )
        try:
            response = await self._provider.interpret(request)
        except Exception as exc:  # noqa: BLE001 — controlled failure below
            raise IntentInterpretationError(f"LLM provider failed: {type(exc).__name__}") from exc

        # --- structured-output validation ----------------------------------
        if response.parsed is not None:
            return response.parsed

        # Malformed or invalid output → safe fallback, never a guess.
        raise IntentInterpretationError(
            "LLM output failed validation; no structured interpretation available"
        )


__all__ = ["IntentInterpreter", "IntentInterpretationError"]
