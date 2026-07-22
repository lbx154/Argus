"""Planner prompt composition and size regression guards."""
from __future__ import annotations

from argus_skill.planner import Planner
from argus_skill.skills.vertical_select import persist_vertical

MATH_SCOPE_BUDGET = 9_500


def _build_math_scope_prompt(tmp_path, monkeypatch) -> tuple[str, str]:
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
        journal_tail="(empty)",
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
    assert "Argus planner role skill:" not in prompt
    assert prompt.count("waiting_contract") == 1
    assert "not a routing command" in prompt
    assert "author its current-stage gate before routing work" in prompt


def test_math_scope_prompt_excludes_unrelated_modules(
    tmp_path,
    monkeypatch,
) -> None:
    prompt, _objective = _build_math_scope_prompt(tmp_path, monkeypatch)

    assert "## Planner operating contract" in prompt
    assert "## Stage checklist (scope)" in prompt
    assert "## Stage gate" in prompt
    assert "## Parallel paper-drafting track" not in prompt
    assert "PAPER_INFRASTRUCTURE_REVIEW.json" not in prompt
    assert "RESULT_PLACEHOLDERS.md" not in prompt
