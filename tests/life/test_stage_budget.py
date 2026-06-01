"""Tests for argus_skill.life.stage_budget."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from argus_skill.life.stage_budget import (
    DEFAULT_ADVISORY_FRACTION,
    KNOWN_STAGES,
    StageBudgetSnapshot,
    StageSpendSignal,
    compute_snapshot,
    read_pipeline_stage,
)


@dataclass
class _StubEntry:
    """Minimal shape of a journal entry the tracker reads from."""
    extra: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    cost_usd: float | None = None


def _e(cost: float, stage: str | None = None, *, via_tag: bool = False) -> _StubEntry:
    extra: dict[str, Any] = {"cost_usd": cost}
    tags: list[str] = []
    if stage and via_tag:
        tags = [f"stage:{stage}"]
    elif stage:
        extra["stage"] = stage
    return _StubEntry(extra=extra, tags=tags)


# ---------------------------------------------------------------------------
# compute_snapshot
# ---------------------------------------------------------------------------


def test_snapshot_aggregates_by_stage_from_extra() -> None:
    entries = [
        _e(1.0, "research"),
        _e(0.5, "research"),
        _e(2.0, "benchmark"),
        _e(0.25, "plan"),
    ]
    snap = compute_snapshot(journal_entries=entries, total_budget_usd=50.0)
    assert snap.spent_by_stage == {
        "research": 1.5,
        "benchmark": 2.0,
        "plan": 0.25,
    }
    assert snap.total_spent_usd == 3.75


def test_snapshot_aggregates_from_stage_tags() -> None:
    entries = [
        _e(1.0, "benchmark", via_tag=True),
        _e(2.0, "benchmark", via_tag=True),
        _e(0.5, "research"),  # extra-key path
    ]
    snap = compute_snapshot(journal_entries=entries, total_budget_usd=50.0)
    assert snap.spent_by_stage == {"benchmark": 3.0, "research": 0.5}


def test_snapshot_attributes_unknown_to_current_stage() -> None:
    """An entry without explicit stage attribution gets bucketed to the
    current pipeline stage. Coarse but useful — at least costs land
    somewhere meaningful."""
    entries = [
        _StubEntry(extra={"cost_usd": 1.0}),  # no stage info
        _StubEntry(extra={"cost_usd": 0.5}, tags=["mission_complete"]),
    ]
    snap = compute_snapshot(
        journal_entries=entries,
        total_budget_usd=50.0,
        current_stage="run",
    )
    assert snap.spent_by_stage == {"run": 1.5}


def test_snapshot_advisory_fires_above_threshold() -> None:
    # 16/50 = 32% > 30% default
    entries = [_e(16.0, "run")]
    snap = compute_snapshot(journal_entries=entries, total_budget_usd=50.0)
    assert len(snap.advisory_signals) == 1
    sig = snap.advisory_signals[0]
    assert sig.stage == "run"
    assert "32" in sig.message
    assert sig.fraction == pytest.approx(0.32)


def test_snapshot_no_advisory_below_threshold() -> None:
    entries = [_e(10.0, "research")]  # 20% of 50
    snap = compute_snapshot(journal_entries=entries, total_budget_usd=50.0)
    assert snap.advisory_signals == []


def test_snapshot_handles_zero_budget_safely() -> None:
    entries = [_e(5.0, "run")]
    snap = compute_snapshot(journal_entries=entries, total_budget_usd=0.0)
    # No division by zero, no advisory
    assert snap.advisory_signals == []
    assert snap.spent_by_stage == {"run": 5.0}


def test_snapshot_ignores_non_numeric_costs() -> None:
    entries = [
        _StubEntry(extra={"cost_usd": "not-a-number"}),
        _StubEntry(extra={}),  # no cost_usd at all
        _e(2.0, "run"),
    ]
    snap = compute_snapshot(journal_entries=entries, total_budget_usd=50.0)
    assert snap.total_spent_usd == 2.0


def test_snapshot_supports_cumulative_cost_field() -> None:
    """Some entries use cumulative_cost_usd instead of cost_usd."""
    entries = [_StubEntry(extra={"cumulative_cost_usd": 3.0, "stage": "draft"})]
    snap = compute_snapshot(journal_entries=entries, total_budget_usd=50.0)
    assert snap.spent_by_stage == {"draft": 3.0}


# ---------------------------------------------------------------------------
# read_pipeline_stage
# ---------------------------------------------------------------------------


def test_read_pipeline_stage_returns_current(tmp_path: Path) -> None:
    state_dir = tmp_path / "research"
    state_dir.mkdir()
    (state_dir / "PIPELINE_STATE.json").write_text(
        json.dumps({"current_stage": "benchmark"}), encoding="utf-8"
    )
    assert read_pipeline_stage(tmp_path) == "benchmark"


def test_read_pipeline_stage_returns_none_when_missing(tmp_path: Path) -> None:
    assert read_pipeline_stage(tmp_path) is None


def test_read_pipeline_stage_tolerates_corrupt_json(tmp_path: Path) -> None:
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        "{not valid", encoding="utf-8"
    )
    assert read_pipeline_stage(tmp_path) is None


# ---------------------------------------------------------------------------
# Anti-regression
# ---------------------------------------------------------------------------


def test_known_stages_lists_canonical_8() -> None:
    # Lock in the canonical stage list against accidental edits
    assert KNOWN_STAGES == (
        "research", "plan", "benchmark", "run",
        "analysis", "draft", "review", "submission",
    )


def test_default_advisory_fraction_is_conservative() -> None:
    # 30% per-stage spend = noteworthy but not panic
    assert DEFAULT_ADVISORY_FRACTION == 0.30
