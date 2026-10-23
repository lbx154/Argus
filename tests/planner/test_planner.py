from __future__ import annotations

import pytest

from argus.core.models import RunnerResult
from argus.planner.planner import PLANNER_SUPERSEDED_ERROR, Planner, PlannerConfig, TaskSpec
from tests.hand_off import plan, submit
from tests.role_rules import lists, rule


def test_planner_forbids_binary_outcome_labels_and_standing_keeps_exploring(
    tmp_path,
) -> None:
    finite = Planner._build_planner_prompt(
        continuous_objective=(
            "Promote the route if it wins; otherwise reject it with decisive evidence."
        ),
        journal_tail="The route was measured and rejected.",
        planning_cycle=2,
        project_root=tmp_path,
        state_root=tmp_path,
        open_ended=False,
    )
    standing = Planner._build_planner_prompt(
        continuous_objective="Keep exploring new optimization mechanisms.",
        journal_tail="The current route was measured and rejected.",
        planning_cycle=2,
        project_root=tmp_path,
        state_root=tmp_path,
        open_ended=True,
    )

    assert "accepted " + "no" + "-go" not in finite.lower()
    assert lists(finite, "reality-and-sources/SKILL.md")
    assert "timing, profiling" in rule("planner", "reality-and-sources/SKILL.md")
    assert "open-ended" not in finite
    assert "open-ended: continue until the operator stops the work" in standing


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


def test_plan_next_disables_schema_and_forces_read_only_tools(monkeypatch) -> None:
    runner = _Runner()

    def interrupt() -> None:
        return None
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **kwargs: "direct execution prompt"),
    )

    verdict = Planner(runner).plan_next(
        continuous_objective="fix the issue",
        config=PlannerConfig(
            working_dir="/tmp/project",
            add_dirs=["/tmp/project-state"],
            dangerous_yolo=True,
            external_interrupt_reason_provider=interrupt,
        ),
    )

    assert verdict.project_done is True
    call = runner.calls[0]
    assert call["run_label"] == "planner.cycle0"
    options = call["options"]
    assert not hasattr(options, "output_schema_path")
    assert not hasattr(options, "_argus_tool_call_observer")
    assert not hasattr(PlannerConfig(), "grounding_max_tool_calls")
    assert not hasattr(PlannerConfig(), "grounding_max_seconds")
    assert options.external_interrupt_reason_provider is interrupt
    # None delegates liveness to the shared backend default; zero disables the
    # watchdog and lets a silent Planner call block a campaign indefinitely.
    assert options.watchdog_hard_idle_seconds is None
    assert options.dangerous_yolo is False
    assert options.full_auto is False
    assert options.sandbox_mode == "read-only"
    assert options.add_dirs == ["/tmp/project-state"]


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


def test_plan_next_defaults_to_read_only_tool_access(monkeypatch) -> None:
    runner = _Runner()
    monkeypatch.setattr(
        Planner,
        "_build_planner_prompt",
        staticmethod(lambda **kwargs: "direct execution prompt"),
    )

    Planner(runner).plan_next(
        continuous_objective="inspect safely",
        config=PlannerConfig(working_dir="/tmp/project"),
    )

    options = runner.calls[0]["options"]
    assert options.dangerous_yolo is False
    assert options.full_auto is False
    assert options.sandbox_mode == "read-only"


def test_planner_reports_newer_operator_generation_as_superseded() -> None:
    class Runner:
        def run_exec(self, **_kwargs):
            return RunnerResult(
                exit_code=1,
                fatal_error=f"External interrupt: {PLANNER_SUPERSEDED_ERROR}",
            )

    verdict = Planner(Runner()).plan_next(
        continuous_objective="keep optimizing Argus",
        config=PlannerConfig(working_dir="/tmp/project", open_ended=True),
    )

    assert verdict.project_done is False
    assert verdict.error == PLANNER_SUPERSEDED_ERROR


def test_planner_zero_exit_fatal_error_preserves_actionable_reason() -> None:
    class Runner:
        def run_exec(self, **_kwargs):
            return RunnerResult(
                exit_code=0,
                fatal_error="backend stream ended before a planner decision",
                stderr_lines=["ignored old stderr", "recent stderr detail"],
            )

    verdict = Planner(Runner()).plan_next(
        continuous_objective="keep optimizing Argus",
        config=PlannerConfig(working_dir="/tmp/project", open_ended=True),
    )

    assert verdict.project_done is False
    assert verdict.error == (
        "backend stream ended before a planner decision\n"
        "ignored old stderr\n"
        "recent stderr detail"
    )


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


@pytest.mark.parametrize("to", ["engineer", "manager"])
def test_a_plan_that_starts_nothing_names_who_acts_next(to: str) -> None:
    verdict, _ = _plan_next(plan(to, "The running experiment decides what comes next."))

    assert (verdict.handoff, verdict.new_tasks, verdict.error) == (to, [], "")


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


def test_a_turn_without_a_handoff_is_no_decision() -> None:
    verdict, _ = _plan_next(None)

    assert verdict.error == "planner ended without handing off" and not verdict.project_done
