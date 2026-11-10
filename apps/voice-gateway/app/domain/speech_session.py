"""Per-voice-session state object (orchestration-level book-keeping).

Extends the transport-level `VoiceSession` with everything the speech pipeline
needs: locked audio format, recognition/synthesis status, bounded transcript
history, pending utterance/response, lifecycle timestamps and provider status.

Data-handling rules (assignment §7/§13):
  * transcript content lives only inside `transcripts` (bounded deque, last
    20 records) and pending-response fields — logs/metrics carry counts and
    lengths, never transcript text;
  * synthesized audio is streamed out in chunks; nothing is stored;
  * `begin_cleanup()` is idempotent via cleanup guard flag per call.
"""

from __future__ import annotations

import time
from collections import deque
from enum import StrEnum
from typing import Final, Literal

from app.domain.audio import AudioFormat
from app.domain.fsm import TransitionReason, VoiceState, VoiceStateMachine
from app.domain.speech import FinalTranscript, PartialTranscript
from app.services.caller_identity import CallerIdentity
from app.services.conversation.conversation_controller import ConversationState


class MediaFormatChangedError(Exception):
    """Raised when a later frame/renegotiation disagrees with the locked format."""

    def __init__(self, locked: AudioFormat, attempted: AudioFormat) -> None:
        super().__init__(
            f"audio format changed after lock (locked={locked.model_dump()!r}, "
            f"attempted={attempted.model_dump()!r}); renegotiation rejected"
        )
        self.locked = locked
        self.attempted = attempted


class ProviderState(StrEnum):
    NOT_STARTED = "not_started"
    ACTIVE = "active"
    STOPPED = "stopped"
    ERROR = "error"


TranscriptKind = Literal["partial", "final"]

_TRANSCRIPT_HISTORY_MAX: Final[int] = 20


class TranscriptRecord:
    """One bounded history entry (partial or final) for a session."""

    __slots__ = ("kind", "text", "confidence", "sequence", "timestamp")

    def __init__(
        self,
        *,
        kind: TranscriptKind,
        text: str,
        sequence: int,
        confidence: float | None = None,
    ) -> None:
        self.kind: TranscriptKind = kind
        self.text: str = text
        self.sequence: int = sequence
        self.confidence: float | None = confidence
        self.timestamp: float = time.time()

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return (
            f"TranscriptRecord(kind={self.kind!r}, chars={len(self.text)}, "
            f"sequence={self.sequence}, confidence={self.confidence})"
        )


class SpeechSession:
    """Stateful orchestration object for one voice call (not a schema/metric)."""

    __slots__ = (
        "session_id",
        "call_sid",
        "stream_sid",
        "practice_id",
        "fsm",
        "audio_format",
        "stt_state",
        "tts_state",
        "transcripts",
        "pending_utterance",
        "pending_response",
        "started_at",
        "last_activity_at",
        "turns_completed",
        "cleanup_done",
        "conversation_state",
        "caller",
    )

    def __init__(
        self,
        *,
        session_id: str,
        call_sid: str | None = None,
        practice_id: str | None = None,
        audio_format: AudioFormat,
        caller: CallerIdentity | None = None,
    ) -> None:
        self.session_id: str = session_id
        self.call_sid: str | None = call_sid
        self.stream_sid: str | None = None
        self.practice_id: str | None = practice_id
        self.fsm = VoiceStateMachine(session_id)
        # The audio format is locked at pipeline creation (before any audio);
        # later disagreements raise MediaFormatChangedError (no silent resample).
        self.audio_format: AudioFormat = audio_format
        self.stt_state: ProviderState = ProviderState.NOT_STARTED
        self.tts_state: ProviderState = ProviderState.NOT_STARTED
        self.transcripts: deque[TranscriptRecord] = deque(maxlen=_TRANSCRIPT_HISTORY_MAX)
        self.pending_utterance: FinalTranscript | None = None
        self.pending_response: str | None = None
        self.started_at: float = time.monotonic()
        self.last_activity_at: float = self.started_at
        self.turns_completed: int = 0
        self.cleanup_done: bool = False
        self.conversation_state: ConversationState = ConversationState()
        # Intake identity (never spoken in full; only `first_name` is used for
        # the greeting, the rest feeds the call record + Jira summary).
        self.caller: CallerIdentity | None = caller

    # --- format locking -----------------------------------------------------

    def lock_audio_format(self, fmt: AudioFormat) -> AudioFormat:
        """Assert/lock the session audio format; raises on any later change."""
        self.touch()
        if fmt != self.audio_format:
            raise MediaFormatChangedError(self.audio_format, fmt)
        return self.audio_format

    # --- transcript history (bounded; text never leaves the process data plane)

    def record_transcript(self, event: PartialTranscript | FinalTranscript) -> TranscriptRecord:
        record = TranscriptRecord(
            kind="final" if isinstance(event, FinalTranscript) else "partial",
            text=event.text,
            sequence=event.sequence,
            confidence=event.confidence,
        )
        self.transcripts.append(record)
        self.touch()
        return record

    @property
    def transcript_counts(self) -> dict[str, int]:
        finals = sum(1 for record in self.transcripts if record.kind == "final")
        return {"partials": len(self.transcripts) - finals, "finals": finals}

    # --- lifecycle ------------------------------------------------------------

    def touch(self) -> None:
        self.last_activity_at = time.monotonic()

    def note_turn_completed(self) -> None:
        self.turns_completed += 1
        self.pending_utterance = None
        self.pending_response = None
        self.touch()

    def begin_cleanup(self) -> None:
        """Idempotent cleanup marker; the route always unregisters as well."""
        self.cleanup_done = True

    @property
    def voice_state(self) -> VoiceState:
        return self.fsm.state

    @property
    def age_seconds(self) -> float:
        return time.monotonic() - self.started_at

    def snapshot(self) -> dict[str, object]:
        """Compact state dump for metrics (ids/counts/lengths only)."""
        return {
            "session_id": self.session_id,
            "call_sid": self.call_sid,
            "practice_id": self.practice_id,
            "caller_named": bool(self.caller and self.caller.first_name),
            "voice_state": self.voice_state.value,
            "stt_state": self.stt_state.value,
            "tts_state": self.tts_state.value,
            "audio_format": self.audio_format.model_dump(),
            "transcript_history": self.transcript_counts,
            "pending_utterance": self.pending_utterance is not None,
            "pending_response_chars": (len(self.pending_response) if self.pending_response else 0),
            "turns_completed": self.turns_completed,
            "age_seconds": round(self.age_seconds, 3),
            "cleanup_done": self.cleanup_done,
            "conversation_state": {
                "captured_requests": len(self.conversation_state.captured_requests),
                "confirmation_pending": self.conversation_state.confirmation_pending,
                "clarification_count": self.conversation_state.clarification_count,
                "confirmed_submissions": len(self.conversation_state.confirmed_submissions),
                "backend_errors": len(self.conversation_state.backend_errors),
            },
        }

    # --- internal helper used by transitions -------------------------------

    def transition(
        self, target: VoiceState, reason: TransitionReason
    ) -> object:  # pragma: no cover - thin passthrough documented for typing
        return self.fsm.transition(target, reason)
