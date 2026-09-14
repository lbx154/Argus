"""Explicit shared contract surface for four-stage metric optimization.

The generic ``setup -> optimize -> measure -> report`` shape and its
metric-agnostic reviewer checklist live here, framework-owned, so that every
optimization vertical -- the built-in ``math_synth`` and the community ones
in ``argus-verticals`` (``speedrun``, ``kernelbench``, ``nanochat``,
``nanogpt_speedrun``) -- specialises one canonical base instead of copying
it. Mission-specific nouns (the editable file, the scorer, the metric name,
the named baseline) come from the operator objective / MISSION.md, never from
this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..skills.stage_machine import ChecklistItem

OPTIMIZATION_STAGE_ORDER = ("setup", "optimize", "measure", "report")

#: Per-stage reviewer checklist for a metric-optimization mission: the
#: deliverable/eval contract is pinned at ``setup``, the candidate is produced
#: and screened at ``optimize``, the repeat-mean / budget measurement happens
#: at ``measure``, and the head-to-head baseline comparison is the ``report``.
OPTIMIZATION_CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "setup": (
        ChecklistItem(
            id="setup.solution_self_contained",
            statement=(
                "What the mission asks for is the EDITABLE file it names (a recipe / "
                "solution file / kernel) that uses the mission's FROZEN harness/scorer "
                "UNCHANGED and modifies ONLY that editable file. The reviewer must "
                "confirm the agent did NOT touch the harness, the evaluation, the metric, "
                "the held-out data, or the budget — they are byte-identical to the scaffold "
                "(hash / `git diff` against the pinned scaffold) — and that the candidate is "
                "scored through the mission's frozen scorer exactly as the mission specifies."
            ),
            evidence_hint=(
                "the editable file + an unchanged-harness hash vs the pinned scaffold + "
                "a run log produced through the mission's frozen scorer"
            ),
        ),
        ChecklistItem(
            id="setup.heldout_val",
            statement=(
                "Evaluation reads the HELD-OUT / contract-specified inputs wired by the "
                "frozen harness, and there is NO leakage or exploit: the candidate does not "
                "see, memorise, or HARDCODE the known eval inputs/answers, and the metric / "
                "eval inputs are the scaffold's, untouched. The reward must reflect genuine "
                "generalisation, so a candidate that obtains its score by encoding the known "
                "eval distribution (e.g. baking in fixed statistics of the test inputs) "
                "rather than COMPUTING the real result is disqualified — that is a reward "
                "hack, not a result."
            ),
            evidence_hint=(
                "the harness's eval wiring + the candidate showing it COMPUTES the real "
                "result rather than encoding/hardcoding the known eval inputs"
            ),
        ),
    ),
    "optimize": (
        ChecklistItem(
            id="optimize.metric_curve",
            statement=(
                "The mission metric over the run MOVES THE RIGHT WAY (the candidate is "
                "actually improving within the budget), OR any flat / wrong-way / noisy / "
                "early-plateau trajectory is EXPLICITLY explained (e.g. budget-bound, "
                "schedule, warmup, divergence) rather than silently accepted. A trajectory "
                "that never improves over the starting point is a dead attempt, not a result. "
                "No real trajectory means this item is UNSATISFIED: an external launch "
                "rejection may justify a blocked mission, but never optimize-stage completion."
            ),
            evidence_hint=(
                "the metric-vs-step (or vs-wall-clock) series in the run log; a one-line "
                "explanation for any non-improving trajectory"
            ),
        ),
    ),
    "measure": (
        ChecklistItem(
            id="measure.repeat_mean_metric",
            statement=(
                "The reported result is the AGGREGATE mission metric across N repeats "
                "(iterate at small N, report the final number at higher N) — NOT a single "
                "lucky run and NEVER the number the candidate printed about itself. A "
                "per-repeat record captures each run's metric as RE-MEASURED by the VERIFIER "
                "re-running the candidate through the frozen scorer under the identical "
                "protocol; the headline the reviewer trusts is the verifier's, because the "
                "agent edits only the editable file and can self-report anything."
            ),
            evidence_hint=(
                "per-repeat record (run, metric) from the verifier's re-runs + the computed "
                "aggregate; each row traceable to a real frozen-scorer output line"
            ),
        ),
        ChecklistItem(
            id="measure.budget_respected",
            statement=(
                "Every scored run respected the FIXED budget the mission declares (wall-clock "
                "and hardware) — the candidate did not extend, bypass, or hand-tune the "
                "budget, and no scored run exceeded it. The contest is the BEST metric "
                "reachable UNDER the fixed budget, so a candidate that only attains its score "
                "by exceeding the budget is invalid; the budget in the frozen harness stays "
                "unchanged."
            ),
            evidence_hint=(
                "per-run wall-clock within the declared budget in the run log / manifest; "
                "the budget in the frozen harness unchanged"
            ),
        ),
    ),
    "report": (
        ChecklistItem(
            id="report.beats_baseline",
            statement=(
                "The proposed candidate's metric BEATS the RE-MEASURED baseline: the named "
                "reference baseline re-run ON OUR harness and hardware under the identical "
                "protocol (same repeats, same budget, same held-out eval) — NOT a published "
                "number from different hardware. The comparison is head-to-head and cites "
                "BOTH per-run records (ours and the re-measured baseline's) so the win is a "
                "like-for-like delta, not a measurement artifact of differing hardware or "
                "protocol. If the candidate "
                "does NOT beat the re-measured baseline, say so plainly and queue a "
                "repair/pivot — do not relabel a loss as a win."
            ),
            evidence_hint=(
                "two per-run records (proposed vs re-measured baseline) under the identical "
                "protocol + the metric delta; baseline re-run on our hardware, not a "
                "published number from other hardware"
            ),
        ),
    ),
}


@dataclass(frozen=True)
class OptimizationBaseContract:
    stage_order: tuple[str, ...]
    checklist_items: dict[str, Any]


def speedrun_base_contract() -> OptimizationBaseContract:
    """Return independent containers for a speedrun-shaped specialization.

    The name is the public seam the ``argus-verticals`` optimization verticals
    import; each call hands out a fresh ``dict`` so a specialization can
    replace or extend stages without mutating the canonical table above.
    """
    return OptimizationBaseContract(
        stage_order=OPTIMIZATION_STAGE_ORDER,
        checklist_items=dict(OPTIMIZATION_CHECKLIST_ITEMS),
    )


__all__ = [
    "OPTIMIZATION_CHECKLIST_ITEMS",
    "OPTIMIZATION_STAGE_ORDER",
    "OptimizationBaseContract",
    "speedrun_base_contract",
]
