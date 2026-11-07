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
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.domain.conversation import (
    ConversationTurn,
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
from app.services.conversation.conversation_controller import (
    ControllerDecision,
    ConversationController,
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

_OUTBOUND_END: object = object()
_LOW_CONFIDENCE_THRESHOLD = 0.5  # below → treat utterance as unclear (deterministic policy)

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

    # --- lifecycle -----------------------------------------------------------------

    async def start(self) -> None:
        """FSM IDLE→CONNECTING→SPEAKING(greeting)→LISTENING; STT streams live."""
        self.session.fsm.transition(VoiceState.CONNECTING, TransitionReason.CALL_ACCEPTED)
        self._metrics.note_transition()
        await self._start_stt()
        self._stt_task = asyncio.create_task(self._consume_stt_events(), name="stt-events")

        greeting = await self._response_handler.greeting()
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

    async def stream_outbound(self) -> AsyncIterator[AudioToSend]:
        """Yield outbound frames until the pipeline closes (route pump consumes)."""
        sequence = 0
        while True:
            item = await self._outbound_queue.get()
            if item is _OUTBOUND_END:
                break
            sequence += 1
            assert isinstance(item, bytes)
            self._metrics.note_outbound_chunk(len(item))
            yield AudioToSend(
                session_id=self.session.session_id,
                sequence=sequence,
                payload=item,
                audio_format=self.session.audio_format,
            )

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
        self._metrics.emit()

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
            case FinalTranscript():
                if not event.text.strip():
                    self._metrics.note_empty_final()
                    return
                self.session.record_transcript(event)
                self._metrics.note_transcript("final", confidence=event.confidence)
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
        """Process one final transcript through guard → LLM → controller.

        Flow: emergency guard → LLM interpretation → Pydantic validation →
        conversation controller → response/TTS → (optional) backend submission.
        The FSM remains the authority for call state.

        Day 9: serialised per session via `_turn_lock`; duplicate turns are
        rejected; barge-in cancels in-flight TTS; turns are recorded in
        conversation history.
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

            # Record the caller turn in history.
            turn_id = new_turn_id()
            self.session.conversation_state.add_turn(
                turn_id=turn_id,
                speaker=Speaker.CALLER,
                content=utterance.text,
                status=TurnStatus.PROCESSING,
                correlation_id=utterance.sequence,
            )

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

            # --- 1. Emergency guard (pre-LLM, always) ---------------------------
            guard = emergency_guard(utterance.text)
            if guard.matched:
                self._metrics.note_provider_error("safety", guard.rule_id, fatal=False)
                # Emergency invalidates all pending ordinary actions.
                controller = ConversationController(self.session.conversation_state)
                emergency_decision = controller.handle_emergency()
                await self._speak(
                    ConversationDecision(response_text=emergency_decision.response_text),
                    enter_reason=TransitionReason.RESPONSE_STARTED,
                    exit_reason=TransitionReason.RESPONSE_COMPLETED,
                    utterance_id=turn_id,
                )
                self.session.conversation_state.add_turn(
                    turn_id=new_turn_id(),
                    speaker=Speaker.ASSISTANT,
                    content=emergency_decision.response_text,
                    status=TurnStatus.COMPLETED,
                )
                self._metrics.note_turn_complete()
                return

            # --- 2. LLM interpretation ------------------------------------------
            try:
                result = await self._interpret_utterance(utterance)
            except IntentInterpretationError as exc:
                if exc.emergency:
                    # Should not happen (guard runs first), but handle defensively.
                    controller = ConversationController(self.session.conversation_state)
                    emergency_decision = controller.handle_emergency()
                    await self._speak(
                        ConversationDecision(
                            response_text=exc.emergency_script or EMERGENCY_SCRIPT
                        ),
                        enter_reason=TransitionReason.RESPONSE_STARTED,
                        exit_reason=TransitionReason.RESPONSE_COMPLETED,
                        utterance_id=turn_id,
                    )
                    self.session.conversation_state.add_turn(
                        turn_id=new_turn_id(),
                        speaker=Speaker.ASSISTANT,
                        content=emergency_decision.response_text,
                        status=TurnStatus.COMPLETED,
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

            # --- 3. Conversation controller -------------------------------------
            controller = ConversationController(self.session.conversation_state)
            decision = controller.handle_interpretation(result)

            # Practice-information requests go through RAG, not the admin
            # conversation flow.
            if decision.action == "submit_patient_request":
                await self._submit_confirmed_requests(decision, utterance, turn_id=turn_id)
            elif decision.action == "submit_handoff":
                await self._submit_confirmed_handoff(decision, utterance)
            elif decision.action == "submit":
                # Generic submit (from the deterministic handler) — route to
                # practice submission.
                await self._submit_confirmed_requests(decision, utterance, turn_id=turn_id)
            elif decision.action == "cancel":
                await self._speak(
                    ConversationDecision(response_text=decision.response_text),
                    enter_reason=TransitionReason.RESPONSE_STARTED,
                    exit_reason=TransitionReason.RESPONSE_COMPLETED,
                    utterance_id=turn_id,
                )
            elif decision.action == "error":
                await self._speak(
                    ConversationDecision(response_text=decision.response_text),
                    enter_reason=TransitionReason.RESPONSE_STARTED,
                    exit_reason=TransitionReason.RESPONSE_COMPLETED,
                    utterance_id=turn_id,
                )
            elif decision.action == "rag":
                # Practice-information query → retrieval + grounded answer.
                await self._handle_practice_information(decision, utterance, turn_id=turn_id)
            else:
                # clarify or confirm → speak the response text.
                await self._speak(
                    ConversationDecision(response_text=decision.response_text),
                    enter_reason=TransitionReason.RESPONSE_STARTED,
                    exit_reason=TransitionReason.RESPONSE_COMPLETED,
                    utterance_id=turn_id,
                )

            self._metrics.note_turn_complete()

    async def _interpret_utterance(self, utterance: FinalTranscript) -> InterpretationResult:
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
            transcript=utterance.text, session_id=self.session.session_id
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
            await self._speak(
                ConversationDecision(
                    response_text=("I'm sorry — practice submissions aren't available right now.")
                ),
                enter_reason=TransitionReason.RESPONSE_STARTED,
                exit_reason=TransitionReason.RESPONSE_COMPLETED,
                utterance_id=turn_id,
            )
            return

        payload = decision.backend_payload or {}
        requests = payload.get("requests", [])
        for i, req in enumerate(requests):
            key = build_request_idempotency_key(
                practice_id=self._practice_id,
                call_sid=self.session.call_sid or self.session.session_id,
                sequence=utterance.sequence + i,
                request_type=req.get("requestType", "unknown"),
            )
            request_type = req.get("requestType", "unknown")

            # Mark as SUBMITTING before the backend call.
            controller = ConversationController(self.session.conversation_state)
            controller.mark_submitting(
                request_type, turn_id=turn_id or new_turn_id(), idempotency_key=key
            )

            try:
                outcome = await asyncio.wait_for(
                    self._practice_client.submit_request(
                        practice_id=self._practice_id,
                        idempotency_key=key,
                        request_type=req.get("requestType", "unknown"),
                        structured_payload=req.get("structuredPayload", {}),
                        call_id=self.session.call_sid,
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

        # All confirmed → truthful spoken response.
        # Mark each submitted request as SUBMITTED.
        controller = ConversationController(self.session.conversation_state)
        for req in requests:
            controller.handle_backend_success(
                request_type=req.get("requestType", "unknown"),
                idempotency_key=build_request_idempotency_key(
                    practice_id=self._practice_id,
                    call_sid=self.session.call_sid or self.session.session_id,
                    sequence=utterance.sequence,
                    request_type=req.get("requestType", "unknown"),
                ),
            )
        await self._speak(
            ConversationDecision(
                response_text=(
                    "Thank you. I've passed your request to the practice team. "
                    "This is a request — not a confirmed appointment."
                )
            ),
            enter_reason=TransitionReason.RESPONSE_STARTED,
            exit_reason=TransitionReason.RESPONSE_COMPLETED,
            utterance_id=turn_id,
        )

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
                    response_text=("I'm sorry — practice information isn't available right now.")
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
                        "I'm unable to access the practice information just now. "
                        "Please contact the practice directly."
                    )
                ),
                enter_reason=TransitionReason.RESPONSE_STARTED,
                exit_reason=TransitionReason.RESPONSE_COMPLETED,
                utterance_id=turn_id,
            )
            return

        # If no results found, abstain
        if not retrieval_result.chunks:
            await self._speak(
                ConversationDecision(
                    response_text=(
                        "I don't have information about that in the practice "
                        "records available to me. Please contact the practice "
                        "directly."
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
                        "I'm unable to answer that question from the practice "
                        "information available to me. Please contact the practice "
                        "directly."
                    )
                ),
                enter_reason=TransitionReason.RESPONSE_STARTED,
                exit_reason=TransitionReason.RESPONSE_COMPLETED,
                utterance_id=turn_id,
            )
            return

        # Track RAG context for multi-turn follow-ups.
        topic = self._extract_topic(query_text)
        self.session.conversation_state.last_grounded_answer = answer_result.answer.answer
        self.session.conversation_state.last_grounded_topic = topic
        self.session.conversation_state.last_grounded_citation_ids = [
            c["chunk_id"] for c in answer_result.answer.citations
        ]
        self.session.conversation_state.last_grounded_turn_id = turn_id

        # Speak the grounded answer
        await self._speak(
            ConversationDecision(response_text=answer_result.answer.response_text),
            enter_reason=TransitionReason.RESPONSE_STARTED,
            exit_reason=TransitionReason.RESPONSE_COMPLETED,
            utterance_id=turn_id,
        )

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
            case SynthesisChunk():
                self._framer.push(event.payload)
                # Steady-state chunking policy: emit once ≥ TARGET, ≤ MIN per turn tail.
                for chunk in self._framer.ready_chunks():
                    self._outbound_queue.put_nowait(chunk)
                self._metrics.note_tts_chunk(len(event.payload))
            case SynthesisCompleted():
                if event.cancelled:
                    self.session.tts_state = ProviderState.STOPPED
                self._metrics.note_tts_completed()

    # --- barge-in ------------------------------------------------------------

    async def cancel_tts(self) -> None:
        """Cancel in-flight TTS for barge-in (interruption handling).

        Called by the route when a new inbound media frame arrives while the
        pipeline is in SPEAKING state. Discards queued outbound audio and
        signals the TTS provider to stop synthesis.
        """
        if self._current_tts_utterance_id is None:
            return
        # Discard any queued outbound frames for the interrupted turn.
        while not self._outbound_queue.empty():
            try:
                self._outbound_queue.get_nowait()
            except Exception:  # noqa: BLE001 — best effort
                break
        self._framer.discard()
        if hasattr(self._tts, "cancel_turn"):
            try:
                await self._tts.cancel_turn(self.session.session_id, self._current_tts_utterance_id)
            except Exception:  # noqa: BLE001 — cancellation is best-effort
                logger.warning(
                    "tts cancel failed",
                    extra={"session_id": self.session.session_id},
                )
        self._current_tts_utterance_id = None

    # --- introspection --------------------------------------------------------------

    @property
    def metrics(self) -> SessionMetrics:
        return self._metrics

    @property
    def framer(self) -> OutboundAudioFramer:
        return self._framer
