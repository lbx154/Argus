from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = (
    Path(__file__).parents[2] / "scripts" / "mle_bench_lite" / "campaign_policy.py"
)
SPEC = importlib.util.spec_from_file_location("mle_campaign_policy", MODULE_PATH)
assert SPEC and SPEC.loader
campaign_policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(campaign_policy)


def choose(
    *,
    retry_after: dict[str, float],
    now: float = 100.0,
    free_slots: int = 1,
    running: set[str] | None = None,
) -> list[str]:
    return campaign_policy.choose_competitions(
        competitions=["near-medal", "fresh-a", "fresh-b"],
        completed=set(),
        running=running or set(),
        retry_after=retry_after,
        now=now,
        free_slots=free_slots,
        is_prepared=lambda _: True,
    )


def test_ready_retry_precedes_fresh_competition() -> None:
    assert choose(retry_after={"near-medal": 99.0}) == ["near-medal"]


def test_cooling_retry_reserves_the_only_free_slot() -> None:
    assert choose(retry_after={"near-medal": 105.0}) == []


def test_extra_slot_still_advances_fresh_campaign_work() -> None:
    assert choose(retry_after={"near-medal": 105.0}, free_slots=2) == ["fresh-a"]


def test_running_retry_does_not_reserve_another_slot() -> None:
    assert choose(
        retry_after={"near-medal": 105.0},
        free_slots=1,
        running={"near-medal"},
    ) == ["fresh-a"]
