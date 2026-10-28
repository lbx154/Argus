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
on its own line read fine. The verdict reader returned ``None``,
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

from argus.core.role_reply import read_block, read_key_values


def test_a_short_key_is_not_found_inside_a_longer_one() -> None:
    """``STATUS`` lives inside ``MILESTONE_STATUS``; an underscore is not a stop."""
    values = read_key_values(
        "Work is finished.MILESTONE_STATUS=done", ["STATUS", "MILESTONE_STATUS"]
    )

    assert values == {"MILESTONE_STATUS": "done"}


def test_a_welded_block_field_still_runs_to_the_next_key() -> None:
    text = "The proof holds.REASON=first line\nsecond line\nSTATUS=done\n"

    assert read_block(text, "REASON", ["STATUS", "REASON"]) == "first line\nsecond line"

