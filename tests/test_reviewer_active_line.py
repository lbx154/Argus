"""Read-only Reviewer checkpoint guidance and minimal verdict parsing."""

from __future__ import annotations

from argus_skill.reviewer import Reviewer, parse_decision_text


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
    assert "Do not inspect or edit checkpoint/context-packet/handoff bookkeeping" in p


def test_reviewer_never_acts_as_checkpoint_editor():
    p = _prompt()
    assert "You do not change the work under review" in p
    assert "Put the next Engineer instruction only in next_action" in p
    assert "Inspect claim-critical uncertainty with proportional tools" in p
    assert (
        "Never reward virtue's form in negative results, hedging, limitation lists, "
        "or repeat runs—only anchored, decision-changing content; positive and "
        "negative claims share one evidence standard."
    ) in p
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

    for forbidden in ("not its sources", "not its artifacts", "not its build"):
        assert forbidden in p, "the prohibition still has to enumerate its scope"
    assert "Recording your own verdict through a command your vertical gives you" in p
    assert "strictly read-only" not in p


def test_checkpoint_state_is_not_copied_into_the_prompt():
    p = _prompt()
    assert "CURATED WORKING MEMORY" not in p
    assert "tried_and_failed" not in p


def test_reviewer_final_handoff_requires_explicit_progress_fields():
    p = _prompt()

    for field in (
        "FORWARD_PROGRESS=true",
        "PLAN_SIGNAL=continue",
        "`plan_challenge`",
        "`plan_alternative`",
        "`authority_impact`",
        "`OPERATOR_OPTIONS=",
    ):
        assert field in p
    assert "Return only STATUS, REASON, NEXT_ACTION and OPERATOR_QUESTION" not in p


def test_reviewer_output_without_confidence_parses_into_verdict():
    # The reviewer no longer self-reports a confidence. A structured output that
    # omits ``confidence`` entirely must still parse into a full verdict — the
    # parser must not depend on a confidence field to render a decision.
    raw = (
        '{"status": "done", "reason": "objective met with verified evidence", '
        '"next_action": "No further action needed.", '
        '"operator_question": null}'
    )
    decision = parse_decision_text(raw)
    assert decision is not None
    assert decision.status == "done"
    assert decision.reason == "objective met with verified evidence"
    # The parsed verdict carries no confidence attribute at all.
    assert not hasattr(decision, "confidence")
