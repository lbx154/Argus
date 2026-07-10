from __future__ import annotations

from argus_skill.core.copilot_guard import (
    acquire_copilot_permit,
    copilot_guard_snapshot,
    release_denied_permit,
)


def _enable(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_GUARD", "1")
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_SLOT_WAIT_S", "0")
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_DAILY_CALL_CAP", "100")
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_HOURLY_CALL_CAP", "100")
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_DAILY_PREMIUM_CAP", "100")


def test_daily_premium_cap_blocks_the_next_provider_call(monkeypatch, tmp_path) -> None:
    _enable(monkeypatch, tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_DAILY_PREMIUM_CAP", "2")

    first = acquire_copilot_permit("engineer-r1")
    assert first.allowed
    first.finish(premium_requests=2.0, success=True)

    blocked = acquire_copilot_permit("reviewer")
    assert not blocked.allowed
    assert "daily premium cap" in blocked.reason
    release_denied_permit(blocked)


def test_policy_denial_opens_a_shared_circuit(monkeypatch, tmp_path) -> None:
    _enable(monkeypatch, tmp_path)

    first = acquire_copilot_permit("reviewer")
    assert first.allowed
    first.finish(
        error_text="Error: Access denied by policy settings",
        success=False,
    )

    blocked = acquire_copilot_permit("manager-stage")
    assert not blocked.allowed
    assert "policy/subscription access denied" in blocked.reason
    release_denied_permit(blocked)
    assert copilot_guard_snapshot()["blocked_until"] > 0


def test_hourly_call_cap_counts_provider_starts(monkeypatch, tmp_path) -> None:
    _enable(monkeypatch, tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_HOURLY_CALL_CAP", "1")

    first = acquire_copilot_permit("matcher")
    assert first.allowed
    first.finish(success=True)

    blocked = acquire_copilot_permit("matcher")
    assert not blocked.allowed
    assert "hourly call cap" in blocked.reason
    release_denied_permit(blocked)


def test_cross_process_slot_cap_refuses_parallel_call(monkeypatch, tmp_path) -> None:
    _enable(monkeypatch, tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_MAX_CONCURRENCY", "1")

    first = acquire_copilot_permit("engineer-r1")
    assert first.allowed
    blocked = acquire_copilot_permit("reviewer")
    assert not blocked.allowed
    assert "concurrency cap" in blocked.reason
    release_denied_permit(blocked)
    first.finish(success=True)


def test_denied_cap_does_not_leak_a_concurrency_slot(monkeypatch, tmp_path) -> None:
    _enable(monkeypatch, tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_DAILY_PREMIUM_CAP", "1")

    first = acquire_copilot_permit("engineer")
    first.finish(premium_requests=1.0, success=True)
    blocked = acquire_copilot_permit("reviewer")
    assert not blocked.allowed

    # The denial itself must release the slot; callers should not have to know
    # that a preflight lock was briefly acquired.
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_DAILY_PREMIUM_CAP", "0")
    next_call = acquire_copilot_permit("planner")
    assert next_call.allowed
    next_call.finish(success=True)


def test_guard_accounting_failure_is_fail_soft(monkeypatch, tmp_path) -> None:
    _enable(monkeypatch, tmp_path)
    permit = acquire_copilot_permit("engineer")
    assert permit.allowed
    monkeypatch.setattr(
        "argus_skill.core.copilot_guard._write_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    permit.finish(premium_requests=1.0, success=True)
