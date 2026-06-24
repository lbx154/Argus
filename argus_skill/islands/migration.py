"""Migration + island-reset selection for multi-island search.

FunSearch / AlphaEvolve mechanics, computed from the agents' OWN recorded state
(metric-blind harness): every island exposes a floor via the vertical's
``search_altitude_facts`` and a saturation signal via the meta layer. Migration
shares the population-wide best candidate as an "inspiration"; reset picks the
stalest island and reseeds it into an axis the population is starved on. None of
this judges research content — it ranks islands by their own floors / frozen
counters and copies self-contained candidate files.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .workspace import DEFAULT_AXES, IslandSpec


@dataclass
class IslandStatus:
    """Live read of one island's search state."""

    spec: IslandSpec
    floor: float | None
    floor_name: str
    since_improve: int
    n_attempts: int
    coverage: dict
    best_candidate: Path | None  # train.candidate.py of the island's best attempt


def _vmod():
    from ..verticals._base import load_vertical

    return load_vertical("nanochat")


def read_status(spec: IslandSpec) -> IslandStatus:
    """Read one island's floor + saturation from its OWN attempts/ (fail-soft)."""
    floor = None
    floor_name = ""
    since = 0
    n = 0
    coverage: dict = {}
    best_path: Path | None = None
    try:
        from ..meta.saturation import analyze
        from ..verticals._base import vertical_search_altitude_facts

        facts = vertical_search_altitude_facts(_vmod(), spec.cwd) or {}
        if facts.get("floor") is not None:
            floor = float(facts["floor"])
        floor_name = str(facts.get("floor_name") or "")
        since = int(facts.get("since_improve", 0) or 0)
        n = int(facts.get("n_attempts", 0) or 0)
        sig = analyze(spec.cwd, _vmod())
        coverage = dict(sig.coverage)
        if floor_name:
            cand = spec.cwd / "attempts" / floor_name / "train.candidate.py"
            if cand.exists():
                best_path = cand
    except Exception:  # noqa: BLE001 — status read must never crash the loop
        pass
    return IslandStatus(
        spec=spec,
        floor=floor,
        floor_name=floor_name,
        since_improve=since,
        n_attempts=n,
        coverage=coverage,
        best_candidate=best_path,
    )


def global_best(statuses: list[IslandStatus]) -> IslandStatus | None:
    """The island with the lowest floor (best val_bpb) that has a candidate file."""
    scored = [s for s in statuses if s.floor is not None and s.best_candidate is not None]
    if not scored:
        return None
    return min(scored, key=lambda s: s.floor)


def reset_target(
    statuses: list[IslandStatus], *, min_frozen: int = 12, protect_id: str = ""
) -> IslandStatus | None:
    """Pick the stalest island to reset: the one frozen the longest beyond
    ``min_frozen``. The current global best is never reset (``protect_id``).
    Returns None if nothing is stale enough yet.
    """
    candidates = [
        s
        for s in statuses
        if s.spec.island_id != protect_id and s.since_improve >= min_frozen
    ]
    if not candidates:
        return None
    # Most frozen first; tie-break on worst (highest) floor.
    return max(candidates, key=lambda s: (s.since_improve, s.floor or 1e9))


def starved_axis(statuses: list[IslandStatus], axes: tuple[str, ...] = DEFAULT_AXES) -> str:
    """An axis the population is most starved on: prefer an axis no ACTIVE island
    is currently seeded toward; else the axis with the least union coverage.
    """
    active_axes = {s.spec.regime_axis for s in statuses}
    for ax in axes:
        if ax not in active_axes:
            return ax
    union: dict[str, int] = {}
    for s in statuses:
        for k, v in (s.coverage or {}).items():
            union[k] = union.get(k, 0) + int(v)
    return min(axes, key=lambda ax: union.get(ax, 0))


def migrate_best(global_status: IslandStatus, statuses: list[IslandStatus]) -> int:
    """Copy the population-best candidate into every OTHER island's
    ``inspirations/`` dir (surfaced to that island's planner as a diverse parent).
    Returns the number of islands seeded. Self-contained file copy.
    """
    import shutil

    if global_status.best_candidate is None:
        return 0
    n = 0
    for s in statuses:
        if s.spec.island_id == global_status.spec.island_id:
            continue
        insp = s.spec.cwd / "inspirations"
        insp.mkdir(exist_ok=True)
        tag = f"global_best_from_{global_status.spec.island_id}_{global_status.floor:.6f}.py"
        try:
            shutil.copy2(global_status.best_candidate, insp / tag)
            n += 1
        except Exception:  # noqa: BLE001
            pass
    return n
