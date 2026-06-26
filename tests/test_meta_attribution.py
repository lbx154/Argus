"""Meta jump-vs-exploit floor-improvement attribution (NO-VERDICT visibility).

Pins the counting in ``ledger.attribution_summary`` and the fail-soft render in
``meta_prompter.attribution_fact`` — the instrument that lets the agent/operator
SEE whether the anti-greedy regime-jumps are MOVING the promoted floor or just
churning regimes (the operator's "is the scaffolding working?" question).
"""
from __future__ import annotations

from pathlib import Path

from argus_skill.meta.ledger import (
    append_decision,
    attribution_summary,
    read_decisions,
)
from argus_skill.meta.meta_prompter import attribution_fact


def test_attribution_counts_jumps_exploits_and_attributes_improvements() -> None:
    rows = [
        {"mode": "exploit", "was_jump": False, "performance": 0.97},
        {"mode": "jump", "was_jump": True, "performance": 0.97},     # a jump cycle
        {"mode": "exploit", "was_jump": False, "performance": 0.96},  # 0.97→0.96 after the jump
        {"mode": "exploit", "was_jump": False, "performance": 0.955}, # 0.96→0.955 after exploit
        {"mode": "jump", "was_jump": True, "performance": 0.955},     # jump, no improvement
    ]
    s = attribution_summary(rows)
    assert s["jumps_fired"] == 2
    assert s["exploits_fired"] == 3
    assert s["floor_improvements"] == 2
    assert s["improvements_after_jump"] == 1
    assert s["improvements_after_exploit"] == 1


def test_attribution_is_failsoft_on_missing_performance() -> None:
    # Rows with no/garbage performance must not crash or count as improvements.
    rows = [
        {"mode": "jump", "was_jump": True},  # no performance
        {"mode": "exploit", "was_jump": False, "performance": "n/a"},
        {"mode": "exploit", "was_jump": False, "performance": 0.96},
    ]
    s = attribution_summary(rows)
    assert s["jumps_fired"] == 1
    assert s["floor_improvements"] == 0  # only one parseable perf → no delta


def test_attribution_fact_empty_until_there_is_activity() -> None:
    # Fresh mission (no jumps, no improvements) → no noise in the prompt.
    assert attribution_fact(attribution_summary([])) == ""
    assert (
        attribution_fact(
            attribution_summary(
                [{"mode": "exploit", "was_jump": False, "performance": 0.97}]
            )
        )
        == ""
    )


def test_attribution_fact_renders_no_verdict_visibility() -> None:
    rows = [
        {"mode": "jump", "was_jump": True, "performance": 0.97},
        {"mode": "exploit", "was_jump": False, "performance": 0.96},
    ]
    out = attribution_fact(attribution_summary(rows))
    assert "NO verdict" in out
    assert "Regime-jumps fired so far: 1" in out
    assert "Promoted-floor improvements: 1" in out
    assert "interpretation is YOURS" in out


def test_read_decisions_roundtrip_feeds_attribution(tmp_path: Path) -> None:
    append_decision(tmp_path, {"mode": "jump", "was_jump": True, "performance": 0.97})
    append_decision(
        tmp_path, {"mode": "exploit", "was_jump": False, "performance": 0.96}
    )
    s = attribution_summary(read_decisions(tmp_path))
    assert s["n_decisions"] == 2
    assert s["jumps_fired"] == 1
    assert s["floor_improvements"] == 1
    assert s["improvements_after_jump"] == 1
