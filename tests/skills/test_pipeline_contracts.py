from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

from argus_skill.skills.academic_language_review import generate_academic_language_review
from argus_skill.skills.pipeline_contracts import (
    _validate_research_md_pdf_text,
    _validate_research_md_reference_depth,
    refresh_artifact_manifest,
    validate_academic_language_review,
    validate_artifact_manifest,
    validate_code_reuse_plan,
    validate_emnlp_paper_contract,
    validate_full_emnlp_readiness,
    validate_idea_provenance,
    validate_image2_figures,
    validate_layout_review,
    validate_literature_grounding,
    validate_paper_format,
    validate_pipeline_state,
    validate_research_md_format_preflight,
    validate_style_exemplar,
    validate_submission_assurance,
    validate_submission_readiness,
)


def test_pipeline_state_contract_accepts_ready_stage_artifacts(tmp_path: Path) -> None:
    _write(tmp_path / "research" / "RESEARCH_BRIEF.md", "brief\n")
    _write(tmp_path / "research" / "LITERATURE_REVIEW.md", "review\n")
    _write(tmp_path / "research" / "LIT_MATRIX.tsv", "title\tvenue\n")
    _write_valid_literature_grounding(tmp_path)
    _write(tmp_path / "research" / "SOURCE_DISCOVERY.md", "sources\n")
    _write(tmp_path / "research" / "TREND_INSIGHTS.md", "insights\n")
    _write(tmp_path / "research" / "NOVELTY_REPORT.md", "novelty\n")
    _write(tmp_path / "research" / "NOVELTY_MAP.md", "map\n")
    _write_valid_idea_provenance(tmp_path)
    _write(tmp_path / "research" / "RELATED_WORK_BLOCKERS.md", "blockers\n")
    _write(tmp_path / "research" / "EXPERIMENT_PLAN.md", "plan\n")
    _write(tmp_path / "research" / "CLAIMS_TO_TEST.md", "claims\n")
    _write(tmp_path / "research" / "BASELINE_AND_BENCHMARK_PLAN.md", "baselines\n")
    _write_valid_code_reuse_plan(tmp_path)
    _write(tmp_path / "experiments" / "BENCHMARK_PROVENANCE.md", "benchmark\n")
    _write_json(
        tmp_path / "research" / "PIPELINE_STATE.json",
        {
            "current_stage": "plan",
            "stages": {
                "brief": {"status": "done"},
                "plan": {"status": "ready"},
            },
        },
    )

    assert validate_pipeline_state(tmp_path) == []


def test_pipeline_state_contract_blocks_plan_without_literature_gate(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "research" / "RESEARCH_BRIEF.md", "brief\n")
    _write(tmp_path / "research" / "EXPERIMENT_PLAN.md", "plan\n")
    _write(tmp_path / "research" / "CLAIMS_TO_TEST.md", "claims\n")
    _write(tmp_path / "research" / "BASELINE_AND_BENCHMARK_PLAN.md", "baselines\n")
    _write(tmp_path / "experiments" / "BENCHMARK_PROVENANCE.md", "benchmark\n")
    _write_json(
        tmp_path / "research" / "PIPELINE_STATE.json",
        {
            "current_stage": "plan",
            "stages": {
                "brief": {"status": "done"},
                "plan": {"status": "ready"},
            },
        },
    )

    issues = validate_pipeline_state(tmp_path)

    assert "research/LITERATURE_REVIEW.md" in {issue.path for issue in issues}
    assert "research/LIT_MATRIX.tsv" in {issue.path for issue in issues}
    assert "research/LITERATURE_GROUNDING.json" in {issue.path for issue in issues}
    assert "research/SOURCE_DISCOVERY.md" in {issue.path for issue in issues}
    assert "research/TREND_INSIGHTS.md" in {issue.path for issue in issues}
    assert "research/IDEA_PROVENANCE.json" in {issue.path for issue in issues}
    assert "research/CODE_REUSE_PLAN.json" in {issue.path for issue in issues}


def test_pipeline_state_contract_reports_missing_artifacts(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "research" / "PIPELINE_STATE.json",
        {
            "current_stage": "run",
            "stages": {
                "run": {"status": "done"},
            },
        },
    )

    issues = validate_pipeline_state(tmp_path)

    assert {issue.code for issue in issues} == {"missing_stage_artifact"}
    assert "experiments/**/manifest.json" in {issue.path for issue in issues}
    assert "experiments/**/progress.jsonl" in {issue.path for issue in issues}


def test_pipeline_state_contract_rejects_unknown_status(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "research" / "PIPELINE_STATE.json",
        {
            "current_stage": "plan",
            "stages": {
                "plan": {"status": "almost-done"},
            },
        },
    )

    issues = validate_pipeline_state(tmp_path)

    assert [issue.code for issue in issues] == ["invalid_stage_status"]


def test_pipeline_state_contract_blocks_draft_without_emnlp_grounding(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "research" / "PIPELINE_STATE.json",
        {
            "current_stage": "draft",
            "stages": {
                "draft": {"status": "ready"},
            },
        },
    )

    issues = validate_pipeline_state(tmp_path)

    codes = {issue.code for issue in issues}
    assert "missing_literature_grounding" in codes
    assert "missing_idea_provenance" in codes
    assert "missing_code_reuse_plan" in codes
    assert "missing_style_exemplar" in codes
    assert "missing_image2_figures_manifest" in codes
    assert "missing_paper_draft_report_json" in codes


def test_submission_assurance_contract_rejects_pass_with_failed_layer(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "paper" / "SUBMISSION_ASSURANCE.json",
        {
            "verdict": "PASS",
            "blocking_issues": [],
            "layers": {
                "experiment_integrity": {"verdict": "FAIL"},
                "result_to_claim": {"verdict": "PASS"},
                "paper_claim_audit": {"verdict": "PASS"},
                "idea_provenance_and_code_reuse": {"verdict": "PASS"},
                "literature_and_exemplar_grounding": {"verdict": "PASS"},
                "citation_audit": {"verdict": "PASS"},
                "kill_argument": {"verdict": "PASS"},
                "paper_quality_calibration": {"verdict": "PASS"},
                "research_md_format_preflight": {"verdict": "PASS"},
                "academic_language_review": {"verdict": "PASS"},
                "layout_aesthetic_review": {"verdict": "PASS"},
                "submission_package": {"verdict": "PASS"},
            },
        },
    )
    _write_valid_quality_calibration(tmp_path)

    issues = validate_submission_assurance(tmp_path)

    assert any(issue.code == "pass_with_blocking_layer" for issue in issues)


def test_submission_assurance_contract_accepts_warn_with_environment_blocker(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "paper" / "SUBMISSION_ASSURANCE.json",
        {
            "verdict": "WARN",
            "blocking_issues": [{"layer": "citation_audit", "issue": "web unavailable"}],
            "layers": {
                "experiment_integrity": {"verdict": "PASS"},
                "result_to_claim": {"verdict": "PASS"},
                "paper_claim_audit": {"verdict": "PASS"},
                "idea_provenance_and_code_reuse": {"verdict": "PASS"},
                "literature_and_exemplar_grounding": {"verdict": "PASS"},
                "citation_audit": {"verdict": "BLOCKED"},
                "kill_argument": {"verdict": "WARN"},
                "paper_quality_calibration": {"verdict": "PASS"},
                "research_md_format_preflight": {"verdict": "PASS"},
                "academic_language_review": {"verdict": "PASS"},
                "layout_aesthetic_review": {"verdict": "PASS"},
                "submission_package": {"verdict": "PASS"},
            },
        },
    )
    _write_valid_quality_calibration(tmp_path)
    _write_valid_artifact_manifest(tmp_path)
    _write_valid_literature_grounding(tmp_path)
    _write_valid_idea_provenance(tmp_path)
    _write_valid_code_reuse_plan(tmp_path)
    _write_valid_style_exemplar(tmp_path)
    _write_valid_image2_figures(tmp_path)
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_layout_review(tmp_path)
    _write_valid_academic_language_review(tmp_path)

    assert validate_submission_assurance(tmp_path) == []


def test_submission_readiness_rejects_non_ready_verdict(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "paper" / "SUBMISSION_ASSURANCE.json",
        {
            "verdict": "FAIL",
            "blocking_issues": [{"layer": "kill_argument", "issue": "weak result"}],
            "layers": {
                "experiment_integrity": {"verdict": "PASS"},
                "result_to_claim": {"verdict": "PASS"},
                "paper_claim_audit": {"verdict": "PASS"},
                "idea_provenance_and_code_reuse": {"verdict": "PASS"},
                "literature_and_exemplar_grounding": {"verdict": "PASS"},
                "citation_audit": {"verdict": "PASS"},
                "kill_argument": {"verdict": "FAIL"},
                "paper_quality_calibration": {"verdict": "FAIL"},
                "research_md_format_preflight": {"verdict": "FAIL"},
                "academic_language_review": {"verdict": "FAIL"},
                "layout_aesthetic_review": {"verdict": "FAIL"},
                "submission_package": {"verdict": "PASS"},
            },
        },
    )
    _write_json(
        tmp_path / "paper" / "PAPER_QUALITY_CALIBRATION.json",
        {
            "verdict": "FAIL",
            "quality_signals": {
                "uses_public_benchmark": False,
                "beats_nontrivial_baseline": False,
                "n_tasks_meets_threshold": False,
                "parser_schema_confound_cleared": False,
                "submission_quality_self_assessment": "pilot",
            },
            "negative_case_regressions": [
                {
                    "case_id": "negative:fresh-demo-pilot-pattern",
                    "matched": True,
                    "hard_failure": True,
                }
            ],
            "quality_signals_from_positive_examples": [
                {"case_id": "positive:emnlp2025-best-infini-gram-mini"}
            ],
        },
    )

    issues = validate_submission_readiness(tmp_path)

    assert any(issue.code == "submission_not_ready_verdict" for issue in issues)


