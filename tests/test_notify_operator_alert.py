"""Tests for the always-on, telegram-independent operator-alert landing."""
from __future__ import annotations

from argus_skill.life import notify
from argus_skill.life.notify import (
    OPERATOR_ATTENTION_KINDS,
    dispatch_journal_entry,
)


def test_loud_kind_lands_in_alert_file_with_no_push_channel(tmp_path, monkeypatch, caplog):
    # No webhook/cmd, telegram off (the live default) — a stall escalation must
    # still land locally so the operator can see a stuck project.
    alert = tmp_path / "operator_alerts.log"
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_ALERT_FILE", str(alert))
    monkeypatch.delenv("ARGUS_SKILL_NOTIFY_WEBHOOK", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_NOTIFY_CMD", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_ENABLE_TELEGRAM", raising=False)

    with caplog.at_level("WARNING", logger=notify.log.name):
        dispatch_journal_entry({
            "kind": "planner_stall_escalation",
            "title": "operator attention: project stalled",
            "summary": "3 consecutive missions with no forward progress",
            "ts": 1782200000.0,
        })

    assert alert.exists()
    body = alert.read_text(encoding="utf-8")
    assert "planner_stall_escalation" in body
    assert "project stalled" in body
    assert "2026-" in body  # timestamped from ts
    assert any("OPERATOR ATTENTION" in r.message for r in caplog.records)


def test_routine_kind_does_not_land(tmp_path, monkeypatch):
    # A routine mission_complete must NOT spam the operator-alert file.
    alert = tmp_path / "operator_alerts.log"
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_ALERT_FILE", str(alert))
    dispatch_journal_entry({
        "kind": "mission_complete",
        "title": "done",
        "summary": "ok",
        "ts": 1782200000.0,
    })
    assert not alert.exists()
    assert "mission_complete" not in OPERATOR_ATTENTION_KINDS


def test_non_notify_kind_is_ignored(tmp_path, monkeypatch):
    alert = tmp_path / "operator_alerts.log"
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_ALERT_FILE", str(alert))
    dispatch_journal_entry({"kind": "engineer.progress", "title": "x", "summary": "y"})
    assert not alert.exists()


def test_alert_write_failure_is_failsoft(monkeypatch):
    # An unwritable path must never raise out of the notify path.
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_ALERT_FILE", "/proc/cannot/write/here.log")
    dispatch_journal_entry({
        "kind": "auth_failure", "title": "auth dead", "summary": "token 403", "ts": 1.0,
    })  # must not raise
