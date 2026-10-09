from __future__ import annotations

import pytest

from argus.provider_integrations import copilot_guard
from argus.provider_integrations.copilot_guard import (
    acquire_copilot_permit,
    copilot_guard_snapshot,
    release_denied_permit,
)


def test_default_concurrency_cap_is_10000() -> None:
    assert copilot_guard._DEFAULT_MAX_CONCURRENCY == 10_000


def _enable(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_GUARD", "1")
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_SLOT_WAIT_S", "0.01")


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
    first.finish(error_text=diagnostic, success=False)
    assert copilot_guard_snapshot()["blocked_until"] == 0
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


def test_guard_accounting_failure_is_fail_soft(monkeypatch, tmp_path) -> None:
    _enable(monkeypatch, tmp_path)
    permit = acquire_copilot_permit("engineer")
    assert permit.allowed
    monkeypatch.setattr(
        "argus.provider_integrations.copilot_guard._write_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    permit.finish(error_text="Error: Access denied by policy settings", success=False)

