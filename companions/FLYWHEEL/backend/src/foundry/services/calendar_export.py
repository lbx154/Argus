"""Small, deterministic RFC 5545 calendar serializer.

The exporter keeps forecast planning points visibly tentative.  It also emits
bytes directly so UTF-8 content-line folding can be checked before Starlette
constructs the HTTP response.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from typing import Any, Mapping, Sequence

_CONTENT_LINE_LIMIT = 75


def escape_ical_text(value: Any) -> str:
    """Escape an RFC 5545 TEXT value without allowing line injection."""

    normalized = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return (
        normalized.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def fold_ical_line(line: str) -> tuple[str, ...]:
    """Fold one logical content line at UTF-8 character boundaries.

    RFC 5545 limits content lines to 75 octets, excluding CRLF.  Continuation
    lines begin with one space, leaving 74 octets for their payload.
    """

    if "\r" in line or "\n" in line:
        raise ValueError("iCalendar logical lines must not contain CR or LF")
    if not line:
        return ("",)

    physical: list[str] = []
    chunk: list[str] = []
    chunk_octets = 0
    capacity = _CONTENT_LINE_LIMIT
    continuation = False
    for character in line:
        octets = len(character.encode("utf-8"))
        if chunk and chunk_octets + octets > capacity:
            physical.append((" " if continuation else "") + "".join(chunk))
            chunk = []
            chunk_octets = 0
            continuation = True
            capacity = _CONTENT_LINE_LIMIT - 1
        if octets > capacity:
            # A Unicode scalar is at most four UTF-8 octets, so this is only a
            # defensive guard if the content-line limit is ever misconfigured.
            raise ValueError("a single character exceeds the iCalendar fold limit")
        chunk.append(character)
        chunk_octets += octets
    physical.append((" " if continuation else "") + "".join(chunk))
    return tuple(physical)


def _safe_uri(value: Any) -> str | None:
    uri = str(value or "").strip()
    if not uri or "\r" in uri or "\n" in uri:
        return None
    return uri


def _stable_deadline_uid(row: Mapping[str, Any]) -> str:
    identity = "\x1f".join(str(row.get(key) or "") for key in (
        "venue_key", "conference_year", "deadline_date", "round_note",
    ))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return f"deadline-{digest}@argus-foundry"


def _deadline_lines(row: Mapping[str, Any], stamp: str) -> list[str]:
    try:
        deadline_day = date.fromisoformat(str(row.get("deadline_date") or ""))
    except ValueError:
        return []
    evidence = str(row.get("evidence_status") or "forecast").casefold()
    display_name = str(row.get("display_name") or row.get("venue_key") or "Venue")
    timezone = str(row.get("timezone") or "unspecified")
    source_url = _safe_uri(row.get("source_url"))

    if evidence == "official_confirmed":
        event_status = "CONFIRMED"
        summary = f"{display_name} full-paper deadline [OFFICIAL CONFIRMED]"
        description = (
            "Official deadline evidence was verified on a conference or organizer source. "
            f"Deadline timezone: {timezone}."
        )
        source_role = "OFFICIAL-DEADLINE-EVIDENCE"
    elif evidence == "forecast":
        event_status = "TENTATIVE"
        summary = f"{display_name} forecast planning point [FORECAST — NOT ANNOUNCED]"
        window_start = str(row.get("forecast_window_start") or "unknown")
        window_end = str(row.get("forecast_window_end") or "unknown")
        description = (
            "PLANNING ESTIMATE — NOT AN ANNOUNCED DEADLINE. "
            f"Point estimate: {deadline_day.isoformat()}; forecast window: "
            f"{window_start} to {window_end}; timezone assumption: {timezone}. "
            "Official confirmation is required before submission decisions."
        )
        source_role = "FORECAST-BASIS"
    else:
        event_status = "TENTATIVE"
        summary = f"{display_name} full-paper date [{evidence.upper()} — VERIFY]"
        description = (
            f"Unconfirmed deadline evidence ({evidence}); deadline timezone: {timezone}. "
            "Verify against an official conference or organizer source before relying on it."
        )
        source_role = "UNCONFIRMED-EVIDENCE"

    lines = [
        "BEGIN:VEVENT",
        f"UID:{_stable_deadline_uid(row)}",
        f"DTSTAMP:{stamp}",
        f"DTSTART;VALUE=DATE:{deadline_day.strftime('%Y%m%d')}",
        f"STATUS:{event_status}",
        "CLASS:PUBLIC",
        f"SUMMARY:{escape_ical_text(summary)}",
        f"DESCRIPTION:{escape_ical_text(description)}",
        "X-ARGUS-EVENT-KIND:SUBMISSION-DEADLINE",
        f"X-ARGUS-EVIDENCE-STATUS:{evidence.upper()}",
        f"X-ARGUS-REQUIRES-CONFIRMATION:{'TRUE' if evidence != 'official_confirmed' else 'FALSE'}",
    ]
    if evidence == "forecast":
        lines.extend([
            f"X-ARGUS-FORECAST-WINDOW-START:{row.get('forecast_window_start') or 'UNKNOWN'}",
            f"X-ARGUS-FORECAST-WINDOW-END:{row.get('forecast_window_end') or 'UNKNOWN'}",
        ])
    if source_url:
        lines.extend([f"URL:{source_url}", f"X-ARGUS-SOURCE-ROLE:{source_role}"])
    lines.append("END:VEVENT")
    return lines


def _reminder_lines(row: Mapping[str, Any], stamp: str) -> list[str]:
    try:
        parsed = datetime.fromisoformat(str(row.get("trigger_at") or ""))
    except ValueError:
        return []
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    start = parsed.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    reminder_id = str(row.get("id") or "")
    if not reminder_id:
        return []
    return [
        "BEGIN:VEVENT",
        f"UID:reminder-{reminder_id}@argus-foundry",
        f"DTSTAMP:{stamp}",
        f"DTSTART:{start}",
        "STATUS:CONFIRMED",
        f"SUMMARY:{escape_ical_text(row.get('title'))}",
        "DESCRIPTION:Flywheel workflow reminder\\; not a conference deadline.",
        "X-ARGUS-EVENT-KIND:WORKFLOW-REMINDER",
        "END:VEVENT",
    ]


def build_ical_calendar(
    deadlines: Sequence[Mapping[str, Any]],
    reminders: Sequence[Mapping[str, Any]],
    *,
    generated_at: datetime | None = None,
) -> bytes:
    """Serialize the Flywheel deadline and reminder feed as UTF-8 RFC 5545."""

    moment = generated_at or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    stamp = moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    logical_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Argus Research Data Flywheel//Deadlines//ZH-CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Argus Research Deadlines",
    ]
    for row in deadlines:
        logical_lines.extend(_deadline_lines(row, stamp))
    for row in reminders:
        logical_lines.extend(_reminder_lines(row, stamp))
    logical_lines.append("END:VCALENDAR")

    physical_lines = [
        physical
        for logical in logical_lines
        for physical in fold_ical_line(logical)
    ]
    payload = ("\r\n".join(physical_lines) + "\r\n").encode("utf-8")
    if any(len(line) > _CONTENT_LINE_LIMIT for line in payload.split(b"\r\n") if line):
        raise AssertionError("iCalendar content line exceeds 75 octets")
    return payload
