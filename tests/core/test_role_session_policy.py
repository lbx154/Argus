from __future__ import annotations

from argus.core.role_session import role_session_policy


def test_resumable_clis_keep_their_role_thread() -> None:
    for backend in ("pi", "codex", "claude", "copilot", "cursor", "opencode", "grok", "qoder"):
        assert role_session_policy(backend) == "rolling"
