"""Planner prompt composition and size regression guards."""

from __future__ import annotations

from types import SimpleNamespace

from argus_skill.core.pipeline_state import read_pipeline_state, write_pipeline_state
from argus_skill.life.supervisor import LifeSupervisor
from argus_skill.planner import Planner
from argus_skill.skills.vertical_select import persist_vertical

# Raised from 9_500 / 15_000 when the math vertical gained the objective mode
# (targeted vs exploratory), the route ledger, and the proof-gap graph — three
# blocks the planner has to see to choose a next step, deliberately added
# rather than prose creep. The existing text was compressed first: the opening,
# the failed-attempt paragraph, and the closing options list are all shorter
# than they were. Compress again before raising these further.
#
# Raised again from 9_700 / 15_400 for two blocks the testbed runs paid for,
# both about work the planner can only place in `scope`:
#   * settle known results before dispatch — several workers on one goal cannot
#     see each other's searches, so a lookup done in `scope` costs once and the
#     same lookup done in `solve` costs once per worker; finding nothing is
#     recorded as a result too;
#   * two genuinely different attacks are two routes, an OR, and the test of
#     "different" is that they fail for different reasons — two routes dying to
#     the same obstruction were one route.
# Neither restates existing text. Compress before raising a third time.
# Raised by 50 to let the decision example carry a real question and a real
# objective. It used to hold placeholders, and the Planner copied them
# through: every campaign shipped a mission literally titled "title", then
# — after those were replaced with angle-bracket slots — one titled
# "<question>". An example a model can paste verbatim without producing
# nonsense is worth fifty characters of fixed policy.
# Raised for the stage checklist intentionally added to staged Planner prompts:
# this is now the shared definition of useful progress for Planner and Reviewer,
# rather than unrelated prompt ceremony. Direct workflows still omit it.
# Raised by 1,100 for the fixed living-research-plan contract. The plan itself
# has a separate 8,000-character projection cap tested below, so future policy
# prose cannot silently consume that dynamic-state allowance.
MATH_SCOPE_BUDGET = 12_650
MATURE_MATH_SCOPE_BUDGET = 18_450
RESEARCH_PLAN_DYNAMIC_BUDGET = 8_000


def _build_math_scope_prompt(
    tmp_path,
    monkeypatch,
    *,
    journal_tail: str = "(empty)",
) -> tuple[str, str]:
    persist_vertical(
        tmp_path,
        "math",
        research_target_level="doctoral",
    )
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_ROOT", str(tmp_path))
    objective = (
        "Search current mathematical literature and primary sources to identify "
        "a genuinely unresolved conjecture for which a counterexample would be "
        "meaningful. Independently choose a tractable candidate only after "
        "verifying that it remains open, then conduct an honest, reproducible "
        "counterexample search with premise checks. Claim a counterexample only "
        "after every hypothesis and the current literature are independently "
        "reviewed. Preserve sources, code, raw outputs, and negative results. "
        "Continue autonomously with another unresolved conjecture when a route "
        "is exhausted."
    )
    runtime_context = (
        "## Manager intent boundary (authoritative)\n"
        "- intent_id: intent-test\n"
        "- source: daemon_boot\n"
        "- interpreted_vertical: math\n"
        "- kind: custom\n"
        "- stages: scope, solve, review\n"
        "- reason: manager completed daemon objective handoff\n\n"
        "Plan only work consistent with this Manager boundary."
    )
    prompt = Planner._build_planner_prompt(
        continuous_objective=objective,
        journal_tail=journal_tail,
        planning_cycle=0,
        runtime_change_summary=runtime_context,
        open_ended=True,
    )
    return prompt, objective


def test_math_scope_prompt_is_compact_and_deduplicated(
    tmp_path,
    monkeypatch,
) -> None:
    prompt, objective = _build_math_scope_prompt(tmp_path, monkeypatch)

    assert len(prompt) < MATH_SCOPE_BUDGET, (
        f"math scope Planner prompt is {len(prompt)} chars; keep fixed policy "
        "compact and move state-specific guidance behind structured triggers"
    )
    assert prompt.count(objective) == 1
    assert "## Manager mission brief (authoritative)\n" + objective in prompt
    assert "## Original operator request (immutable anchor)" not in prompt
    assert "Argus planner role skill:" not in prompt
    assert "waiting_contract" not in prompt
    assert prompt.count("PROJECT_DONE=false") == 1
    assert "TASK_KEY=k1" in prompt
    assert "not a routing command" in prompt
    assert prompt.count(
        "Integrity and reproducibility are admission constraints"
    ) == 1
    assert "delegate implementation to Engineer" in prompt
    assert "JSON matching the provided schema" not in prompt


