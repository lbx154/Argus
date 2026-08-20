from __future__ import annotations

import json
from pathlib import Path

from argus_skill.skills.vertical_select import persist_vertical
from argus_skill.verticals._base import (
    load_vertical,
    vertical_stage_completion_issues,
)
from argus_skill.verticals.research.publication_scale import (
    ASSESSMENT_PATH,
    publication_scale_issues,
)
from argus_skill.verticals.research.stages import stage_completion_issues


def _target(root: Path, level: str = "publishable") -> None:
    persist_vertical(root, "research", research_target_level=level)


def _assessment(root: Path, **assessment_overrides) -> dict:
    result = root / "results" / "main.json"
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text('{"metric": 0.7}\n', encoding="utf-8")
    assessment = {
        "pilot_only": False,
        "proxy_only": False,
        "publication_scale_supported": True,
        "independent_value": (
            "The method establishes a reproducible improvement and mechanism "
            "across the claim-bearing public settings."
        ),
        "comparison_to_accepted_work": (
            "The executed evidence matches the accepted comparators on the "
            "dimensions material to this scoped claim."
        ),
        "strongest_reject_reason": (
            "The remaining concern is whether the mechanism transfers beyond "
            "the evaluated model family."
        ),
    }
    assessment.update(assessment_overrides)
    return {
        "schema_version": 1,
        "research_target_level": "publishable",
        "contribution_shape": "method",
        "accepted_comparators": [
            {
                "title": "Accepted Method One",
                "venue": "ICLR 2026",
                "official_acceptance_url": "https://iclr.cc/virtual/2026/poster/1",
                "why_comparable": "It studies the same intervention and endpoint.",
                "evidence_scale_summary": (
                    "Multiple public settings, strong baselines, repeats, and uncertainty."
                ),
            },
            {
                "title": "Accepted Method Two",
                "venue": "ICLR 2026",
                "official_acceptance_url": "https://iclr.cc/virtual/2026/poster/2",
                "why_comparable": "It makes a similarly scoped mechanism claim.",
                "evidence_scale_summary": (
                    "Cross-model public evaluation with ablations and paired statistics."
                ),
            },
        ],
        "claim_bearing_evidence": [
            {
                "role": "primary",
                "claim": "The proposed method improves the public endpoint reliably.",
                "source_type": "public benchmark with official evaluator",
                "evaluation_unit": "independent held-out benchmark examples",
                "uncertainty_method": "paired confidence interval over repeated runs",
                "strongest_comparisons": ["current strongest feasible baseline"],
                "artifacts": ["results/main.json"],
            }
        ],
        "scale_dimensions": {
            "models_or_systems": "Two current systems that exercise the claimed mechanism.",
            "public_sources": "Two versioned public task sources with official evaluators.",
            "evaluation_units": "Independent held-out units appropriate to the estimand.",
            "repeats_or_proof_obligations": "Repeated runs cover optimization variability.",
            "strong_comparisons": "The strongest feasible same-information methods are run.",
            "uncertainty_or_formal_guarantee": (
                "Paired intervals quantify the uncertainty of the primary endpoint."
            ),
        },
        "assessment": assessment,
    }


def _write(root: Path, payload: dict) -> None:
    path = root / ASSESSMENT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_exploratory_project_does_not_require_publication_scale_file(
    tmp_path: Path,
) -> None:
    _target(tmp_path, "exploratory")

    assert publication_scale_issues(tmp_path) == ()


def test_publishable_analysis_blocks_without_assessment(tmp_path: Path) -> None:
    _target(tmp_path)

    issues = publication_scale_issues(tmp_path)

    assert any("missing paper/PUBLICATION_SCALE_ASSESSMENT.json" in issue for issue in issues)
    assert any(
        "[publication_scale]" in issue
        for issue in stage_completion_issues("analysis", tmp_path)
    )


def test_external_state_root_target_cannot_bypass_assessment(
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "workdir"
    state_root = tmp_path / "state" / "projects" / "session"
    workdir.mkdir()
    _target(state_root)

    issues = vertical_stage_completion_issues(
        load_vertical("research", project_root=state_root),
        stage="analysis",
        project_root=workdir,
        state_root=state_root,
    )

    assert any("missing paper/PUBLICATION_SCALE_ASSESSMENT.json" in issue for issue in issues)


def test_valid_accepted_paper_calibrated_assessment_passes(tmp_path: Path) -> None:
    _target(tmp_path)
    _write(tmp_path, _assessment(tmp_path))

    assert publication_scale_issues(tmp_path) == ()
    assert not any(
        "[publication_scale]" in issue
        for issue in stage_completion_issues("analysis", tmp_path)
    )


def test_claim_narrowing_cannot_rescue_pilot_or_proxy_only_evidence(
    tmp_path: Path,
) -> None:
    _target(tmp_path)
    payload = _assessment(
        tmp_path,
        pilot_only=True,
        proxy_only=True,
        publication_scale_supported=False,
    )
    _write(tmp_path, payload)

    issues = publication_scale_issues(tmp_path)

    assert any("underpowered pilot cannot become publishable" in issue for issue in issues)
    assert any("proxy/diagnostic evidence" in issue for issue in issues)
    assert any("publication_scale_supported must be true" in issue for issue in issues)


def test_claim_bearing_artifacts_must_be_real_and_project_local(tmp_path: Path) -> None:
    _target(tmp_path)
    payload = _assessment(tmp_path)
    payload["claim_bearing_evidence"][0]["artifacts"] = [
        "results/missing.json",
        "../outside.json",
    ]
    _write(tmp_path, payload)

    issues = publication_scale_issues(tmp_path)

    assert any("artifact does not exist" in issue for issue in issues)
    assert any("artifact escapes project root" in issue for issue in issues)
