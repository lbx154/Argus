"""A long-lived session should not keep the name of its first toy task.

Operator report (2026-07-26): a session was still labelled after a small
arithmetic question long after it had moved on to unrelated complex work, so the
resume picker showed a name that told you nothing about what the session had
become.

Naming on every task would churn the picker instead. The event worth renaming on
already exists: the Manager records when a new operator objective *supersedes*
the standing one — the same event it resets the pipeline for. Anything additive
(a clarification, an authorisation, an extra constraint) is explicitly not that.
"""

from __future__ import annotations

from argus.manager.front_door import (
    objective_update_requires_stage_reset,
)

# -- the behaviour that was reported ----------------------------------------


# -- and it fires on the right event ----------------------------------------


def test_an_additive_clarification_is_not_a_replacement() -> None:
    """Otherwise every clarification would rename the session."""
    assert not objective_update_requires_stage_reset(
        "optimise the attention kernel",
        "optimise the attention kernel on B200",
    )
