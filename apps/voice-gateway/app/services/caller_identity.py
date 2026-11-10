"""Caller identity captured from the browser intake form (Day 19).

The browser form collects a first name, last name, mobile number and a stated
reason for calling before the call starts. Only the **first name** is ever used
to address the caller; the whole record is kept server-side for the call record
and the Jira record (masked by policy — see `app.services.call_summary`).

Safety rules enforced here (single place, used by every transport):

* Nothing typed by a caller is ever spoken or persisted verbatim: values are
  normalised and length-capped, and names are restricted to letters, spaces,
  apostrophes and hyphens. Anything else yields `None` rather than an
  interpreter-friendly surprise.
* A name is optional. A missing/invalid name degrades to the generic greeting —
  never a guess and never a raw echo.
* Identity never carries the practice id: that stays server-authoritative.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_MAX_NAME_CHARS = 40
_MAX_REASON_CHARS = 200
_MAX_MOBILE_CHARS = 20

_NAME_ALLOWED = re.compile(r"[^A-Za-z' -]")
_NAME_STRIP = re.compile(r"^[^A-Za-z]+|[^A-Za-z]+$")
_MULTI_SPACE = re.compile(r"\s+")
_MOBILE_ALLOWED = re.compile(r"[^0-9+ ]")

# Control characters would break TTS phrasing and log/Jira formatting.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _clean_text(raw: Any, *, max_chars: int) -> str:
    """Normalise one free-text field: control-free, single-spaced, capped."""
    if not isinstance(raw, str):
        return ""
    text = unicodedata.normalize("NFKC", raw)
    text = _CONTROL_CHARS.sub(" ", text)
    text = _MULTI_SPACE.sub(" ", text).strip()
    return text[:max_chars].strip()


def normalize_first_name(raw: Any) -> str | None:
    """Return a speak-safe first name, or None when nothing usable was given.

    Letters, spaces, apostrophes and hyphens only; surrounding junk is trimmed
    so `"  sarah! "` becomes `"Sarah"`. Names that clean down to nothing (digits,
    emoji, punctuation, over-long payloads) yield None.
    """
    text = _clean_text(raw, max_chars=_MAX_NAME_CHARS)
    if not text:
        return None
    text = _NAME_ALLOWED.sub("", text)
    text = _MULTI_SPACE.sub(" ", text).strip(" -'")
    text = _NAME_STRIP.sub("", text)
    if not text or len(text) > _MAX_NAME_CHARS:
        return None
    return _title_case(text)


def _title_case(text: str) -> str:
    """Title-case each word, including hyphen/apostrophe compounds.

    "sarah-jane" → "Sarah-Jane", "o'brien" → "O'Brien", "van der berg" →
    "Van Der Berg". Nothing else about the name is rewritten.
    """
    words: list[str] = []
    for word in text.split(" "):
        pieces = re.split(r"([\-' ])", word)
        words.append(
            "".join(
                piece if piece in ("-", "'", " ", "") else piece[:1].upper() + piece[1:].lower()
                for piece in pieces
            )
        )
    return " ".join(words)


def normalize_last_name(raw: Any) -> str | None:
    """Same policy as the first name (used for the call record only)."""
    return normalize_first_name(raw)


def normalize_reason(raw: Any) -> str | None:
    """Free-text 'reason for calling' — trimmed, single-spaced, capped."""
    text = _clean_text(raw, max_chars=_MAX_REASON_CHARS)
    return text or None


def normalize_mobile(raw: Any) -> str | None:
    """Keep digits/spaces/leading '+' only (never spoken, never in a URL)."""
    text = _clean_text(raw, max_chars=_MAX_MOBILE_CHARS)
    if not text:
        return None
    text = _MOBILE_ALLOWED.sub("", text)
    text = _MULTI_SPACE.sub(" ", text).strip()
    if sum(ch.isdigit() for ch in text) < 7:
        return None
    return text


@dataclass(frozen=True, slots=True)
class CallerIdentity:
    """One caller's intake record (all fields optional and normalised)."""

    first_name: str | None = None
    last_name: str | None = None
    mobile: str | None = None
    reason: str | None = None

    @property
    def display_name(self) -> str | None:
        """`"Sarah J."` for records; None when no name was supplied."""
        if not self.first_name:
            return None
        if self.last_name:
            return f"{self.first_name} {self.last_name[:1]}."
        return self.first_name

    @property
    def has_any(self) -> bool:
        return any((self.first_name, self.last_name, self.mobile, self.reason))


_EMPTY = CallerIdentity()


def parse_caller(payload: Any) -> CallerIdentity:
    """Build a sanitised `CallerIdentity` from an untrusted mapping.

    Accepts both snake_case (token endpoint / gateway) and camelCase (browser)
    keys so the UI contract stays natural while Python stays consistent.
    """
    if not isinstance(payload, Mapping):
        return _EMPTY

    def pick(*keys: str) -> Any:
        for key in keys:
            if key in payload:
                return payload[key]
        return None

    identity = CallerIdentity(
        first_name=normalize_first_name(pick("first_name", "firstName")),
        last_name=normalize_last_name(pick("last_name", "lastName")),
        mobile=normalize_mobile(pick("mobile", "phone")),
        reason=normalize_reason(pick("reason", "reason_for_call", "reasonForCall")),
    )
    return identity if identity.has_any else _EMPTY


def parse_first_name_param(raw: Any) -> CallerIdentity:
    """Build a caller from a single transport query parameter (name only)."""
    return CallerIdentity(first_name=normalize_first_name(raw))


def first_name_from_participant(participant: Any) -> str | None:
    """Extract a sanitised first name from a LiveKit participant.

    Order: participant `name` (set server-side when minting the token), then the
    token metadata JSON (`{"first_name": ...}`), then None. Metadata parsing is
    defensive: any malformed/absent value simply yields None.
    """
    name = normalize_first_name(getattr(participant, "name", None))
    if name:
        return name

    metadata = getattr(participant, "metadata", None)
    if not isinstance(metadata, str) or not metadata.strip():
        return None
    import json

    try:
        parsed = json.loads(metadata)
    except ValueError:
        return None
    if isinstance(parsed, dict):
        return normalize_first_name(parsed.get("first_name"))
    return None


__all__ = [
    "CallerIdentity",
    "first_name_from_participant",
    "normalize_first_name",
    "normalize_last_name",
    "normalize_mobile",
    "normalize_reason",
    "parse_caller",
    "parse_first_name_param",
]
