from __future__ import annotations

from types import SimpleNamespace

from argus_skill.manager.plan_mode import Plan, PlanStep
from argus_skill.webapi import manager_bridge


def test_plan_preview_uses_lightweight_role_config_and_caches_exact_repeat(
    tmp_path, monkeypatch,
) -> None:
    sid = "s-plan-fast"
    (tmp_path / "projects" / sid).mkdir(parents=True)
    manager_bridge._STATES.clear()
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_PLAN_MODEL", "planner-deep-model")
    monkeypatch.delenv("ARGUS_SKILL_PLAN_PREVIEW_MODEL", raising=False)
    monkeypatch.delenv(
        "ARGUS_SKILL_PLAN_PREVIEW_REASONING_EFFORT",
        raising=False,
    )
    backend = object()
    monkeypatch.setattr(
        "argus_skill.manager.front_door._ensure_manager_runner",
        lambda *_args, **_kwargs: SimpleNamespace(planner_backend=backend),
    )
    calls: list[dict] = []

    def draft(runner, objective, **kwargs):
        calls.append({"runner": runner, "objective": objective, **kwargs})
        return Plan(
            objective=objective,
            steps=[PlanStep("Inspect", "find the constraint")],
        )

    monkeypatch.setattr("argus_skill.manager.plan_mode.draft_plan", draft)

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
    assert calls[0]["model"] == "gpt-5.4-mini"
    assert calls[0]["reasoning_effort"] == "low"


def test_plan_preview_effort_has_explicit_override(tmp_path, monkeypatch) -> None:
    sid = "s-plan-effort"
    (tmp_path / "projects" / sid).mkdir(parents=True)
    manager_bridge._STATES.clear()
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_PLAN_PREVIEW_MODEL", "preview-model")
    monkeypatch.setenv("ARGUS_SKILL_PLAN_PREVIEW_REASONING_EFFORT", "medium")
    monkeypatch.setattr(
        "argus_skill.manager.front_door._ensure_manager_runner",
        lambda *_args, **_kwargs: SimpleNamespace(planner_backend=object()),
    )
    seen: dict = {}

    def draft(_runner, objective, **kwargs):
        seen.update(kwargs)
        return Plan(objective=objective, steps=[PlanStep("Draft")])

    monkeypatch.setattr("argus_skill.manager.plan_mode.draft_plan", draft)

    manager_bridge.manager_plan(sid, "draft it", global_root=tmp_path)

    assert seen["reasoning_effort"] == "medium"
    assert seen["model"] == "preview-model"


def test_plan_preview_auto_inherits_planner_model_on_claude(
    tmp_path, monkeypatch,
) -> None:
    sid = "s-plan-claude"
    (tmp_path / "projects" / sid).mkdir(parents=True)
    manager_bridge._STATES.clear()
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_PLANNER_BACKEND", "claude")
    monkeypatch.setenv("ARGUS_SKILL_PLAN_MODEL", "claude-sonnet-test")
    monkeypatch.delenv("ARGUS_SKILL_PLAN_PREVIEW_MODEL", raising=False)
    monkeypatch.setattr(
        "argus_skill.manager.front_door._ensure_manager_runner",
        lambda *_args, **_kwargs: SimpleNamespace(planner_backend=object()),
    )
    seen: dict = {}

    def draft(_runner, objective, **kwargs):
        seen.update(kwargs)
        return Plan(objective=objective, steps=[PlanStep("Draft")])

    monkeypatch.setattr("argus_skill.manager.plan_mode.draft_plan", draft)

    manager_bridge.manager_plan(sid, "draft it", global_root=tmp_path)

    assert seen["model"] == "claude-sonnet-test"
