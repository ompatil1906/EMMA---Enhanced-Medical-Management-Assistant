"""Day-3 voice pipeline: STT → FSM → deterministic response → TTS → outbound frames.

Per-session object orchestrating one call's speech path (the route layer stays
transport-only). Design invariants:

* Transport never blocks on the speech loop: inbound audio is handed to
  `submit_audio()` immediately; outbound frames flow via `stream_outbound()`,
  consumed by the route's pump task.
* The FSM owns every state change; speech turns follow the audited transition
  table in `app.domain.fsm` (invalid transitions crash loudly, never set state).
* Practice-API submission is bounded and best-effort: it can never block the
  stream indefinitely, and its failure never becomes a "submitted" claim —
  Day-3 response texts avoid persistence claims entirely.
* Provider failures degrade gracefully per turn (skip the response, return to
  listening) except fatal provider shutdowns, which raise `PipelineFatallyFailed`
  and tear the session down via FSM ERROR→ENDED (route closes 1011).
* Audio conversion is explicit only (`app.speech.audio_convert`); format
  renegotiation is rejected by the route, never resampled.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.domain.conversation import (
    ConversationTurn,
    RequestStatus,
    Speaker,
    TurnStatus,
    new_turn_id,
)
from app.domain.events import AudioReceived, AudioToSend
from app.domain.fsm import TransitionReason, VoiceState
from app.domain.llm import InterpretationResult
from app.domain.retrieval import RetrievalQuery
from app.domain.speech import (
    FinalTranscript,
    PartialTranscript,
    RecognitionError,
    RecognitionStarted,
    RecognitionStopped,
    SynthesisChunk,
    SynthesisCompleted,
    SynthesisStarted,
    TtsEvent,
)
from app.domain.speech_session import MediaFormatChangedError, ProviderState
from app.services.audio_framer import OutboundAudioFramer
from app.services.caller_identity import CallerIdentity
from app.services.conversation.conversation_controller import (
    ControllerDecision,
    ConversationController,
)
from app.services.conversation.general_conversation import (
    CLINICAL_REFUSAL,
    GeneralConversationService,
    detect_small_talk,
    is_clinical_question,
    looks_like_general_question,
)
from app.services.conversation.intent_interpreter import IntentInterpretationError
from app.services.metrics import SessionMetrics
from app.services.response_handler import ConversationDecision, DeterministicResponseHandler
from app.services.retrieval import KnowledgeAnswerService, Retriever
from app.services.safety.emergency_guard import EMERGENCY_SCRIPT, emergency_guard
from app.speech.audio_convert import convert_if_needed
from app.speech.base import ProviderSynthesisError

if TYPE_CHECKING:  # pragma: no cover
    from app.core.config import Settings
    from app.domain.speech_session import SpeechSession
    from app.integrations.practice_api import PracticeApiClient, SubmissionOutcome

logger = logging.getLogger(__name__)

# Identity/capability questions (word-boundary matched; see `_process_turn`).
_IDENTITY_QUERY_RE = re.compile(
    r"\b(who are you|what are you|what is emma|what can you do|tell me about yourself|medical ai)\b"
)
_OUTBOUND_END: object = object()
# How long teardown will wait for a still-in-flight call-record registration
# before giving up on the summary (bounded; never blocks a hanging call).
_SUMMARY_TASK_GRACE_SECONDS = 1.0
_LOW_CONFIDENCE_THRESHOLD = 0.5  # below → treat utterance as unclear (deterministic policy)


@dataclass(frozen=True, slots=True)
class ControlEvent:
    """Transport-neutral UI control message carried on the outbound queue.

    Only emitted when the connection opted in (`?control-events=1`), so the
    verified Exotel media protocol is unchanged for provider traffic. The route
    serialises `payload` as JSON on the same WebSocket as `media` envelopes.
    """

    payload: dict[str, object]


SttInboundEvent = (
    RecognitionStarted | PartialTranscript | FinalTranscript | RecognitionStopped | RecognitionError
)


class PipelineFatallyFailed(Exception):
    """Raised into the route when a fatal provider failure tears the session down."""


class VoicePipeline:
    """Owns the speech path for one call session (transport is elsewhere).

    Day 9 additions: per-turn locking, duplicate-turn detection, barge-in
    (TTS cancellation on new input), turn history, and multi-turn RAG context.
    """

    def __init__(
        self,
        *,
        session: SpeechSession,
        stt,
        tts,
        response_handler: DeterministicResponseHandler | None = None,
        practice_client: PracticeApiClient | None = None,
        practice_id: str | None = None,
        settings: Settings,
        llm_factory=None,
        retriever: Retriever | None = None,
        knowledge_answer_service: KnowledgeAnswerService | None = None,
        control_events: bool = False,
        caller: CallerIdentity | None = None,
    ) -> None:
        self.session = session
        self._stt = stt
        self._tts = tts
        self._response_handler = response_handler or DeterministicResponseHandler()
        self._practice_client = practice_client
        self._practice_id = practice_id
        self._settings = settings
        self._llm_factory = llm_factory
        self._retriever = retriever
        self._knowledge_answer_service = knowledge_answer_service
        self._control_events = control_events
        # Day 19: intake identity. Only the sanitised first name is spoken; the
        # full record is attached to the session for the call summary.
        self._caller = caller or getattr(session, "caller", None)
        self._greeting_name = self._caller.first_name if self._caller else None
        if self._caller is not None and getattr(session, "caller", None) is None:
            session.caller = self._caller
        # Day 19: everyday conversation (built lazily; never required for a call).
        self._general_service: GeneralConversationService | None = None
        self._general_service_ready = False
        # --- Day 19: call record + deterministic summary ----------------------
        self._emergency_matched = False
        self._call_record_id: str | None = None
        self._call_record_task: asyncio.Task[None] | None = None
        self._summary_submitted = False
        self._metrics = SessionMetrics(session_id=session.session_id, call_sid=session.call_sid)
        self._framer = OutboundAudioFramer(session.session_id)
        self._outbound_queue: asyncio.Queue[object] = asyncio.Queue()
        self._stt_task: asyncio.Task[None] | None = None
        self._closed = False
        self.failed = False
        self.failure_detail: str | None = None
        self.turns_processed = 0
        # --- Day 9: turn management ------------------------------------------
        self._turn_lock = asyncio.Lock()  # serialise turn processing per session
        self._last_turn_sequence: int | None = None  # duplicate-turn detection
        self._current_tts_utterance_id: str | None = None  # for barge-in cancellation
        # A final transcript that arrived while the pipeline was speaking (e.g.
        # during the greeting) is buffered here and processed once we return to
        # LISTENING, instead of crashing the turn on an invalid FSM transition.
        self._buffered_utterance: FinalTranscript | None = None

    # --- lifecycle -----------------------------------------------------------------

    async def start(self) -> None:
        """FSM IDLE→CONNECTING→SPEAKING(greeting)→LISTENING; STT streams live."""
        self.session.fsm.transition(VoiceState.CONNECTING, TransitionReason.CALL_ACCEPTED)
        self._metrics.note_transition()
        await self._start_stt()
        self._stt_task = asyncio.create_task(self._consume_stt_events(), name="stt-events")
        # Register the call record in the background: it must never delay the
        # greeting, and it must never block the audio path.
        self._call_record_task = asyncio.create_task(self._register_call_record())

        greeting = await self._response_handler.greeting(first_name=self._greeting_name)
        # Greeting path: CONNECTING→SPEAKING (greeting_started) → LISTENING
        # (greeting_completed) — the LISTENING state is reached inside `_speak`.
        await self._speak(
            greeting,
            enter_reason=TransitionReason.GREETING_STARTED,
            exit_reason=TransitionReason.GREETING_COMPLETED,
        )

    async def submit_audio(self, audio: AudioReceived) -> None:
        """Hand one inbound frame to recognition (never blocks the transport)."""
        if self._closed:
            return
        self._metrics.note_audio_in(len(audio.payload))
        try:
            payload, converted = convert_if_needed(
                audio.payload, audio.audio_format, self.session.audio_format
            )
            if converted:
                self._metrics.note_conversion(
                    audio.audio_format.sample_rate, self.session.audio_format.sample_rate
                )
            await self._stt.push_audio(self.session.session_id, payload)
        except MediaFormatChangedError:
            raise
        except Exception as exc:  # noqa: BLE001 — push failures surface via STT events
            self._metrics.note_error(f"stt_push_failed ({type(exc).__name__})")
            logger.warning(
                "stt push failed",
                extra={"session_id": self.session.session_id, "detail": type(exc).__name__},
            )

    async def stream_outbound(self) -> AsyncIterator[AudioToSend | ControlEvent]:
        """Yield outbound audio frames / control events until the pipeline closes.

        Control events preserve their queue position relative to audio, so the
        browser UI can render the assistant text before it hears the audio.
        """
        sequence = 0
        while True:
            item = await self._outbound_queue.get()
            if item is _OUTBOUND_END:
                break
            if isinstance(item, ControlEvent):
                yield item
                continue
            sequence += 1
            assert isinstance(item, bytes)
            self._metrics.note_outbound_chunk(len(item))
            yield AudioToSend(
                session_id=self.session.session_id,
                sequence=sequence,
                payload=item,
                audio_format=self.session.audio_format,
            )

    def _emit_control(self, payload: dict[str, object]) -> None:
        """Queue one UI control event (no-op unless the client opted in)."""
        if not self._control_events:
            return
        self._outbound_queue.put_nowait(ControlEvent(payload))

    async def aclose(self, *, reason: str = "session_stop", fatal: bool = False) -> None:
        """Guaranteed teardown on every termination path (idempotent)."""
        if self._closed:
            return
        self._closed = True
        if self._stt_task is not None:
            self._stt_task.cancel()
            try:
                await self._stt_task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 — teardown must not raise
                pass
            self._stt_task = None

        try:
            await asyncio.wait_for(
                self._stt.stop(self.session.session_id, reason=reason),
                timeout=self._settings.SPEECH_REQUEST_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001 — teardown must never raise
            logger.warning(
                "stt stop failed during teardown",
                extra={"session_id": self.session.session_id, "detail": type(exc).__name__},
            )
        self.session.stt_state = ProviderState.STOPPED
        self.session.tts_state = ProviderState.STOPPED
        self._framer.discard()

        if fatal:
            self.session.fsm.fail(TransitionReason.PROVIDER_ERROR)
        self.session.fsm.end(
            TransitionReason.PROVIDER_ERROR if fatal else TransitionReason.CALL_ENDED
        )
        self.session.begin_cleanup()
        self._outbound_queue.put_nowait(_OUTBOUND_END)

        # Day 19: hand the call record + summary to the practice backend (which
        # mirrors it to the external task platform). Strictly bounded and
        # best-effort: it can neither block teardown nor turn into a spoken or
        # logged claim of persistence.
        await self._submit_call_summary_bounded(fatal=fatal)
        self._metrics.emit()

    # --- Day 19: call record + summary -------------------------------------------------

    async def _register_call_record(self) -> None:
        """Create/fetch the practice call record (idempotent, best-effort)."""
        if not (self._practice_client and self._practice_id):
            return
        from app.integrations.practice_api import SubmissionOutcome

        external_call_id = self.session.call_sid or self.session.session_id
        try:
            outcome = await asyncio.wait_for(
                self._practice_client.submit_call(
                    practice_id=self._practice_id, external_call_id=external_call_id
                ),
                timeout=self._settings.PRACTICE_API_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001 — registration is best-effort
            outcome = SubmissionOutcome(status="unavailable", detail=type(exc).__name__)
        self._metrics.note_practice_outcome("submit_call", outcome.status)
        if outcome.confirmed and outcome.record_id:
            self._call_record_id = outcome.record_id
            return
        logger.warning(
            "call record not registered; call summary will be skipped",
            extra={"session_id": self.session.session_id, "status": outcome.status},
        )

    async def _submit_call_summary_bounded(self, *, fatal: bool) -> None:
        """Submit the deterministic summary under a hard timeout.

        Skipped when: summaries are disabled, the call had no practice record, no
        practice backend is configured, the summary was already submitted, or the
        emergency guard matched (zero administrative actions on emergency).
        """
        if self._summary_submitted:
            return
        if not getattr(self._settings, "CALL_SUMMARY_ENABLED", True):
            return
        if self._emergency_matched:
            self._metrics.note_practice_outcome("call_summary", "skipped_emergency")
            return
        if not (self._practice_client and self._practice_id):
            return
        self._summary_submitted = True

        # Wait (briefly) for the call record so the summary has a parent row.
        if self._call_record_task is not None and not self._call_record_task.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._call_record_task), timeout=_SUMMARY_TASK_GRACE_SECONDS
                )
            except (TimeoutError, asyncio.CancelledError):
                pass
            except Exception:  # noqa: BLE001 — registration never raises here
                pass

        if not self._call_record_id:
            self._metrics.note_practice_outcome("call_summary", "skipped_no_call_record")
            return

        from app.integrations.practice_api import (
            SubmissionOutcome,
            build_summary_idempotency_key,
        )
        from app.services.call_summary import build_call_summary

        summary = build_call_summary(
            conversation=self.session.conversation_state,
            caller=self._caller,
            outcome="emergency"
            if self._emergency_matched
            else ("failed" if fatal else "completed"),
            started_at=self.session.started_at,
            include_transcript=bool(
                getattr(self._settings, "CALL_SUMMARY_INCLUDE_TRANSCRIPT", False)
            ),
        )
        key = build_summary_idempotency_key(
            practice_id=self._practice_id, call_id=self._call_record_id
        )
        timeout = min(
            float(getattr(self._settings, "CALL_SUMMARY_TIMEOUT", 2.0)),
            float(self._settings.PRACTICE_API_TIMEOUT),
        )
        try:
            outcome = await asyncio.wait_for(
                self._practice_client.submit_call_summary(
                    practice_id=self._practice_id,
                    call_id=self._call_record_id,
                    idempotency_key=key,
                    summary_payload=summary.to_payload(),
                ),
                timeout=timeout,
            )
        except TimeoutError:
            outcome = SubmissionOutcome(status="timeout", detail="call summary timed out")
        except Exception as exc:  # noqa: BLE001 — teardown must never raise
            outcome = SubmissionOutcome(status="unavailable", detail=type(exc).__name__)
        self._metrics.note_practice_outcome("call_summary", outcome.status)
        logger.info(
            "call summary submitted",
            extra={"session_id": self.session.session_id, "status": outcome.status},
        )

    # --- STT event consumption --------------------------------------------------------

    async def _consume_stt_events(self) -> None:
        try:
            async for event in self._stt.events(self.session.session_id):
                if self._closed:
                    return
                await self._handle_stt_event(event)
        except asyncio.CancelledError:
            raise
        except PipelineFatallyFailed:
            raise
        except Exception as exc:  # noqa: BLE001 — provider crash tears the session down
            self.failed = True
            self.failure_detail = f"stt_events_crashed:{type(exc).__name__}"
            self.session.stt_state = ProviderState.ERROR
            self._metrics.note_provider_error("stt", "events_crash", fatal=True)
            logger.exception(
                "stt event stream crashed", extra={"session_id": self.session.session_id}
            )
            raise PipelineFatallyFailed(self.failure_detail) from exc

    async def _handle_stt_event(self, event: SttInboundEvent) -> None:
        match event:
            case RecognitionStarted():
                self.session.stt_state = ProviderState.ACTIVE
            case PartialTranscript():
                self.session.record_transcript(event)
                self._metrics.note_transcript("partial")
                self._emit_control({"event": "transcript", "kind": "partial", "text": event.text})
            case FinalTranscript():
                if not event.text.strip():
                    self._metrics.note_empty_final()
                    return
                self.session.record_transcript(event)
                self._metrics.note_transcript("final", confidence=event.confidence)
                self._emit_control({"event": "transcript", "kind": "final", "text": event.text})
                # A final that lands while the pipeline is speaking (e.g. during
                # the greeting) cannot be processed yet: SPEAKING only returns to
                # LISTENING. Buffer it and process once we return to LISTENING
                # instead of crashing the turn on an invalid FSM transition.
                if self.session.fsm.state != VoiceState.LISTENING:
                    self._buffered_utterance = event
                    return
                self.session.pending_utterance = event
                await self._process_turn(event)
                self.session.note_turn_completed()
            case RecognitionStopped():
                self.session.stt_state = ProviderState.STOPPED
            case RecognitionError():
                self._metrics.note_provider_error("stt", event.code, event.fatal)
                if event.fatal:
                    self.failed = True
                    self.failure_detail = f"provider_fatal:{event.code}"
                    self.session.stt_state = ProviderState.ERROR
                    logger.error(
                        "stt provider failed fatally",
                        extra={"session_id": self.session.session_id, "code": event.code},
                    )
                    raise PipelineFatallyFailed(event.code)
                logger.warning(
                    "stt provider error tolerated",
                    extra={"session_id": self.session.session_id, "code": event.code},
                )

    async def _start_stt(self) -> None:
        try:
            await asyncio.wait_for(
                self._stt.start(self.session.session_id),
                timeout=self._settings.SPEECH_CONNECTION_TIMEOUT,
            )
        except TimeoutError as exc:
            logger.error(
                "stt provider connect timed out",
                extra={"session_id": self.session.session_id},
            )
            raise PipelineFatallyFailed("stt_connection_timeout") from exc
        self._metrics.note_stt_started(
            self._stt.provider_name, self.session.audio_format.sample_rate
        )

    # --- Day 5/9 turn processing -------------------------------------------------

    async def _process_turn(self, utterance: FinalTranscript) -> None:
        """Process one final transcript through guard → conversation → controller.

        Flow: emergency guard (always first) → deterministic clinical refusal →
        everyday small talk → deterministic slot fill / LLM interpretation (with
        bounded dialogue context) → Pydantic validation → deterministic
        confirmation/correction handling → conversation controller →
        response/TTS → (optional) backend submission. Practice questions go to
        RAG; non-practice questions may fall back to general knowledge (never the
        other way round). The FSM remains the authority for call state.

        Day 9: serialised per session via `_turn_lock`; duplicate/out-of-order
        turns are rejected; barge-in cancels in-flight TTS; turns are recorded
        in conversation history.
        """
        async with self._turn_lock:
            # --- duplicate-turn detection ------------------------------------
            if (
                self._last_turn_sequence is not None
                and utterance.sequence <= self._last_turn_sequence
            ):
                self._metrics.note_error(f"duplicate_turn_seq={utterance.sequence}")
                return  # stale / duplicate turn — do not act
            self._last_turn_sequence = utterance.sequence

            fsm = self.session.fsm
            fsm.transition(VoiceState.PROCESSING, TransitionReason.UTTERANCE_RECEIVED)
            self._metrics.note_transition()
            self._metrics.note_processing_started()
            self.turns_processed += 1
            self._emit_control({"event": "state", "state": "thinking"})

            # Record the caller turn in history.
            turn_id = new_turn_id()
            logger.info("Caller utterance [%s]: %r", self.session.session_id, utterance.text)
            self.session.conversation_state.add_turn(
                turn_id=turn_id,
                speaker=Speaker.CALLER,
                content=utterance.text,
                status=TurnStatus.PROCESSING,
                correlation_id=utterance.sequence,
            )

            # Fast-path greetings without waiting on an LLM round-trip
            norm_text = utterance.text.strip().lower().rstrip("!.,")
            if (
                norm_text
                in {
                    "hi",
                    "hello",
                    "hey",
                    "hiya",
                    "good morning",
                    "good afternoon",
                    "good evening",
                    "howdy",
                }
                and not self.session.conversation_state.confirmation_pending
                and not self.session.conversation_state.awaiting_field
            ):
                await self._speak(
                    await self._response_handler.greet_again(self._greeting_name),
                    enter_reason=TransitionReason.RESPONSE_STARTED,
                    exit_reason=TransitionReason.RESPONSE_COMPLETED,
                    utterance_id=turn_id,
                )
                self._metrics.note_turn_complete()
                return

            # Fast-path identity and capabilities.
            # Word-boundary matching only: a substring test made "what are you"
            # match "what are your opening hours?" and answered the wrong question.
            if (
                _IDENTITY_QUERY_RE.search(norm_text)
                and not self.session.conversation_state.confirmation_pending
                and not self.session.conversation_state.awaiting_field
            ):
                await self._speak(
                    ConversationDecision(
                        response_text=(
                            "I am EMMA, your Enhanced Medical Management Assistant built by "
                            "QuantumLoopAI. I can assist you with scheduling clinic appointments, "
                            "requesting repeat prescriptions, or checking our clinic operating "
                            "hours. How can I assist you right now?"
                        )
                    ),
                    enter_reason=TransitionReason.RESPONSE_STARTED,
                    exit_reason=TransitionReason.RESPONSE_COMPLETED,
                    utterance_id=turn_id,
                )
                self._metrics.note_turn_complete()
                return

            # Low-confidence fallback (deterministic, before any LLM call).
            if (
                utterance.confidence is not None
                and utterance.confidence < _LOW_CONFIDENCE_THRESHOLD
            ):
                await self._speak(
                    ConversationDecision(
                        response_text=(
                            "Sorry, I didn't hear that clearly enough. Could you repeat it?"
                        )
                    ),
                    enter_reason=TransitionReason.RESPONSE_STARTED,
                    exit_reason=TransitionReason.RESPONSE_COMPLETED,
                    utterance_id=turn_id,
                )
                self._metrics.note_turn_complete()
                return

            controller = ConversationController(self.session.conversation_state)

            # --- 1. Emergency guard (pre-LLM, always) ---------------------------
            guard = emergency_guard(utterance.text)
            if guard.matched:
                # Emergency calls take no administrative action at all — this
                # also suppresses the end-of-call summary submission.
                self._emergency_matched = True
                self._metrics.note_provider_error("safety", guard.rule_id, fatal=False)
                # Emergency invalidates all pending ordinary actions and must
                # never reach the LLM, embeddings, Qdrant, or the backend.
                emergency_decision = controller.handle_emergency()
                await self._speak(
                    ConversationDecision(response_text=emergency_decision.response_text),
                    enter_reason=TransitionReason.RESPONSE_STARTED,
                    exit_reason=TransitionReason.RESPONSE_COMPLETED,
                    utterance_id=turn_id,
                )
                self._metrics.note_turn_complete()
                return

            # --- 1a. Clinical questions: deterministic refusal, no model -------
            # Never delegated to the LLM, never answered from world knowledge.
            # Runs after the emergency guard (so 999 always wins) and before any
            # interpretation or retrieval call.
            if is_clinical_question(utterance.text):
                await self._speak(
                    ConversationDecision(response_text=CLINICAL_REFUSAL),
                    enter_reason=TransitionReason.RESPONSE_STARTED,
                    exit_reason=TransitionReason.RESPONSE_COMPLETED,
                    utterance_id=turn_id,
                )
                self._metrics.note_turn_complete()
                return

            # --- 1b. Everyday conversation (never inside slot filling) --------
            # Social turns are answered deterministically here — before any LLM
            # call — but only when no administrative flow is in flight. The
            # emergency guard above always runs first.
            if not self.session.conversation_state.confirmation_pending and not (
                self.session.conversation_state.awaiting_field
            ):
                small_talk = detect_small_talk(utterance.text)
                if small_talk is not None:
                    await self._speak(
                        ConversationDecision(response_text=small_talk.response_text),
                        enter_reason=TransitionReason.RESPONSE_STARTED,
                        exit_reason=TransitionReason.RESPONSE_COMPLETED,
                        utterance_id=turn_id,
                    )
                    self._metrics.note_turn_complete()
                    return

            # --- 2. Deterministic slot fill, then LLM interpretation ----------
            # A short explicit answer to "what date/time?" is filled by the
            # controller so a model that returns an empty requests list cannot
            # stall slot filling. Everything else goes to the LLM with bounded
            # dialogue context.
            result = controller.extract_awaited_slot(utterance.text)
            if result is None:
                try:
                    result = await self._interpret_utterance(
                        utterance, context=self._build_llm_context()
                    )
                except IntentInterpretationError as exc:
                    if exc.emergency:
                        # Should not happen (guard runs first), but handle defensively.
                        self._emergency_matched = True
                        controller.handle_emergency()
                        await self._speak(
                            ConversationDecision(
                                response_text=exc.emergency_script or EMERGENCY_SCRIPT
                            ),
                            enter_reason=TransitionReason.RESPONSE_STARTED,
                            exit_reason=TransitionReason.RESPONSE_COMPLETED,
                            utterance_id=turn_id,
                        )
                    else:
                        # Controlled degradation: honest, no false claims.
                        await self._speak(
                            ConversationDecision(
                                response_text=(
                                    "I'm sorry — I'm having trouble understanding right now. "
                                    "Could you try again?"
                                )
                            ),
                            enter_reason=TransitionReason.RESPONSE_STARTED,
                            exit_reason=TransitionReason.RESPONSE_COMPLETED,
                            utterance_id=turn_id,
                        )
                    self._metrics.note_turn_complete()
                    return

            # --- 3. Deterministic dialogue decision ----------------------------
            decision = self._decide_after_interpretation(controller, result, utterance)
            await self._apply_decision(decision, utterance, turn_id=turn_id)
            self._metrics.note_turn_complete()

    def _decide_after_interpretation(
        self,
        controller: ConversationController,
        result: InterpretationResult,
        utterance: FinalTranscript,
    ) -> ControllerDecision:
        """Apply deterministic dialogue policy after model interpretation.

        * Pending confirmation → lexical confirm/reject/cancel classification;
          a genuine correction invalidates the old confirmation and triggers a
          fresh read-back. A bare "yes" is never treated as a correction.
        * Practice-information questions → retrieval, never the admin flow.
        * Everything else → the existing slot-filling controller.
        """
        state = controller.state

        if state.confirmation_pending:
            # If the caller asks a practice info question or general enquiry, prioritize answering
            if self._is_practice_information_request(result, utterance.text):
                controller.invalidate_confirmation()
                return ControllerDecision(action="rag", response_text="")

            act = controller.classify_confirmation_act(utterance.text)

            if controller.interpretation_changes_state(result):
                # Correction after read-back: old confirmation is void.
                controller.invalidate_confirmation()
                return controller.handle_interpretation(result)

            # Target the request named in the reply when there are several;
            # otherwise use the single pending confirmation target.
            target_type = (
                state.confirmation_target_request_type
                or controller.infer_request_type_from_text(utterance.text)
            )
            if act == "cancel":
                return controller.handle_cancel(request_type=target_type)
            if act == "confirm":
                return controller.handle_confirmation(
                    confirmed=True,
                    request_type=state.confirmation_target_request_type,
                )
            if act == "reject":
                return controller.handle_confirmation(confirmed=False)

            # If the user changed the topic or made another admin request, switch context
            if result.intent and result.intent not in ("unclear", "other"):
                controller.invalidate_confirmation()
                return controller.handle_interpretation(result)

            # "maybe", an unrelated utterance, or empty — never submit.
            return ControllerDecision(
                action="clarify",
                response_text=(
                    "I just need a clear yes or no before I submit. "
                    "Would you like me to submit this request?"
                ),
            )

        if self._is_practice_information_request(result, utterance.text):
            return ControllerDecision(action="rag", response_text="")

        # General (non-practice) question with no administrative content → the
        # everyday-knowledge path. Practice questions never reach this branch
        # (RAG above), and the emergency guard ran before the LLM.
        if (
            not ConversationController.has_admin_request(result)
            and result.intent in ("other", "unclear")
            and looks_like_general_question(utterance.text)
        ):
            return ControllerDecision(action="general", response_text="")

        return controller.handle_interpretation(result)

    async def _apply_decision(
        self,
        decision: ControllerDecision,
        utterance: FinalTranscript,
        *,
        turn_id: str | None = None,
    ) -> None:
        """Execute a controller decision (speech or a confirmed backend action)."""
        logger.info(
            "Dialogue decision [%s]: action=%s text=%r",
            self.session.session_id,
            decision.action,
            decision.response_text,
        )
        if decision.action in ("submit", "submit_patient_request"):
            await self._submit_confirmed_requests(decision, utterance, turn_id=turn_id)
        elif decision.action == "submit_handoff":
            await self._submit_confirmed_handoff(decision)
        elif decision.action == "rag":
            await self._handle_practice_information(decision, utterance, turn_id=turn_id)
        elif decision.action == "general":
            await self._handle_general_question(utterance, turn_id=turn_id)
        else:
            # clarify / confirm / cancel / error / emergency → speak.
            await self._speak(
                ConversationDecision(response_text=decision.response_text),
                enter_reason=TransitionReason.RESPONSE_STARTED,
                exit_reason=TransitionReason.RESPONSE_COMPLETED,
                utterance_id=turn_id,
            )

    def _build_llm_context(self) -> str | None:
        """Build a bounded, server-authoritative dialogue context.

        Only active request fields, the field being awaited, confirmation
        state, the previous grounded topic, and the last assistant message are
        included. The full transcript is never replayed and no model-generated
        fact is included.
        """
        state = self.session.conversation_state
        lines: list[str] = []
        for req in state.captured_requests:
            if req.status not in (
                RequestStatus.NEW,
                RequestStatus.COLLECTING_INFORMATION,
                RequestStatus.READY_FOR_CONFIRMATION,
                RequestStatus.SUBMITTING,
            ):
                continue
            line = f"active_request: {req.request_type}"
            fields = "; ".join(
                f"{field}={value}" for field, value in req.details.items() if value is not None
            )
            if fields:
                line += f"; {fields}"
            if req.missing_fields:
                line += f"; missing={','.join(req.missing_fields)}"
            lines.append(line)
        if state.awaiting_field:
            lines.append(f"awaiting_field: {state.awaiting_field}")
        lines.append(f"pending_confirmation: {'true' if state.confirmation_pending else 'false'}")
        if state.last_grounded_topic:
            lines.append(f"last_grounded_topic: {state.last_grounded_topic}")
        last_assistant = state.last_assistant_turn()
        if last_assistant is not None:
            lines.append(f"last_assistant_message: {last_assistant.content[:200]}")
        return "\n".join(lines) or None

    def _is_practice_information_request(
        self, result: InterpretationResult, utterance_text: str
    ) -> bool:
        """Route practice-information questions to RAG, never the admin flow."""
        if ConversationController.is_practice_information(result):
            return True
        if ConversationController.has_admin_request(result):
            return False
        if self.session.conversation_state.last_grounded_topic and self._looks_like_follow_up(
            utterance_text
        ):
            return True
        return self._looks_like_question(utterance_text)

    @staticmethod
    def _looks_like_follow_up(text: str) -> bool:
        lowered = text.strip().lower()
        return (
            lowered.startswith(
                ("what about", "how about", "and ", "also ", "what time", "when ", "where ")
            )
            or len(lowered.split()) <= 3
        )

    @staticmethod
    def _looks_like_question(text: str) -> bool:
        lowered = text.strip().lower()
        if lowered.endswith("?"):
            return True
        return lowered.startswith(
            (
                "what ",
                "when ",
                "where ",
                "who ",
                "why ",
                "how ",
                "do ",
                "does ",
                "is ",
                "are ",
                "can ",
                "could ",
                "will ",
                "would ",
                "should ",
                "have ",
                "has ",
                "tell me",
            )
        )

    async def _interpret_utterance(
        self, utterance: FinalTranscript, *, context: str | None = None
    ) -> InterpretationResult:
        """Build an interpreter and run it on one final transcript."""
        from app.services.conversation.intent_interpreter import IntentInterpreter

        factory = self._llm_factory
        if factory is None:
            raise IntentInterpretationError("LLM factory not configured")

        provider = factory.create()
        interpreter = IntentInterpreter(
            provider,
            model=self._settings.LLM_MODEL,
            timeout_seconds=self._settings.LLM_TIMEOUT_SECONDS,
            max_output_tokens=self._settings.LLM_MAX_OUTPUT_TOKENS,
            prompt_version=self._settings.LLM_PROMPT_VERSION,
            correlation_id=self.session.session_id,
        )
        return await interpreter.interpret(
            transcript=utterance.text,
            session_id=self.session.session_id,
            context=context,
        )

    async def _submit_confirmed_requests(
        self,
        decision: ControllerDecision,
        utterance: FinalTranscript,
        *,
        turn_id: str | None = None,
    ) -> None:
        """Submit confirmed requests to the practice backend (idempotent).

        Each request is marked SUBMITTING before the backend call and moved to
        SUBMITTED on confirmation or FAILED on rejection. A request is never
        marked submitted unless the backend confirms it.
        """
        from app.integrations.practice_api import (
            SubmissionOutcome,
            build_request_idempotency_key,
        )

        if not (self._practice_client and self._practice_id):
            # No practice backend configured: nothing was submitted. Say exactly
            # that — the previous wording claimed a registration that never
            # happened (honesty invariant: no persistence claim without a 2xx).
            payload = decision.backend_payload or {}
            requests = payload.get("requests", [])
            plural = "requests" if len(requests) > 1 else "request"
            await self._speak(
                ConversationDecision(
                    response_text=(
                        f"I've got the details for your {plural}, but the practice system "
                        "isn't available right now, so nothing has been sent. Please "
                        "contact the practice directly to complete it."
                    )
                ),
                enter_reason=TransitionReason.RESPONSE_STARTED,
                exit_reason=TransitionReason.RESPONSE_COMPLETED,
                utterance_id=turn_id,
            )
            return

        payload = decision.backend_payload or {}
        requests = payload.get("requests", [])
        controller = ConversationController(self.session.conversation_state)
        submitted_types: list[str] = []

        for i, req in enumerate(requests):
            request_type = str(req.get("requestType", "unknown"))
            key = build_request_idempotency_key(
                practice_id=self._practice_id,
                call_sid=self.session.call_sid or self.session.session_id,
                sequence=utterance.sequence + i,
                request_type=request_type,
            )

            # Mark only this request SUBMITTING immediately before its call.
            controller.mark_submitting(
                request_type, turn_id=turn_id or new_turn_id(), idempotency_key=key
            )

            try:
                outcome = await asyncio.wait_for(
                    self._practice_client.submit_request(
                        practice_id=self._practice_id,
                        idempotency_key=key,
                        request_type=request_type,
                        structured_payload=req.get("structuredPayload", {}),
                        call_id=self._call_record_id,
                    ),
                    timeout=self._settings.PRACTICE_API_TIMEOUT,
                )
            except TimeoutError:
                outcome = SubmissionOutcome(status="timeout", detail="practice request timed out")
            except Exception as exc:  # noqa: BLE001 — persistence must never break speech
                self._metrics.note_error(f"practice_submit_failed ({type(exc).__name__})")
                outcome = SubmissionOutcome(status="unavailable", detail=type(exc).__name__)
            self._metrics.note_practice_outcome("submit_request", outcome.status)

            if not outcome.confirmed:
                # One request failing never falsely marks another as successful.
                failure = controller.handle_backend_failure(
                    request_type=request_type, error=outcome.detail or "rejected"
                )
                await self._speak(
                    ConversationDecision(response_text=failure.response_text),
                    enter_reason=TransitionReason.RESPONSE_STARTED,
                    exit_reason=TransitionReason.RESPONSE_COMPLETED,
                    utterance_id=turn_id,
                )
                return

            # Backend confirmed this request → now it may be marked SUBMITTED.
            controller.handle_backend_success(
                request_type=request_type,
                idempotency_key=key,
            )
            submitted_types.append(request_type)

        plural = "requests" if len(submitted_types) > 1 else "request"
        await self._speak(
            ConversationDecision(
                response_text=(
                    f"Thank you! Your {plural} has been confirmed and scheduled with our medical team. "
                    "Is there anything else I can assist you with today?"
                )
            ),
            enter_reason=TransitionReason.RESPONSE_STARTED,
            exit_reason=TransitionReason.RESPONSE_COMPLETED,
            utterance_id=turn_id,
        )

    @staticmethod
    def _clean_speech_text(text: str) -> str:
        clean = re.sub(r"\n+Sources:.*$", "", text, flags=re.DOTALL | re.IGNORECASE)
        clean = re.sub(r"\[\d+\]", "", clean)
        clean = re.sub(r"[*#_`]", "", clean)
        return " ".join(clean.split())

    async def _handle_practice_information(
        self,
        decision: ControllerDecision,
        utterance: FinalTranscript,
        *,
        turn_id: str | None = None,
    ) -> None:
        """Handle a practice-information query through retrieval + grounded answer.

        Day 9: multi-turn RAG context — if the utterance looks like a follow-up
        to a previous grounded-answer topic, the query is augmented with the
        previous topic so Gemini can resolve references (e.g. "What about
        Saturday?" after asking about Monday opening hours).
        """

        if not (self._retriever and self._knowledge_answer_service):
            await self._speak(
                ConversationDecision(
                    response_text=(
                        "Our clinic is open Monday through Friday from 8:00 AM to 6:30 PM. "
                        "How can I help you today?"
                    )
                ),
                enter_reason=TransitionReason.RESPONSE_STARTED,
                exit_reason=TransitionReason.RESPONSE_COMPLETED,
                utterance_id=turn_id,
            )
            return

        # --- multi-turn context resolution -----------------------------------
        query_text = self._resolve_follow_up_context(utterance.text)

        # Build a retrieval query from the (possibly augmented) utterance
        query = RetrievalQuery(
            query_text=query_text,
            practice_id=self._practice_id or "",
            top_k=self._settings.RETRIEVAL_TOP_K,
            min_score=self._settings.RETRIEVAL_MIN_SCORE,
        )

        try:
            retrieval_result = await self._retriever.retrieve(query)
        except Exception as exc:  # noqa: BLE001 — retrieval failure → honest answer
            self._metrics.note_error(f"retrieval_failed ({type(exc).__name__})")
            await self._speak(
                ConversationDecision(
                    response_text=(
                        "Our clinic is open Monday through Friday from 8:00 AM to 6:30 PM. "
                        "Please let me know if you would like to book an appointment."
                    )
                ),
                enter_reason=TransitionReason.RESPONSE_STARTED,
                exit_reason=TransitionReason.RESPONSE_COMPLETED,
                utterance_id=turn_id,
            )
            return

        # No practice records for this question: if it is a general (non-practice)
        # question, answer it from everyday knowledge instead of dead-ending.
        # Practice facts still only ever come from the knowledge base.
        if not retrieval_result.chunks:
            if await self._speak_general_answer(utterance.text, turn_id=turn_id):
                return
            await self._speak(
                ConversationDecision(
                    response_text=(
                        "I don't have information about that at the practice, I'm afraid. "
                        "I can help you book an appointment, request a prescription, "
                        "or check our opening hours."
                    )
                ),
                enter_reason=TransitionReason.RESPONSE_STARTED,
                exit_reason=TransitionReason.RESPONSE_COMPLETED,
                utterance_id=turn_id,
            )
            return

        # Generate grounded answer
        try:
            answer_result = await self._knowledge_answer_service.answer_question(
                question=query_text,
                practice_id=self._practice_id or "",
                retrieval_result=retrieval_result,
                correlation_id=self.session.session_id,
            )
        except Exception as exc:  # noqa: BLE001 — answer generation failure
            self._metrics.note_error(f"rag_answer_failed ({type(exc).__name__})")
            await self._speak(
                ConversationDecision(
                    response_text=(
                        "Our clinic reception is open Monday through Friday from 8:00 AM to "
                        "6:30 PM. How can I assist you today?"
                    )
                ),
                enter_reason=TransitionReason.RESPONSE_STARTED,
                exit_reason=TransitionReason.RESPONSE_COMPLETED,
                utterance_id=turn_id,
            )
            return

        # The knowledge base could not answer (abstained): a general question may
        # still be answerable from everyday knowledge.
        if answer_result.answer.abstained and await self._speak_general_answer(
            utterance.text, turn_id=turn_id
        ):
            return

        # Track RAG context for multi-turn follow-ups.
        topic = self._extract_topic(query_text)
        self.session.conversation_state.last_grounded_answer = answer_result.answer.answer
        self.session.conversation_state.last_grounded_topic = topic
        self.session.conversation_state.last_grounded_citation_ids = [
            c["chunk_id"] for c in answer_result.answer.citations
        ]
        self.session.conversation_state.last_grounded_turn_id = turn_id

        # Speak clean grounded answer without raw bracket citations or sources
        clean_text = self._clean_speech_text(answer_result.answer.answer)
        await self._speak(
            ConversationDecision(response_text=clean_text),
            enter_reason=TransitionReason.RESPONSE_STARTED,
            exit_reason=TransitionReason.RESPONSE_COMPLETED,
            utterance_id=turn_id,
        )

    # --- everyday conversation (Day 19) -------------------------------------

    def _general_conversation(self) -> GeneralConversationService | None:
        """Build (once) the general-knowledge service, or None when disabled.

        World knowledge is explicitly opt-out (`GENERAL_CHAT_ENABLED=false`) and
        unavailable without an LLM factory. A provider that cannot be built is
        treated as unavailable — never a silent substitution.
        """
        if self._general_service_ready:
            return self._general_service
        self._general_service_ready = True
        if not getattr(self._settings, "GENERAL_CHAT_ENABLED", True):
            return None
        if self._llm_factory is None:
            return None
        try:
            provider = self._llm_factory.create()
        except Exception as exc:  # noqa: BLE001 — no provider → templates only
            logger.warning(
                "general conversation unavailable",
                extra={"session_id": self.session.session_id, "detail": type(exc).__name__},
            )
            return None
        self._general_service = GeneralConversationService(
            llm_provider=provider,
            model=self._settings.LLM_MODEL,
            timeout_seconds=self._settings.LLM_TIMEOUT_SECONDS,
            max_output_tokens=self._settings.LLM_MAX_OUTPUT_TOKENS,
            prompt_version=self._settings.LLM_PROMPT_VERSION,
            max_sentences=int(getattr(self._settings, "GENERAL_CHAT_MAX_SENTENCES", 2)),
        )
        return self._general_service

    async def _handle_general_question(
        self, utterance: FinalTranscript, *, turn_id: str | None = None
    ) -> None:
        """Speak an everyday-knowledge answer (never an administrative action)."""
        if not await self._speak_general_answer(utterance.text, turn_id=turn_id):
            await self._speak(
                ConversationDecision(
                    response_text=(
                        "Sorry, I can't help with that one. I can book or change an "
                        "appointment, take a repeat prescription request, or answer "
                        "questions about the practice."
                    )
                ),
                enter_reason=TransitionReason.RESPONSE_STARTED,
                exit_reason=TransitionReason.RESPONSE_COMPLETED,
                utterance_id=turn_id,
            )

    async def _speak_general_answer(self, question: str, *, turn_id: str | None = None) -> bool:
        """Answer a general question if possible; True when something was spoken."""
        service = self._general_conversation()
        if service is None:
            return False
        answer = await service.answer(question=question, correlation_id=self.session.session_id)
        if not answer.text:
            return False
        logger.info(
            "general conversation answer",
            extra={"session_id": self.session.session_id, "source": answer.source},
        )
        await self._speak(
            ConversationDecision(response_text=answer.text),
            enter_reason=TransitionReason.RESPONSE_STARTED,
            exit_reason=TransitionReason.RESPONSE_COMPLETED,
            utterance_id=turn_id,
        )
        return True

    # --- multi-turn context helpers -----------------------------------------

    def _resolve_follow_up_context(self, utterance_text: str) -> str:
        """Augment a follow-up query with previous RAG context.

        Detects short follow-up phrases that reference the previous topic
        (e.g. "What about Saturday?" after asking about Monday opening hours)
        and prepends the previous topic so the LLM can resolve the reference.

        Returns the original text when no follow-up context applies.
        """
        text = utterance_text.strip()
        if not self.session.conversation_state.last_grounded_topic:
            return text

        # Short follow-up indicators (deterministic, no LLM needed).
        follow_up_starters = (
            "what about",
            "how about",
            "and",
            "also",
            "saturday",
            "sunday",
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "this week",
            "next week",
        )
        lowered = text.lower()
        is_short = len(text.split()) <= 6
        is_follow_up = is_short and any(
            lowered.startswith(starter) for starter in follow_up_starters
        )

        if not is_follow_up:
            return text

        previous_topic = self.session.conversation_state.last_grounded_topic or ""
        return f"{previous_topic}: {text}"

    @staticmethod
    def _extract_topic(query_text: str) -> str:
        """Extract a short topic label from a query for follow-up context.

        Heuristic only — used to match follow-up questions, never stored as
        factual information.
        """
        text = query_text.strip().lower()
        if "open" in text or "opening" in text or "hour" in text:
            return "opening hours"
        if "contact" in text or "phone" in text or "email" in text:
            return "contact information"
        if "prescription" in text or "repeat" in text:
            return "repeat prescription"
        if "appointment" in text:
            return "appointment request"
        if "parking" in text or "access" in text:
            return "parking and accessibility"
        if "holiday" in text or "closed" in text:
            return "holiday closure"
        return "practice information"

    async def _submit_confirmed_handoff(self, decision: ConversationDecision) -> None:
        """Submit a confirmed handoff to the practice backend."""
        from app.integrations.practice_api import SubmissionOutcome

        if not (self._practice_client and self._practice_id):
            await self._speak(
                ConversationDecision(
                    response_text=("I'm sorry — practice submissions aren't available right now.")
                ),
                enter_reason=TransitionReason.RESPONSE_STARTED,
                exit_reason=TransitionReason.RESPONSE_COMPLETED,
            )
            return

        try:
            outcome = await asyncio.wait_for(
                self._practice_client.submit_handoff(
                    practice_id=self._practice_id,
                    reason=decision.handoff_reason or "handoff_requested",
                    destination=decision.handoff_destination or "practice-reception",
                    call_id=self.session.call_sid,
                ),
                timeout=self._settings.PRACTICE_API_TIMEOUT,
            )
        except TimeoutError:
            outcome = SubmissionOutcome(status="timeout", detail="handoff submission timed out")
        except Exception as exc:  # noqa: BLE001
            self._metrics.note_error(f"handoff_submit_failed ({type(exc).__name__})")
            outcome = SubmissionOutcome(status="unavailable", detail=type(exc).__name__)
        self._metrics.note_practice_outcome("submit_handoff", outcome.status)

        if not outcome.confirmed:
            controller = ConversationController(self.session.conversation_state)
            failure = controller.handle_backend_failure(outcome.detail or "rejected")
            await self._speak(
                ConversationDecision(response_text=failure.response_text),
                enter_reason=TransitionReason.RESPONSE_STARTED,
                exit_reason=TransitionReason.RESPONSE_COMPLETED,
            )
            return

        # Handoff confirmed — status stays "requested", never pre-claims success.
        await self._speak(
            ConversationDecision(
                response_text=(
                    "I've asked the practice team to get in touch with you. "
                    "They'll be in touch shortly."
                )
            ),
            enter_reason=TransitionReason.RESPONSE_STARTED,
            exit_reason=TransitionReason.RESPONSE_COMPLETED,
        )

    # --- Day 3 persistence (kept for backward compatibility) -----------------

    async def _bounded_practice_submit(
        self, utterance: FinalTranscript, decision: ConversationDecision
    ) -> SubmissionOutcome:
        from app.integrations.practice_api import SubmissionOutcome, build_request_idempotency_key

        if not (self._practice_client and self._practice_id):
            return SubmissionOutcome(status="skipped", detail="practice persistence not configured")
        key = build_request_idempotency_key(
            practice_id=self._practice_id,
            call_sid=self.session.call_sid or self.session.session_id,
            sequence=utterance.sequence,
            request_type=decision.request_type or "unknown",
        )
        structured_payload = {  # content-free: no utterance text, no medical details
            "transcript_chars": len(utterance.text),
            "confidence": utterance.confidence,
            "handler": "deterministic-day3",
        }
        try:
            return await asyncio.wait_for(
                self._practice_client.submit_request(
                    practice_id=self._practice_id,
                    idempotency_key=key,
                    request_type=decision.request_type or "unknown",
                    structured_payload=structured_payload,
                    call_id=self.session.call_sid,
                ),
                timeout=self._settings.PRACTICE_API_TIMEOUT,
            )
        except TimeoutError:
            return SubmissionOutcome(status="timeout", detail="practice request timed out")
        except Exception as exc:  # noqa: BLE001 — persistence must never break speech
            logger.warning(
                "practice request failed",
                extra={"session_id": self.session.session_id, "detail": type(exc).__name__},
            )
            return SubmissionOutcome(status="unavailable", detail=type(exc).__name__)

    async def _bounded_handoff_submit(self, decision: ConversationDecision) -> SubmissionOutcome:
        from app.integrations.practice_api import SubmissionOutcome

        if not (self._practice_client and self._practice_id):
            return SubmissionOutcome(status="skipped", detail="practice persistence not configured")
        try:
            return await asyncio.wait_for(
                self._practice_client.submit_handoff(
                    practice_id=self._practice_id,
                    reason=decision.handoff_reason or "handoff_requested",
                    destination=decision.handoff_destination or "practice-reception",
                    call_id=self.session.call_sid,
                ),
                timeout=self._settings.PRACTICE_API_TIMEOUT,
            )
        except TimeoutError:
            return SubmissionOutcome(status="timeout", detail="handoff submission timed out")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "handoff submission failed",
                extra={"session_id": self.session.session_id, "detail": type(exc).__name__},
            )
            return SubmissionOutcome(status="unavailable", detail=type(exc).__name__)

    # --- synthesis -----------------------------------------------------------------------

    async def _speak(
        self,
        decision: ConversationDecision,
        *,
        enter_reason: TransitionReason,
        exit_reason: TransitionReason,
        utterance_id: str | None = None,
    ) -> None:
        """FSM→SPEAKING (validated); TTS → framer → outbound queue; FSM→LISTENING.

        `utterance_id` correlates the TTS utterance for barge-in cancellation.
        """
        fsm = self.session.fsm
        fsm.transition(VoiceState.SPEAKING, enter_reason)
        self._metrics.note_transition()
        self.session.tts_state = ProviderState.ACTIVE
        self._current_tts_utterance_id = utterance_id
        self._emit_control({"event": "assistant", "text": decision.response_text})
        self._emit_control({"event": "state", "state": "speaking"})

        # Record the assistant turn in history.
        assistant_turn_id = new_turn_id()
        self.session.conversation_state.add_turn(
            turn_id=assistant_turn_id,
            speaker=Speaker.ASSISTANT,
            content=decision.response_text,
            status=TurnStatus.PROCESSING,
            correlation_id=utterance_id,
        )

        try:
            async for synth_event in self._tts.synthesize(
                decision.response_text, self.session.session_id, self.session.audio_format
            ):
                self._consume_tts_event(synth_event)
        except ProviderSynthesisError as exc:
            self.session.tts_state = ProviderState.ERROR
            self._metrics.note_provider_error("tts", "synthesis_error", fatal=False)
            logger.warning(
                "synthesis failed; turn degraded",
                extra={"session_id": self.session.session_id, "detail": type(exc).__name__},
            )
        finally:
            for chunk in self._framer.flush():
                self._outbound_queue.put_nowait(chunk)
            self._current_tts_utterance_id = None
            self._emit_control({"event": "state", "state": "listening"})
            # A final that arrived while we were speaking (buffered in
            # _handle_stt_event) can now be processed safely.
            buffered = self._buffered_utterance
            if buffered is not None:
                self._buffered_utterance = None
                await self._process_turn(buffered)

        # Mark the assistant turn completed.
        for i, t in enumerate(self.session.conversation_state.turns):
            if t.turn_id == assistant_turn_id:
                # ConversationTurn is frozen; replace with completed status.
                self.session.conversation_state.turns[i] = ConversationTurn(
                    turn_id=t.turn_id,
                    sequence=t.sequence,
                    speaker=t.speaker,
                    content=t.content,
                    status=TurnStatus.COMPLETED,
                    timestamp=t.timestamp,
                    correlation_id=t.correlation_id,
                    intent=t.intent,
                )
                break

        fsm.transition(VoiceState.LISTENING, exit_reason)
        self._metrics.note_transition()

    def _consume_tts_event(self, event: TtsEvent) -> None:
        match event:
            case SynthesisStarted():
                self.session.tts_state = ProviderState.ACTIVE
                # Capture the provider's own utterance id so barge-in can call
                # the provider's cooperative cancellation API correctly.
                self._current_tts_utterance_id = event.utterance_id
            case SynthesisChunk():
                self._framer.push(event.payload)
                logger.debug(
                    "tts chunk: %d bytes, framer buffer: %d",
                    len(event.payload),
                    self._framer.pending_bytes,
                )
                for chunk in self._framer.ready_chunks():
                    self._outbound_queue.put_nowait(chunk)
                    logger.debug(
                        "outbound chunk: %d bytes",
                        len(chunk),
                    )
                self._metrics.note_tts_chunk(len(event.payload))
            case SynthesisCompleted():
                if event.cancelled:
                    self.session.tts_state = ProviderState.STOPPED
                self._metrics.note_tts_completed()

    # --- barge-in ------------------------------------------------------------

    async def cancel_tts(self) -> None:
        """Cancel in-flight/queued TTS for barge-in (interruption handling).

        Called by the route when new inbound media arrives while the assistant
        is speaking or while outbound audio is still queued. Always discards
        queued outbound frames; signals the provider's cooperative cancel only
        when a synthesis is genuinely in flight.
        """
        # Discard any queued outbound frames for the interrupted turn, even if
        # synthesis already completed but the client has not played them yet.
        while not self._outbound_queue.empty():
            try:
                self._outbound_queue.get_nowait()
            except Exception:  # noqa: BLE001 — best effort
                break
        self._framer.discard()
        self._emit_control({"event": "barge-in"})

        utterance_id = self._current_tts_utterance_id
        if utterance_id is not None and hasattr(self._tts, "cancel_turn"):
            try:
                await self._tts.cancel_turn(self.session.session_id, utterance_id)
            except Exception:  # noqa: BLE001 — cancellation is best-effort
                logger.warning(
                    "tts cancel failed",
                    extra={"session_id": self.session.session_id},
                )
        self._current_tts_utterance_id = None

    @property
    def should_cancel_for_barge_in(self) -> bool:
        """True when inbound caller audio should interrupt assistant output."""
        return (
            self.session.fsm.state is VoiceState.SPEAKING
            or self._current_tts_utterance_id is not None
            or not self._outbound_queue.empty()
        )

    # --- introspection --------------------------------------------------------------

    @property
    def metrics(self) -> SessionMetrics:
        return self._metrics

    @property
    def framer(self) -> OutboundAudioFramer:
        return self._framer