def test_math_scope_prompt_excludes_unrelated_modules(
    tmp_path,
    monkeypatch,
) -> None:
    prompt, _objective = _build_math_scope_prompt(tmp_path, monkeypatch)

    assert "## Planner read-only delegation contract" in prompt
    assert "## Current workflow stage" in prompt
    assert "current: `scope`" in prompt
    assert "## Stage checklist (scope)" in prompt
    assert "## Stage gate" not in prompt
    assert "## Parallel paper-drafting track" not in prompt
    assert "PAPER_INFRASTRUCTURE_REVIEW.json" not in prompt
    assert "RESULT_PLACEHOLDERS.md" not in prompt


def test_direct_workflow_suppresses_stage_artifact_ceremony(
    tmp_path,
    monkeypatch,
) -> None:
    persist_vertical(
        tmp_path,
        "kernel_engineering",
        workflow_mode="direct",
    )
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_ROOT", str(tmp_path))

    prompt = Planner._build_planner_prompt(
        continuous_objective="Directly optimize MiniMax H3 inference on M4 Pro.",
        journal_tail="(empty)",
        planning_cycle=0,
        open_ended=True,
    )

    assert "## Direct workflow — objective first" in prompt
    assert "semantic context, not a mandatory artifact phase" in prompt
    assert "## Stage checklist (scope)" not in prompt
    assert "## Stage gate" not in prompt
    assert "KERNEL_SCOPE.md" not in prompt


