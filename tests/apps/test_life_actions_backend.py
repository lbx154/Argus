from __future__ import annotations

from typing import Any

from argus_skill.apps import _runtime
from argus_skill.apps._life_actions import (
    render_backend_cmd,
    render_run_command,
    render_skills_cmd,
)


def test_remote_backend_command_accepts_opencode() -> None:
    state: dict[str, Any] = {
        "backend": "codex",
        "config": {"continuous": False},
    }

    assert render_backend_cmd(["opencode"], state) == "backend: opencode"
    assert state["backend"] == "opencode"


def test_runtime_skill_cannot_be_promoted_into_source() -> None:
    assert render_skills_cmd(["promote", "learned-skill"]) == (
        "unknown /skills subcommand: promote  (try ls)"
    )


def test_remote_run_accepts_opencode(
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_invoke_supervisor(**kwargs: Any) -> tuple[dict[str, Any], None]:
        calls.append(kwargs)
        return {}, None

    monkeypatch.setattr(_runtime, "_invoke_supervisor", fake_invoke_supervisor)
    output = render_run_command(
        object(),
        ["--backend", "opencode", "--once"],
        {"backend": "codex", "config": {"cycles": 6}},
    )

    assert calls[0]["backend"] == "opencode"
    assert "/run: backend=opencode" in output
