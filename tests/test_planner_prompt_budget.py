"""Planner prompt composition and size regression guards."""
from __future__ import annotations

from types import SimpleNamespace

from argus_skill.life.supervisor import LifeSupervisor
from argus_skill.planner import Planner
from argus_skill.skills.vertical_select import persist_vertical

MATH_SCOPE_BUDGET = 9_500
MATURE_MATH_SCOPE_BUDGET = 15_000


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


def test_mature_math_prompt_keeps_only_bounded_terminal_history(
    tmp_path,
    monkeypatch,
) -> None:
    journal = "\n".join(
        f"- [07-22 12:0{index}] mission_complete: result-{index} — "
        + ("evidence " * 210)
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
    supervisor.memory = SimpleNamespace(
        journal=SimpleNamespace(tail=lambda _count: entries)
    )

    rendered = supervisor._render_journal_for_planner()

    assert "start-noise" not in rendered
    assert "planner-noise" not in rendered
    assert "terminal-0" not in rendered
    assert "terminal-1" not in rendered
    assert all(f"terminal-{index}" in rendered for index in range(2, 4))
    assert "paused-methods" in rendered
    assert len(rendered) <= 3 * 1_800 + 2
