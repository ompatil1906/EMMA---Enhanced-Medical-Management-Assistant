"""Day 20: observability (latency percentiles, diagnostics endpoint).

Pipecat reports per-service TTFB/TTFA; EMMA records the same numbers per session.
These tests pin that the aggregation is correct, bounded and PII-free, and that
`GET /api/v1/diagnostics` + `GET /api/v1/tools` expose them without leaking
transcripts, arguments or credentials.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.observability import LatencyRegistry, SessionRecord, percentiles


def _metrics_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "session_id": "session-abc",
        "call_sid": "call-abc",
        "providers": {"stt": "mock", "tts": "mock", "llm": "mock"},
        "latency": {
            "final_to_processing_ms": [12.0, 20.0],
            "processing_to_first_audio_ms": [300.0, 500.0],
            "turn_total_ms": [600.0, 900.0],
            "llm_ms": [200.0, 300.0],
            "tts_ms": [80.0, 120.0],
        },
        "turns": {"dropped": 1, "held": 0, "interrupted": 2, "interruption_latency_ms": [45.0]},
        "vad": {"speech_starts": 3, "early_barge_ins": 2, "barge_in_latency_ms": [30.0, 60.0]},
        "provider_errors": [],
        "errors": [],
    }
    record.update(overrides)
    return record


def test_percentiles_are_correct_and_bounded() -> None:
    assert percentiles([]) == {"p50": None, "p95": None, "max": None, "count": 0}
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    result = percentiles(values)
    assert result["p50"] == 30.0
    assert result["p95"] == 50.0
    assert result["max"] == 50.0
    assert result["count"] == 5


def test_registry_adapts_metrics_and_aggregates() -> None:
    registry = LatencyRegistry()
    registry.record_from_metrics(metrics_record=_metrics_record(), started_at=0.0)
    registry.record_from_metrics(
        metrics_record=_metrics_record(
            session_id="session-def",
            latency={"turn_total_ms": [1200.0], "llm_ms": [400.0]},
        ),
        started_at=0.0,
    )
    snapshot = registry.snapshot()
    assert snapshot["sessions_recorded"] == 2
    assert snapshot["providers"]["stt"] == "mock"
    assert snapshot["latency_ms"]["turn_total_ms"]["count"] == 3
    assert snapshot["latency_ms"]["turn_total_ms"]["max"] == 1200.0
    assert snapshot["counters"]["interruptions"] == 4
    assert snapshot["counters"]["early_barge_ins"] == 4


def test_registry_is_bounded() -> None:
    registry = LatencyRegistry(maxlen=3)
    for i in range(10):
        registry.record_from_metrics(
            metrics_record=_metrics_record(session_id=f"s{i}"), started_at=0.0
        )
    assert registry.size == 3
    assert [s["session_id"] for s in registry.recent()] == ["s7", "s8", "s9"]


def test_record_never_carries_transcript_text() -> None:
    registry = LatencyRegistry()
    record = registry.record_from_metrics(
        metrics_record=_metrics_record(
            errors=["stt:recognition_error:nonfatal"],
        ),
        started_at=0.0,
    )
    assert isinstance(record, SessionRecord)
    assert "transcript" not in str(record.counters).lower()
    # Errors are short typed phrases, never payloads.
    assert record.errors == ("stt:recognition_error:nonfatal",)


def test_session_record_duration_is_non_negative() -> None:
    record = SessionRecord(session_id="s", call_sid=None, started_at=10.0, ended_at=5.0)
    assert record.duration_seconds == 0.0


# --- HTTP surface ---------------------------------------------------------


def _client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app())


def test_tools_endpoint_lists_catalog_and_schemas() -> None:
    response = _client().get("/api/v1/tools")
    assert response.status_code == 200
    body = response.json()
    names = {tool["name"] for tool in body["tools"]}
    assert names == {
        "lookup_practice_information",
        "create_appointment_request",
        "request_human_handoff",
    }
    # Tool schemas are safe to expose; handlers and secrets are not.
    assert all("handler" not in tool for tool in body["tools"])
    declarations = {d["name"]: d for d in body["function_declarations"]}
    assert "parameters" in declarations["create_appointment_request"]


def test_diagnostics_endpoint_reports_latency_and_config() -> None:
    response = _client().get("/api/v1/diagnostics")
    assert response.status_code == 200
    body = response.json()

    assert body["providers"]["stt"] in {"mock", "azure", "deepgram"}
    assert "vad_enabled" in body["pipeline"]
    assert "turn_hold_ms" in body["pipeline"]
    assert set(body["latency"]) == {"sessions_recorded", "providers", "counters", "latency_ms"}
    assert isinstance(body["tools"], list)

    serialised = str(body).lower()
    for secret_marker in ("api_key", "api_token", "secret", "password", "transcript"):
        assert secret_marker not in serialised
