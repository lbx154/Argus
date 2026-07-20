from __future__ import annotations

from pathlib import Path

from argus_skill.skills.stage_checklists import STAGE_CHECKLISTS

_SKILLS = Path(__file__).parents[2] / "argus_skill" / "builtin_skills"


def _skill(relative: str) -> str:
    return (_SKILLS / relative).read_text(encoding="utf-8")


def test_paper_drafting_skills_stay_compact() -> None:
    paths = (
        "engineer/emnlp-paper-drafting.md",
        "engineer/aaai-paper-drafting.md",
        "engineer/research-brief-to-experiment-plan.md",
    )

    sizes = {path: len(_skill(path)) for path in paths}
    assert all(size < 9_000 for size in sizes.values()), sizes
    assert sum(sizes.values()) < 22_000


def test_protocol_does_not_auto_publish_negative_results() -> None:
    results_review = _skill("reviewer/experiment-results-review.md")
    runner = _skill("engineer/research-experiment-runner.md")
    pipeline = _skill("engineer/auto-research-pipeline.md")

    assert "at most ONE" not in results_review
    assert "write the paper on the current results" not in results_review
    assert "There is no fixed number of optimization passes" in results_review
    assert "does not automatically advance to drafting" in runner
    assert "not automatic write-up" in pipeline


def test_live_checklist_requires_thesis_and_implementation_adequacy() -> None:
    run = {item.id: item.statement for item in STAGE_CHECKLISTS["run"]}
    analysis = {item.id: item.statement for item in STAGE_CHECKLISTS["analysis"]}
    draft = {item.id: item.statement for item in STAGE_CHECKLISTS["draft"]}
    review = {item.id: item.statement for item in STAGE_CHECKLISTS["review"]}

    assert "under-engineered" in run["run.method_diagnosis_recall"]
    assert "selective argument" in analysis["analysis.thesis"]
    assert "same thesis" in draft["draft.tex"]
    assert "weak result cannot be rescued" in review["review.publication_value"]