def test_planner_keeps_operator_actions_ahead_of_optional_hardening(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_ROOT", str(tmp_path))
    prompt = Planner._build_planner_prompt(
        continuous_objective=(
            "Download the BF16 model, quantize it, and prove local inference works."
        ),
        journal_tail="An older 8-bit model already has a local manifest.",
        planning_cycle=0,
        open_ended=False,
    )

    assert "Follow the operator's requested actions and order" in prompt
    assert "a usable" in prompt
    assert "alternative do not replace the first unmet requested action" in prompt
    assert "Optional hardening never keeps a finite objective alive" in prompt


def test_bounded_planner_rejects_tautological_acceptance_checks() -> None:
    from argus_skill.roles.prompts.planner import build_bounded_dag_prompt

    prompt = build_bounded_dag_prompt("Create exact.txt without changing README.")

    assert "must fail when its claimed requirement is violated" in prompt
    assert "never emit `or True`, `|| true`, unconditional success" in prompt


def test_research_submission_prompt_prescribes_final_submission_scope(
    tmp_path,
    monkeypatch,
) -> None:
    persist_vertical(
        tmp_path,
        "research",
        research_target_level="publishable",
    )
    state = read_pipeline_state(tmp_path)
    state["current_stage"] = "submission"
    write_pipeline_state(tmp_path, state)
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_ROOT", str(tmp_path))

    prompt = Planner._build_planner_prompt(
        continuous_objective=(
            "Obtain final independent certification for the research submission."
        ),
        journal_tail="(empty)",
        planning_cycle=0,
        project_root=tmp_path,
        state_root=tmp_path,
        open_ended=True,
    )

    assert "## Final-submission task scope" in prompt
    assert '`scope:"final_submission"`' in prompt
    assert "`TASK_SCOPE=final_submission`" in prompt
    assert "verticals without a final-submission or research-target gate" in prompt


def test_mature_math_prompt_keeps_only_bounded_terminal_history(
    tmp_path,
    monkeypatch,
) -> None:
    journal = "\n".join(
        f"- [07-22 12:0{index}] mission_complete: result-{index} — " + ("evidence " * 210)
        for index in range(3)
    )

    prompt, _objective = _build_math_scope_prompt(
        tmp_path,
        monkeypatch,
        journal_tail=journal,
    )

    assert len(prompt) < MATURE_MATH_SCOPE_BUDGET


def test_planner_journal_uses_latest_three_terminal_outcomes() -> None:
    entries = [
        SimpleNamespace(
            kind="mission_started",
            ts=0.0,
            title="start-noise",
            summary="",
            extra={},
        ),
        *[
            SimpleNamespace(
                kind="mission_complete",
                ts=float(index),
                title=f"terminal-{index}",
                summary="result " + ("x" * 3_000),
                extra={},
            )
            for index in range(4)
        ],
        SimpleNamespace(
            kind="research_pause",
            ts=8.0,
            title="paused-methods",
            summary="current approach exhausted",
            extra={},
        ),
        SimpleNamespace(
            kind="planner_cycle",
            ts=9.0,
            title="planner-noise",
            summary="",
            extra={},
        ),
    ]
    supervisor = LifeSupervisor.__new__(LifeSupervisor)
    supervisor.memory = SimpleNamespace(journal=SimpleNamespace(tail=lambda _count: entries))

    rendered = supervisor._render_journal_for_planner()

    assert "start-noise" not in rendered
    assert "planner-noise" not in rendered
    assert "terminal-0" not in rendered
    assert "terminal-1" not in rendered
    assert all(f"terminal-{index}" in rendered for index in range(2, 4))
    assert "paused-methods" in rendered
    assert len(rendered) <= 3 * 1_800 + 2


def _research_plan(*, experiment_fill: str = "") -> str:
    return (
        "# Research plan\n"
        "Objective: establish a decisive publishable result.\n\n"
        "## Central hypotheses\n"
        "1. [untested] H1 — evidence: journal mission-1\n\n"
        "## Experiment program\n"
        "- Run the discriminating experiment. " + experiment_fill + "\n\n"
        "## Established results\n"
        "- Baseline reproduced — evidence: results/base.json\n\n"
        "## Dead ends\n"
        "- Old route — abandoned after null result in journal mission-0\n\n"
        "## Next milestone\n"
        "A cross-dataset effect with an independently checked mechanism."
    )


def test_planner_prompt_renders_existing_living_research_plan(
    tmp_path,
) -> None:
    from argus_skill.life.research_plan import render_research_plan_for_planner

    plan = _research_plan()
    (tmp_path / "RESEARCH_PLAN.md").write_text(plan, encoding="utf-8")
    rendered = render_research_plan_for_planner(tmp_path)
    prompt = Planner._build_planner_prompt(
        continuous_objective="Establish the result.",
        journal_tail="(empty)",
        research_plan=rendered,
        planning_cycle=2,
        project_root=tmp_path,
        state_root=tmp_path,
    )

    assert "## Research plan (living document)" in prompt
    assert plan in prompt
    assert "PLAN_UPDATE" in prompt
    assert "never delete a Dead" not in prompt  # guard exact contract wording below
    assert "Never delete a Dead\nends entry" in prompt


def test_planner_prompt_marks_absent_or_corrupt_plan_for_creation(tmp_path) -> None:
    from argus_skill.life.research_plan import render_research_plan_for_planner

    absent = render_research_plan_for_planner(tmp_path)
    (tmp_path / "RESEARCH_PLAN.md").write_text("not the contract", encoding="utf-8")
    corrupt = render_research_plan_for_planner(tmp_path)

    assert "no plan yet" in absent
    assert "create RESEARCH_PLAN.md" in absent
    assert corrupt == absent


def test_oversize_research_plan_keeps_head_and_next_milestone_with_hard_cap(
    tmp_path,
) -> None:
    from argus_skill.life.research_plan import render_research_plan_for_planner

    (tmp_path / "RESEARCH_PLAN.md").write_text(
        _research_plan(experiment_fill="x" * 12_000),
        encoding="utf-8",
    )
    rendered = render_research_plan_for_planner(tmp_path)

    assert len(rendered) <= RESEARCH_PLAN_DYNAMIC_BUDGET
    assert rendered.startswith("# Research plan")
    assert "living plan truncated" in rendered
    assert "## Next milestone" in rendered
    assert "independently checked mechanism" in rendered
