"""The research notes at the project root and the stage context that carries them."""
from __future__ import annotations

import json
from pathlib import Path

from argus.verticals.research.notes import (
    RESEARCH_NOTES_FILENAME,
    notes_heading,
)
from argus.verticals.research.prompt_policy import (
    active_context_paths,
    active_research_context,
)


def test_headings_name_the_stage_in_plain_words() -> None:
    assert notes_heading("idea") == "# Research notes — Idea stage"
    assert notes_heading("experiment") == "# Research notes — Experiment stage"
    assert notes_heading("paper") == "# Research notes — Paper stage"
    assert notes_heading("review") == "# Research notes"


def test_stage_context_loads_the_notes(tmp_path: Path) -> None:
    assert active_context_paths("experiment") == (RESEARCH_NOTES_FILENAME,)
    (tmp_path / RESEARCH_NOTES_FILENAME).write_text(
        "# Research notes — Experiment stage\n\nUNIQUE_THESIS_LINE", encoding="utf-8"
    )
    block = active_research_context("experiment", tmp_path)
    assert "UNIQUE_THESIS_LINE" in block
    assert f"`{RESEARCH_NOTES_FILENAME}`" in block


def test_the_engineer_rewrites_the_notes_for_the_next_stage() -> None:
    from argus.verticals.research import stages

    assert "rewrite the research notes, RESEARCH_NOTES.md" in stages.role_banner("engineer")
    assert stages.role_banner("reviewer") == stages._REVIEWER_RESEARCH_JUDGEMENT


def _review_project(tmp_path: Path, stage: str) -> Path:
    from argus.skills.vertical_select import persist_vertical

    persist_vertical(tmp_path, "research")
    state = tmp_path / ".argus" / "PIPELINE_STATE.json"
    payload = json.loads(state.read_text(encoding="utf-8"))
    payload["current_stage"] = stage
    state.write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "paper").mkdir()
    (tmp_path / "paper" / "REVIEW.md").write_text(
        "# Review\n\nUNIQUE_REVIEW_LINE: Table 3 came from commit "
        "9f8e7d6c5b4a39281706f5e4d3c2b1a098765432; rerun it at HEAD.\n",
        encoding="utf-8",
    )
    return tmp_path


def test_review_stage_reviewer_uses_the_supplied_review_instead_of_rereading_it(
    tmp_path: Path,
) -> None:
    from argus.roles.prompts import resolve_role_prompt
    from argus.roles.prompts.reviewer import evaluate_request

    root = _review_project(tmp_path, "review")
    resolved = resolve_role_prompt(
        evaluate_request(root, scope="final-submission", include_role_context=True)
    )

    assert "Read the latest paper/REVIEW.md" not in resolved.role_banner
    assert "rely on its copy under Active research context in this prompt" in (
        resolved.role_banner
    )
    assert "open the file only if that copy is absent or marked truncated" in (
        resolved.role_banner
    )
    # The copy arrives once, verbatim, in the same prompt.
    assert (resolved.role_banner + resolved.role_context).count("UNIQUE_REVIEW_LINE") == 1
    assert "9f8e7d6c5b4a39281706f5e4d3c2b1a098765432" in resolved.role_context


def test_review_stage_engineer_uses_the_supplied_review_and_reopens_omitted_values(
    tmp_path: Path,
) -> None:
    from argus.roles.prompts import resolve_role_prompt
    from argus.roles.prompts.engineer import assemble_round_prompt, mission_request

    root = _review_project(tmp_path, "review")
    resolved = resolve_role_prompt(
        # The round loop passes the stage it read, as engineer/round_prompt.py does.
        mission_request(
            root, vertical="research", stage="review", include_role_context=True
        )
    )
    prompt = assemble_round_prompt(
        resolved.role_banner, role_context=resolved.role_context
    )

    assert "Read the latest paper/REVIEW.md" not in prompt
    assert prompt.count("UNIQUE_REVIEW_LINE") == 1
    # The round tail redacts integrity values, so the policy names the
    # placeholder as a reason to open the file.
    assert "9f8e7d6c5b4a39281706f5e4d3c2b1a098765432" not in prompt
    assert "<machine-integrity-metadata omitted>; rerun it" in prompt
    assert (
        "open the file only if that copy is absent, marked truncated, or shows "
        "`<machine-integrity-metadata omitted>` where you need the value"
    ) in prompt


def test_final_submission_reviewer_without_a_supplied_review_still_reads_it(
    tmp_path: Path,
) -> None:
    from argus.roles.prompts import resolve_role_prompt
    from argus.roles.prompts.reviewer import evaluate_request

    root = _review_project(tmp_path, "paper")
    resolved = resolve_role_prompt(
        evaluate_request(root, scope="final-submission", include_role_context=True)
    )

    assert "## Integrated final paper review" in resolved.role_banner
    assert "UNIQUE_REVIEW_LINE" not in resolved.role_banner + resolved.role_context
    assert "Read the latest paper/REVIEW.md to close resolved concerns" in (
        resolved.role_banner
    )
    assert "rely on its copy under Active research context" not in resolved.role_banner


def test_preloaded_research_context_says_when_to_reopen_its_file(tmp_path: Path) -> None:
    # Every role that receives the notes or the review in its prompt is told
    # the copy is current, so it opens the file only to edit it or recover
    # text the copy lacks; an absent file still gets the "absent" wording.
    (tmp_path / RESEARCH_NOTES_FILENAME).write_text("# Research notes\n\nThe thesis.", encoding="utf-8")
    (tmp_path / "paper").mkdir()
    (tmp_path / "paper" / "REVIEW.md").write_text("# Review\n\nRerun Table 3.", encoding="utf-8")

    for stage, relative in (("experiment", RESEARCH_NOTES_FILENAME), ("review", "paper/REVIEW.md")):
        block = active_research_context(stage, tmp_path)
        assert f"This is the current text of `{relative}`: open that file only to edit it" in block
        assert "marked truncated or omits a value you need" in block
    (tmp_path / "paper" / "REVIEW.md").unlink()
    assert "currently absent or empty" in active_research_context("review", tmp_path)


def test_planner_in_experiment_without_a_route_plans_the_selection_in_one_step(
    tmp_path: Path,
) -> None:
    """s-dacac579 23:58-00:03: with no route recorded, the Experiment Planner idled
    three times asking for the stage mismatch to be fixed before it planned the
    selection itself. The prompt names that move."""
    from argus.verticals.research.prompt_policy import render_role_prompt_context

    def planner(stage: str) -> str:
        return render_role_prompt_context(
            role="planner", operation="plan", stage=stage, scope="", project_root=tmp_path,
        )

    block = planner("experiment")
    assert "## No selected route" in block
    assert "plan the selection as this stage's first task" in block
    assert "do not idle or hand it to the Manager" in block
    assert "## No selected route" not in planner("idea")
    for role in ("engineer", "reviewer"):
        assert "## No selected route" not in render_role_prompt_context(
            role=role, operation="evaluate", stage="experiment", scope="", project_root=tmp_path,
        )

    (tmp_path / RESEARCH_NOTES_FILENAME).write_text("Selected route: segmentation.\n", encoding="utf-8")
    assert "## No selected route" not in planner("experiment")
