from __future__ import annotations

import json
import sys
from pathlib import Path

from argus_skill.tools import stage_check


def _write(path: Path, text: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: dict) -> None:
    _write(path, json.dumps(payload))


def _seed_plan_files(root: Path) -> None:
    for path in (
        "research/EXPERIMENT_PLAN.md",
        "research/IDEA_REJECTION_LOG.md",
        "research/CODE_STUDY_NOTES.md",
        "research/BASELINE_AND_BENCHMARK_PLAN.md",
    ):
        _write(root / path)


def _seed_valid_draft_outline(root: Path) -> None:
    """A complete plan now carries a filled paper/DRAFT_OUTLINE.md
    (≥4 sections, ≥3 figures, ≥1 experiment) under the draft-first contract."""
    _write(
        root / "paper" / "DRAFT_OUTLINE.md",
        "---\n"
        "outline_version: 1\n"
        "---\n\n"
        "## Sections\n"
        "- title: Introduction\n"
        "  goal: motivate\n"
        "- title: Method\n"
        "  goal: describe\n"
        "- title: Experiments\n"
        "  goal: setup\n"
        "- title: Conclusion\n"
        "  goal: wrap up\n\n"
        "## Figures\n"
        "- id: F1_teaser\n"
        "  style_ref: X Fig.1\n"
        "  data_source: data/a.jsonl\n"
        "  caption_placeholder: teaser\n"
        "- id: F2_results\n"
        "  style_ref: Y Tab.2\n"
        "  data_source: paper/artifacts/results_table.tsv\n"
        "  caption_placeholder: results\n"
        "- id: F3_ablation\n"
        "  style_ref: Z Fig.3\n"
        "  data_source: paper/artifacts/ablation.tsv\n"
        "  caption_placeholder: ablation\n\n"
        "## Experiments\n"
        "- id: E1_main\n"
        "  cell_spec: 3 models x 2 conditions\n"
        "  expected_metric: accuracy\n"
        "  n_seeds: 3\n",
    )


def _seed_benchmark_blockers(root: Path) -> None:
    _write_json(
        root / "experiments" / "BENCHMARK_PROVENANCE.json",
        {
            "plan_viability": {
                "status": "blocked_plan_stage_benchmark_package_viability",
                "reason": "only one authentic scored family is local",
                "local_authentic_scored_family_count": 1,
                "minimum_required_family_count": 3,
            }
        },
    )
    _write_json(
        root / "experiments" / "BENCHMARK_ACCESS_REVIEW.json",
        {"passed": False, "blockers": [{"family_id": "wise", "id": "license_access_not_cleared"}]},
    )
    _write_json(
        root / "experiments" / "BENCHMARK_ARTIFACT_BUNDLE_STATUS.json",
        {"passed": False, "blockers": [{"family_id": "official_compbench20", "id": "artifact.missing_raw_artifact"}]},
    )
    _write_json(
        root / "experiments" / "BENCHMARK_EVALUATOR_AUTHENTICITY.json",
        {"passed": False, "blockers": [{"family_id": "oneig", "id": "raw_scored_artifact_missing_or_invalid"}]},
    )


def test_stage_check_fails_closed_on_blocked_benchmark_package(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _seed_plan_files(tmp_path)
    _write_json(
        tmp_path / "research" / "PIPELINE_STATE.json",
        {
            "current_stage": "plan",
            "status": "blocked",
            "last_gate": {
                "verdict": "benchmark_deadlock_pivot_plan_blocked_no_training",
                "reason": "selected benchmark package requires unavailable artifacts",
            },
            "stages": {
                "plan": {
                    "status": "blocked",
                    "gate": "NO-GO for benchmark-stage advancement from local artifacts.",
                },
                "benchmark": {"status": "blocked", "reason": "external artifacts missing"},
            },
        },
    )
    _seed_benchmark_blockers(tmp_path)

    monkeypatch.setattr(sys, "argv", ["stage-check", "--project-root", str(tmp_path), "--stage", "plan"])
    status = stage_check.main()
    out = capsys.readouterr().out

    assert status == 1
    assert "Fail-closed pipeline state" in out
    assert "plan viability is blocked" in out
    assert "local authentic scored benchmark family count below minimum: 1 < 3" in out
    assert "benchmark artifact bundle is blocked" in out


def test_stage_check_allows_minimal_unblocked_plan(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _seed_plan_files(tmp_path)
    _seed_valid_draft_outline(tmp_path)
    _write_json(
        tmp_path / "research" / "PIPELINE_STATE.json",
        {
            "current_stage": "plan",
            "status": "active",
            "stages": {"plan": {"status": "done"}},
        },
    )
    _write_json(
        tmp_path / "experiments" / "BENCHMARK_PROVENANCE.json",
        {
            "plan_viability": {
                "status": "ready",
                "local_authentic_scored_family_count": 3,
                "minimum_required_family_count": 3,
            }
        },
    )
    for name in (
        "BENCHMARK_ACCESS_REVIEW.json",
        "BENCHMARK_ARTIFACT_BUNDLE_STATUS.json",
        "BENCHMARK_EVALUATOR_AUTHENTICITY.json",
    ):
        _write_json(tmp_path / "experiments" / name, {"passed": True, "blockers": []})

    monkeypatch.setattr(sys, "argv", ["stage-check", "--project-root", str(tmp_path), "--stage", "plan"])
    status = stage_check.main()
    out = capsys.readouterr().out

    assert status == 0
    assert "Fail-closed pipeline state" not in out
    assert "0 fail-closed state finding(s)" in out


def test_missing_draft_outline_fails_closed_at_plan(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    # A complete plan with NO paper/DRAFT_OUTLINE.md must fail closed via the
    # in-process _plan_outline_findings (negative path for the draft-first gate).
    _seed_plan_files(tmp_path)
    _write_json(
        tmp_path / "research" / "PIPELINE_STATE.json",
        {"current_stage": "plan", "status": "active", "stages": {"plan": {"status": "done"}}},
    )
    _write_json(
        tmp_path / "experiments" / "BENCHMARK_PROVENANCE.json",
        {"plan_viability": {"status": "ready",
                            "local_authentic_scored_family_count": 3,
                            "minimum_required_family_count": 3}},
    )
    for name in (
        "BENCHMARK_ACCESS_REVIEW.json",
        "BENCHMARK_ARTIFACT_BUNDLE_STATUS.json",
        "BENCHMARK_EVALUATOR_AUTHENTICITY.json",
    ):
        _write_json(tmp_path / "experiments" / name, {"passed": True, "blockers": []})

    monkeypatch.setattr(sys, "argv", ["stage-check", "--project-root", str(tmp_path), "--stage", "plan"])
    status = stage_check.main()
    out = capsys.readouterr().out

    assert status == 1
    assert "draft outline:" in out
    assert "Fail-closed pipeline state" in out


def test_missing_draft_outline_is_advisory_when_bounded(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    # Bounded missions downgrade the outline finding to advisory (status 0).
    _seed_plan_files(tmp_path)
    _write_json(
        tmp_path / "research" / "PIPELINE_STATE.json",
        {"current_stage": "plan", "status": "active"},
    )
    monkeypatch.setattr(
        sys, "argv",
        ["stage-check", "--project-root", str(tmp_path), "--stage", "plan", "--bounded"],
    )
    status = stage_check.main()
    out = capsys.readouterr().out

    assert status == 0
    assert "draft outline:" in out
    assert "Advisory paper-pipeline state" in out
    assert "0 fail-closed state finding(s)" in out
