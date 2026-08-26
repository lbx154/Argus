"""The example task shipped inside the planner prompt, so nothing runs it.

The planner prompt shows the required JSON with one filled-in task so the
shape is unambiguous. Campaigns have returned that example verbatim as their
actual plan -- run-01 handed it back in place of a real ImageNet-C mission,
and run-04 spent a mission on it. It reads like a plausible research question,
which is exactly why it was chosen for the prompt and exactly why nothing
downstream notices it is not this campaign's work.

Rejecting it when the plan is validated is not enough: an example that reached
the backlog before the check existed stays there, and a stored item is claimed
without ever being planned again. So the same fact is checked at both gates.

Pinned by test, so the prompt and this module cannot drift apart.
"""

from __future__ import annotations

PROMPT_EXAMPLE_TASKS = frozenset(
    {
        # The example shipped when this guard was written. Still listed: a
        # backlog that stored it before the guard existed keeps it forever.
        ("does pruning beat 4-bit at equal latency?", "match latency, read top-1"),
        # Retired schema example: keep rejecting copies already in a backlog.
        (
            "run the next decisive check",
            "execute the concrete check required by current evidence",
        ),
        # The ambitious experiment-program example the prompt ships today.
        (
            "launch the strongest untested attack on the core hypothesis",
            "design and run the experiment whose outcome most changes what we "
            "believe, with success and failure criteria stated in advance",
        ),
    }
)


def is_prompt_example_task(title: str, objective: str) -> bool:
    """True when this is the prompt's own illustration rather than a plan.

    Matched on title *and* objective together: a campaign is allowed to
    genuinely study pruning against 4-bit, and would not arrive at the
    prompt's abbreviated objective while doing so.
    """
    key = (" ".join(title.split()).lower(), " ".join(objective.split()).lower())
    return key in PROMPT_EXAMPLE_TASKS
