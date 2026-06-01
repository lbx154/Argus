"""Shared validation policy vocabulary for auto-research gates.

This taxonomy is intentionally restricted to STRUCTURAL and ANTI-FABRICATION
failure classes. Paper-quality concerns (prose, layout, length, figure style,
section placement) are deliberately absent: deciding whether a paper is good is
the reviewer agent's job against the stage checklist, not a harness taxonomy.
"""
from __future__ import annotations

VALIDATION_FAILURE_CLASSES: tuple[str, ...] = (
    "freshness",
    "experiment_evidence",
    "claim_graph",
    "artifact_manifest",
)

VALIDATION_PRIORITY_EXPECTED_ORDER: tuple[str, ...] = VALIDATION_FAILURE_CLASSES

DEFAULT_VALIDATION_ISSUE_PREFIXES: dict[str, tuple[str, ...]] = {
    "freshness": ("artifact_freshness", "freshness_", "artifact_stale", "artifact_modified"),
    "experiment_evidence": (
        "missing_full_scale_experiment_run",
        "incomplete_full_scale_experiment_run",
        "missing_baseline_condition_run",
        "pilot_pdf_without_full_scale_evidence",
        "proposed_result_missing",
        "quality_signal_contradicts_results",
        "unsupported_result_ratio",
        "method_result_number_mismatch",
        "hosted_model_contradicts_no_external_model_claim",
        "benchmark_provenance",
    ),
    "claim_graph": ("claim_graph", "claim_", "unsupported_claim", "evidence_gap"),
    "artifact_manifest": ("artifact_", "manifest_", "generated_artifact"),
}

VALIDATION_REPAIR_MODE_REQUIRED_TOKENS: dict[str, tuple[str, ...]] = {
    "experiment_evidence": ("experiment", "benchmark", "run"),
}
