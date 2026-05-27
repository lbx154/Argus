"""Shared validation policy vocabulary for auto-research gates."""
from __future__ import annotations

VALIDATION_FAILURE_CLASSES: tuple[str, ...] = (
    "freshness",
    "experiment_evidence",
    "claim_graph",
    "content_sufficiency",
    "exemplar_suitability",
    "exemplar_structure",
    "figure_table_style",
    "format_layout",
    "layout_vision",
    "academic_language",
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
        "single_source_benchmark",
        "insufficient_executed_benchmark_sources",
        "missing_benchmark",
        "benchmark_provenance",
    ),
    "claim_graph": ("claim_graph", "claim_", "unsupported_claim", "evidence_gap"),
    "content_sufficiency": (
        "missing_main_content_pages",
        "abstract_too_short",
        "introduction_too_short",
        "introduction_missing_literature_hooks",
        "introduction_missing_contribution_roadmap",
        "introduction_missing_quantified_result_preview",
        "method_section_too_short",
        "experimental_setup_too_short",
        "contrastive_template_overuse",
        "over_defensive_scope_caveats",
        "stock_transition_overuse",
        "underlength_emnlp_paper",
        "rendered_pdf_underlength",
        "rendered_main_body_underfilled",
        "references_before_full_body",
        "appendix_before_page_9",
        "references_share_page_with_body_sections",
        "insufficient_rendered_reference_pages",
        "missing_midpaper_visual_pages",
        "draft_not_submission_quality",
    ),
    "exemplar_suitability": ("exemplar_suitability", "style_exemplar_suitability"),
    "exemplar_structure": ("style_structure", "structure_", "unmapped_final_section"),
    "figure_table_style": ("figure_table_style", "style_guide_", "float_inventory"),
    "format_layout": (
        "severe_overfull_hbox",
        "code_like_display_label",
        "table_caption",
        "body_figure",
        "too_many_body_figures",
        "too_many_wide_body_figures",
        "missing_paired_significance",
        "conceptual_body_figure",
        "noncanonical_latex_output",
    ),
    "layout_vision": ("layout_", "stale_layout", "low_layout", "pass_layout_review"),
    "academic_language": (
        "academic_",
        "stale_academic",
        "low_academic",
        "pass_academic_language_review",
        "missing_experiment_model",
    ),
    "artifact_manifest": ("artifact_", "manifest_", "generated_artifact"),
}

VALIDATION_REPAIR_MODE_REQUIRED_TOKENS: dict[str, tuple[str, ...]] = {
    "experiment_evidence": ("experiment", "benchmark", "run"),
    "content_sufficiency": ("evidence", "experiment", "ablation", "failure", "analysis"),
}
