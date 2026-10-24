"""A Reviewer finding contested with labeled evidence must be resolved, not obeyed.

Observed failure: a read-only Reviewer matched unlabeled parallel counts by
position, rejected a correct artifact, the Manager sided with the Reviewer as
"verified", the Engineer overwrote the correct file, and the Reviewer approved
the swapped values without checking them.
"""

from __future__ import annotations

from importlib import resources

from argus.reviewer import Reviewer

_DISPUTE = (
    "I disagree with the review: `grep -c '\\[ERROR\\]' today.log` prints 370 and "
    "`grep -c '\\[WARNING\\]' today.log` prints 463, so the CSV is already correct."
)


def test_reviewer_role_requires_labels_from_the_same_output() -> None:
    text = (resources.files("argus.builtin_skills") / "reviewer" / "argus-reviewer-role.md").read_text(
        encoding="utf-8")
    assert "label comes from the same" in text
    assert "explain what is wrong with that output" in text
    assert "contested review" in text


def test_engineer_labeled_dispute_reaches_next_review_as_something_to_resolve() -> None:
    reviewer = Reviewer(runner=None, skill_store=None)
    prompt = reviewer._build_prompt(
        objective="Write per-period log level counts to summary.csv",
        operator_messages=["count the log levels"],
        planner_review_instruction="",
        round_index=2,
        session_id=None,
        main_summary=_DISPUTE,
        main_error=None,
        prior_checkpoint={},
        prev_review_summary="continue: ERROR and WARNING are swapped in the today row.",
    )
    assert _DISPUTE in prompt
    assert "is not settled" in prompt
    assert "explain what is wrong with the Engineer's output" in prompt
    assert "independently check every value changed in response to a contested" in prompt
