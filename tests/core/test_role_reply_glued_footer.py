"""A verdict welded to the end of a sentence is still a verdict.

Testbed run 15 (``s-f0dbba19``) round 1: the Reviewer inspected a finished
final submission and ruled ``done``, with a reason, a research result, a
frontier report and nineteen named fields — 6767 output tokens, $0.675. The
harness threw all of it away and logged::

    {"type":"round.review.completed","status":"continue",
     "reason":"Reviewer output did not contain a valid named verdict footer."}

The footer was there. Eighteen of the nineteen fields parsed. The message ran::

    ...that are the material compilation evidence for the requested Lean source.STATUS=done
    REASON=Final submission gate is satisfied: ...
    NEXT_ACTION=
    ...

One missing newline. ``read_key_values`` anchors on ``^``, so ``STATUS`` welded
to the end of the preceding sentence was invisible while every field below it
on its own line read fine. ``parse_decision_text`` returned ``None``,
``_core.py`` substituted a hardcoded ``continue``, and the run bought an
Engineer round and a second Reviewer round to re-derive the verdict it had
already been handed. Those two rounds changed no product file; the second
Reviewer returned the same ``done``. $1.64 of a $13.43 run, 293 seconds.

The rescue is narrow on purpose. Splitting on *any* mid-line key would let a
model quoting its own instructions — ``end with `MILESTONE_STATUS=done|continue``
— manufacture a verdict. Only a sentence terminator counts, which is what the
one real occurrence had and what the six instruction echoes in the same run's
prompts did not.

The status floor matters as much as the money. An unreadable reply becomes
``continue``, so a Reviewer writing ``...this is unsound.STATUS=blocked`` was
rewritten into "keep going" and the Engineer was told to build on work the
Reviewer had just rejected.
"""

from __future__ import annotations

import pytest

from argus_skill.core.role_reply import read_block, read_key_values, read_records
from argus_skill.reviewer._parsing import (
    _find_decision_in_messages,
    describe_unparsed_verdict,
)

#: Verbatim from run 15's ``agent_io.jsonl``, message
#: ``cceec252-a852-42ab-bd8a-630087b6597e``, abridged in the prose above the
#: footer and in the evidence arrays. The weld is exact.
RUN_15_REVIEW = (
    "I inspected the Lean build log and the two artefacts referenced by the "
    "checkpoint, which are the material compilation evidence for the requested "
    "Lean source.STATUS=done\n"
    "REASON=Final submission gate is satisfied: the certified records mark "
    "review complete and the Lean file builds with no sorry and no axiom.\n"
    "NEXT_ACTION=\n"
    "OPERATOR_QUESTION=none\n"
    "FORWARD_PROGRESS=true\n"
)


def test_run_15s_lost_verdict_is_read() -> None:
    decision = _find_decision_in_messages([RUN_15_REVIEW])

    assert decision is not None
    assert decision.status == "done"


def test_the_reason_comes_with_it() -> None:
    """A status with no rationale is refused upstream, so both must survive."""
    decision = _find_decision_in_messages([RUN_15_REVIEW])

    assert "Final submission gate is satisfied" in decision.reason


def test_the_fields_below_the_weld_still_read() -> None:
    """The rescue must not disturb what already worked."""
    values = read_key_values(
        RUN_15_REVIEW, ["STATUS", "REASON", "NEXT_ACTION", "OPERATOR_QUESTION"]
    )

    assert values["STATUS"] == "done"
    assert values["OPERATOR_QUESTION"] == "none"


@pytest.mark.parametrize("status", ["done", "continue", "blocked", "replan_requested"])
def test_every_status_survives_the_weld_not_just_the_permissive_one(
    status: str,
) -> None:
    """``blocked`` is the one that costs most to lose.

    An unreadable reply is defaulted to ``continue``, so a welded ``blocked``
    became "carry on" and the Engineer kept building on rejected work.
    """
    text = f"The construction does not hold.STATUS={status}\nREASON=stated above\n"

    decision = _find_decision_in_messages([text])

    assert decision is not None
    assert decision.status == status


