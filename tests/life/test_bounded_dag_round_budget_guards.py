"""Round-budget guards must stay reachable when ``max_rounds`` is overridden.

``SupervisedConfig`` sizes ``stall_threshold`` / ``soft_round_limit`` /
``hard_escalate_rounds`` for the default 500-round budget. A caller may hand one
mission a far smaller budget — ``bounded_dag_node_max_rounds()`` (see the
sibling ``test_bounded_dag_round_budget``) yields 3 — and before the
``SupervisedConfig.__post_init__`` rescale every one of those guards silently
became unreachable. What remained was ``no_progress_threshold``, which counts
EMPTY rounds only and therefore cannot tell a converging mission from a
spinning one.

These tests pin the arithmetic AND the observable classifier behaviour, because
the arithmetic alone is what regressed unnoticed: the guards stayed in the
config, were passed to the classifier, and evaluated to ``False`` forever.
"""

from __future__ import annotations

from argus_skill.core.models import ReviewDecision
from argus_skill.engineer.round_config import SupervisedConfig
from argus_skill.engineer.round_settlement import RoundSettlementMixin


def _classify_stalled(config: SupervisedConfig, *, streak: int, round_index: int):
    return RoundSettlementMixin._classify(
        review=ReviewDecision(
            status="continue",
            reason="Residual still open.",
            next_action="Discharge the next conjunct.",
        ),
        no_progress_streak=0,
        no_progress_threshold=config.no_progress_threshold,
        semantic_stall_streak=streak,
        stall_threshold=config.stall_threshold,
        round_index=round_index,
        max_rounds=config.max_rounds,
        hard_escalate_rounds=config.hard_escalate_rounds,
    )


def test_default_budget_leaves_every_guard_untouched() -> None:
    """The 500-round default must stay byte-for-byte identical."""
    config = SupervisedConfig()

    assert config.max_rounds == 500
    assert config.stall_threshold == 4
    assert config.soft_round_limit == 12
    assert config.hard_escalate_rounds == 24


def test_small_budget_rescales_guards_into_reach() -> None:
    config = SupervisedConfig(max_rounds=3)

    # A stall streak can never exceed the round index, and the classifier also
    # requires ``round_index < max_rounds``, so the guard has to sit at or
    # below ``max_rounds - 1`` to be satisfiable at all.
    assert config.stall_threshold <= config.max_rounds - 1
    assert config.soft_round_limit <= config.max_rounds - 1
    assert config.hard_escalate_rounds <= config.max_rounds


def test_semantic_stall_guard_can_actually_fire_on_a_bounded_node() -> None:
    """The regression this file exists for.

    With ``stall_threshold=4`` and ``max_rounds=3`` the classifier needs a
    streak of 4 while the round index is still below 3 — unsatisfiable — so a
    spinning bounded node burned its whole budget without the semantic guard
    ever being consulted.
    """
    config = SupervisedConfig(max_rounds=3)

    classifications = [
        _classify_stalled(config, streak=streak, round_index=streak)[0]
        for streak in range(1, config.max_rounds + 1)
    ]

    assert "no_progress" in classifications, (
        "the semantic stall guard is unreachable inside a 3-round budget; "
        f"classifications were {classifications}"
    )


def test_unbounded_budget_keeps_explicitly_disabled_guards_disabled() -> None:
    """A progressive experiment matrix zeroes both escalation guards on purpose."""
    config = SupervisedConfig(
        max_rounds=2_147_483_647,
        soft_round_limit=0,
        hard_escalate_rounds=0,
    )

    assert config.soft_round_limit == 0
    assert config.hard_escalate_rounds == 0


def test_single_round_budget_never_produces_a_disabled_looking_guard() -> None:
    """A repair capability pins ``max_rounds`` to 1; 0 would read as 'disabled'."""
    config = SupervisedConfig(max_rounds=1)

    assert config.stall_threshold >= 1
    assert config.soft_round_limit >= 1
    assert config.hard_escalate_rounds >= 1


def test_nonpositive_budget_is_left_alone() -> None:
    config = SupervisedConfig(max_rounds=0)

    assert config.stall_threshold == 4
    assert config.hard_escalate_rounds == 24
