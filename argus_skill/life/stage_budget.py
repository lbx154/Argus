"""Per-stage budget tracker (Opt #2).

Tracks how much budget each pipeline stage has consumed by scanning
journal entries with ``cost_usd`` fields. The harness uses this to
surface ADVISORY signals when a single stage has eaten a
disproportionate share of the project's total budget — informing the
agent + operator that something may be off (long doom loops,
out-of-control planner cycles, runaway experiments) before it
silently exhausts the daily cap.

Pure plumbing per nssmd skill 04: this module surfaces facts
(`stage X has spent $Y, that's Z% of total budget`). The decision
"should I do something about that" stays with the reviewer / planner
agent and the operator. No auto-quarantine here (that would be a
quality judgment about whether the stage cost was justified).

Pipeline placement:

* Supervisor reads the tracker once per tick (before _run_one).
* Cockpit (--status) surfaces the current snapshot.
* Stage attribution comes from ``PIPELINE_STATE.json``'s
  ``current_stage`` at the time each cost-bearing journal entry was
  written; we re-derive by joining journal timestamps with
  pipeline-state-change events. If PIPELINE_STATE is missing, all
  cost lands in stage="unknown" (still useful — at least you see
  total spend).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# Soft advisory threshold: if any single stage has eaten more than
# this fraction of the project's total budget, surface a signal.
DEFAULT_ADVISORY_FRACTION = 0.30

# Canonical 8 stages — used to filter out test/unknown stage values.
KNOWN_STAGES: tuple[str, ...] = (
    "research", "plan", "benchmark", "run",
    "analysis", "draft", "review", "submission",
)


@dataclass(frozen=True)
class StageSpendSignal:
    """Advisory: one stage has consumed >= advisory_fraction of total."""

    stage: str
    spent_usd: float
    budget_usd: float
    fraction: float
    message: str

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "spent_usd": round(self.spent_usd, 4),
            "budget_usd": round(self.budget_usd, 4),
            "fraction": round(self.fraction, 4),
            "message": self.message,
        }


@dataclass
class StageBudgetSnapshot:
    """Aggregate facts about per-stage spend."""

    spent_by_stage: dict[str, float] = field(default_factory=dict)
    total_spent_usd: float = 0.0
    total_budget_usd: float = 0.0
    advisory_signals: list[StageSpendSignal] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "spent_by_stage": {k: round(v, 4) for k, v in self.spent_by_stage.items()},
            "total_spent_usd": round(self.total_spent_usd, 4),
            "total_budget_usd": round(self.total_budget_usd, 4),
            "advisory_signals": [s.to_dict() for s in self.advisory_signals],
        }


def _coerce_cost(entry: Any) -> float:
    """Best-effort: pull a numeric cost_usd out of a journal entry's
    extra dict or attribute. Returns 0.0 if absent / malformed."""
    extra = getattr(entry, "extra", None)
    candidates: list[Any] = []
    if isinstance(extra, dict):
        candidates.extend([extra.get("cost_usd"), extra.get("cumulative_cost_usd")])
    candidates.append(getattr(entry, "cost_usd", None))
    for c in candidates:
        if c is None:
            continue
        try:
            return float(c)
        except (TypeError, ValueError):
            continue
    return 0.0


def _stage_from_entry(entry: Any) -> str:
    """Best-effort: extract a stage tag from a journal entry.

    Looks at:
    1. extra["stage"] if set
    2. tag of form "stage:<name>" in tags
    3. else "unknown"
    """
    extra = getattr(entry, "extra", None)
    if isinstance(extra, dict):
        stage = extra.get("stage")
        if isinstance(stage, str) and stage.strip():
            return stage.strip().lower()
    tags = getattr(entry, "tags", None) or []
    for tag in tags:
        if isinstance(tag, str) and tag.startswith("stage:"):
            return tag.split(":", 1)[1].strip().lower()
    return "unknown"


def compute_snapshot(
    *,
    journal_entries: Iterable[Any],
    total_budget_usd: float,
    advisory_fraction: float = DEFAULT_ADVISORY_FRACTION,
    current_stage: str | None = None,
) -> StageBudgetSnapshot:
    """Compute per-stage spend by scanning recent journal entries.

    ``current_stage`` is used to attribute entries that don't carry
    their own stage tag (most pre-existing mission_complete entries
    don't) — they're assumed to be from the current stage. This is
    a coarse approximation; a proper attribution requires writing
    the stage explicitly when each cost-bearing entry is appended.
    """
    spent: dict[str, float] = {}
    total = 0.0
    for entry in journal_entries:
        cost = _coerce_cost(entry)
        if cost <= 0:
            continue
        stage = _stage_from_entry(entry)
        if stage == "unknown" and current_stage:
            stage = current_stage
        spent[stage] = spent.get(stage, 0.0) + cost
        total += cost

    signals: list[StageSpendSignal] = []
    if total_budget_usd > 0:
        for stage, amount in sorted(spent.items(), key=lambda kv: -kv[1]):
            fraction = amount / total_budget_usd
            if fraction >= advisory_fraction:
                signals.append(StageSpendSignal(
                    stage=stage,
                    spent_usd=amount,
                    budget_usd=total_budget_usd,
                    fraction=fraction,
                    message=(
                        f"stage {stage!r} has spent ${amount:.2f} = "
                        f"{fraction*100:.1f}% of total budget "
                        f"${total_budget_usd:.2f}; reviewer/planner: "
                        f"consider whether this stage is converging"
                    ),
                ))
    return StageBudgetSnapshot(
        spent_by_stage=spent,
        total_spent_usd=total,
        total_budget_usd=total_budget_usd,
        advisory_signals=signals,
    )


def read_pipeline_stage(project_root: Path) -> str | None:
    """Best-effort: read current_stage from research/PIPELINE_STATE.json.
    Returns None if file missing or malformed."""
    path = project_root / "research" / "PIPELINE_STATE.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        stage = data.get("current_stage")
        if isinstance(stage, str) and stage.strip():
            return stage.strip().lower()
    except (OSError, json.JSONDecodeError):
        return None
    return None