def test_full_emnlp_readiness_rejects_plan_only_pipeline_pass(
    tmp_path: Path,
) -> None:
    _write_valid_plan_stage(tmp_path)

    assert validate_pipeline_state(tmp_path) == []

    issues = validate_full_emnlp_readiness(tmp_path)
    codes = {issue.code for issue in issues}

    assert "missing_stage_artifact" in codes
    assert "missing_submission_assurance" in codes
    assert "submission_stage_not_successful" in codes


def test_full_emnlp_readiness_reports_underpowered_benchmark_scale(
    tmp_path: Path,
) -> None:
    _write_valid_plan_stage(tmp_path)
    _write(
        tmp_path / "experiments" / "BENCHMARK_PROVENANCE.md",
        "Planned episodes: 60 total\n",
    )

    issues = validate_full_emnlp_readiness(tmp_path)

    assert "underpowered_pilot" in {issue.code for issue in issues}


def test_full_emnlp_readiness_accepts_complete_submission_package(
    tmp_path: Path,
) -> None:
    _write_valid_full_emnlp_package(tmp_path)

    assert validate_full_emnlp_readiness(tmp_path) == []


def test_artifact_manifest_accepts_generated_artifact_with_canonical_source(
    tmp_path: Path,
) -> None:
    _write_valid_artifact_manifest(tmp_path)

    assert validate_artifact_manifest(tmp_path) == []


def test_artifact_manifest_rejects_digest_drift(tmp_path: Path) -> None:
    _write_valid_artifact_manifest(tmp_path)
    _write(tmp_path / "paper" / "RESULTS_REPORT.md", "stale number changed by hand\n")

    issues = validate_artifact_manifest(tmp_path)

    assert "artifact_digest_mismatch" in {issue.code for issue in issues}


def test_artifact_manifest_rejects_tsv_schema_drift(tmp_path: Path) -> None:
    _write_valid_artifact_manifest(tmp_path)
    _write(tmp_path / "paper" / "artifacts" / "results_table.tsv", "metric\tvalue\textra\n")
    refresh_artifact_manifest(tmp_path)
    manifest_path = tmp_path / "paper" / "ARTIFACT_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["canonical_sources"][0]["columns"] = ["metric", "value"]
    _write_json(manifest_path, manifest)

    issues = validate_artifact_manifest(tmp_path)

    assert "tsv_schema_mismatch" in {issue.code for issue in issues}


def test_artifact_manifest_rejects_unsafe_path(tmp_path: Path) -> None:
    _write_valid_artifact_manifest(tmp_path)
    manifest_path = tmp_path / "paper" / "ARTIFACT_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["canonical_sources"][0]["path"] = "../outside.tsv"
    _write_json(manifest_path, manifest)

    issues = validate_artifact_manifest(tmp_path)

    assert "invalid_artifact_manifest_path" in {issue.code for issue in issues}


def test_artifact_manifest_rejects_generated_source_cycles(tmp_path: Path) -> None:
    _write_valid_artifact_manifest(tmp_path)
    manifest_path = tmp_path / "paper" / "ARTIFACT_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["generated_artifacts"][0]["sources"] = ["paper/RESULTS_REPORT.md"]
    _write_json(manifest_path, manifest)

    issues = validate_artifact_manifest(tmp_path)

    assert "generated_artifact_source_cycle" in {issue.code for issue in issues}


