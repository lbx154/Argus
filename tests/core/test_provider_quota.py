from __future__ import annotations

from argus_skill.core.provider_quota import (
    acquire_codex_permit,
    codex_quota_snapshot,
    provider_usage_snapshot,
)


def test_codex_daily_cap_blocks_after_counted_start(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_CODEX_GUARD", "1")
    monkeypatch.setenv("ARGUS_SKILL_CODEX_DAILY_CALL_CAP", "1")

    first = acquire_codex_permit("engineer-r1")
    assert first.allowed
    assert first.daily_calls == 1
    first.finish(success=True)

    blocked = acquire_codex_permit("reviewer")
    assert not blocked.allowed
    assert "daily call cap 1 reached" in blocked.reason
    snapshot = codex_quota_snapshot()
    assert snapshot["daily_calls"] == 1
    assert snapshot["completed_calls"] == 1
    assert snapshot["remaining"] == 0


def test_provider_snapshot_combines_codex_and_copilot(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_CODEX_GUARD", "1")
    monkeypatch.setenv("ARGUS_SKILL_CODEX_DAILY_CALL_CAP", "5")
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_GUARD", "1")
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_DAILY_CALL_CAP", "7")
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_DAILY_PREMIUM_CAP", "11")

    permit = acquire_codex_permit("matcher")
    permit.finish(success=False, error_text="test failure")
    snapshot = provider_usage_snapshot()

    assert snapshot["codex"]["daily_calls"] == 1
    assert snapshot["codex"]["daily_cap"] == 5
    assert snapshot["codex"]["failed_calls"] == 1
    assert snapshot["copilot"]["daily_cap"] == 7
    assert snapshot["copilot"]["premium_cap"] == 11
