"""A review that closes on the Engineer's wait line is a deferral, not a backend failure.

Asked to judge a round that had sent a benchmark to the background, the
Reviewer wrote that every clause depended on the run finishing and closed with
the Engineer's own wait line instead of the Decision block. The host read that
as "no STATUS line" twice and stopped the mission while the run kept going.
"""
from __future__ import annotations

from argus.reviewer._parsing import decision_from_deferred_wait

PROSE = (
    "The script passed its nine checks and the run is on the GPU; the full-cache row is in.\n\n"
    "Every acceptance clause depends on the run finishing."
)
WAIT = '{"wait_for": "subagent", "wait_id": "qwen25-longbench-eval-01"}'


def test_prose_that_ends_on_the_wait_line_reads_as_continue_against_the_run() -> None:
    decision = decision_from_deferred_wait([PROSE + "\n\n" + WAIT])
    assert decision is not None and decision.status == "continue"
    assert "nine checks" in decision.reason and "wait_for" not in decision.reason
    assert "qwen25-longbench-eval-01" in decision.next_action
    assert not decision.backend_unavailable


def test_a_bare_wait_line_or_an_ordinary_reply_is_not_read_this_way() -> None:
    assert decision_from_deferred_wait([WAIT]) is None
    assert decision_from_deferred_wait(["STATUS=done\nREASON=fine"]) is None
    assert decision_from_deferred_wait([PROSE + '\n{"wait_for": "coffee", "wait_id": "x"}']) is None
    assert decision_from_deferred_wait([PROSE + '\n{"wait_for": "subagent"}']) is None
    assert decision_from_deferred_wait([]) is None
    assert decision_from_deferred_wait([WAIT + "\n" + PROSE]) is None
