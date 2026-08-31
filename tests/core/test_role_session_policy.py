from __future__ import annotations

from argus_skill.core.role_session import (
    configured_role_session_policy,
    effective_role_session_policy,
)


def test_auto_policy_uses_bounded_rolling_sessions_for_resumable_clis(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_ROLE_SESSION_POLICY", raising=False)

    assert configured_role_session_policy() == "auto"
    for backend in ("pi", "codex", "claude", "copilot", "cursor", "opencode", "grok", "qoder"):
        assert effective_role_session_policy("auto", backend) == "rolling"


def test_auto_policy_keeps_unknown_and_fresh_only_backends_fresh() -> None:
    assert effective_role_session_policy("auto", "dsh") == "fresh"
    assert effective_role_session_policy("auto", "MemoryBackend") == "fresh"
    assert effective_role_session_policy("auto", "pi", allow_resume=False) == "fresh"


def test_explicit_policy_remains_available_for_adapter_tests_and_rollback() -> None:
    assert effective_role_session_policy("fresh", "pi") == "fresh"
    assert effective_role_session_policy("mission", "MemoryBackend") == "mission"
    assert effective_role_session_policy("rolling", "pi") == "rolling"
    assert effective_role_session_policy("rolling", "dsh") == "fresh"
