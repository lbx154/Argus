"""Tests for run_evidence_health gate (Opt #6 — verifier call_failed
detection that summary.tsv hides)."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.verticals.research.run_evidence_health import (
    validate_run_evidence_health,
)


def _write_ctrf(path: Path, *, call_failed: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "results": {
            "tests": [
                {
                    "name": "t1",
                    "raw_status": "call_failed" if call_failed else "passed",
                }
            ]
        }
    }
    path.write_text(json.dumps(body), encoding="utf-8")


def _seed_bundle(
    root: Path, name: str, *, ctrf_total: int, n_failed: int,
) -> None:
    bundle = root / "benchmarks" / "evidence" / name
    bundle.mkdir(parents=True, exist_ok=True)
    for i in range(ctrf_total):
        _write_ctrf(
            bundle / "jobs" / "raw" / f"task{i}" / "verifier" / "ctrf.json",
            call_failed=(i < n_failed),
        )


# ---------------------------------------------------------------------------
# Empty / no bundles → no-op
# ---------------------------------------------------------------------------


def test_no_evidence_dir_is_noop(tmp_path: Path) -> None:
    report = validate_run_evidence_health(tmp_path)
    assert report.ok
    assert report.bundles == []


def test_empty_evidence_dir_is_noop(tmp_path: Path) -> None:
    (tmp_path / "benchmarks" / "evidence").mkdir(parents=True)
    report = validate_run_evidence_health(tmp_path)
    assert report.ok
    assert report.bundles == []


def test_bundle_with_no_ctrf_files_skipped(tmp_path: Path) -> None:
    """Scaffold-only bundles (no jobs/raw yet) must not produce
    spurious failures — gate is health, not progress."""
    bundle = tmp_path / "benchmarks" / "evidence" / "scaffold-only"
    (bundle / "jobs" / "raw").mkdir(parents=True)
    (bundle / "manifest.json").write_text("{}", encoding="utf-8")
    report = validate_run_evidence_health(tmp_path)
    assert report.ok
    assert report.bundles == []


# ---------------------------------------------------------------------------
# Threshold behavior
# ---------------------------------------------------------------------------


def test_clean_bundle_passes(tmp_path: Path) -> None:
    _seed_bundle(tmp_path, "clean", ctrf_total=10, n_failed=0)
    report = validate_run_evidence_health(tmp_path)
    assert report.ok, report.to_text()
    assert len(report.bundles) == 1
    assert report.bundles[0].call_failed_fraction == 0.0


def test_just_below_threshold_passes(tmp_path: Path) -> None:
    # 24% < 25% → surfaced in bundle list but not a structural fail
    _seed_bundle(tmp_path, "borderline-low", ctrf_total=100, n_failed=24)
    report = validate_run_evidence_health(tmp_path)
    assert report.ok
    assert abs(report.bundles[0].call_failed_fraction - 0.24) < 1e-9


def test_at_or_above_threshold_fails(tmp_path: Path) -> None:
    # 25% ≥ 25% → structural fail
    _seed_bundle(tmp_path, "borderline-fail", ctrf_total=100, n_failed=25)
    report = validate_run_evidence_health(tmp_path)
    assert not report.ok
    codes = {i.code for i in report.issues}
    assert "high_verifier_call_failed_rate" in codes


def test_v1_style_100pct_fails(tmp_path: Path) -> None:
    """The 100% call_failed case that was found live in a
    detached fullbench evidence bundle (this gate's motivation)."""
    _seed_bundle(tmp_path, "totally-broken", ctrf_total=12, n_failed=12)
    report = validate_run_evidence_health(tmp_path)
    assert not report.ok
    assert report.bundles[0].call_failed_fraction == 1.0
    assert "totally-broken" in report.issues[0].detail


# ---------------------------------------------------------------------------
# Multi-bundle isolation
# ---------------------------------------------------------------------------


def test_one_broken_bundle_does_not_taint_clean_bundle(tmp_path: Path) -> None:
    _seed_bundle(tmp_path, "ok-bundle", ctrf_total=20, n_failed=0)
    _seed_bundle(tmp_path, "broken-bundle", ctrf_total=20, n_failed=20)
    report = validate_run_evidence_health(tmp_path)
    assert not report.ok
    assert len(report.bundles) == 2
    assert len(report.issues) == 1  # only the broken one
    bad = next(b for b in report.bundles if b.bundle_name == "broken-bundle")
    good = next(b for b in report.bundles if b.bundle_name == "ok-bundle")
    assert bad.call_failed_fraction == 1.0
    assert good.call_failed_fraction == 0.0


# ---------------------------------------------------------------------------
# Real-world ctrf shape: nested raw_status inside results.tests[]
# ---------------------------------------------------------------------------


def test_nested_raw_status_detected(tmp_path: Path) -> None:
    """The actual benchmark ctrf.json puts raw_status deep inside
    results.tests[*]. Walker must find it at any depth."""
    bundle = tmp_path / "benchmarks" / "evidence" / "deep-nesting"
    ctrf = bundle / "jobs" / "raw" / "t0" / "verifier" / "ctrf.json"
    ctrf.parent.mkdir(parents=True)
    ctrf.write_text(json.dumps({
        "results": {
            "tool": "verifier",
            "tests": [
                {
                    "name": "subtest",
                    "stages": [
                        {"raw_status": "call_failed", "duration_ms": 200},
                    ],
                },
            ],
        },
    }), encoding="utf-8")
    report = validate_run_evidence_health(tmp_path)
    assert report.bundles[0].ctrf_call_failed == 1


def test_malformed_ctrf_does_not_crash_or_count_as_failed(tmp_path: Path) -> None:
    bundle = tmp_path / "benchmarks" / "evidence" / "garbled"
    ctrf = bundle / "jobs" / "raw" / "t0" / "verifier" / "ctrf.json"
    ctrf.parent.mkdir(parents=True)
    ctrf.write_text("{not valid json", encoding="utf-8")
    report = validate_run_evidence_health(tmp_path)
    # Malformed = unable to determine call_failed → counted as not-failed
    # (anti-fab principle: only count what we can read). 1 ctrf scanned,
    # 0 call_failed observed.
    assert report.bundles[0].ctrf_total == 1
    assert report.bundles[0].ctrf_call_failed == 0
    assert report.ok


# ---------------------------------------------------------------------------
# automated_gates wiring
# ---------------------------------------------------------------------------


def test_automated_gates_wires_run_evidence_health() -> None:
    from argus_skill.skills.automated_gates import (
        GATE_KINDS,
        STAGE_GATES,
        gates_for_stage,
    )

    for stage in ("run", "analysis", "review", "submission"):
        assert "run_evidence_health" in STAGE_GATES[stage]
        assert "run_evidence_health" in gates_for_stage(stage)
    # NOT at draft — draft is paper-side, not bundle-side.
    assert "run_evidence_health" not in STAGE_GATES["draft"]
    assert GATE_KINDS["run_evidence_health"] == "structural"


def test_run_stage_gates_run_evidence_health_blocks_when_broken_bundle_exists(tmp_path: Path) -> None:
    _seed_bundle(tmp_path, "broken", ctrf_total=10, n_failed=10)
    from argus_skill.skills.automated_gates import run_stage_gates
    results = run_stage_gates(tmp_path, stage="analysis")
    names = {r.name for r in results}
    assert "run_evidence_health" in names
    reh = next(r for r in results if r.name == "run_evidence_health")
    assert reh.passed is False
    assert reh.is_blocking is True


def test_run_stage_gates_run_evidence_health_passes_when_clean(tmp_path: Path) -> None:
    _seed_bundle(tmp_path, "clean", ctrf_total=10, n_failed=0)
    from argus_skill.skills.automated_gates import run_stage_gates
    results = run_stage_gates(tmp_path, stage="run")
    reh = next(r for r in results if r.name == "run_evidence_health")
    assert reh.passed is True
