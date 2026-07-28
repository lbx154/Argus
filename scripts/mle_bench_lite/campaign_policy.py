"""Pure scheduling policy for the medal-gated MLE campaign."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence


def choose_competitions(
    *,
    competitions: Sequence[str],
    completed: Collection[str],
    running: Collection[str],
    retry_after: Mapping[str, float],
    now: float,
    free_slots: int,
    is_prepared: Callable[[str], bool],
) -> list[str]:
    """Choose starts while reserving capacity for cooling-down retries.

    A below-medal run should not lose its GPU for hours merely because its
    short retry cooldown overlaps one scheduler tick. Ready retries come first;
    each near-future retry reserves one otherwise-free slot. Any remaining
    capacity continues the first-pass campaign.
    """
    if free_slots <= 0:
        return []
    eligible = [
        competition
        for competition in competitions
        if competition not in completed
        and competition not in running
        and is_prepared(competition)
    ]
    ready_retries = sorted(
        (
            competition
            for competition in eligible
            if competition in retry_after and retry_after[competition] <= now
        ),
        key=lambda competition: (retry_after[competition], competitions.index(competition)),
    )
    waiting_retries = [
        competition
        for competition in eligible
        if competition in retry_after and retry_after[competition] > now
    ]
    fresh = [competition for competition in eligible if competition not in retry_after]

    selected = ready_retries[:free_slots]
    remaining_slots = free_slots - len(selected)
    reserved_slots = min(remaining_slots, len(waiting_retries))
    selected.extend(fresh[: remaining_slots - reserved_slots])
    return selected
