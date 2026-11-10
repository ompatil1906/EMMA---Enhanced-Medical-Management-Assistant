"""Excel-compatible call record and appointment spreadsheet log.

Stores callers' intake form details (including email), call summaries, request labels,
and appointment dates and times into an Excel-compatible CSV sheet (`data/call_records.csv`).
"""

from __future__ import annotations

import csv
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.call_summary import CallSummary
from app.services.caller_identity import CallerIdentity

logger = logging.getLogger(__name__)

DEFAULT_CSV_PATH = Path("data/call_records.csv")

HEADERS = [
    "Call ID",
    "Timestamp (UTC)",
    "First Name",
    "Last Name",
    "Email",
    "Mobile",
    "Intake Reason",
    "Request Label",
    "Appointment Date",
    "Appointment Time",
    "Outcome",
    "Turns",
    "Duration (sec)",
    "Call Summary Headline",
    "Call Summary Details",
    "Jira Issue Key",
]


def record_call_to_sheet(
    *,
    call_id: str,
    summary: CallSummary,
    caller: CallerIdentity | None,
    jira_issue_key: str | None = None,
    filepath: Path | str = DEFAULT_CSV_PATH,
) -> Path:
    """Append one call's complete record to the Excel-compatible CSV sheet.

    Ensures the header exists if the file is new. Thread-safe for atomic appends.
    """
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    file_exists = path.exists() and path.stat().st_size > 0

    # Extract appointment date/time and request labels from summary tasks
    request_labels: list[str] = []
    appointment_date = ""
    appointment_time = ""

    for task in summary.tasks:
        req_type = str(task.get("requestType", ""))
        label = str(task.get("title", req_type))
        if label and label not in request_labels:
            request_labels.append(label)

        # Check if appointment details exist in the task
        details = task.get("details") or {}
        if isinstance(details, dict):
            if "preferred_date" in details and details["preferred_date"]:
                appointment_date = str(details["preferred_date"])
            elif "date" in details and details["date"]:
                appointment_date = str(details["date"])

            if "preferred_time" in details and details["preferred_time"]:
                appointment_time = str(details["preferred_time"])
            elif "time" in details and details["time"]:
                appointment_time = str(details["time"])

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    row = [
        call_id,
        now_iso,
        (caller.first_name if caller else "") or summary.caller.get("firstName", ""),
        (caller.last_name if caller else "") or summary.caller.get("lastName", ""),
        (caller.email if caller else "") or summary.caller.get("email", ""),
        (caller.mobile if caller else "") or summary.caller.get("mobile", ""),
        (caller.reason if caller else "") or summary.caller.get("reason", ""),
        "; ".join(request_labels) if request_labels else "General Inquiry",
        appointment_date,
        appointment_time,
        summary.outcome,
        summary.turns,
        summary.duration_seconds,
        summary.headline,
        " ".join(summary.bullets),
        jira_issue_key or "N/A",
    ]

    try:
        with open(path, mode="a", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(HEADERS)
            writer.writerow(row)
        logger.info(
            "Recorded call summary to Excel sheet",
            extra={"path": str(path), "call_id": call_id, "email": row[4]},
        )
    except Exception as exc:
        logger.error("Failed to append call record to sheet: %s", exc)

    return path


__all__ = ["DEFAULT_CSV_PATH", "HEADERS", "record_call_to_sheet"]
