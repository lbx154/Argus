from __future__ import annotations

from argus.life.supervisor._planning_cycle_helpers import _revision_reason


def test_revision_reason_falls_back_to_stable_nonempty_value() -> None:
    assert _revision_reason({}) == "Reviewer requested replacement of the active plan"
    assert _revision_reason({"reason": "replace stale node"}) == "replace stale node"
