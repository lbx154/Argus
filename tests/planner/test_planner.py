from __future__ import annotations

import pytest

from argus.core.models import RunnerResult
from argus.planner.planner import Planner, PlannerConfig, TaskSpec
from tests.hand_off import plan, submit


class _Runner:
    """A Planner backend whose turn submits ``action``, or nothing."""

    def __init__(self, action: tuple[str, dict] | None = plan("done", "Focused verification passed.")) -> None:
        self.action = action
        self.calls: list[dict] = []
        self.refusal = ""

    def run_exec(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        if self.action:
            self.refusal = submit(kwargs["options"], self.action)
        return RunnerResult(exit_code=0, agent_messages=["Changed the implementation."])


def test_plan_next_reads_the_skill_libraries_its_prompt_names(tmp_path, monkeypatch) -> None:
    """s-757b9dac: the read-only Planner was pointed at the shared vertical
    library but not given it, so its lookup there failed."""
    from argus.skills.layered import LayeredSkillStore

    store = LayeredSkillStore(project_dir=tmp_path / "project-skills", global_dir=tmp_path / "shared-skills")
    runner = _Runner()
    monkeypatch.setattr(Planner, "_build_planner_prompt", staticmethod(lambda **kwargs: "prompt"))

    Planner(runner, skill_store=store).plan_next(
        continuous_objective="fix the issue",
        config=PlannerConfig(working_dir=str(tmp_path / "work"), add_dirs=["/tmp/project-state"]),
    )

    add_dirs = runner.calls[0]["options"].add_dirs
    assert add_dirs[0] == "/tmp/project-state"
    assert {str(root) for root in Planner(None, skill_store=store).mission.libraries().library_roots} <= set(add_dirs)
    assert str((tmp_path / "shared-skills").resolve()) in add_dirs


def _plan_next(action, **config) -> tuple:
    runner = _Runner(action)
    planner = Planner(runner)
    planner._build_planner_prompt = staticmethod(lambda **kwargs: "prompt")
    verdict = planner.plan_next(continuous_objective="improve the parser", config=PlannerConfig(working_dir="/tmp/project", **config))
    return verdict, runner.refusal


def test_the_planner_hands_on_its_tasks_and_bookkeeping() -> None:
    verdict, refusal = _plan_next(plan(
        "engineer", "Measure the parser, then fix the slow path.",
        tasks=[
            {"key": "m", "title": "Measure", "objective": "Profile the parser.", "parallel_safe": True, "owns_paths": ["bench/"]},
            {"key": "f", "deps": ["m"], "title": "Fix", "objective": "Fix the slow path.", "gpu_count": 1},
        ],
        retire=[{"id": "item-a", "reason": "Refuted by the benchmark."}],
        settle_instructions=[7, 7, 9], advance_to_stage="build", plan_update="# Research plan\nNew.",
        assumptions=[{"chose": "the CPU parser", "instead_of": "the GPU parser"}],
    ), stages=("scope", "build"))

    assert refusal == ""
    assert verdict.summary == "Measure the parser, then fix the slow path." and verdict.handoff == ""
    assert verdict.new_tasks == [
        TaskSpec(key="m", title="Measure", objective="Profile the parser.", parallel_safe=True, owns_paths=["bench/"]),
        TaskSpec(key="f", deps=["m"], title="Fix", objective="Fix the slow path.", gpu_count=1),
    ]
    assert verdict.retire_tasks == (("item-a", "Refuted by the benchmark."),)
    assert verdict.settled_instructions == (7, 9)
    assert (verdict.advance_to_stage, verdict.plan_update) == ("build", "# Research plan\nNew.")
    assert verdict.assumptions == ({"chose": "the CPU parser", "instead_of": "the GPU parser"},)


def test_a_question_for_the_operator_carries_its_answers() -> None:
    options = [{"label": "Keep it"}, {"label": "Drop it"}]
    verdict, _ = _plan_next(plan("operator", "The two requirements conflict.", ask={"question": "Keep the old output?", "options": options}))

    assert (verdict.handoff, verdict.operator_question, verdict.operator_options) == ("operator", "Keep the old output?", tuple(options))


@pytest.mark.parametrize(("action", "config", "refusal"), [
    (plan("done", tasks=[{"title": "More", "objective": "More work."}]), {}, "to=done starts no tasks"),
    (plan("done"), {"open_ended": True}, "ongoing and never done"),
    (plan(advance_to_stage="ship"), {"stages": ("scope", "build")}, "advance_to_stage: one of scope, build."),
    (plan(advance_to_stage="ship"), {}, "this project has no stages"),
    (plan(tasks=[{"title": "Fix", "objective": "Fix it.", "deps": ["elsewhere"]}]), {}, "name only keys of tasks in this call"),
    (plan(tasks=[{"title": "Fix", "objective": "Fix it.", "parallel_safe": True}]), {}, "parallel_safe needs the owns_paths"),
    (plan(tasks=[{"title": "Fix", "objective": "Fix it.", "owns_paths": ["src/**"]}]), {}, "'src/**' is not a literal path"),
    (plan(tasks=[{"title": "Fix", "objective": "Fix it.", "owns_paths": ["/abs/cpc"]}]), {}, "'/abs/cpc' is not a literal path"),
    (plan(tasks=[{"title": "Fix", "objective": "Fix it.", "key": "a b"}]), {}, "does not match"),
])
def test_the_form_refuses_a_plan_the_host_cannot_act_on(action, config, refusal) -> None:
    verdict, said = _plan_next(action, **config)

    assert refusal in said
    assert verdict.error == "planner ended without handing off"


def test_a_staged_plan_without_a_stage_holds_the_current_one() -> None:
    verdict, _ = _plan_next(plan(tasks=[{"title": "Fix", "objective": "Fix it."}]), stages=("scope", "build"))

    assert verdict.diagnostics == ("advance_to_stage missing; holding current stage",)
