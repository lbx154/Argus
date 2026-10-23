"""Read-only Reviewer checkpoint guidance and minimal verdict parsing."""

from __future__ import annotations

from argus.reviewer import Reviewer
from tests.role_rules import lists, rule


def _prompt(checkpoint_path: str = "/tmp/project/CHECKPOINT.md") -> str:
    r = Reviewer(runner=None, skill_store=None)
    return r._build_prompt(
        objective="minimize val_bpb",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="(handoff)",
        main_error=None,
        checkpoint_path=checkpoint_path,
    )


def test_reviewer_is_not_given_checkpoint_bookkeeping():
    p = _prompt()
    assert "/tmp/project/CHECKPOINT.md" not in p
    assert "CHECKPOINT_RECOMMENDED" not in p
    assert "neither read nor edit CHECKPOINT.md or other context records" in rule("reviewer", "argus-reviewer-role.md")


def test_reviewer_never_acts_as_checkpoint_editor():
    p = _prompt()
    assert 'change no source, output or build file' in p
    role = rule("reviewer", "argus-reviewer-role.md")
    assert "What the Engineer receives is your `note`" in role
    assert "Ground negatives, hedging, limitations and reruns in their consequences; all claims share one evidence standard." in role
    assert "six total read/search tool calls" not in p


def test_the_no_mutation_rule_says_what_it_covers_and_what_it_does_not():
    """It used to read "You are strictly read-only", which was not true.

    Verticals hand the Reviewer commands that write: math's review skill tells
    it to file `math_state judge` and `citation_check attribute`, and those
    records are the independent-review evidence channel — the one thing only a
    Reviewer can supply. A model holding both instructions has to pick one, and
    the read-only sentence is the categorical one, so the channel starves
    silently and the gate that waits on it never sees a check that was never
    filed. The rule is about the *work under review*, so it now says that.
    """
    p = _prompt()

    assert "change no source, output or build file" in p
    assert "use the vertical's own command for judgments" in rule("reviewer", "argus-reviewer-role.md")
    assert "strictly read-only" not in p


def test_checkpoint_state_is_not_copied_into_the_prompt():
    p = _prompt()
    assert "CURATED WORKING MEMORY" not in p
    assert "tried_and_failed" not in p


def test_reviewer_handoff_uses_actions():
    p = _prompt()

    assert "End your turn with hand_off" in p
    assert lists(p, "deciding/SKILL.md")
    assert "STATUS=" not in p
