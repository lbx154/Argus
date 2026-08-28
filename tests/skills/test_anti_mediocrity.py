"""Tests for argus_skill.skills.anti_mediocrity (advisory fact extractor).

This module used to be the F3 "anti-mediocrity hard gate" with baked-in
thresholds. It was rewritten after review c6b11d3 into a pure fact
extractor — no verdicts, no thresholds, no exit-code effects. The tests
here check that the fact extraction is correct and that nothing in the
module makes a research-quality judgment.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.skills.anti_mediocrity import (
    AggregateRow,
    MediocrityFinding,
    collect_mediocrity_finding,
    format_finding,
)
from argus_skill.skills.anti_mediocrity import (
    main as anti_mediocrity_main,
)

# ---------------------------------------------------------------------------
# AggregateRow.is_noisy — presentation label, never a verdict
# ---------------------------------------------------------------------------


def _agg(condition: str, reward: float, *, total: int = 89, errored: int = 0) -> AggregateRow:
    return AggregateRow(
        bundle=f"benchmarks/evidence/{condition}",
        condition=condition,
        reward=reward,
        n_total_trials=total,
        n_completed_trials=total - errored,
        n_errored_trials=errored,
    )


def test_noisy_flag_fires_at_25pct_errored() -> None:
    assert _agg("x", 0.5, total=100, errored=25).is_noisy
    assert _agg("x", 0.5, total=100, errored=24).is_noisy is False


def test_noisy_flag_safe_on_empty_aggregate() -> None:
    row = AggregateRow(
        bundle="x", condition="y",
        reward=None, n_total_trials=None,
        n_completed_trials=None, n_errored_trials=None,
    )
    assert row.is_noisy is False


# ---------------------------------------------------------------------------
# MediocrityFinding properties — pure derivations from facts
# ---------------------------------------------------------------------------


def test_best_proposed_picks_max_reward() -> None:
    finding = MediocrityFinding(
        project_root=Path("."),
        proposed_condition="argus",
        baseline_condition="bare",
        aggregates=[
            _agg("argus", 0.60),
            _agg("argus", 0.72),
            _agg("bare", 0.55),
        ],
    )
    assert finding.best_proposed_reward == 0.72
    assert finding.best_baseline_reward == 0.55
    assert finding.proposed_minus_baseline == pytest.approx(0.17)


def test_finding_handles_missing_condition() -> None:
    finding = MediocrityFinding(
        project_root=Path("."),
        proposed_condition="argus",
        baseline_condition="bare",
        aggregates=[_agg("argus", 0.60)],
    )
    # No baseline rows → baseline reward is None → delta is None.
    assert finding.best_baseline_reward is None
    assert finding.proposed_minus_baseline is None


def test_finding_ok_only_reflects_structural_errors() -> None:
    # No judgment — even with terrible-looking aggregates, ok=True
    # as long as the read itself succeeded.
    bad = MediocrityFinding(
        project_root=Path("."),
        proposed_condition="argus",
        baseline_condition="bare",
        aggregates=[_agg("argus", 0.0), _agg("bare", 0.99)],
    )
    assert bad.ok is True

    broken = MediocrityFinding(
        project_root=Path("."),
        proposed_condition=None, baseline_condition=None,
        structural_errors=["could not parse summary.tsv: bad header"],
    )
    assert broken.ok is False


# ---------------------------------------------------------------------------
# collect_mediocrity_finding — end-to-end fact extraction
# ---------------------------------------------------------------------------


def _write_bundle(
    root: Path, name: str, *,
    condition: str = "argus", reward: float = 0.7,
    dataset_id: str = "harbor-bench@1.0",
    total: int = 89, errored: int = 0,
) -> None:
    bundle = root / "benchmarks" / "evidence" / name
    bundle.mkdir(parents=True, exist_ok=True)
    header = (
        "row_kind\tcondition\treward\tn_total_trials\t"
        "n_completed_trials\tn_errored_trials\n"
    )
    body = f"aggregate\t{condition}\t{reward}\t{total}\t{total - errored}\t{errored}\n"
    (bundle / "summary.tsv").write_text(header + body, encoding="utf-8")
    (bundle / "manifest.json").write_text(
        json.dumps({"dataset_id": dataset_id, "condition": condition}),
        encoding="utf-8",
    )


def test_collect_finding_surfaces_facts(tmp_path: Path) -> None:
    _write_bundle(tmp_path, "a", condition="argus", reward=0.72)
    _write_bundle(tmp_path, "b", condition="bare", reward=0.60)
    _write_bundle(tmp_path, "c", condition="argus", reward=0.65,
                  dataset_id="swebench-pro@1.0")

    finding = collect_mediocrity_finding(
        tmp_path,
        proposed_condition="argus",
        baseline_condition="bare",
    )

    assert len(finding.aggregates) == 3
    assert sorted(finding.benchmark_families) == ["harbor-bench@1.0", "swebench-pro@1.0"]
    assert finding.best_proposed_reward == 0.72
    assert finding.best_baseline_reward == 0.60
    assert finding.proposed_minus_baseline == pytest.approx(0.12)
    # No verdict; no thresholds; ok=True regardless of numbers.
    assert finding.ok is True


def test_collect_finding_with_no_evidence_returns_empty_not_error(tmp_path: Path) -> None:
    finding = collect_mediocrity_finding(tmp_path)
    assert finding.aggregates == []
    assert finding.benchmark_families == []
    # Critically: ok=True even with zero evidence. The harness does NOT
    # rule that "no evidence" is a quality problem.
    assert finding.ok is True


# ---------------------------------------------------------------------------
# format_finding — reviewer-facing text
# ---------------------------------------------------------------------------


def test_format_finding_includes_checklist(tmp_path: Path) -> None:
    _write_bundle(tmp_path, "a", condition="argus", reward=0.72)
    finding = collect_mediocrity_finding(tmp_path, proposed_condition="argus")
    text = format_finding(finding)
    assert "no harness verdict — reviewer rules" in text
    assert "Reviewer judgement points" in text
    # The checklist tells the reviewer it's their call:
    assert "you decide" in text or "reviewer rules" in text


def test_format_finding_marks_noisy_aggregates(tmp_path: Path) -> None:
    _write_bundle(tmp_path, "dirty",
                  condition="argus", reward=0.7, total=100, errored=40)
    finding = collect_mediocrity_finding(tmp_path)
    text = format_finding(finding)
    assert "[NOISY]" in text


# ---------------------------------------------------------------------------
# CLI — exits 0 except on structural read error
# ---------------------------------------------------------------------------


def test_cli_exits_zero_even_with_terrible_numbers(tmp_path: Path, capsys) -> None:
    # Proposed reward is WORSE than baseline → still exit 0. The harness
    # never rules "mediocre"; reviewer does.
    _write_bundle(tmp_path, "p", condition="argus", reward=0.10)
    _write_bundle(tmp_path, "b", condition="bare", reward=0.90)

    rc = anti_mediocrity_main(
        [
            "--project-root", str(tmp_path),
            "--proposed-condition", "argus",
            "--baseline-condition", "bare",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    # The negative delta IS surfaced — reviewer reads it.
    assert "proposed - baseline" in out
    assert "-" in out  # negative sign shows up in formatted delta


def test_cli_exits_zero_on_empty_project(tmp_path: Path, capsys) -> None:
    rc = anti_mediocrity_main(["--project-root", str(tmp_path)])
    assert rc == 0


def test_cli_json_emits_no_verdict_field(tmp_path: Path, capsys) -> None:
    _write_bundle(tmp_path, "p", condition="argus", reward=0.10)
    rc = anti_mediocrity_main(
        ["--project-root", str(tmp_path), "--json"]
    )
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    # Schema must NOT include verdict-style fields like "passed" or "issues".
    assert "passed" not in payload
    assert "issues" not in payload
    assert "issue_count" not in payload
    # Schema MUST include the facts.
    assert "best_proposed_reward" in payload
    assert "best_baseline_reward" in payload
    assert "proposed_minus_baseline" in payload
    assert "benchmark_families" in payload
    assert "aggregates" in payload
    assert payload["ok"] is True  # structural read succeeded


# ---------------------------------------------------------------------------
# Anti-regression: the module must NOT export old verdict-style names
# ---------------------------------------------------------------------------


def test_old_verdict_api_is_gone() -> None:
    """Lock in the post-c6b11d3 rewrite: no threshold constants, no
    check_* / run_*_gate verdict functions, no MediocrityIssue. If any
    of these are reintroduced, this test fails — and that's a signal
    the harness is sneaking research judgment back in.
    """
    import argus_skill.skills.anti_mediocrity as mod
    forbidden_names = [
        "DEFAULT_MIN_DELTA",
        "DEFAULT_MIN_FAMILIES",
        "MediocrityIssue",
        "check_baseline_reproduced",
        "check_improvement_threshold",
        "check_benchmark_diversity",
        "run_anti_mediocrity_gate",
    ]
    for name in forbidden_names:
        assert not hasattr(mod, name), (
            f"{name!r} is a verdict-style API and must stay deleted "
            f"(see docs/PRINCIPLES.md)"
        )
