"""A Reviewer finding contested with labeled evidence must be resolved, not obeyed.

Observed failure: a read-only Reviewer matched unlabeled parallel counts by
position, rejected a correct artifact, the Manager sided with the Reviewer as
"verified", the Engineer overwrote the correct file, and the Reviewer approved
the swapped values without checking them.
"""

from __future__ import annotations

from argus.reviewer import Reviewer
from argus.roles.prompts.engineer import build_mission_prompt
from tests.role_rules import lists, rule

_DISPUTE = (
    "I disagree with the review: `grep -c '\\[ERROR\\]' today.log` prints 370 and "
    "`grep -c '\\[WARNING\\]' today.log` prints 463, so the CSV is already correct."
)


def test_reviewer_role_requires_labels_from_the_same_output() -> None:
    text = rule("reviewer", "re-review/SKILL.md")
    assert "label comes from the same output" in text
    assert "explain what is wrong with the Engineer's output" in text
    assert "contested finding" in text


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
    assert lists(prompt, "re-review/SKILL.md")
    assert "read re-review before reusing it" in prompt
    assert "is not settled" in rule("reviewer", "re-review/SKILL.md")


def test_engineer_keeps_values_its_own_labeled_measurement_supports() -> None:
    for include_static in (True, False):
        prompt = build_mission_prompt(
            task="Write summary.csv",
            skill_text="",
            next_action="Swap ERROR and WARNING in every row.",
            include_static=include_static,
        )
        assert "Read review-rounds before acting on it." in prompt
    assert "Never write a value your own labeled measurement contradicts" in rule("engineer", "review-rounds/SKILL.md")
