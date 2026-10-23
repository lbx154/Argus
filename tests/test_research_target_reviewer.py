from __future__ import annotations

from pathlib import Path

from argus.core.models import RunnerResult
from argus.reviewer import Reviewer, ReviewerConfig
from argus.skills.vertical_select import persist_vertical
from tests.hand_off import hand_off
from tests.role_rules import rule


class _Backend:
    def __init__(self, status: str, venue_review: dict | None = None) -> None:
        self.status = status
        self.venue_review = venue_review
        self.prompt = ""
        self.options = None

    def run_exec(self, **kwargs) -> RunnerResult:
        self.prompt = kwargs["prompt"]
        self.options = kwargs["options"]
        from argus.core.role_actions import PREFIX
        from argus.core.role_tool_bridge import bridge_request

        holder = "done" if self.status == "done" else "planner"
        payload = hand_off(holder, "Independent judgment from the inspected evidence.")[1]
        if self.venue_review:
            payload["recommendation"] = self.venue_review["recommendation"]
        bridge_request(PREFIX, "hand_off", payload, env=self.options.extension_env)
        return RunnerResult(exit_code=0)


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
    assert "Project research target: `publishable`" in backend.prompt
    assert "## This round's bar" in backend.prompt
    # The project bar and this round's bar are separate statements: a
    # publishable target must not be read as this round's requirement.
    assert "not this round's bar" in rule("reviewer", "deciding/SKILL.md")
    assert "and its 'Research target' section" in backend.prompt
    assert (
        "what observed result or pattern most changed your belief, including a "
        "positive surprise, and what is the cheapest observation that would tell a "
        "new scientific explanation from an artifact?"
        in rule("reviewer", "deciding/SKILL.md")
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