@pytest.mark.parametrize(
    "glue",
    ["source.", "source!", "source?", 'source"', "source)", "source]"],
)
def test_a_sentence_terminator_is_what_licenses_the_split(glue: str) -> None:
    values = read_key_values(f"prose {glue}STATUS=done", ["STATUS"])

    assert values == {"STATUS": "done"}


@pytest.mark.parametrize(
    "echo",
    [
        "End with a decisive check, `MILESTONE_STATUS=done|continue|blocked`, then stop.",
        "Set STATUS=done only when the gate is certified, never before.",
        "The field is named STATUS: done and continue are both legal.",
    ],
)
def test_an_instruction_echo_mid_sentence_is_not_a_verdict(echo: str) -> None:
    """Six of these appear in run 15's own prompts. None is a ruling.

    A comma, a backtick and a colon are all places a model says the name of a
    field while talking about it. Only a sentence terminator says it has
    stopped talking and started answering.
    """
    assert read_key_values(echo, ["STATUS", "MILESTONE_STATUS"]) == {}


def test_discussing_a_verdict_after_a_sentence_is_not_a_verdict() -> None:
    assert read_key_values("Work continues. STATUS: done", ["STATUS"]) == {}


def test_a_short_key_is_not_found_inside_a_longer_one() -> None:
    """``STATUS`` lives inside ``MILESTONE_STATUS``; an underscore is not a stop."""
    values = read_key_values(
        "Work is finished.MILESTONE_STATUS=done", ["STATUS", "MILESTONE_STATUS"]
    )

    assert values == {"MILESTONE_STATUS": "done"}


def test_a_welded_block_field_still_runs_to_the_next_key() -> None:
    text = "The proof holds.REASON=first line\nsecond line\nSTATUS=done\n"

    assert read_block(text, "REASON", ["STATUS", "REASON"]) == "first line\nsecond line"


def test_welded_records_are_read_as_records() -> None:
    text = (
        "Here is the backlog.TASK_TITLE=first\nTASK_DETAIL=a\n"
        "TASK_TITLE=second\nTASK_DETAIL=b\n"
    )

    records = read_records(
        text, ["TASK_TITLE", "TASK_DETAIL"], start_key="TASK_TITLE"
    )

    assert [r["TASK_TITLE"] for r in records] == ["first", "second"]


def test_a_normal_footer_is_untouched() -> None:
    text = "All checks pass.\n\nSTATUS=done\nREASON=everything verified\n"

    assert read_key_values(text, ["STATUS", "REASON"])["STATUS"] == "done"


def test_the_rescue_never_overrides_a_key_that_already_parsed() -> None:
    """Strict first, and only the keys it missed are retried.

    Otherwise a reply that reads correctly today could be reinterpreted by the
    rescue tomorrow, which is a worse failure than the one being fixed.
    """
    text = "Do not confuse this.STATUS=blocked\nSTATUS=done\n"

    assert read_key_values(text, ["STATUS"]) == {"STATUS": "done"}


def test_an_unreadable_reply_says_which_field_failed() -> None:
    """"No footer" was printed against a reply with eighteen parsed fields."""
    text = "REASON=I could not decide\nNEXT_ACTION=keep going\n"

    detail = describe_unparsed_verdict([text])

    assert "no readable STATUS" in detail
    assert "REASON" in detail


def test_an_illegal_status_is_named_with_its_value() -> None:
    detail = describe_unparsed_verdict(["STATUS=approved\nREASON=looks fine\n"])

    assert "approved" in detail
    assert "replan_requested" in detail


def test_a_verdict_with_no_rationale_says_so() -> None:
    detail = describe_unparsed_verdict(["STATUS=done\nREASON=\n"])

    assert "REASON" in detail
    assert "done" in detail


def test_empty_output_is_not_described_as_a_footer_problem() -> None:
    assert "no output" in describe_unparsed_verdict([])
