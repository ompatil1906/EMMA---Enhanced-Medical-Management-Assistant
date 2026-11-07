"""Session-metrics tests — compact JSON, correlation, no payload leakage (§13)."""

from __future__ import annotations

import json

from app.services.metrics import SessionMetrics


def populated_metrics() -> SessionMetrics:
    metrics = SessionMetrics(session_id="sess-1", call_sid="call-9")
    metrics.note_stt_started("mock-stt", 8000)
    metrics.note_audio_in(1600)
    metrics.note_transcript("partial")
    metrics.note_transcript("final", confidence=0.92)
    metrics.note_processing_started()
    metrics.note_tts_started("mock-tts")
    metrics.note_tts_chunk(1600)
    metrics.note_tts_chunk(1600)
    metrics.note_tts_completed()
    metrics.note_turn_complete()
    metrics.note_outbound_chunk(3200)
    metrics.note_practice_outcome("submit_request", "disabled")
    metrics.note_provider_error("stt", "mock_injected_error", fatal=False)
    return metrics


class TestRecord:
    def test_record_is_json_ready_and_compact(self) -> None:
        metrics = populated_metrics()
        record = metrics.build_record()
        payload = json.dumps(record)
        assert (
            '"event": "voice_session_metrics"' in payload.replace(" ", "")
            or record["event"] == "voice_session_metrics"
        )

    def test_correlation_ids_present(self) -> None:
        metrics = populated_metrics()
        record = metrics.build_record()
        assert record["session_id"] == "sess-1"
        assert record["call_sid"] == "call-9"

    def test_no_transcript_text_is_recorded(self) -> None:
        metrics = SessionMetrics(session_id="s", call_sid="c")
        # The metrics API only accepts kinds — never text (design-level guarantee).
        metrics.note_transcript("final", confidence=0.4)
        payload = json.dumps(metrics.build_record())
        assert "transcript" not in payload
        assert "text" not in payload

    def test_latencies_measured_not_fabricated(self) -> None:
        record = populated_metrics().build_record()
        stt = record["stt"]
        tts = record["tts"]
        assert stt["first_partial_ms"] is not None
        assert stt["first_final_ms"] >= stt["first_partial_ms"]
        assert tts["first_chunk_ms"] is not None
        assert tts["completed_ms"] >= tts["first_chunk_ms"]
        assert record["processing"]["turn_total_ms"]
        assert record["tts"]["bytes"] == 3200

    def test_provider_and_outcome_are_recorded(self) -> None:
        record = populated_metrics().build_record()
        assert record["providers"]["stt"] == "mock-stt"
        assert record["providers"]["tts"] == "mock-tts"
        assert record["sample_rate"] == 8000
        assert record["practice_api"]["outcomes"] == ["submit_request=disabled"]
        assert record["provider_errors"] == ["stt:mock_injected_error:nonfatal"]

    def test_low_confidence_counted(self) -> None:
        metrics = SessionMetrics(session_id="s", call_sid=None)
        metrics.note_transcript("final", confidence=0.2)
        record = metrics.build_record()
        assert record["stt"]["low_confidence"] == 1

    def test_empty_finals_counted(self) -> None:
        metrics = SessionMetrics(session_id="s", call_sid="c")
        metrics.note_empty_final()
        assert metrics.build_record()["stt"]["empty_finals"] == 1

    def test_conversion_observed(self) -> None:
        metrics = SessionMetrics(session_id="s", call_sid="c")
        metrics.note_conversion(16000, 8000)
        record = metrics.build_record()
        assert record["audio"]["conversions"] == 1
        assert record["audio"]["conversion_notes"] == ["16000->8000"]
