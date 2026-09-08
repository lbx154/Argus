from __future__ import annotations

import json
from pathlib import Path

from argus_skill.core.models import RunnerResult
from argus_skill.reviewer import Reviewer, ReviewerConfig
from argus_skill.skills.vertical_select import persist_vertical


class _Backend:
    def __init__(self, status: str, venue_review: dict | None = None) -> None:
        self.status = status
        self.venue_review = venue_review
        self.prompt = ""
        self.options = None

    def run_exec(self, **kwargs) -> RunnerResult:
        self.prompt = kwargs["prompt"]
        self.options = kwargs["options"]
        return RunnerResult(
            exit_code=0,
            agent_messages=[json.dumps({
                "status": self.status,
                "reason": "Independent judgment from the inspected evidence.",
                "next_action": "" if self.status == "done" else "Choose a better direction.",
                "operator_question": None,
                "venue_review": self.venue_review,
            })],
        )


def test_research_target_is_prompt_context_not_output_schema(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "research", research_target_level="publishable")
    backend = _Backend("replan_requested")
    decision = Reviewer(backend).evaluate(
        objective="produce publishable research",
        original_objective="produce publishable research",
        round_index=1,
        session_id="mission",
        main_summary="evidence landed",
        main_error=None,
        config=ReviewerConfig(working_dir=str(tmp_path)),
        scope="bounded",
    )

    assert decision.status == "replan_requested"
    assert "Project target `publishable`" in backend.prompt
    # The project bar and this round's bar are now separate statements:
    # a publishable target must not be read as this round's requirement.
    assert "not this round's bar" in backend.prompt
    assert (
        "What observed result or pattern most changed your belief — including a "
        "positive surprise — and what is the cheapest observation that would "
        "distinguish a new scientific explanation from an artifact?"
        in backend.prompt
    )
    # The point of this test is *where* the target lives: in the prompt the
    # Reviewer reads, never encoded into a machine-enforced output shape. Since
    # 2026-07-26 no role is forced to emit a schema at all, so the second half
    # of that property is now structural rather than a comparison of paths.
    assert not hasattr(backend.options, "output_schema_path")


def test_reviewer_verdict_is_not_rewritten_from_formal_result_labels(
    tmp_path: Path,
) -> None:
    persist_vertical(
        tmp_path, "research", research_target_level="doctoral", target_venue="ICLR",
    )
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "main.tex").write_text("Current manuscript")
    (paper / "main.pdf").write_bytes(b"Current rendered paper")
    # The aspirational target must not override an independent acceptance of
    # the current paper. Final submission still needs an actual venue judgment.
    backend = _Backend("done", {
        "venue": "ICLR", "recommendation": "weak_accept",
        "acceptance_clear": True, "rationale": "The current evidence supports acceptance.",
        "blocking_issues": [], "revision_required": False,
    })
    decision = Reviewer(backend).evaluate(
        objective="judge the current result",
        round_index=1,
        session_id="mission",
        main_summary="current evidence",
        main_error=None,
        config=ReviewerConfig(working_dir=str(tmp_path)),
        scope="final_submission",
    )

    assert decision.status == "done"
    assert decision.final_submission_certified
