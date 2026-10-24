from __future__ import annotations

from types import SimpleNamespace

from argus.manager.plan_mode import Plan, PlanStep
from argus.webapi import manager_bridge, manager_state


def test_plan_preview_uses_lightweight_role_config_and_caches_exact_repeat(
    tmp_path, monkeypatch,
) -> None:
    sid = "s-plan-fast"
    (tmp_path / "projects" / sid).mkdir(parents=True)
    manager_state._STATES.clear()
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "codex")
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_MODEL", "manager-model")
    monkeypatch.delenv("ARGUS_SKILL_LIGHT_MODEL", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_LIGHT_REASONING_EFFORT", raising=False)
    backend = object()
    monkeypatch.setattr(
        "argus.manager.front_door._ensure_manager_runner",
        lambda *_args, **_kwargs: SimpleNamespace(planner_backend=backend),
    )
    calls: list[dict] = []

    def draft(runner, objective, **kwargs):
        calls.append({"runner": runner, "objective": objective, **kwargs})
        return Plan(
            objective=objective,
            steps=[PlanStep("Inspect", "find the constraint")],
        )

    monkeypatch.setattr("argus.manager.plan_mode.draft_plan", draft)

    first = manager_bridge.manager_plan(
        sid,
        "optimize it",
        global_root=tmp_path,
    )
    second = manager_bridge.manager_plan(
        sid,
        "optimize it",
        global_root=tmp_path,
    )

    assert first == second
    assert len(calls) == 1
    assert calls[0]["runner"] is backend
    assert calls[0]["model"] == "manager-model"
    assert calls[0]["reasoning_effort"] == "low"
