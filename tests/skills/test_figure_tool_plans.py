"""The paper-figure ``*_plan`` directives must actually reach the prompt.

``render_paper_figure_prompt`` / ``write_paper_figure_prompt`` accept seven plan
parameters (caption_plan, legend_plan, body_reference_plan,
core_step_visibility_plan, claimed_improvement_anchor, symbol_formula_necessity,
semantic_contract) that are wired from real CLI flags and instructed by the
paper-illustration skill — but they used to be SILENTLY DROPPED, so an agent that
passed ``--caption-plan`` got a no-op. These tests pin the wiring: empty plans
leave the tuned base prompt byte-for-byte unchanged; a non-empty plan appears as
an explicit "must honor" constraint.
"""
from __future__ import annotations

from pathlib import Path

from argus_skill.verticals.research.figure_tool import (
    render_paper_figure_prompt,
    write_paper_figure_prompt,
)


def test_empty_plans_leave_base_prompt_unchanged() -> None:
    base = render_paper_figure_prompt(figure_title="X", content='- Title: "X"')
    assert "Figure plan" not in base  # no stray section when nothing is planned


def test_nonempty_plans_reach_the_prompt() -> None:
    p = render_paper_figure_prompt(
        figure_title="X",
        content='- Title: "X"',
        caption_plan="caption states the measured win",
        semantic_contract="arrows mean data flow, not control flow",
        core_step_visibility_plan="the eval step must be visible",
    )
    assert "Figure plan (must honor):" in p
    assert "caption states the measured win" in p
    assert "arrows mean data flow, not control flow" in p
    assert "the eval step must be visible" in p
    # untouched plans are not emitted as empty bullets
    assert "Legend plan:" not in p


def test_write_forwards_plans_to_the_prompt_file(tmp_path: Path) -> None:
    out = tmp_path / "prompt.txt"
    write_paper_figure_prompt(
        out,
        figure_title="X",
        content='- Title: "X"',
        legend_plan="legend maps each colour to a phase",
    )
    text = out.read_text(encoding="utf-8")
    assert "Figure plan (must honor):" in text
    assert "legend maps each colour to a phase" in text
