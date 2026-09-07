from __future__ import annotations

import pytest

from argus_skill.provider_integrations import copilot_guard
from argus_skill.provider_integrations.copilot_guard import (
    acquire_copilot_permit,
    copilot_guard_snapshot,
    release_denied_permit,
)


def test_default_guard_caps_are_10000() -> None:
    assert copilot_guard._DEFAULT_DAILY_PREMIUM_CAP == 10_000.0
    assert copilot_guard._DEFAULT_DAILY_CALL_CAP == 10_000
    assert copilot_guard._DEFAULT_HOURLY_CALL_CAP == 10_000
    assert copilot_guard._DEFAULT_MAX_CONCURRENCY == 10_000


def _enable(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_GUARD", "1")
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_SLOT_WAIT_S", "0.01")
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


@pytest.mark.parametrize("diagnostic", [
    "2026-09-07T04:28:00.429010Z WARN local history projection failed",
    "expected ordinal 429, got 428",
    "mse=0.429",
    "Provider turn cap reached: allowance 40\nHTTP 429 Too Many Requests",
    "Provider turn cap reached: allowance 40\nAccess denied by policy settings",
    "Code Mode is unavailable because failed to spawn code-mode host worker: "
    "host executable was not found (startup failure)\nHTTP 429 Too Many Requests",
])
def test_local_diagnostics_do_not_block_the_next_quota_permit(
    monkeypatch, tmp_path, diagnostic,
) -> None:
    _enable(monkeypatch, tmp_path)
    first = acquire_copilot_permit("engineer-r1")
    assert first.allowed
    first.finish(error_text=diagnostic, premium_requests=2.5, success=False)
    snapshot = copilot_guard_snapshot()
    assert snapshot["blocked_until"] == 0
    assert snapshot["daily_calls"] == 1
    assert snapshot["premium_requests_remaining"] == 97.5
    continuation = acquire_copilot_permit("engineer-r1.winddown")
    assert continuation.allowed
    continuation.finish(success=True)


@pytest.mark.parametrize("diagnostic", [
    "HTTP 429", '{"status_code":429}', "429 Too Many Requests", "quota exceeded",
])
def test_real_rate_limit_still_blocks_the_next_quota_permit(
    monkeypatch, tmp_path, diagnostic,
) -> None:
    _enable(monkeypatch, tmp_path)
    first = acquire_copilot_permit("engineer-r1")
    first.finish(error_text=diagnostic, success=False)
    blocked = acquire_copilot_permit("engineer-r2")
    assert not blocked.allowed
    assert blocked.stop_kind == "provider_cooldown"
    release_denied_permit(blocked)


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
        "argus_skill.provider_integrations.copilot_guard._write_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    permit.finish(premium_requests=1.0, success=True)


def test_snapshot_exposes_operator_caps(monkeypatch, tmp_path) -> None:
    _enable(monkeypatch, tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_DAILY_CALL_CAP", "9")
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_DAILY_PREMIUM_CAP", "12")

    permit = acquire_copilot_permit("engineer")
    permit.finish(premium_requests=2.5, success=True)
    snapshot = copilot_guard_snapshot()

    assert snapshot["daily_calls"] == 1
    assert snapshot["daily_call_cap"] == 9
    assert snapshot["daily_calls_remaining"] == 8
    assert snapshot["daily_premium_cap"] == 12
    assert snapshot["premium_requests_remaining"] == 9.5
