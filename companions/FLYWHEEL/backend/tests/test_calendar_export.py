from __future__ import annotations

from datetime import UTC, datetime

from foundry.services.calendar_export import (
    build_ical_calendar,
    escape_ical_text,
    fold_ical_line,
)


def _unfold(payload: bytes) -> str:
    return payload.replace(b"\r\n ", b"").decode("utf-8")


def test_text_escaping_normalizes_newlines_and_reserved_characters() -> None:
    assert escape_ical_text("一,二;三\\四\r\n五\r六") == "一\\,二\\;三\\\\四\\n五\\n六"


def test_utf8_folding_never_splits_characters_or_exceeds_75_octets() -> None:
    logical = "SUMMARY:" + "突破性研究，" * 20 + "end"
    physical = fold_ical_line(logical)

    assert len(physical) > 1
    assert all(len(line.encode("utf-8")) <= 75 for line in physical)
    assert all(line.startswith(" ") for line in physical[1:])
    assert "".join((line[1:] if index else line) for index, line in enumerate(physical)) == logical


def test_calendar_bytes_are_crlf_folded_and_keep_forecast_tentative() -> None:
    deadlines = [
        {
            "venue_key": "OFFICIAL",
            "display_name": "官方会议,主会场;A\\B",
            "conference_year": 2027,
            "deadline_date": "2027-01-02",
            "timezone": "AoE",
            "round_note": "full",
            "evidence_status": "official_confirmed",
            "source_url": "https://official.example/cfp",
        },
        {
            "venue_key": "FORECAST",
            "display_name": "预测会议" * 18,
            "conference_year": 2027,
            "deadline_date": "2027-05-06",
            "timezone": "AoE",
            "evidence_status": "forecast",
            "forecast_window_start": "2027-04-20",
            "forecast_window_end": "2027-05-20",
            "source_url": "https://history.example/prior-cycle",
        },
    ]
    reminders = [{
        "id": "r-1",
        "trigger_at": "2027-01-01T09:30:00+08:00",
        "title": "人工复核,不要当成;官方日期",
    }]

    payload = build_ical_calendar(
        deadlines,
        reminders,
        generated_at=datetime(2026, 8, 23, 0, 0, tzinfo=UTC),
    )

    assert payload.endswith(b"\r\n")
    assert b"\n" not in payload.replace(b"\r\n", b"")
    assert all(len(line) <= 75 for line in payload.split(b"\r\n") if line)
    unfolded = _unfold(payload)
    assert "DTSTAMP:20260823T000000Z" in unfolded
    assert "STATUS:CONFIRMED\r\n" in unfolded
    assert "[OFFICIAL CONFIRMED]" in unfolded
    assert "X-ARGUS-SOURCE-ROLE:OFFICIAL-DEADLINE-EVIDENCE" in unfolded
    assert "STATUS:TENTATIVE\r\n" in unfolded
    assert "[FORECAST — NOT ANNOUNCED]" in unfolded
    assert "PLANNING ESTIMATE — NOT AN ANNOUNCED DEADLINE." in unfolded
    assert "X-ARGUS-REQUIRES-CONFIRMATION:TRUE" in unfolded
    assert "X-ARGUS-FORECAST-WINDOW-START:2027-04-20" in unfolded
    assert "X-ARGUS-SOURCE-ROLE:FORECAST-BASIS" in unfolded
    assert "官方会议\\,主会场\\;A\\\\B" in unfolded
    assert "X-ARGUS-EVENT-KIND:WORKFLOW-REMINDER" in unfolded