def test_refresh_artifact_manifest_updates_digests_and_tsv_columns(tmp_path: Path) -> None:
    _write_valid_artifact_manifest(tmp_path)
    _write(tmp_path / "paper" / "artifacts" / "results_table.tsv", "metric\tvalue\tn\n")
    _write(tmp_path / "paper" / "RESULTS_REPORT.md", "updated report\n")

    issues = refresh_artifact_manifest(tmp_path)

    assert issues == []
    manifest = json.loads((tmp_path / "paper" / "ARTIFACT_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["canonical_sources"][0]["columns"] == ["metric", "value", "n"]


def test_ready_assurance_requires_valid_artifact_manifest(tmp_path: Path) -> None:
    _write_valid_quality_calibration(tmp_path)
    _write_valid_literature_grounding(tmp_path)
    _write_valid_idea_provenance(tmp_path)
    _write_valid_code_reuse_plan(tmp_path)
    _write_valid_style_exemplar(tmp_path)
    _write_valid_image2_figures(tmp_path)
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_layout_review(tmp_path)
    _write_valid_academic_language_review(tmp_path)
    _write_json(
        tmp_path / "paper" / "SUBMISSION_ASSURANCE.json",
        {
            "verdict": "PASS",
            "blocking_issues": [],
            "layers": {
                "experiment_integrity": {"verdict": "PASS"},
                "result_to_claim": {"verdict": "PASS"},
                "paper_claim_audit": {"verdict": "PASS"},
                "idea_provenance_and_code_reuse": {"verdict": "PASS"},
                "literature_and_exemplar_grounding": {"verdict": "PASS"},
                "citation_audit": {"verdict": "PASS"},
                "kill_argument": {"verdict": "PASS"},
                "paper_quality_calibration": {"verdict": "PASS"},
                "research_md_format_preflight": {"verdict": "PASS"},
                "academic_language_review": {"verdict": "PASS"},
                "layout_aesthetic_review": {"verdict": "PASS"},
                "submission_package": {"verdict": "PASS"},
            },
        },
    )

    issues = validate_submission_assurance(tmp_path)

    assert "missing_artifact_manifest" in {issue.code for issue in issues}


def test_literature_grounding_requires_recent_classic_and_trend_sources(tmp_path: Path) -> None:
    _write_valid_literature_grounding(tmp_path, recent_count=2)

    issues = validate_literature_grounding(tmp_path)

    assert "insufficient_recent_high_quality_papers" in {issue.code for issue in issues}


def test_literature_grounding_allows_unbacked_news_trend_signals(tmp_path: Path) -> None:
    _write_valid_literature_grounding(tmp_path, trend_backing=None)

    assert validate_literature_grounding(tmp_path) == []


def test_idea_provenance_rejects_agent_brainstormed_idea(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "research" / "IDEA_PROVENANCE.json",
        {
            "idea_generation_mode": "agent_brainstorm",
            "agent_generated": True,
            "candidate_ideas": [],
            "selected_idea": {
                "title": "Generic agent benchmark",
                "research_gap": "unknown",
                "novelty_delta": "unknown",
                "selection_rationale": "agent thought of it",
                "derived_from": [],
            },
        },
    )

    issues = validate_idea_provenance(tmp_path)

    codes = {issue.code for issue in issues}
    assert "invalid_idea_generation_mode" in codes
    assert "agent_brainstormed_idea" in codes
    assert "missing_not_agent_brainstorm_attestation" in codes


def test_idea_provenance_accepts_literature_derived_selection(tmp_path: Path) -> None:
    _write_valid_idea_provenance(tmp_path)

    assert validate_idea_provenance(tmp_path) == []


def test_code_reuse_plan_requires_search_and_license_aware_sources(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "research" / "CODE_REUSE_PLAN.json",
        {
            "searched_queries": [],
            "code_sources": [
                {
                    "url": "https://github.com/example/paper-code",
                    "source_type": "official_paper_code",
                    "paper_or_project": "Example paper",
                    "reuse_decision": "adapt",
                }
            ],
        },
    )

    issues = validate_code_reuse_plan(tmp_path)

    codes = {issue.code for issue in issues}
    assert "missing_code_search_queries" in codes
    assert "missing_code_source_field" in codes
    assert "missing_code_reuse_attribution" in codes


def test_code_reuse_plan_accepts_license_aware_paper_code(tmp_path: Path) -> None:
    _write_valid_code_reuse_plan(tmp_path)

    assert validate_code_reuse_plan(tmp_path) == []


def test_style_exemplar_requires_open_access_structural_profile(tmp_path: Path) -> None:
    _write_valid_style_exemplar(tmp_path)

    assert validate_style_exemplar(tmp_path) == []


def test_style_exemplar_rejects_url_only_exemplar(tmp_path: Path) -> None:
    _write(tmp_path / "paper" / "style_ref" / "STYLE_PROFILE.md", "section profile\n")
    _write_json(
        tmp_path / "paper" / "style_ref" / "EXEMPLAR.json",
        {
            "exemplars": [
                {
                    "title": "Thin URL-only exemplar",
                    "url": "https://aclanthology.org/2025.emnlp-main.1/",
                    "venue": "EMNLP",
                    "year": 2025,
                    "source_type": "official-award-metadata",
                    "open_access": True,
                    "usage": "structural_style_only",
                    "no_prose_copy": True,
                    "structural_profile": "paper/style_ref/STYLE_PROFILE.md",
                }
            ]
        },
    )

    codes = {issue.code for issue in validate_style_exemplar(tmp_path)}

    assert "too_few_style_exemplars" in codes
    assert "missing_style_exemplar_field" in codes
    assert "style_exemplar_profile_too_thin" in codes


def test_style_exemplar_requires_structure_blueprint(tmp_path: Path) -> None:
    _write_valid_style_exemplar(tmp_path)
    (tmp_path / "paper" / "style_ref" / "PAPER_STRUCTURE_BLUEPRINT.md").unlink()

    codes = {issue.code for issue in validate_style_exemplar(tmp_path)}

    assert "missing_style_structure_blueprint" in codes


def test_style_exemplar_rejects_pdf_hash_mismatch(tmp_path: Path) -> None:
    _write_valid_style_exemplar(tmp_path)
    path = tmp_path / "paper" / "style_ref" / "EXEMPLAR.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["exemplars"][0]["pdf_sha256"] = "0" * 64
    _write_json(path, payload)

    codes = {issue.code for issue in validate_style_exemplar(tmp_path)}

    assert "style_exemplar_pdf_hash_mismatch" in codes


def test_image2_figures_require_conceptual_image2_but_allow_secondary_tikz(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "paper" / "figures" / "system.prompt.txt", "draw system figure\n")
    _write_bytes(tmp_path / "paper" / "figures" / "system.png", _png_bytes(1536, 1024))
    _write_json(
        tmp_path / "paper" / "figures" / "system.review.json",
        {"score_1_to_5": 4, "keep_or_regenerate": "keep"},
    )
    _write_image2_provenance(
        tmp_path,
        "paper/figures/system.prompt.txt",
        "paper/figures/system.png",
        "paper/figures/system.provenance.json",
    )
    _write_json(
        tmp_path / "paper" / "figures" / "IMAGE2_FIGURES.json",
        {
            "figures": [
                {
                    "figure_id": "method-overview",
                    "figure_type": "method",
                    "source": "raster",
                    "generator": "codex-image2",
                    "model": "image-2",
                    "prompt_path": "paper/figures/system.prompt.txt",
                    "output_path": "paper/figures/system.png",
                    "generation_provenance_path": "paper/figures/system.provenance.json",
                    "review_path": "paper/figures/system.review.json",
                },
                {
                    "figure_id": "formal-flow",
                    "figure_type": "tikz_diagram",
                    "source": "tikz",
                },
            ]
        },
    )

    assert validate_image2_figures(tmp_path) == []


def test_image2_figures_reject_square_1024_conceptual_figure(tmp_path: Path) -> None:
    _write(tmp_path / "paper" / "figures" / "system.prompt.txt", "draw system figure\n")
    _write_bytes(tmp_path / "paper" / "figures" / "system.png", _png_bytes(1024, 1024))
    _write_json(
        tmp_path / "paper" / "figures" / "system.review.json",
        {"score_1_to_5": 4, "keep_or_regenerate": "keep"},
    )
    _write_image2_provenance(
        tmp_path,
        "paper/figures/system.prompt.txt",
        "paper/figures/system.png",
        "paper/figures/system.provenance.json",
    )
    _write_json(
        tmp_path / "paper" / "figures" / "IMAGE2_FIGURES.json",
        {
            "figures": [
                {
                    "figure_id": "method-overview",
                    "figure_type": "method",
                    "source": "raster",
                    "generator": "codex-image2",
                    "model": "image-2",
                    "prompt_path": "paper/figures/system.prompt.txt",
                    "output_path": "paper/figures/system.png",
                    "generation_provenance_path": "paper/figures/system.provenance.json",
                    "review_path": "paper/figures/system.review.json",
                    "requested_size": "1024x1024",
                }
            ]
        },
    )

    issues = validate_image2_figures(tmp_path)

    codes = {issue.code for issue in issues}
    assert "disallowed_square_image_request" in codes
    assert "square_conceptual_figure" in codes


def test_image2_figures_reject_raster_conceptual_non_image2(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "paper" / "figures" / "IMAGE2_FIGURES.json",
        {
            "figures": [
                {
                    "figure_id": "method-overview",
                    "figure_type": "method",
                    "source": "raster",
                    "generator": "manual-raster",
                }
            ]
        },
    )

    issues = validate_image2_figures(tmp_path)

    assert "conceptual_figure_not_image2" in {issue.code for issue in issues}
    assert "missing_image2_conceptual_figure" in {issue.code for issue in issues}


def test_image2_figures_reject_body_overview_when_manifest_has_no_image2(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "paper" / "figures" / "IMAGE2_FIGURES.json",
        {
            "figures": [
                {
                    "figure_id": "method-overview",
                    "figure_type": "method",
                    "source": "pil",
                    "generator": "local-pil-blocked",
                    "output_path": "paper/figures/method.png",
                }
            ]
        },
    )
    _write_main_tex_with_figures(
        tmp_path,
        [("figures/method.png", "fig:overview", "Overview of our method and verifier pipeline.")],
    )

    codes = {issue.code for issue in validate_image2_figures(tmp_path)}

    assert "missing_image2_conceptual_figure" in codes
    assert "conceptual_body_figure_not_image2" in codes


def test_image2_figures_reject_self_drawn_teaser_or_overall_manifest(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "paper" / "figures" / "IMAGE2_FIGURES.json",
        {
            "figures": [
                {
                    "figure_id": "overall-teaser",
                    "figure_type": "teaser",
                    "source": "matplotlib",
                    "generator": "python-script",
                    "output_path": "paper/figures/overall_teaser.pdf",
                }
            ]
        },
    )

    issues = validate_image2_figures(tmp_path)

    codes = {issue.code for issue in issues}
    assert "conceptual_figure_not_image2" in codes
    assert "missing_image2_conceptual_figure" in codes


def test_image2_figures_reject_body_conceptual_pdf_substitution(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_image2_figures(tmp_path)
    _write(tmp_path / "paper" / "figures" / "method_overview_clean.pdf", "%PDF-1.4\n")
    _write_main_tex_with_figures(
        tmp_path,
        [
            (
                "figures/method_overview_clean.pdf",
                "fig:method",
                "Overview of our method from warm-up experience to executable policy reuse.",
            )
        ],
    )

    image2_codes = {issue.code for issue in validate_image2_figures(tmp_path)}
    preflight_codes = {issue.code for issue in validate_research_md_format_preflight(tmp_path)}

    assert "image2_conceptual_figure_not_included_in_main_tex" in image2_codes
    assert "conceptual_body_figure_not_image2" in image2_codes
    assert "conceptual_body_figure_not_image2" in preflight_codes


def test_image2_figures_reject_body_overall_or_teaser_pdf_substitution(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_image2_figures(tmp_path)
    _write(tmp_path / "paper" / "figures" / "overall.pdf", "%PDF-1.4\n")
    _write_main_tex_with_figures(
        tmp_path,
        [
            (
                "figures/overall.pdf",
                "fig:overall",
                "Overall framework teaser for the proposed system.",
            )
        ],
    )

    image2_codes = {issue.code for issue in validate_image2_figures(tmp_path)}
    preflight_codes = {issue.code for issue in validate_research_md_format_preflight(tmp_path)}

    assert "image2_conceptual_figure_not_included_in_main_tex" in image2_codes
    assert "conceptual_body_figure_not_image2" in image2_codes
    assert "conceptual_body_figure_not_image2" in preflight_codes


def test_image2_figures_accept_body_conceptual_png_output(tmp_path: Path) -> None:
    _write_valid_image2_figures(tmp_path)
    _write_main_tex_with_figures(
        tmp_path,
        [
            (
                "figures/method.png",
                "fig:method",
                "Overview of our method as an executable policy card.",
            )
        ],
    )

    assert validate_image2_figures(tmp_path) == []


def test_image2_figures_reject_cropped_or_resaved_image2_output(tmp_path: Path) -> None:
    _write(tmp_path / "paper" / "figures" / "method.prompt.txt", "draw method overview\n")
    _write_bytes(tmp_path / "paper" / "figures" / "method.png", _png_bytes(1343, 564))
    _write_json(
        tmp_path / "paper" / "figures" / "method.review.json",
        {"score_1_to_5": 4, "keep_or_regenerate": "keep", "image": {"width": 1536, "height": 1024}},
    )
    _write_json(
        tmp_path / "paper" / "figures" / "method.sidecar.json",
        {
            "model": "image-2",
            "generator": "codex-image2",
            "prompt_path": "paper/figures/method.prompt.txt",
            "output_path": "paper/figures/method.png",
            "output_sha256": hashlib.sha256((tmp_path / "paper" / "figures" / "method.png").read_bytes()).hexdigest(),
            "image": {"width": 1536, "height": 1024},
            "requested_size": "1536x1024",
        },
    )
    _write_json(
        tmp_path / "paper" / "figures" / "IMAGE2_FIGURES.json",
        {
            "figures": [
                {
                    "figure_id": "method-overview",
                    "figure_type": "method",
                    "source": "raster",
                    "generator": "codex-image2",
                    "model": "image-2",
                    "prompt_path": "paper/figures/method.prompt.txt",
                    "output_path": "paper/figures/method.png",
                    "generation_provenance_path": "paper/figures/method.sidecar.json",
                    "review_path": "paper/figures/method.review.json",
                    "sidecar_path": "paper/figures/method.sidecar.json",
                    "requested_size": "1536x1024",
                }
            ]
        },
    )

    codes = {issue.code for issue in validate_image2_figures(tmp_path)}

    assert "low_resolution_image2_conceptual_output" in codes
    assert "image2_output_dimensions_mismatch_requested_size" in codes
    assert "image2_recorded_dimensions_mismatch_output" in codes


def test_image2_figures_reject_pil_generated_output_mislabeled_as_image2(
    tmp_path: Path,
) -> None:
    _write_valid_image2_figures(tmp_path)
    _write(
        tmp_path / "code" / "generate_figures.py",
        "\n".join(
            [
                "from pathlib import Path",
                "from PIL import Image, ImageDraw",
                "out = Path('paper/figures/method.png')",
                "img = Image.new('RGB', (1536, 1024), 'white')",
                "draw = ImageDraw.Draw(img)",
                "draw.rectangle((10, 10, 100, 100))",
                "img.save(out)",
            ]
        )
        + "\n",
    )

    codes = {issue.code for issue in validate_image2_figures(tmp_path)}

    assert "local_conceptual_figure_generation_detected" in codes


def test_image2_figures_reject_named_matplotlib_overview_renderer(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "paper" / "figures" / "method_overview.prompt.txt", "draw method overview\n")
    _write_bytes(tmp_path / "paper" / "figures" / "method_overview.png", _png_bytes(1536, 1024))
    _write_json(
        tmp_path / "paper" / "figures" / "method_overview.review.json",
        {"score_1_to_5": 4, "keep_or_regenerate": "keep"},
    )
    _write_image2_provenance(
        tmp_path,
        "paper/figures/method_overview.prompt.txt",
        "paper/figures/method_overview.png",
        "paper/figures/method_overview.provenance.json",
    )
    _write_json(
        tmp_path / "paper" / "figures" / "IMAGE2_FIGURES.json",
        {
            "figures": [
                {
                    "figure_id": "method-overview",
                    "figure_type": "method",
                    "source": "raster",
                    "generator": "codex-image2",
                    "model": "image-2",
                    "prompt_path": "paper/figures/method_overview.prompt.txt",
                    "output_path": "paper/figures/method_overview.png",
                    "generation_provenance_path": "paper/figures/method_overview.provenance.json",
                    "review_path": "paper/figures/method_overview.review.json",
                    "requested_size": "1536x1024",
                }
            ]
        },
    )
    _write(
        tmp_path / "code" / "make_paper.py",
        "\n".join(
            [
                "def render_method_overview(path):",
                "    import matplotlib.pyplot as plt",
                "    fig = plt.figure()",
                "    fig.savefig(path)",
            ]
        )
        + "\n",
    )

    codes = {issue.code for issue in validate_image2_figures(tmp_path)}

    assert "local_conceptual_figure_generation_detected" in codes


def test_image2_figures_allow_data_plot_pdf_when_image2_conceptual_in_body(tmp_path: Path) -> None:
    _write_valid_image2_figures(tmp_path)
    _write(tmp_path / "paper" / "figures" / "results.pdf", "%PDF-1.4\n")
    _write_main_tex_with_figures(
        tmp_path,
        [
            (
                "figures/method.png",
                "fig:method",
                "Overview of our method as an executable policy card.",
            ),
            ("figures/results.pdf", "fig:results", "Accuracy and F1 results by benchmark family."),
        ],
    )

    assert validate_image2_figures(tmp_path) == []


def test_image2_figures_allow_accuracy_plot_caption_with_proposed_method(tmp_path: Path) -> None:
    _write_valid_image2_figures(tmp_path)
    _write(tmp_path / "paper" / "figures" / "main_results.pdf", "%PDF-1.4\n")
    _write_main_tex_with_figures(
        tmp_path,
        [
            (
                "figures/method.png",
                "fig:method",
                "Overview of our method as an executable policy card.",
            ),
            (
                "figures/main_results.pdf",
                "fig:main-results",
                "Overall accuracy with confidence intervals. The proposed method separates cleanly from the replay baseline.",
            ),
        ],
    )

    assert validate_image2_figures(tmp_path) == []


def test_image2_figures_allow_overall_results_plot_when_image2_conceptual_in_body(
    tmp_path: Path,
) -> None:
    _write_valid_image2_figures(tmp_path)
    _write(tmp_path / "paper" / "figures" / "overall_results.pdf", "%PDF-1.4\n")
    _write_main_tex_with_figures(
        tmp_path,
        [
            (
                "figures/method.png",
                "fig:method",
                "Overview of our method as an executable policy card.",
            ),
            (
                "figures/overall_results.pdf",
                "fig:overall-results",
                "Overall accuracy and F1 results by benchmark family.",
            ),
        ],
    )

    assert validate_image2_figures(tmp_path) == []


def test_image2_figures_reject_image2_conceptual_only_in_appendix(tmp_path: Path) -> None:
    _write_valid_image2_figures(tmp_path)
    _write(tmp_path / "paper" / "figures" / "results.pdf", "%PDF-1.4\n")
    _write_main_tex_with_figures(
        tmp_path,
        [("figures/results.pdf", "fig:results", "Accuracy and F1 results by benchmark family.")],
        appendix_figures=[
            (
                "figures/method.png",
                "fig:method-appendix",
                "Overview of our method as an executable policy card.",
            )
        ],
    )

    codes = {issue.code for issue in validate_image2_figures(tmp_path)}

    assert "image2_conceptual_figure_not_included_in_main_tex" in codes
    assert "conceptual_body_figure_not_image2" not in codes


def test_emnlp_paper_contract_rejects_pilot_length(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path, scope="pilot-note", pages=4.0)

    issues = validate_emnlp_paper_contract(tmp_path)

    assert "not_long_paper_scope" in {issue.code for issue in issues}
    assert "underlength_emnlp_paper" in {issue.code for issue in issues}


def test_emnlp_paper_contract_requires_emnlp_target(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path, target_venue="ACL")

    issues = validate_emnlp_paper_contract(tmp_path)

    assert "invalid_target_venue" in {issue.code for issue in issues}


def test_paper_format_rejects_appendix_before_references(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write(
        tmp_path / "paper" / "main.tex",
        "\n".join(
            [
                "\\documentclass{article}",
                "\\begin{document}",
                "Main paper text.",
                "\\appendix",
                "Appendix material.",
                "\\bibliography{references}",
                "\\end{document}",
            ]
        ),
    )

    issues = validate_paper_format(tmp_path)

    assert "appendix_before_references" in {issue.code for issue in issues}


def test_paper_format_rejects_overfull_hbox_above_research_md_limit(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write(tmp_path / "paper" / "main.log", "Overfull \\hbox (5.1pt too wide) in paragraph\n")

    issues = validate_paper_format(tmp_path)

    assert "severe_overfull_hbox" in {issue.code for issue in issues}


def test_research_md_format_preflight_accepts_complete_review_paper(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)

    assert validate_research_md_format_preflight(tmp_path) == []


def test_research_md_format_preflight_rejects_unverified_bib(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write(
        tmp_path / "paper" / "references.bib",
        "% UNVERIFIED\n@article{example,title={Example},year={2025}}\n",
    )

    issues = validate_research_md_format_preflight(tmp_path)

    assert "unverified_bib_entry" in {issue.code for issue in issues}


def test_research_md_format_preflight_rejects_thin_bibliography(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write(tmp_path / "paper" / "references.bib", "@article{one,title={One},year={2025}}\n")

    issues = validate_research_md_format_preflight(tmp_path)

    assert "insufficient_verified_bibliography_entries" in {issue.code for issue in issues}


def test_research_md_format_preflight_rejects_too_few_unique_citations(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    keys = _valid_reference_keys()
    main_path = tmp_path / "paper" / "main.tex"
    main_text = main_path.read_text(encoding="utf-8")
    main_text = main_text.replace(f"\\citep{{{','.join(keys[:15])}}}", "\\citep{verifiedref01}")
    main_text = main_text.replace(f"\\citep{{{','.join(keys[15:30])}}}", "\\citep{verifiedref02}")
    _write(main_path, main_text)

    issues = validate_research_md_format_preflight(tmp_path)

    assert "insufficient_unique_citations" in {issue.code for issue in issues}


def test_research_md_reference_depth_rejects_one_page_rendered_references() -> None:
    keys = _valid_reference_keys()
    tex_text = f"Related work \\citep{{{','.join(keys[:30])}}}."
    issues = _validate_research_md_reference_depth(
        {"paper/references.bib": _valid_references_bibtex()},
        tex_text,
        [
            "Title\nIntroduction\n",
            "References\nVerified Reference 1\nVerified Reference 2\n",
            "Appendix\nReproducibility\n",
        ],
    )

    assert "insufficient_rendered_reference_pages" in {issue.code for issue in issues}


def test_research_md_pdf_text_rejects_underfilled_main_body() -> None:
    issues = _validate_research_md_pdf_text(
        [
            "Title\nIntroduction\n",
            "Related Work\n",
            "Method\n",
            "Results\nConclusion\n",
            "References\nPaper A\nPaper B\n",
        ]
    )

    codes = {issue.code for issue in issues}
    assert "rendered_main_body_underfilled" in codes
    assert "references_before_full_body" in codes


def test_research_md_format_preflight_rejects_transitive_placeholder(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    main_path = tmp_path / "paper" / "main.tex"
    main_text = main_path.read_text(encoding="utf-8")
    main_text = main_text.replace("\\section{Introduction}", "\\input{sections/intro}\n\\section{Introduction}")
    _write(main_path, main_text)
    _write(tmp_path / "paper" / "sections" / "intro.tex", "TODO: replace this placeholder.\n")

    issues = validate_research_md_format_preflight(tmp_path)

    assert "research_md_placeholder_text" in {issue.code for issue in issues}


def test_research_md_format_preflight_rejects_missing_anonymity_and_sections(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    main_path = tmp_path / "paper" / "main.tex"
    main_text = main_path.read_text(encoding="utf-8")
    main_text = main_text.replace("\\author{Anonymous EMNLP Submission}", "\\author{Named Authors}")
    main_text = main_text.replace("\\section*{Limitations}", "\\section*{Scope Notes}")
    main_text = main_text.replace("\\section*{Ethical Considerations}", "\\section*{Responsible Use}")
    _write(main_path, main_text)

    issues = validate_research_md_format_preflight(tmp_path)

    codes = {issue.code for issue in issues}
    assert "missing_anonymous_emnlp_author" in codes
    assert "missing_limitations_section" in codes
    assert "missing_ethics_section" in codes


def test_research_md_format_preflight_rejects_unreferenced_figure(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    main_path = tmp_path / "paper" / "main.tex"
    main_text = main_path.read_text(encoding="utf-8")
    main_text = main_text.replace(" with Figure~\\ref{fig:method}", "")

    _write(main_path, main_text)

    issues = validate_research_md_format_preflight(tmp_path)

    assert "body_figure_not_referenced" in {issue.code for issue in issues}


def test_research_md_format_preflight_rejects_table_caption_without_number(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    main_path = tmp_path / "paper" / "main.tex"
    main_text = main_path.read_text(encoding="utf-8")
    main_text = main_text.replace(
        "SkillGuard improves success by 8 points over the baseline.",
        "SkillGuard improves success over the baseline.",
    )
    main_text = main_text.replace(
        "Paired McNemar significance remains below p=0.01 across 120 tasks.",
        "Paired McNemar significance remains below the threshold across all tasks.",
    )
    _write(main_path, main_text)

    issues = validate_research_md_format_preflight(tmp_path)

    assert "table_caption_missing_number" in {issue.code for issue in issues}


def test_research_md_format_preflight_rejects_excess_body_figures(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    main_path = tmp_path / "paper" / "main.tex"
    extra_figures = "\n".join(
        [
            "\\begin{figure}[t]",
            "\\centering",
            "\\caption{Additional ablation shows 1 key result.}",
            "\\label{fig:extra-%d}",
            "\\end{figure}",
        ]
    )
    main_text = main_path.read_text(encoding="utf-8")
    refs = " ".join(f"Figure~\\ref{{fig:extra-{index}}}" for index in range(6))
    main_text = main_text.replace(
        "\\section{Conclusion}",
        f"{refs}\n\\input{{sections/extras}}\n\\section{{Conclusion}}",
    )
    _write(main_path, main_text)
    _write(
        tmp_path / "paper" / "sections" / "extras.tex",
        "\n".join(extra_figures % index for index in range(6)),
    )

    issues = validate_research_md_format_preflight(tmp_path)

    assert "too_many_body_figures" in {issue.code for issue in issues}


def test_paper_format_rejects_code_like_table_labels(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write(
        tmp_path / "paper" / "main.tex",
        "\n".join(
            [
                "\\documentclass{article}",
                "\\begin{document}",
                "\\begin{abstract}Ready paper.\\end{abstract}",
                "\\section{Results}",
                "\\begin{tabular}{ll}",
                "Method & audited\\_success \\\\",
                "\\texttt{handoff\\_and\\_finalize} & 1.0 \\\\",
                "\\end{tabular}",
                "\\bibliography{references}",
                "\\end{document}",
            ]
        ),
    )

    issues = validate_paper_format(tmp_path)

    assert "code_like_display_label" in {issue.code for issue in issues}


def test_layout_review_accepts_visual_pass_with_fresh_page_snapshot(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_layout_review(tmp_path)

    assert validate_layout_review(tmp_path) == []


def test_layout_review_rejects_missing_review(tmp_path: Path) -> None:
    issues = validate_layout_review(tmp_path)

    assert "missing_layout_review" in {issue.code for issue in issues}


def test_layout_review_rejects_low_score_and_pending_revision(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_layout_review(
        tmp_path,
        score=3.25,
        verdict="FAIL",
        needs_revision=True,
        blocking_issues=[],
        revision_directives=[
            {
                "action": "split_table",
                "target": "page 5",
                "rationale": "too many table captions on one page",
            }
        ],
    )

    issues = validate_layout_review(tmp_path)

    codes = {issue.code for issue in issues}
    assert "layout_review_not_pass" in codes
    assert "low_layout_review_score" in codes
    assert "layout_review_needs_revision" in codes


def test_layout_review_rejects_non_visual_pass(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_layout_review(tmp_path, review_method="heuristic_only")

    assert "layout_review_not_visual" in {issue.code for issue in validate_layout_review(tmp_path)}


def test_layout_review_rejects_forged_stale_hashes(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_layout_review(tmp_path)
    _write(tmp_path / "paper" / "main.pdf", "%PDF-1.5\nchanged\n")

    assert "stale_layout_review_artifact" in {issue.code for issue in validate_layout_review(tmp_path)}


def test_academic_language_review_accepts_model_pass_with_fresh_sources(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_academic_language_review(tmp_path)

    assert validate_academic_language_review(tmp_path) == []


def test_academic_language_review_rejects_missing_review(tmp_path: Path) -> None:
    issues = validate_academic_language_review(tmp_path)

    assert "missing_academic_language_review" in {issue.code for issue in issues}


def test_academic_language_review_rejects_low_score_and_pending_revision(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_academic_language_review(
        tmp_path,
        score=3.25,
        verdict="FAIL",
        needs_revision=True,
        blocking_issues=[],
        revision_directives=[
            {
                "action": "rewrite_introduction",
                "target": "paper/main.tex",
                "rationale": "generic opening and weak contribution framing",
            }
        ],
    )

    issues = validate_academic_language_review(tmp_path)

    codes = {issue.code for issue in issues}
    assert "academic_language_review_not_pass" in codes
    assert "low_academic_language_review_score" in codes
    assert "academic_language_review_needs_revision" in codes


def test_academic_language_review_rejects_non_model_pass(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_academic_language_review(tmp_path, review_method="heuristic_only")

    codes = {issue.code for issue in validate_academic_language_review(tmp_path)}
    assert "academic_language_review_not_model_backed" in codes


def test_academic_language_review_rejects_stale_source_hash(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_academic_language_review(tmp_path)
    _write(
        tmp_path / "paper" / "main.tex",
        "\\documentclass{article}\nchanged academic source\n",
    )

    assert "stale_academic_language_review_source" in {
        issue.code for issue in validate_academic_language_review(tmp_path)
    }


def test_academic_language_review_rejects_unreviewed_input_file(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_academic_language_review(tmp_path)
    _write(
        tmp_path / "paper" / "main.tex",
        (tmp_path / "paper" / "main.tex").read_text(encoding="utf-8")
        + "\\input{sections/new_intro}\n",
    )
    _write(tmp_path / "paper" / "sections" / "new_intro.tex", "New unreviewed prose.\n")

    codes = {issue.code for issue in validate_academic_language_review(tmp_path)}
    assert "unreviewed_academic_language_source" in codes


def test_academic_language_review_rejects_generic_opening_even_with_pass_json(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    text = (tmp_path / "paper" / "main.tex").read_text(encoding="utf-8")
    _write(
        tmp_path / "paper" / "main.tex",
        text.replace(
            "A complete EMNLP-style long paper.",
            "Large language models have achieved remarkable success. We propose SkillCycle.",
        ),
    )
    _write_valid_academic_language_review(tmp_path)

    codes = {issue.code for issue in validate_academic_language_review(tmp_path)}
    assert "academic_language_generic_llm_success_opening" in codes


def test_academic_language_review_rejects_validator_shaped_abstract_even_with_pass_json(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    text = (tmp_path / "paper" / "main.tex").read_text(encoding="utf-8")
    bad_abstract = (
        "\\begin{abstract}"
        "240/240 boundary-transfer episodes beat 84/240 baselines under controlled "
        "synthetic benchmark-scoped conditions, which is not causal proof. "
        "Appendix Figure~\\ref{fig:method} and the validator evidence span document "
        "the claim. We propose Boundary Skill Transfer for agent benchmark failures. "
        "The method improves verified completion by 65 points through persisted "
        "boundary rules. This result suggests targeted skill memory can reduce "
        "repeated planning failures."
        "\\end{abstract}"
    )
    _write(
        tmp_path / "paper" / "main.tex",
        text.replace(
            "\\begin{abstract}A complete EMNLP-style long paper.\\end{abstract}",
            bad_abstract,
        ),
    )
    _write_valid_academic_language_review(tmp_path)

    codes = {issue.code for issue in validate_academic_language_review(tmp_path)}
    assert "academic_language_result_first_abstract" in codes
    assert "academic_language_abstract_references_layout_artifact" in codes
    assert "academic_language_abstract_mentions_internal_review_artifact" in codes
    assert "academic_language_over_defensive_abstract" in codes


def test_heuristic_academic_language_review_flags_validator_shaped_abstract(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    text = (tmp_path / "paper" / "main.tex").read_text(encoding="utf-8")
    bad_abstract = (
        "\\begin{abstract}"
        "% evidence: paper/artifacts/result_to_claim.tsv\n"
        "240/240 boundary-transfer episodes beat 84/240 baselines under controlled "
        "synthetic benchmark-scoped conditions, which is not causal proof. "
        "Appendix Figure~\\ref{fig:method} and the validator evidence span document "
        "the claim. We propose Boundary Skill Transfer for agent benchmark failures. "
        "The method improves verified completion by 65 points through persisted "
        "boundary rules. This result suggests targeted skill memory can reduce "
        "repeated planning failures."
        "\\end{abstract}"
    )
    _write(
        tmp_path / "paper" / "main.tex",
        text.replace(
            "\\begin{abstract}A complete EMNLP-style long paper.\\end{abstract}",
            bad_abstract,
        ),
    )

    review = generate_academic_language_review(tmp_path, review_mode="heuristic", write=False)

    codes = {issue["code"] for issue in review["issues"]}
    assert "abstract_contains_internal_evidence_comment" in codes
    assert "result_first_abstract" in codes
    assert "over_defensive_abstract" in codes
    assert review["needs_revision"] is True


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _png_bytes(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", width, height)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_valid_quality_calibration(root: Path) -> None:
    _write(root / "paper" / "artifacts" / "significance.tsv", "test\tp\nmcnemar\t0.01\n")
    _write(
        root / "paper" / "artifacts" / "results_summary.tsv",
        "\n".join(
            [
                "scope\tsplit_name\tprotocol\tsuccess_rate\tjson_parse_rate\tn_tasks",
                "overall\tmain\tno_skill\t0.500\t1.000\t240",
                "overall\tmain\traw_memory\t0.610\t1.000\t240",
                "overall\tmain\treflexion\t0.850\t1.000\t240",
                "overall\tmain\tstatic_skill_lib\t0.620\t1.000\t240",
                "overall\tmain\tskillcycle\t0.920\t1.000\t240",
                "overall\tpublic_validation\tno_skill\t0.500\t1.000\t30",
                "overall\tpublic_validation\traw_memory\t0.600\t1.000\t30",
                "overall\tpublic_validation\treflexion\t0.800\t1.000\t30",
                "overall\tpublic_validation\tstatic_skill_lib\t0.600\t1.000\t30",
                "overall\tpublic_validation\tskillcycle\t0.867\t1.000\t30",
            ]
        )
        + "\n",
    )
    _write_json(
        root / "paper" / "PAPER_QUALITY_CALIBRATION.json",
        {
            "verdict": "PASS",
            "quality_signals": {
                "uses_public_benchmark": True,
                "beats_nontrivial_baseline": True,
                "proposed_contribution_beats_strong_baseline": True,
                "statistical_support_for_headline": True,
                "n_tasks_meets_threshold": True,
                "parser_schema_confound_cleared": True,
                "submission_quality_self_assessment": "ready",
            },
            "paper_contribution": {
                "contribution_sentence": (
                    "We propose SkillCycle. We show SkillCycle improves procedural "
                    "tool-use accuracy by 7.0 points because it validates reusable "
                    "skills before admitting them."
                ),
                "proposed_artifact": "SkillCycle",
                "proposed_protocol": "skillcycle",
                "primary_metric": "success_rate",
                "metric_direction": "higher_is_better",
                "primary_split": "main",
                "primary_baselines": [
                    "raw_memory",
                    "reflexion",
                    "static_skill_lib",
                ],
                "primary_improvement": "7.0 accuracy points over reflexion",
                "mechanism": "verifier-gated skill admission prevents bad replay",
                "positive_headline_supported": True,
                "negative_result": False,
                "statistical_support": {
                    "artifact_path": "paper/artifacts/significance.tsv",
                    "test": "paired bootstrap",
                    "p_value": 0.01,
                },
            },
            "negative_case_regressions": [],
            "quality_signals_from_positive_examples": [
                {
                    "case_id": "positive:emnlp2025-best-infini-gram-mini",
                    "signals_used": ["clear_problem_with_broad_relevance"],
                }
            ],
        },
    )


def _write_valid_plan_stage(root: Path) -> None:
    _write(root / "research" / "RESEARCH_BRIEF.md", "brief\n")
    _write(root / "research" / "LITERATURE_REVIEW.md", "review\n")
    _write(root / "research" / "LIT_MATRIX.tsv", "title\tvenue\n")
    _write_valid_literature_grounding(root)
    _write(root / "research" / "SOURCE_DISCOVERY.md", "sources\n")
    _write(root / "research" / "TREND_INSIGHTS.md", "insights\n")
    _write(root / "research" / "NOVELTY_REPORT.md", "novelty\n")
    _write(root / "research" / "NOVELTY_MAP.md", "map\n")
    _write_valid_idea_provenance(root)
    _write(root / "research" / "RELATED_WORK_BLOCKERS.md", "blockers\n")
    _write(root / "research" / "EXPERIMENT_PLAN.md", "plan\n")
    _write(root / "research" / "CLAIMS_TO_TEST.md", "claims\n")
    _write(root / "research" / "BASELINE_AND_BENCHMARK_PLAN.md", "baselines\n")
    _write_valid_code_reuse_plan(root)
    _write(root / "experiments" / "BENCHMARK_PROVENANCE.md", "benchmark\n")
    _write_json(
        root / "research" / "PIPELINE_STATE.json",
        {
            "current_stage": "plan",
            "stages": {
                "brief": {"status": "done"},
                "plan": {"status": "ready"},
            },
        },
    )


def _write_valid_full_emnlp_package(root: Path) -> None:
    _write_valid_plan_stage(root)
    _write(root / "experiments" / "run_001" / "manifest.json", "{}\n")
    _write(root / "experiments" / "run_001" / "status.json", "{}\n")
    _write(root / "experiments" / "run_001" / "progress.jsonl", "{}\n")
    _write(root / "paper" / "artifacts" / "claims_evidence.tsv", "claim\tevidence\n")
    _write(root / "paper" / "artifacts" / "result_to_claim.tsv", "result\tclaim\n")
    _write(root / "research" / "NARRATIVE_REPORT.md", "narrative\n")
    _write(root / "paper" / "main.tex", "\\documentclass{article}\n")
    _write(root / "paper" / "main.pdf", "%PDF-1.5\n")
    _write(
        root / "paper" / "artifacts" / "results_summary.tsv",
        "\n".join(
            [
                "scope\tsplit_name\tprotocol\tsuccess_rate\tjson_parse_rate\tn_tasks",
                "overall\tmain\tno_skill\t0.500\t1.000\t240",
                "overall\tmain\traw_memory\t0.610\t1.000\t240",
                "overall\tmain\treflexion\t0.850\t1.000\t240",
                "overall\tmain\tstatic_skill_lib\t0.620\t1.000\t240",
                "overall\tmain\tskillcycle\t0.920\t1.000\t240",
                "overall\tpublic_validation\tno_skill\t0.500\t1.000\t30",
                "overall\tpublic_validation\traw_memory\t0.600\t1.000\t30",
                "overall\tpublic_validation\treflexion\t0.800\t1.000\t30",
                "overall\tpublic_validation\tstatic_skill_lib\t0.600\t1.000\t30",
                "overall\tpublic_validation\tskillcycle\t0.867\t1.000\t30",
            ]
        )
        + "\n",
    )
    _write(root / "paper" / "PAGE_BUDGET.md", "7.8 pages\n")
    _write(root / "paper" / "TEMPLATE_SOURCE.md", "ACL style files\n")
    _write(root / "paper" / "PAPER_DRAFT_REPORT.md", "ready\n")
    _write_valid_quality_calibration(root)
    _write_valid_style_exemplar(root)
    _write_valid_image2_figures(root)
    _write_valid_paper_draft_report(root)
    _write_valid_layout_review(root)
    _write_valid_academic_language_review(root)
    _write_valid_artifact_manifest(root)
    _write(root / "paper" / "SUBMISSION_ASSURANCE.md", "PASS\n")
    _write(root / "paper" / "CLAIMS_EVIDENCE_AUDIT.tsv", "claim\tevidence\n")
    _write_json(root / "paper" / "CLAIMS_EVIDENCE_AUDIT.json", {"claims": []})
    _write_json(
        root / "paper" / "SUBMISSION_ASSURANCE.json",
        {
            "verdict": "PASS",
            "blocking_issues": [],
            "layers": {
                "experiment_integrity": {"verdict": "PASS"},
                "result_to_claim": {"verdict": "PASS"},
                "paper_claim_audit": {"verdict": "PASS"},
                "idea_provenance_and_code_reuse": {"verdict": "PASS"},
                "literature_and_exemplar_grounding": {"verdict": "PASS"},
                "citation_audit": {"verdict": "PASS"},
                "kill_argument": {"verdict": "PASS"},
                "paper_quality_calibration": {"verdict": "PASS"},
                "research_md_format_preflight": {"verdict": "PASS"},
                "academic_language_review": {"verdict": "PASS"},
                "layout_aesthetic_review": {"verdict": "PASS"},
                "submission_package": {"verdict": "PASS"},
            },
        },
    )
    _write_json(
        root / "research" / "PIPELINE_STATE.json",
        {
            "current_stage": "submission",
            "stages": {
                "submission": {"status": "ready"},
            },
        },
    )


def _write_valid_literature_grounding(
    root: Path,
    recent_count: int = 10,
    trend_backing: str | None = None,
) -> None:
    recent = [
        {
            "title": f"Recent EMNLP-quality paper {index}",
            "url": f"https://aclanthology.org/2025.test-{index}/",
            "year": 2025,
            "venue_or_status": "EMNLP",
            "relevance": "close benchmark or method signal",
        }
        for index in range(recent_count)
    ]
    classic = [
        {
            "title": f"Classic anchor paper {index}",
            "url": f"https://aclanthology.org/201{index}.classic/",
            "year": 2010 + index,
            "venue_or_status": "ACL",
            "relevance": "classic task or evaluation anchor",
        }
        for index in range(3)
    ]
    trend_source = {
        "name": "机器之心",
        "url": "https://www.jiqizhixin.com/",
        "accessed_on": "2026-05-24",
        "signals": ["agent evaluation trend"],
    }
    if trend_backing is not None:
        trend_source["paper_or_benchmark_backing"] = trend_backing
    _write_json(
        root / "research" / "LITERATURE_GROUNDING.json",
        {
            "recent_high_quality_papers": recent,
            "classic_papers": classic,
            "trend_sources": [trend_source],
        },
    )


def _write_valid_idea_provenance(root: Path) -> None:
    paper_refs = [
        {
            "type": "recent_paper",
            "title": "Recent EMNLP-quality paper 0",
            "url": "https://aclanthology.org/2025.test-0/",
        },
        {
            "type": "classic_paper",
            "title": "Classic anchor paper 0",
            "url": "https://aclanthology.org/2010.classic/",
        },
    ]
    _write_json(
        root / "research" / "IDEA_PROVENANCE.json",
        {
            "idea_generation_mode": "literature_and_code_grounded",
            "not_agent_brainstorm": True,
            "candidate_ideas": [
                {
                    "title": "Paper-derived candidate 0",
                    "source_refs": [paper_refs[0]],
                },
                {
                    "title": "Paper-derived candidate 1",
                    "source_refs": [paper_refs[1]],
                },
                {
                    "title": "Paper-derived candidate 2",
                    "source_refs": [paper_refs[0]],
                },
            ],
            "selected_idea": {
                "title": "Selected literature-derived idea",
                "research_gap": "recent systems lack robust benchmark coverage",
                "novelty_delta": "tests a missing setting identified by surveyed papers",
                "selection_rationale": "best gap-to-feasibility tradeoff from the matrix",
                "derived_from": paper_refs,
            },
        },
    )


def _write_valid_code_reuse_plan(root: Path) -> None:
    _write_json(
        root / "research" / "CODE_REUSE_PLAN.json",
        {
            "searched_queries": [
                "Recent EMNLP-quality paper 0 code",
                "site:github.com agent benchmark official code",
            ],
            "code_sources": [
                {
                    "url": "https://github.com/example/paper-code",
                    "source_type": "official_paper_code",
                    "paper_or_project": "Recent EMNLP-quality paper 0",
                    "license_or_terms": "MIT",
                    "reuse_decision": "adapt",
                    "attribution": "Example authors, official paper code",
                }
            ],
        },
    )


def _write_valid_style_exemplar(root: Path) -> None:
    profile = _style_profile_text()
    _write(root / "paper" / "style_ref" / "STYLE_PROFILE.md", profile)
    _write(
        root / "paper" / "style_ref" / "PAPER_STRUCTURE_BLUEPRINT.md",
        _style_blueprint_text(),
    )
    exemplars = []
    for slug, title, venue, year, source_type, award_status in (
        (
            "emnlp-award",
            "Infini-gram mini: Exact n-gram Search at the Internet Scale",
            "EMNLP",
            2025,
            "official-award-paper",
            "EMNLP best paper",
        ),
        (
            "acl-evaluation",
            "Stateful Evaluation for Language Agents",
            "ACL",
            2024,
            "acl-anthology-paper",
            "same-direction exemplar",
        ),
    ):
        pdf_path = root / "paper" / "style_ref" / "exemplars" / slug / "paper.pdf"
        text_path = root / "paper" / "style_ref" / "exemplars" / slug / "paper.txt"
        pdf_bytes = b"%PDF-1.5\n" + (f"{title}\n".encode("utf-8") * 256)
        _write_bytes(pdf_path, pdf_bytes)
        _write(
            text_path,
            (
                f"{title}\nAbstract\nIntroduction\nRelated Work\nMethod\n"
                "Experiments\nResults\nAnalysis\nLimitations\nReferences\n"
                "This extracted text is a local structural reference for page allocation, "
                "section order, figure density, table placement, evaluation layout, and "
                "academic paper-writing decisions. "
            )
            * 24,
        )
        exemplars.append(
            {
                "title": title,
                "url": f"https://aclanthology.org/{year}.test-{slug}/",
                "venue": venue,
                "year": year,
                "source_type": source_type,
                "award_status": award_status,
                "open_access": True,
                "license": "acl-anthology-open-access",
                "pdf_storage_policy": "local_research_cache_not_redistributed",
                "usage": "structural_style_only",
                "no_prose_copy": True,
                "local_pdf": pdf_path.relative_to(root).as_posix(),
                "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                "text_extract": text_path.relative_to(root).as_posix(),
                "structural_profile": "paper/style_ref/STYLE_PROFILE.md",
            }
        )
    _write_json(
        root / "paper" / "style_ref" / "EXEMPLAR.json",
        {"exemplar_schema_version": 2, "exemplars": exemplars},
    )


def _style_profile_text() -> str:
    sections = [
        "# Thick exemplar style profile",
        "## Abstract shape\n"
        "The exemplar abstracts open with a concrete problem, state the missing mechanism, "
        "name the method or resource, report the strongest measured result, and close with "
        "the implication. The profile records sentence roles rather than reusable wording.",
        "## Section/page allocation\n"
        "The papers allocate early space to motivation and related work, reserve the center "
        "of the body for method and experimental setup, and keep results plus analysis dense "
        "with tables and figures. Conclusion arrives before the body limit.",
        "## Figure/table inventory\n"
        "The exemplars use one main conceptual figure, compact result tables, and appendix "
        "diagnostic tables. Captions make numerical claims and every visual is referenced.",
        "## Related-work shape\n"
        "Related work is organized by methodological gap and evaluation limitation, not as "
        "a chronological list. It explains why existing benchmarks or memory methods do not "
        "answer the target question.",
        "## Evaluation layout\n"
        "Evaluation moves from setup to main results, ablation, robustness or transfer, and "
        "failure analysis. Strong baselines appear before claims, and significance evidence "
        "is close to the main comparison.",
        "## Formatting/layout lessons\n"
        "Use ACL-style page density, readable tables, few body figures, short captions with "
        "takeaways, and avoid wall-of-text pages. Keep references before appendices.",
        "## Writing lessons\n"
        "Use active verbs, concrete nouns, calibrated claims, and stress-position sentences. "
        "Avoid hype, generic openings, and unsupported top-paper language.",
        "## Transfer plan\n"
        "Apply these structural lessons to our paper by mapping each section to local evidence, "
        "using the same figure/table density, and checking that every claim traces to artifacts.",
        "## No prose copy policy\n"
        "This profile is structural style only. Do not copy prose, examples, terminology, "
        "claims, figure design, bibliography text, or sentence templates from exemplars.",
    ]
    return "\n\n".join(sections) + "\n" + ("Structural evidence note. " * 120)


def _style_blueprint_text() -> str:
    sections = [
        "# Paper structure blueprint from exemplars",
        "## Section order\n"
        "Introduction, Related Work, Method, Benchmark, Experiments, Analysis, "
        "Conclusion, Limitations, Ethics, and Reproducibility are ordered to match "
        "top-conference paper flow while using only local project evidence.",
        "## Page budget\n"
        "The page allocation reserves one page for introduction, one for related work, "
        "one and a half for method, one for benchmark provenance, two for experiments "
        "and analysis, and half a page for conclusion plus limitations hooks.",
        "## Paragraph roles\n"
        "Each paragraph has a planned role: problem, gap, method mechanism, benchmark "
        "definition, main result, ablation explanation, failure analysis, and scope.",
        "## Figure/table plan\n"
        "Use one image-2 overview figure, compact result tables, an ablation table, and "
        "a failure-analysis visual placed where the evidence is discussed.",
        "## Related-work grouping\n"
        "Related work is grouped by method family, benchmark gap, and failure mode, with "
        "citations next to the specific claim they support.",
        "## Evaluation sequence\n"
        "Evaluation sequence moves from setup to baselines, main comparison, ablations, "
        "confidence intervals, robustness, and qualitative error analysis.",
        "## Local evidence mapping\n"
        "Every section maps claims to claims-evidence rows, result tables, benchmark "
        "provenance, and generated artifacts before prose is written.",
        "## No prose copy policy\n"
        "This plan uses structural style only. Do not copy prose, examples, terminology, "
        "claims, figure design, bibliography text, or sentence templates from exemplars.",
    ]
    return "\n\n".join(sections) + "\n" + ("Blueprint evidence mapping note. " * 80)


def _write_valid_image2_figures(root: Path) -> None:
    _write(root / "paper" / "figures" / "method.prompt.txt", "draw method overview\n")
    _write_bytes(root / "paper" / "figures" / "method.png", _png_bytes(1536, 1024))
    _write_json(
        root / "paper" / "figures" / "method.review.json",
        {"score_1_to_5": 4, "keep_or_regenerate": "keep"},
    )
    _write_image2_provenance(
        root,
        "paper/figures/method.prompt.txt",
        "paper/figures/method.png",
        "paper/figures/method.provenance.json",
    )
    _write_json(
        root / "paper" / "figures" / "IMAGE2_FIGURES.json",
        {
            "figures": [
                {
                    "figure_id": "method-overview",
                    "figure_type": "method",
                    "source": "raster",
                    "generator": "codex-image2",
                    "model": "image-2",
                    "prompt_path": "paper/figures/method.prompt.txt",
                    "output_path": "paper/figures/method.png",
                    "generation_provenance_path": "paper/figures/method.provenance.json",
                    "review_path": "paper/figures/method.review.json",
                    "requested_size": "1536x1024",
                }
            ]
        },
    )


def _write_image2_provenance(root: Path, prompt_path: str, output_path: str, provenance_path: str) -> None:
    output = root / output_path
    _write_json(
        root / provenance_path,
        {
            "generator": "codex-image2",
            "model": "image-2",
            "tool": "codex-image2",
            "prompt_path": prompt_path,
            "output_path": output_path,
            "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        },
    )


def _write_main_tex_with_figures(
    root: Path,
    body_figures: list[tuple[str, str, str]],
    *,
    appendix_figures: list[tuple[str, str, str]] | None = None,
) -> None:
    def latex_figure(path: str, label: str, caption: str) -> str:
        return (
            "\\begin{figure}[t]\n"
            "\\centering\n"
            f"\\includegraphics[width=\\linewidth]{{{path}}}\n"
            f"\\caption{{{caption}}}\n"
            f"\\label{{{label}}}\n"
            "\\end{figure}\n"
        )

    refs = " ".join(f"Figure~\\ref{{{label}}}." for _, label, _ in body_figures)
    body = "\n".join(latex_figure(*figure) for figure in body_figures)
    appendix = "\n".join(latex_figure(*figure) for figure in appendix_figures or [])
    _write(
        root / "paper" / "main.tex",
        "\\documentclass{article}\n"
        "\\usepackage{graphicx}\n"
        "\\begin{document}\n"
        f"{refs}\n"
        f"{body}\n"
        "\\appendix\n"
        f"{appendix}\n"
        "\\end{document}\n",
    )


def _write_valid_layout_review(
    root: Path,
    *,
    score: float = 4.4,
    verdict: str = "PASS",
    needs_revision: bool = False,
    review_method: str = "hybrid_vision_heuristic",
    blocking_issues: list[dict[str, object]] | None = None,
    revision_directives: list[dict[str, object]] | None = None,
) -> None:
    pdf_path = root / "paper" / "main.pdf"
    if not pdf_path.exists():
        _write(pdf_path, "%PDF-1.5\n")
    page_path = root / "paper" / "layout_review" / "pages" / "page-1.png"
    _write_bytes(page_path, _png_bytes(612, 792))
    _write_json(
        root / "paper" / "LAYOUT_REVIEW.json",
        {
            "schema_version": 1,
            "generated_by": "argus_skill.skills.paper_layout_review",
            "iteration": 1,
            "review_method": review_method,
            "verdict": verdict,
            "score_1_to_5": score,
            "threshold": 4.0,
            "needs_revision": needs_revision,
            "pdf_path": "paper/main.pdf",
            "pdf_sha256": _sha256(pdf_path),
            "page_snapshots": [
                {
                    "page": 1,
                    "path": "paper/layout_review/pages/page-1.png",
                    "sha256": _sha256(page_path),
                }
            ],
            "criteria_scores": {
                "typography": 4.2,
                "table_readability": 4.1,
                "float_balance": 4.5,
                "page_flow": 4.3,
            },
            "issues": [],
            "blocking_issues": blocking_issues or [],
            "revision_directives": revision_directives or [],
        },
    )


def _write_valid_academic_language_review(
    root: Path,
    *,
    score: float = 4.4,
    verdict: str = "PASS",
    needs_revision: bool = False,
    review_method: str = "hybrid_llm_heuristic",
    blocking_issues: list[dict[str, object]] | None = None,
    revision_directives: list[dict[str, object]] | None = None,
    required_checks: dict[str, bool] | None = None,
) -> None:
    main_path = root / "paper" / "main.tex"
    if not main_path.exists():
        _write_valid_paper_draft_report(root)
    references_path = root / "paper" / "references.bib"
    if not references_path.exists():
        _write(references_path, _valid_references_bibtex())
    tex_text = main_path.read_text(encoding="utf-8")
    quote = _first_noncommand_sentence(tex_text)
    source_paths = [main_path, references_path]
    source_snapshots = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256(path),
        }
        for path in source_paths
    ]
    section_scores = {
        "abstract": score,
        "introduction": score,
        "contribution_framing": score,
        "evidence_alignment": score,
        "related_work_positioning": score,
        "style_and_clarity": score,
    }
    checks = {
        "clear_problem_gap_contribution": True,
        "evidence_aligned_claims": True,
        "five_sentence_abstract_or_equivalent": True,
        "related_work_methodological": True,
        "calibrated_no_hype": True,
        "limitations_scope_present": True,
    }
    if required_checks:
        checks.update(required_checks)
    _write_json(
        root / "paper" / "ACADEMIC_LANGUAGE_REVIEW.json",
        {
            "schema_version": 1,
            "generated_by": "argus_skill.skills.academic_language_review",
            "iteration": 1,
            "review_method": review_method,
            "verdict": verdict,
            "score_1_to_5": score,
            "threshold": 4.0,
            "needs_revision": needs_revision,
            "source_snapshots": source_snapshots,
            "reviewed_source_count": len(source_snapshots),
            "section_scores": section_scores,
            "required_checks": checks,
            "evidence_spans": [
                {
                    "section": section,
                    "source_path": "paper/main.tex",
                    "line": 1,
                    "quote": quote,
                    "why": f"Reviewer evidence for {section}.",
                }
                for section in section_scores
            ],
            "issues": [],
            "blocking_issues": blocking_issues or [],
            "revision_directives": revision_directives or [],
        },
    )


def _first_noncommand_sentence(tex_text: str) -> str:
    for raw_line in tex_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("\\"):
            continue
        sentence = line.split(".", 1)[0].strip()
        if sentence:
            return sentence + "."
    return "A complete EMNLP-style long paper."


def _write_valid_paper_draft_report(
    root: Path,
    *,
    target_venue: str = "EMNLP",
    scope: str = "long-paper",
    pages: float = 7.8,
) -> None:
    citation_keys = _valid_reference_keys()
    _write(
        root / "paper" / "main.tex",
        "\n".join(
            [
                "\\documentclass[11pt]{article}",
                "\\usepackage[review]{acl}",
                "\\usepackage{xcolor}",
                "\\usepackage{booktabs}",
                "\\usepackage{graphicx}",
                "\\definecolor{tabheader}{HTML}{E8EDF7}",
                "\\definecolor{tabours}{HTML}{EAF6EA}",
                "\\renewcommand{\\arraystretch}{1.15}",
                "\\setlength{\\tabcolsep}{4pt}",
                "\\title{SkillGuard: Evidence-Calibrated Skill Transfer}",
                "\\author{Anonymous EMNLP Submission}",
                "\\begin{document}",
                "\\maketitle",
                "\\begin{abstract}A complete EMNLP-style long paper.\\end{abstract}",
                "\\section{Introduction}",
                "This paper is formatted as a reviewable long paper with Figure~\\ref{fig:method}.",
                "\\section{Related Work}",
                (
                    "Prior work motivates the benchmark and transfer setting "
                    f"\\citep{{{','.join(citation_keys[:15])}}}, while recent agent-memory "
                    f"and verifier systems frame the implementation gap \\citep{{{','.join(citation_keys[15:30])}}}."
                ),
                "\\section{Method}",
                "\\begin{figure}[t]",
                "\\centering",
                "\\includegraphics[width=0.82\\linewidth]{figures/method.png}",
                "\\caption{SkillGuard routing improves verified completion by 8 points.}",
                "\\label{fig:method}",
                "\\end{figure}",
                "The method uses a conservative routing policy.",
                "\\section{Experimental Setup}",
                "We report paired tests in Table~\\ref{tab:significance}.",
                "\\begin{table}[t]",
                "\\centering",
                "\\footnotesize",
                "\\rowcolors{2}{tabours}{white}",
                "\\begin{tabular}{lcc}",
                "\\rowcolor{tabheader}",
                "\\toprule",
                "System & Success & Cost \\\\",
                "\\midrule",
                "Baseline & 62 & 1.0 \\\\",
                "SkillGuard & 70 & 0.9 \\\\",
                "\\bottomrule",
                "\\end{tabular}",
                "\\caption{SkillGuard improves success by 8 points over the baseline.}",
                "\\label{tab:main}",
                "\\end{table}",
                "\\section{Results}",
                "Table~\\ref{tab:main} summarizes the main result.",
                "\\begin{table}[t]",
                "\\centering",
                "\\footnotesize",
                "\\begin{tabular}{lc}",
                "\\toprule",
                "Test & p-value \\\\",
                "\\midrule",
                "Paired McNemar & 0.01 \\\\",
                "\\bottomrule",
                "\\end{tabular}",
                "\\caption{Paired McNemar significance remains below p=0.01 across 120 tasks.}",
                "\\label{tab:significance}",
                "\\end{table}",
                "\\section{Conclusion}",
                "The paper concludes within the main-page budget.",
                "\\section*{Limitations}",
                "The evaluation is limited to the available task distribution.",
                "\\section*{Ethical Considerations}",
                "The work uses synthetic tasks and reports safety-relevant limitations.",
                "\\bibliography{references}",
                "\\appendix",
                "\\section{Reproducibility}",
                "We include commands, seeds, and artifact hashes in the supplementary package.",
                "\\end{document}",
            ]
        )
        + "\n",
    )
    _write(root / "paper" / "references.bib", _valid_references_bibtex())
    _write(root / "paper" / "main.log", "Clean LaTeX build.\n")
    _write(root / "paper" / "main.pdf", "%PDF-1.5\n")
    _write(root / "paper" / "FORMAT_PREFLIGHT.md", "validate-research-md-format: PASS\n")
    _write_json(
        root / "paper" / "PAPER_DRAFT_REPORT.json",
        {
            "target_venue": target_venue,
            "paper_scope": scope,
            "main_content_pages": pages,
            "official_acl_template": True,
            "submission_quality_self_assessment": "ready",
            "submission_phase": "review",
            "paired_significance_table": "tab:significance",
        },
    )


def _valid_reference_keys(count: int = 35) -> list[str]:
    return [f"verifiedref{index:02d}" for index in range(1, count + 1)]


def _valid_references_bibtex(count: int = 35) -> str:
    entries = []
    for index, key in enumerate(_valid_reference_keys(count), start=1):
        entries.append(
            "\n".join(
                [
                    f"@inproceedings{{{key},",
                    f"  title = {{Verified Reference {index}}},",
                    f"  author = {{Author, Test {index}}},",
                    f"  booktitle = {{Proceedings of EMNLP {2020 + (index % 6)}}},",
                    f"  year = {{{2020 + (index % 6)}}}",
                    "}",
                ]
            )
        )
    return "\n\n".join(entries) + "\n"


def _write_valid_artifact_manifest(root: Path) -> None:
    source_path = root / "paper" / "artifacts" / "results_table.tsv"
    report_path = root / "paper" / "RESULTS_REPORT.md"
    _write(source_path, "metric\tvalue\naccuracy\t0.5\n")
    _write(report_path, "accuracy is 0.5\n")
    _write_json(
        root / "paper" / "ARTIFACT_MANIFEST.json",
        {
            "version": 1,
            "canonical_sources": [
                {
                    "path": "paper/artifacts/results_table.tsv",
                    "sha256": _sha256(source_path),
                    "columns": ["metric", "value"],
                }
            ],
            "generated_artifacts": [
                {
                    "path": "paper/RESULTS_REPORT.md",
                    "sha256": _sha256(report_path),
                    "sources": ["paper/artifacts/results_table.tsv"],
                    "generator": "unit-test",
                }
            ],
        },
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
