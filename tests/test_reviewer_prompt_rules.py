"""Snapshot-style tests for the reviewer prompt's decision rules.

These guard against accidental regressions of the "stop early when
evidence is already in front of you" lever and the "blocked is for
needs-user-input only" lever — both have been observed silently breaking
agent throughput when the wording slips. We assert directly on the
prompt text that the model receives.
"""

from __future__ import annotations

from argus_skill.engineer.reviewer import Reviewer


class _StubRunner:
    """Minimal stand-in so we can construct a Reviewer for prompt testing."""

    def run_exec(self, *args, **kwargs):  # pragma: no cover — never called
        raise NotImplementedError


def _build_prompt() -> str:
    rev = Reviewer(_StubRunner())
    return rev._build_prompt(
        objective="implement add()",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="ran pytest, 3 passed",
        main_error=None,
        checks=[],
    )


def test_rule_1a_continue_to_done_lever_present() -> None:
    """Reviewer must stop when verbatim evidence already satisfies request.

    Without this rule the reviewer wastes a full round demanding a re-run
    of commands the agent just executed and pasted back.
    """
    prompt = _build_prompt()
    # The exact phrase that flips it from continue to done.
    assert "Symmetric stop rule" in prompt
    assert "WRONG. Choose `done`" in prompt
    # Anti-pattern hint must call out the re-run trap explicitly.
    assert "wastes rounds" in prompt or "waste" in prompt.lower()


def test_blocked_rule_forbids_fixable_failures() -> None:
    """`blocked` must be reserved for genuine user-input dependencies.

    Failing tests / runtime errors are `continue` not `blocked` — the
    latter terminates the loop early in engineer/runner.py.
    """
    prompt = _build_prompt()
    # Rule 4 names "credentials" as one of the legitimate blocked cases.
    assert "credentials" in prompt
    assert "tests still failing" in prompt
    assert "NOT" in prompt and "blocked" in prompt


def test_evidence_requirement_still_present() -> None:
    """Rule 1 (demand evidence for `done`) must remain — rule 1a softens
    not removes it. Both must coexist."""
    prompt = _build_prompt()
    assert "CONCRETE EVIDENCE" in prompt
    assert "bare assertion" in prompt


def test_reviewer_role_skill_is_injected() -> None:
    prompt = _build_prompt()

    assert "Argus reviewer role skill" in prompt
    assert "Argus Reviewer Role" in prompt
    assert "evidence gate" in prompt
    assert "done" in prompt and "continue" in prompt and "blocked" in prompt


def test_rule_8_structural_spec_adherence_present() -> None:
    """Rule 8: reviewer must reject unjustified structural deviations
    (file paths, framework choice, package layout) even when the work
    is functionally correct.

    Regression guard for the codex demo where the spec asked for
    `tracker.py` + pytest and the engineer produced an
    `expense_tracker/` package with unittest, and the reviewer accepted
    it without flagging the deviation.
    """
    prompt = _build_prompt()
    assert "Spec adherence" in prompt
    assert "STRUCTURAL CONSTRAINTS" in prompt
    assert "pytest" in prompt and "unittest" in prompt
    assert "Functional correctness alone is NOT sufficient" in prompt


def test_final_submission_scope_requires_full_emnlp_gate() -> None:
    prompt = _build_prompt()
    assert "Final-submission scope" in prompt
    assert "planner_scope: final_submission" in prompt
    assert "validate-full-emnlp" in prompt
    assert "paper_contribution" in prompt
    assert "negative-result pivot" in prompt
    assert "validate-pipeline" in prompt
    assert "bounded" in prompt
    assert "paper_optimization_task" in prompt
    assert "validate-research-md-format" in prompt


def test_reviewer_prompt_includes_validator_toolbelt() -> None:
    prompt = _build_prompt()

    assert "Validator toolbelt (reviewer)" in prompt
    assert "python -m argus_skill.tools.validator_toolbelt list --role reviewer" in prompt
    assert "validate-academic-language-review --project-root ." in prompt
    assert "validate-full-emnlp --project-root ." in prompt
    assert "not substitutes for final readiness" in prompt


def test_academic_peer_review_skill_injected_for_complete_paper_scope() -> None:
    rev = Reviewer(_StubRunner())
    prompt = rev._build_prompt(
        objective=(
            "planner_scope: final_submission\n"
            "Finish the EMNLP academic paper in paper/main.tex."
        ),
        operator_messages=[
            "If paper/main.pdf is complete, simulate a reviewer before accepting."
        ],
        planner_review_instruction="Judge whether the manuscript is publication quality.",
        round_index=3,
        session_id="paper-review-test",
        main_summary=(
            "Compiled PDF at paper/main.pdf and ran validate-full-emnlp, "
            "but some reviewer-facing blockers may remain."
        ),
        main_error=None,
        checks=[],
        active_skill_id="auto-research-pipeline.md",
    )

    assert "Academic-paper peer review benchmark skill" in prompt
    assert "Simulate a strict EMNLP/ACL-style program-committee reviewer" in prompt
    assert "Strong Accept" in prompt
    assert "Weak Reject" in prompt
    assert "240 unique semantic scored main tasks/episodes" in prompt
    assert "selected benchmark sources/components" in prompt
    assert "image-2/codex-image2" in prompt
    assert "35 verified BibTeX entries" in prompt
    assert "next_action" in prompt


def test_academic_peer_review_skill_not_injected_for_generic_code_task() -> None:
    prompt = _build_prompt()

    assert "Academic-paper peer review benchmark skill" not in prompt
    assert "Simulate a strict EMNLP/ACL-style program-committee reviewer" not in prompt
