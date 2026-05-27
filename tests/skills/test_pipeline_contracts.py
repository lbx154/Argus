from __future__ import annotations

import hashlib
import json
import os
import re
import struct
from pathlib import Path

from argus_skill.skills.academic_language_review import (
    _has_quantified_claim,
    _has_reader_facing_contribution,
    generate_academic_language_review,
)
from argus_skill.skills.pipeline_contracts import (
    _latex_environment_word_count,
    _pdf_page_count,
    _validate_rendered_pdf_page_budget,
    _validate_research_md_manual_page_breaks,
    _validate_research_md_pdf_text,
    _validate_research_md_reference_depth,
    refresh_artifact_freshness,
    refresh_artifact_manifest,
    repair_emnlp_contract_artifacts,
    validate_academic_language_review,
    validate_artifact_freshness,
    validate_artifact_manifest,
    validate_claim_graph,
    validate_code_reuse_plan,
    validate_emnlp_paper_contract,
    validate_exemplar_suitability,
    validate_figure_table_style_guide,
    validate_full_emnlp_readiness,
    validate_full_scale_experiment_evidence,
    validate_idea_provenance,
    validate_image2_figures,
    validate_layout_review,
    validate_literature_grounding,
    validate_paper_format,
    validate_paper_infrastructure_review,
    validate_pipeline_state,
    validate_research_md_format_preflight,
    validate_style_exemplar,
    validate_submission_assurance,
    validate_submission_readiness,
    validate_validation_priority_policy,
    write_validation_priority_policy,
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
                "paper_infrastructure_review": {"verdict": "PASS"},
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
                "paper_infrastructure_review": {"verdict": "PASS"},
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
    _write_valid_paper_infrastructure_review(tmp_path)
    _write_full_scale_experiment_run(tmp_path, methods=["no_skill"], task_count=300)

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
                "paper_infrastructure_review": {"verdict": "FAIL"},
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
    assert all(
        "final EMNLP readiness requires this stage artifact" in issue.message
        for issue in issues
        if issue.code == "missing_stage_artifact"
    )


def test_full_emnlp_readiness_without_pipeline_state_does_not_emit_stage_artifact_noise(
    tmp_path: Path,
) -> None:
    issues = validate_full_emnlp_readiness(tmp_path)
    codes = {issue.code for issue in issues}

    assert "missing_pipeline_state" in codes
    assert "missing_stage_artifact" not in codes
    assert "missing_literature_grounding" in codes


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


def test_full_scale_evidence_rejects_smoke_only_drafting(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "research" / "PIPELINE_STATE.json",
        {"current_stage": "draft", "stages": {"draft": {"status": "ready"}}},
    )
    _write(tmp_path / "paper" / "main.pdf", "%PDF-1.5\n")
    _write_full_scale_experiment_run(tmp_path, methods=["no_skill"], task_count=5)

    issues = validate_pipeline_state(tmp_path)
    codes = {issue.code for issue in issues}

    assert "missing_full_scale_experiment_run" in codes
    assert "pilot_pdf_without_full_scale_evidence" in codes


def test_full_scale_evidence_ignores_benchmark_construction_without_run(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "benchmarks" / "full" / "manifest.json",
        {"task_count": 300, "unique_semantic_tasks": 300},
    )
    _write(tmp_path / "paper" / "main.pdf", "%PDF-1.5\n")

    issues = validate_full_scale_experiment_evidence(tmp_path)
    codes = {issue.code for issue in issues}

    assert "missing_full_scale_experiment_run" in codes
    assert "pilot_pdf_without_full_scale_evidence" in codes


def test_full_scale_evidence_uses_raw_rows_not_declared_task_count(
    tmp_path: Path,
) -> None:
    _write_full_scale_experiment_run(
        tmp_path,
        methods=["no_skill"],
        task_count=5,
        declared_task_count=300,
    )

    issues = validate_full_scale_experiment_evidence(tmp_path)

    assert "missing_full_scale_experiment_run" in {issue.code for issue in issues}


def test_full_scale_evidence_rejects_missing_required_condition(tmp_path: Path) -> None:
    _write(
        tmp_path / "research" / "BASELINE_AND_BENCHMARK_PLAN.md",
        "Required methods: no_skill, raw_memory, reflexion, static_skill_lib, skillcycle\n",
    )
    _write_full_scale_experiment_run(
        tmp_path,
        methods=["no_skill", "raw_memory", "reflexion", "static_skill_lib"],
        task_count=300,
    )

    issues = validate_full_scale_experiment_evidence(tmp_path)

    assert "missing_baseline_condition_run" in {issue.code for issue in issues}


def test_full_scale_evidence_accepts_complete_required_matrix(tmp_path: Path) -> None:
    methods = ["no_skill", "raw_memory", "reflexion", "static_skill_lib", "skillcycle"]
    _write(
        tmp_path / "research" / "BASELINE_AND_BENCHMARK_PLAN.md",
        "Required methods: no_skill, raw_memory, reflexion, static_skill_lib, skillcycle\n",
    )
    _write_valid_benchmark_provenance(tmp_path)
    _write_full_scale_experiment_run(tmp_path, methods=methods, task_count=300)

    assert validate_full_scale_experiment_evidence(tmp_path) == []


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


def test_claim_graph_rejects_claims_for_missing_sections_and_unknown_artifacts(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_artifact_manifest(tmp_path)
    _write_valid_claim_graph(tmp_path)
    path = tmp_path / "paper" / "CLAIM_GRAPH.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["claims"][0]["section"] = "Audit Trail"
    payload["claims"][0]["evidence_sources"] = ["paper/artifacts/missing.tsv"]
    _write_json(path, payload)

    codes = {issue.code for issue in validate_claim_graph(tmp_path)}

    assert "claim_graph_section_not_in_main_tex" in codes
    assert "claim_graph_unknown_evidence_source" in codes


def test_claim_graph_rejects_weak_claim_left_in_main_body(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_artifact_manifest(tmp_path)
    _write_valid_claim_graph(tmp_path)
    path = tmp_path / "paper" / "CLAIM_GRAPH.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["claims"].append(
        {
            "id": "weak-left-in-body",
            "claim": "A controller routes skill-memory state through verifier policy checks",
            "section": "Method",
            "status": "weak",
            "evidence_gap_id": "gap-routing",
            "revised_claim": "The routing policy is reported conservatively pending more evidence.",
        }
    )
    _write_json(path, payload)
    _write_json(
        tmp_path / "paper" / "EVIDENCE_GAPS.json",
        {"evidence_gap_schema_version": 1, "gaps": [{"id": "gap-routing"}]},
    )

    codes = {issue.code for issue in validate_claim_graph(tmp_path)}

    assert "unsupported_claim_in_main_body" in codes


def test_figure_table_style_guide_requires_inventory_to_match_body_floats(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_artifact_manifest(tmp_path)
    _write_valid_figure_table_style_guide(tmp_path)
    path = tmp_path / "paper" / "FIGURE_TABLE_STYLE_GUIDE.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["float_inventory"] = payload["float_inventory"][:1]
    _write_json(path, payload)

    codes = {issue.code for issue in validate_figure_table_style_guide(tmp_path)}

    assert "too_few_figure_table_style_floats" in codes


def test_figure_table_style_guide_accepts_multiple_source_artifacts(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_artifact_manifest(tmp_path)
    _write_valid_figure_table_style_guide(tmp_path)
    _write_bytes(tmp_path / "paper" / "figures" / "method.png", b"png")
    _write(tmp_path / "paper" / "artifacts" / "component_breakdown.tsv", "component\tvalue\nx\t1\n")

    path = tmp_path / "paper" / "FIGURE_TABLE_STYLE_GUIDE.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["float_inventory"][1]["source_artifact"] = [
        "paper/artifacts/results_table.tsv",
        "paper/artifacts/component_breakdown.tsv",
    ]
    payload["float_inventory"][2]["source_artifact"] = (
        "paper/artifacts/results_table.tsv and paper/artifacts/component_breakdown.tsv"
    )
    _write_json(path, payload)

    codes = {issue.code for issue in validate_figure_table_style_guide(tmp_path)}

    assert "float_inventory_unknown_source_artifact" not in codes


def test_artifact_freshness_rejects_changed_inputs(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_artifact_manifest(tmp_path)
    _write_valid_claim_graph(tmp_path)
    _write_valid_figure_table_style_guide(tmp_path)
    _write_valid_validation_priority_policy(tmp_path)
    _write_valid_artifact_freshness(tmp_path)
    _write(tmp_path / "paper" / "artifacts" / "results_table.tsv", "metric\tvalue\naccuracy\t0.7\n")

    codes = {issue.code for issue in validate_artifact_freshness(tmp_path)}

    assert "artifact_stale_vs_inputs" in codes


def test_validation_priority_policy_accepts_complete_failure_routing(tmp_path: Path) -> None:
    _write_valid_validation_priority_policy(tmp_path)

    assert validate_validation_priority_policy(tmp_path) == []


def test_write_validation_priority_policy_creates_complete_failure_routing(
    tmp_path: Path,
) -> None:
    issues = write_validation_priority_policy(tmp_path)

    assert issues == []
    payload = json.loads(
        (tmp_path / "paper" / "VALIDATION_PRIORITY_POLICY.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["priority_order"] == [
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
        "paper_infrastructure",
        "artifact_manifest",
    ]
    assert set(payload["failure_routing"]) == set(payload["priority_order"])
    assert "experiment" in payload["failure_routing"]["experiment_evidence"]["repair_mode"]
    assert "analysis" in payload["failure_routing"]["content_sufficiency"]["repair_mode"]


def test_validation_priority_policy_requires_experiment_and_content_routes(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "paper" / "VALIDATION_PRIORITY_POLICY.json",
        {
            "priority_policy_schema_version": 1,
            "priority_order": [
                "freshness",
                "claim_graph",
                "exemplar_suitability",
                "exemplar_structure",
                "figure_table_style",
                "layout_vision",
                "academic_language",
                "artifact_manifest",
            ],
            "failure_routing": {
                "freshness": {"issue_code_prefixes": ["artifact_"], "repair_mode": "refresh artifacts"},
                "claim_graph": {"issue_code_prefixes": ["claim_"], "repair_mode": "fix claims"},
                "exemplar_suitability": {"issue_code_prefixes": ["exemplar_"], "repair_mode": "fix exemplar"},
                "exemplar_structure": {"issue_code_prefixes": ["style_"], "repair_mode": "fix structure"},
                "figure_table_style": {"issue_code_prefixes": ["float_"], "repair_mode": "fix figures"},
                "layout_vision": {"issue_code_prefixes": ["layout_"], "repair_mode": "fix layout"},
                "academic_language": {"issue_code_prefixes": ["academic_"], "repair_mode": "fix language"},
                "artifact_manifest": {"issue_code_prefixes": ["manifest_"], "repair_mode": "fix manifest"},
            },
            "reset_policy": {"max_non_improving_rounds": 2, "actions": ["reset skeleton"]},
        },
    )

    codes = {issue.code for issue in validate_validation_priority_policy(tmp_path)}

    assert "incomplete_validation_priority_order" in codes
    assert "missing_validation_failure_route" in codes


def test_validation_priority_policy_rejects_content_route_without_evidence_or_experiment_action(
    tmp_path: Path,
) -> None:
    _write_valid_validation_priority_policy(tmp_path)
    path = tmp_path / "paper" / "VALIDATION_PRIORITY_POLICY.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["failure_routing"]["content_sufficiency"]["repair_mode"] = "adjust margins and spacing"
    _write_json(path, payload)

    codes = {issue.code for issue in validate_validation_priority_policy(tmp_path)}

    assert "validation_failure_route_bad_repair_mode" in codes


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


def test_refresh_artifact_manifest_coerces_legacy_string_entries_and_sources(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "experiments" / "run-1" / "results.jsonl"
    table_path = tmp_path / "paper" / "artifacts" / "results_table.tsv"
    report_path = tmp_path / "paper" / "RESULTS_REPORT.md"
    _write(source_path, '{"task_id": "a", "success": true}\n')
    _write(table_path, "metric\tvalue\nsuccess\t0.7\n")
    _write(report_path, "success is 0.7\n")
    _write_json(
        tmp_path / "paper" / "ARTIFACT_MANIFEST.json",
        {
            "canonical_sources": ["experiments/run-1/results.jsonl"],
            "generated_artifacts": [
                {"path": "paper/artifacts/results_table.tsv"},
                {"path": "paper/RESULTS_REPORT.md"},
            ],
        },
    )

    issues = refresh_artifact_manifest(tmp_path)

    assert issues == []
    manifest = json.loads(
        (tmp_path / "paper" / "ARTIFACT_MANIFEST.json").read_text(encoding="utf-8")
    )
    assert manifest["version"] == 1
    assert manifest["canonical_sources"][0]["path"] == "experiments/run-1/results.jsonl"
    canonical_by_path = {
        entry["path"]: entry for entry in manifest["canonical_sources"]
    }
    assert canonical_by_path["paper/artifacts/results_table.tsv"]["columns"] == [
        "metric",
        "value",
    ]
    generated_by_path = {
        entry["path"]: entry for entry in manifest["generated_artifacts"]
    }
    assert "paper/artifacts/results_table.tsv" in generated_by_path[
        "paper/RESULTS_REPORT.md"
    ]["sources"]


def test_refresh_artifact_manifest_bootstraps_missing_manifest(tmp_path: Path) -> None:
    _write(tmp_path / "experiments" / "run-1" / "results.jsonl", '{"task_id": "a"}\n')
    _write(tmp_path / "paper" / "artifacts" / "results_table.tsv", "metric\tvalue\n")
    _write(tmp_path / "paper" / "RESULTS_REPORT.md", "result summary\n")

    issues = refresh_artifact_manifest(tmp_path)

    assert issues == []
    manifest = json.loads(
        (tmp_path / "paper" / "ARTIFACT_MANIFEST.json").read_text(encoding="utf-8")
    )
    assert {entry["path"] for entry in manifest["canonical_sources"]} == {
        "experiments/run-1/results.jsonl",
        "paper/artifacts/results_table.tsv",
    }
    generated_by_path = {
        entry["path"]: entry for entry in manifest["generated_artifacts"]
    }
    assert generated_by_path["paper/RESULTS_REPORT.md"]["sources"] == [
        "paper/artifacts/results_table.tsv",
        "experiments/run-1/results.jsonl",
    ]


def test_repair_emnlp_contract_artifacts_repairs_manifest_policy_and_freshness(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "experiments" / "run-1" / "results.jsonl", '{"task_id": "a"}\n')
    _write(tmp_path / "paper" / "artifacts" / "results_table.tsv", "metric\tvalue\n")
    _write(tmp_path / "paper" / "RESULTS_REPORT.md", "result summary\n")
    _write_json(
        tmp_path / "paper" / "ARTIFACT_MANIFEST.json",
        {
            "canonical_sources": ["experiments/run-1/results.jsonl"],
            "generated_artifacts": [{"path": "paper/RESULTS_REPORT.md"}],
        },
    )
    _write_json(
        tmp_path / "paper" / "VALIDATION_PRIORITY_POLICY.json",
        {
            "priority_policy_schema_version": 1,
            "priority_order": ["freshness"],
            "failure_routing": {},
        },
    )
    _write_json(
        tmp_path / "paper" / "ARTIFACT_FRESHNESS.json",
        {
            "freshness_schema_version": 1,
            "records": [
                {
                    "path": "paper/ARTIFACT_FRESHNESS.json",
                    "role": "generated",
                    "sha256": "0" * 64,
                    "inputs": [],
                }
            ],
        },
    )

    issues = repair_emnlp_contract_artifacts(tmp_path)

    assert issues == []
    assert validate_artifact_manifest(tmp_path) == []
    assert validate_validation_priority_policy(tmp_path) == []
    assert validate_artifact_freshness(tmp_path) == []


def test_refresh_artifact_manifest_removes_self_freshness_and_reclassifies_paper_sources(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "paper" / "artifacts" / "results_table.tsv", "metric\tvalue\n")
    _write(tmp_path / "paper" / "CLAIM_GRAPH.json", "{}\n")
    _write(tmp_path / "paper" / "FIGURE_TABLE_STYLE_GUIDE.json", "{}\n")
    _write(tmp_path / "paper" / "figures" / "IMAGE2_FIGURES.json", "{}\n")
    _write_bytes(tmp_path / "paper" / "figures" / "method_overview.png", b"png bytes")
    _write(tmp_path / "paper" / "main.tex", "\\section{Method}\n")
    _write(tmp_path / "paper" / "ARTIFACT_FRESHNESS.json", "{}\n")
    _write_json(
        tmp_path / "paper" / "ARTIFACT_MANIFEST.json",
        {
            "version": 1,
            "canonical_sources": [
                "paper/main.tex",
                "paper/artifacts/results_table.tsv",
                "paper/ARTIFACT_FRESHNESS.json",
            ],
            "generated_artifacts": [
                {
                    "path": "paper/FIGURE_TABLE_STYLE_GUIDE.json",
                    "sources": ["paper/figures/IMAGE2_FIGURES.json"],
                },
                {
                    "path": "paper/figures/IMAGE2_FIGURES.json",
                    "sources": ["paper/main.tex"],
                },
                {
                    "path": "paper/main.tex",
                    "sources": ["paper/FIGURE_TABLE_STYLE_GUIDE.json"],
                },
                {
                    "path": "paper/CLAIM_GRAPH.json",
                    "sources": ["paper/artifacts/results_table.tsv"],
                },
            ],
        },
    )

    issues = refresh_artifact_manifest(tmp_path)

    assert issues == []
    manifest = json.loads(
        (tmp_path / "paper" / "ARTIFACT_MANIFEST.json").read_text(encoding="utf-8")
    )
    canonical_paths = {entry["path"] for entry in manifest["canonical_sources"]}
    generated_by_path = {
        entry["path"]: entry for entry in manifest["generated_artifacts"]
    }
    assert "paper/ARTIFACT_FRESHNESS.json" not in canonical_paths
    assert "paper/main.tex" not in canonical_paths
    assert "paper/main.tex" in generated_by_path
    assert generated_by_path["paper/FIGURE_TABLE_STYLE_GUIDE.json"]["sources"] == [
        "paper/figures/IMAGE2_FIGURES.json",
        "paper/artifacts/results_table.tsv",
    ]
    assert set(generated_by_path["paper/figures/IMAGE2_FIGURES.json"]["sources"]) == {
        "paper/figures/method_overview.png",
        "paper/artifacts/results_table.tsv",
    }
    assert "paper/main.tex" not in generated_by_path[
        "paper/figures/IMAGE2_FIGURES.json"
    ]["sources"]


def test_refresh_artifact_freshness_builds_records_from_manifest(
    tmp_path: Path,
) -> None:
    _write_valid_artifact_manifest(tmp_path)

    issues = refresh_artifact_freshness(tmp_path)

    assert issues == []
    payload = json.loads(
        (tmp_path / "paper" / "ARTIFACT_FRESHNESS.json").read_text(encoding="utf-8")
    )
    records = {record["path"]: record for record in payload["records"]}
    assert records["paper/artifacts/results_table.tsv"]["role"] == "canonical"
    report = records["paper/RESULTS_REPORT.md"]
    assert report["role"] == "generated"
    assert report["inputs"] == [
        {
            "path": "paper/artifacts/results_table.tsv",
            "sha256": _sha256(tmp_path / "paper" / "artifacts" / "results_table.tsv"),
        }
    ]


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
    _write_valid_paper_infrastructure_review(tmp_path)
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
                "paper_infrastructure_review": {"verdict": "PASS"},
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


def test_exemplar_suitability_requires_primary_to_match_downloaded_exemplar(tmp_path: Path) -> None:
    _write_valid_style_exemplar(tmp_path)
    path = tmp_path / "paper" / "style_ref" / "EXEMPLAR_SUITABILITY.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["primary_exemplar"] = "unrelated-paper"
    _write_json(path, payload)

    codes = {issue.code for issue in validate_exemplar_suitability(tmp_path)}

    assert "primary_exemplar_not_in_exemplar_json" in codes
    assert "primary_exemplar_not_in_suitability_candidates" in codes


def test_style_exemplar_accepts_normalized_profile_and_blueprint_headings(tmp_path: Path) -> None:
    _write_valid_style_exemplar(tmp_path)
    _write(
        tmp_path / "paper" / "style_ref" / "STYLE_PROFILE.md",
        _style_profile_text()
        .replace("Abstract shape", "abstract_shape")
        .replace("Section/page allocation", "section_page_allocation")
        .replace("Figure/table inventory", "figure-table-inventory")
        .replace("Related-work shape", "related_work_shape")
        .replace("Evaluation layout", "evaluation_layout")
        .replace("Formatting/layout lessons", "formatting_layout_lessons")
        .replace("Writing lessons", "writing-lessons")
        .replace("Transfer plan", "transfer_plan")
        .replace("No prose copy policy", "no_prose-copy-policy"),
    )
    _write(
        tmp_path / "paper" / "style_ref" / "PAPER_STRUCTURE_BLUEPRINT.md",
        _style_blueprint_text()
        .replace("Section order", "section_order")
        .replace("Page budget", "page-budget")
        .replace("Paragraph roles", "paragraph_roles")
        .replace("Figure/table plan", "figure/table-plan")
        .replace("Related-work grouping", "related_work_grouping")
        .replace("Evaluation sequence", "evaluation_sequence")
        .replace("Local evidence mapping", "local-evidence_mapping")
        .replace("No prose copy policy", "no_prose-copy-policy"),
    )

    assert validate_style_exemplar(tmp_path) == []


def test_style_exemplar_does_not_require_final_structure_conformance(tmp_path: Path) -> None:
    _write_valid_style_exemplar(tmp_path)
    conformance_dir = tmp_path / "paper" / "style_ref"
    for filename in ("STRUCTURE_CONFORMANCE.md", "STRUCTURE_CONFORMANCE.json"):
        path = conformance_dir / filename
        if path.exists():
            path.unlink()

    assert validate_style_exemplar(tmp_path) == []


def test_style_exemplar_rejects_pdf_hash_mismatch(tmp_path: Path) -> None:
    _write_valid_style_exemplar(tmp_path)
    path = tmp_path / "paper" / "style_ref" / "EXEMPLAR.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["exemplars"][0]["pdf_sha256"] = "0" * 64
    _write_json(path, payload)

    codes = {issue.code for issue in validate_style_exemplar(tmp_path)}

    assert "style_exemplar_pdf_hash_mismatch" in codes


def test_image2_figures_reject_secondary_tikz_non_data_manifest_entry(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "paper" / "figures" / "system.prompt.txt", _valid_image2_teaser_prompt())
    _write_bytes(tmp_path / "paper" / "figures" / "system.png", _png_bytes(1536, 1024))
    _write_json(
        tmp_path / "paper" / "figures" / "system.review.json",
        _valid_image_review_payload(tmp_path, "paper/figures/system.png"),
    )
    _write_image2_provenance(
        tmp_path,
        "paper/figures/system.prompt.txt",
        "paper/figures/system.png",
        "paper/figures/system.provenance.json",
    )
    _write_image2_inspect(tmp_path, "paper/figures/system.png", "paper/figures/system.inspect.json")
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
                    "sidecar_path": "paper/figures/system.provenance.json",
                    "inspect_path": "paper/figures/system.inspect.json",
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

    codes = {issue.code for issue in validate_image2_figures(tmp_path)}

    assert "non_data_figure_not_image2" in codes


def test_image2_figures_reject_thin_freehand_teaser_prompt(tmp_path: Path) -> None:
    _write(tmp_path / "paper" / "figures" / "method.prompt.txt", "draw method overview\n")
    _write_bytes(tmp_path / "paper" / "figures" / "method.png", _png_bytes(1536, 1024))
    _write_json(
        tmp_path / "paper" / "figures" / "method.review.json",
        _valid_image_review_payload(tmp_path, "paper/figures/method.png"),
    )
    _write_image2_provenance(
        tmp_path,
        "paper/figures/method.prompt.txt",
        "paper/figures/method.png",
        "paper/figures/method.provenance.json",
    )
    _write_image2_inspect(tmp_path, "paper/figures/method.png", "paper/figures/method.inspect.json")
    _write_json(
        tmp_path / "paper" / "figures" / "IMAGE2_FIGURES.json",
        {
            "figures": [
                {
                    "figure_id": "method-overview",
                    "figure_type": "teaser",
                    "source": "raster",
                    "generator": "codex-image2",
                    "model": "image-2",
                    "prompt_path": "paper/figures/method.prompt.txt",
                    "output_path": "paper/figures/method.png",
                    "generation_provenance_path": "paper/figures/method.provenance.json",
                    "sidecar_path": "paper/figures/method.provenance.json",
                    "inspect_path": "paper/figures/method.inspect.json",
                    "review_path": "paper/figures/method.review.json",
                    "requested_size": "1536x1024",
                }
            ]
        },
    )

    codes = {issue.code for issue in validate_image2_figures(tmp_path)}

    assert "thin_image2_teaser_prompt" in codes
    assert "incomplete_image2_teaser_prompt_scaffold" in codes


def test_image2_figures_reject_square_1024_conceptual_figure(tmp_path: Path) -> None:
    _write(tmp_path / "paper" / "figures" / "system.prompt.txt", _valid_image2_teaser_prompt())
    _write_bytes(tmp_path / "paper" / "figures" / "system.png", _png_bytes(1024, 1024))
    _write_json(
        tmp_path / "paper" / "figures" / "system.review.json",
        _valid_image_review_payload(tmp_path, "paper/figures/system.png"),
    )
    _write_image2_provenance(
        tmp_path,
        "paper/figures/system.prompt.txt",
        "paper/figures/system.png",
        "paper/figures/system.provenance.json",
    )
    _write_image2_inspect(tmp_path, "paper/figures/system.png", "paper/figures/system.inspect.json")
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
                    "sidecar_path": "paper/figures/system.provenance.json",
                    "inspect_path": "paper/figures/system.inspect.json",
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


def test_image2_figures_reject_manifest_without_tool_sidecars(tmp_path: Path) -> None:
    _write(tmp_path / "paper" / "figures" / "method.prompt.txt", _valid_image2_teaser_prompt())
    _write_bytes(tmp_path / "paper" / "figures" / "method.png", _png_bytes(1536, 1024))
    _write_json(
        tmp_path / "paper" / "figures" / "method.review.json",
        _valid_image_review_payload(tmp_path, "paper/figures/method.png"),
    )
    _write_json(
        tmp_path / "paper" / "figures" / "method.provenance.json",
        {
            "generator": "codex-image2",
            "model": "codex-image2",
            "prompt_path": "paper/figures/method.prompt.txt",
            "output_path": "paper/figures/method.png",
            "output_sha256": hashlib.sha256((tmp_path / "paper" / "figures" / "method.png").read_bytes()).hexdigest(),
            "requested_size": "1536x1024",
            "width": 1536,
            "height": 1024,
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
                    "model": "codex-image2",
                    "prompt_path": "paper/figures/method.prompt.txt",
                    "output_path": "paper/figures/method.png",
                    "generation_provenance_path": "paper/figures/method.provenance.json",
                    "review_path": "paper/figures/method.review.json",
                    "requested_size": "1536x1024",
                }
            ]
        },
    )

    codes = {issue.code for issue in validate_image2_figures(tmp_path)}

    assert "missing_image2_sidecar_path" in codes
    assert "missing_image2_inspect_path" in codes


def test_image2_figures_reject_manual_only_review(tmp_path: Path) -> None:
    _write_valid_image2_figures(tmp_path)
    _write_json(
        tmp_path / "paper" / "figures" / "method.review.json",
        {
            "review_method": "manual visual check",
            "score_1_to_5": 4.5,
            "keep_or_regenerate": "keep",
        },
    )

    codes = {issue.code for issue in validate_image2_figures(tmp_path)}

    assert "manual_image_review_not_allowed" in codes


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


def test_image2_figures_reject_any_non_data_body_figure_not_image2(tmp_path: Path) -> None:
    _write_valid_image2_figures(tmp_path)
    _write(tmp_path / "paper" / "figures" / "trajectory_example.pdf", "%PDF-1.4\n")
    _write_main_tex_with_figures(
        tmp_path,
        [
            (
                "figures/method.png",
                "fig:method",
                "Overview of our method as an executable policy card.",
            ),
            (
                "figures/trajectory_example.pdf",
                "fig:trajectory-example",
                "Qualitative trajectory example showing the candidate selection failure mode.",
            ),
        ],
    )

    codes = {issue.code for issue in validate_image2_figures(tmp_path)}

    assert "non_data_body_figure_not_image2" in codes


def test_image2_figures_reject_non_data_manifest_entry_not_image2(tmp_path: Path) -> None:
    _write_valid_image2_figures(tmp_path)
    manifest_path = tmp_path / "paper" / "figures" / "IMAGE2_FIGURES.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["figures"].append(
        {
            "figure_id": "qualitative-example",
            "figure_type": "qualitative_example",
            "source": "script",
            "generator": "local-script",
            "output_path": "paper/figures/trajectory_example.pdf",
        }
    )
    _write_json(manifest_path, payload)

    codes = {issue.code for issue in validate_image2_figures(tmp_path)}

    assert "non_data_figure_not_image2" in codes


def test_image2_figures_reject_sidecar_prompt_text_hash_mismatch(tmp_path: Path) -> None:
    _write_valid_image2_figures(tmp_path)
    sidecar_path = tmp_path / "paper" / "figures" / "method.provenance.json"
    payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    payload["prompt"] = "older prompt text that was not used by the current prompt file"
    _write_json(sidecar_path, payload)

    codes = {issue.code for issue in validate_image2_figures(tmp_path)}

    assert "mismatched_image2_sidecar_prompt_text_sha256" in codes


def test_image2_figures_accept_prompt_file_trailing_newline_with_stripped_sidecar(
    tmp_path: Path,
) -> None:
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
    prompt_path = tmp_path / "paper" / "figures" / "method.prompt.txt"
    prompt_text = prompt_path.read_text(encoding="utf-8")
    if not prompt_text.endswith("\n"):
        _write(prompt_path, prompt_text + "\n")
        prompt_text = prompt_path.read_text(encoding="utf-8")
    stripped_prompt = prompt_text.strip()
    sidecar_path = tmp_path / "paper" / "figures" / "method.provenance.json"
    payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    payload["prompt"] = stripped_prompt
    payload["prompt_sha256"] = hashlib.sha256(stripped_prompt.encode("utf-8")).hexdigest()
    _write_json(sidecar_path, payload)

    assert validate_image2_figures(tmp_path) == []


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


def test_image2_figures_accept_relative_project_root(tmp_path: Path) -> None:
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
    previous = Path.cwd()
    try:
        os.chdir(tmp_path)
        assert validate_image2_figures(Path(".")) == []
    finally:
        os.chdir(previous)


def test_image2_figures_reject_cropped_or_resaved_image2_output(tmp_path: Path) -> None:
    _write(tmp_path / "paper" / "figures" / "method.prompt.txt", _valid_image2_teaser_prompt())
    _write_bytes(tmp_path / "paper" / "figures" / "method.png", _png_bytes(1343, 564))
    _write_json(
        tmp_path / "paper" / "figures" / "method.review.json",
        {
            **_valid_image_review_payload(tmp_path, "paper/figures/method.png"),
            "image": {
                "image": "paper/figures/method.png",
                "sha256": hashlib.sha256((tmp_path / "paper" / "figures" / "method.png").read_bytes()).hexdigest(),
                "width": 1536,
                "height": 1024,
            },
        },
    )
    prompt_text = (tmp_path / "paper" / "figures" / "method.prompt.txt").read_text(encoding="utf-8")
    output_sha = hashlib.sha256((tmp_path / "paper" / "figures" / "method.png").read_bytes()).hexdigest()
    _write_json(
        tmp_path / "paper" / "figures" / "method.sidecar.json",
        {
            "model": "gpt-image-2",
            "generator": "codex-image2",
            "tool": "argus_skill.tools.image_tool.generate_image",
            "created_at_unix": 1700000000,
            "prompt_path": "paper/figures/method.prompt.txt",
            "prompt_sha256": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
            "output_path": "paper/figures/method.png",
            "output_sha256": output_sha,
            "image": {"sha256": output_sha, "width": 1536, "height": 1024},
            "requested_size": "1536x1024",
            "api": {"provider": "openai-compatible", "wire_api": "images", "endpoint": "/images/generations"},
        },
    )
    _write_image2_inspect(tmp_path, "paper/figures/method.png", "paper/figures/method.inspect.json")
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
                    "inspect_path": "paper/figures/method.inspect.json",
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
    _write(
        tmp_path / "paper" / "figures" / "method_overview.prompt.txt",
        _valid_image2_teaser_prompt(),
    )
    _write_bytes(tmp_path / "paper" / "figures" / "method_overview.png", _png_bytes(1536, 1024))
    _write_json(
        tmp_path / "paper" / "figures" / "method_overview.review.json",
        _valid_image_review_payload(tmp_path, "paper/figures/method_overview.png"),
    )
    _write_image2_provenance(
        tmp_path,
        "paper/figures/method_overview.prompt.txt",
        "paper/figures/method_overview.png",
        "paper/figures/method_overview.provenance.json",
    )
    _write_image2_inspect(
        tmp_path,
        "paper/figures/method_overview.png",
        "paper/figures/method_overview.inspect.json",
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
                    "sidecar_path": "paper/figures/method_overview.provenance.json",
                    "inspect_path": "paper/figures/method_overview.inspect.json",
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


def test_emnlp_paper_contract_requires_structure_conformance(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    (tmp_path / "paper" / "style_ref" / "STRUCTURE_CONFORMANCE.md").unlink()
    (tmp_path / "paper" / "style_ref" / "STRUCTURE_CONFORMANCE.json").unlink()

    codes = {issue.code for issue in validate_emnlp_paper_contract(tmp_path)}

    assert "missing_style_structure_conformance" in codes
    assert "missing_style_structure_conformance_json" in codes


def test_emnlp_paper_contract_rejects_fake_acl_template(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write(
        tmp_path / "paper" / "acl.sty",
        "% minimal ACL compatibility layer\n\\ProvidesPackage{acl}\n",
    )

    codes = {issue.code for issue in validate_emnlp_paper_contract(tmp_path)}

    assert "fake_or_minimal_acl_style" in codes


def test_emnlp_paper_contract_rejects_fake_acl_log(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write(tmp_path / "paper" / "main.log", "Loaded minimal ACL compatibility layer.\n")

    codes = {issue.code for issue in validate_emnlp_paper_contract(tmp_path)}

    assert "fake_acl_style_loaded" in codes


def test_emnlp_paper_contract_rejects_unmapped_filler_section(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    main_path = tmp_path / "paper" / "main.tex"
    text = main_path.read_text(encoding="utf-8")
    text = text.replace(
        "\\section{Results}\nTable~\\ref{tab:main} summarizes the main result.",
        "\\section{Protocol Notes}\nTable~\\ref{tab:main} summarizes the main result.",
    )
    _write(main_path, text)

    codes = {issue.code for issue in validate_emnlp_paper_contract(tmp_path)}

    assert "unmapped_final_section" in codes
    assert "stale_structure_section_mapping" in codes


def test_emnlp_paper_contract_allows_justified_paper_specific_section(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    main_path = tmp_path / "paper" / "main.tex"
    text = main_path.read_text(encoding="utf-8")
    text = text.replace(
        "\\section{Results}\nTable~\\ref{tab:main} summarizes the main result.",
        "\\section{Transfer Diagnostics}\nTable~\\ref{tab:main} summarizes the main result.",
    )
    _write(main_path, text)

    path = tmp_path / "paper" / "style_ref" / "STRUCTURE_CONFORMANCE.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for mapping in payload["section_mappings"]:
        if mapping["section"] == "Results":
            mapping["section"] = "Transfer Diagnostics"
            mapping["maps_to_exemplar_phase"] = "results"
            mapping["deviation_rationale"] = (
                "This paper reports transfer diagnostics as its main result section because "
                "the local benchmark measures cross-family transfer rather than a single "
                "task score; the section still follows the exemplar result phase."
            )
    _write_json(path, payload)

    codes = {issue.code for issue in validate_emnlp_paper_contract(tmp_path)}

    assert "unjustified_nonstandard_section" not in codes
    assert "unmapped_final_section" not in codes
    assert "stale_structure_section_mapping" not in codes


def test_emnlp_paper_contract_rejects_shallow_core_sections(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    main_path = tmp_path / "paper" / "main.tex"
    text = main_path.read_text(encoding="utf-8")
    text = re.sub(
        r"\\begin\{abstract\}.*?\\end\{abstract\}",
        lambda _match: "\\begin{abstract}A short validator-shaped abstract.\\end{abstract}",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"\\section\{Introduction\}.*?\\section\{Related Work\}",
        lambda _match: "\\section{Introduction}\nThis is too short.\n\\section{Related Work}",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"\\section\{Method\}.*?\\section\{Experimental Setup\}",
        lambda _match: "\\section{Method}\nThe method is too short.\n\\section{Experimental Setup}",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"\\section\{Experimental Setup\}.*?\\section\{Results\}",
        lambda _match: "\\section{Experimental Setup}\nThe setup is too short.\n\\section{Results}",
        text,
        flags=re.DOTALL,
    )
    _write(main_path, text)

    codes = {issue.code for issue in validate_emnlp_paper_contract(tmp_path)}

    assert {
        "abstract_too_short",
        "introduction_too_short",
        "method_section_too_short",
        "experimental_setup_too_short",
    }.issubset(codes)


def test_latex_contract_word_count_preserves_escaped_percent() -> None:
    prefix = " ".join(f"before{i}" for i in range(80))
    suffix = " ".join(f"after{i}" for i in range(100))
    tex_text = (
        "\\begin{abstract}\n"
        f"{prefix} The score rises from 0.82\\% to 6.15\\% in evaluation. {suffix}\n"
        "\\end{abstract}\n"
    )

    assert _latex_environment_word_count(tex_text, "abstract") >= 180


def test_emnlp_paper_contract_rejects_uncited_and_formulaic_intro(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    main_path = tmp_path / "paper" / "main.tex"
    text = main_path.read_text(encoding="utf-8")
    text = re.sub(r"\\citep\{[^{}]+\}", "prior studies", text, count=1)
    repeated_template = " ".join(
        "The section explains storage rather than admission."
        for _ in range(7)
    )
    text = text.replace(
        "\\section{Related Work}",
        f"{repeated_template}\n\\section{{Related Work}}",
    )
    _write(main_path, text)

    codes = {issue.code for issue in validate_emnlp_paper_contract(tmp_path)}

    assert "introduction_missing_literature_hooks" in codes
    assert "contrastive_template_overuse" in codes


def test_emnlp_paper_contract_requires_model_identifier_and_settings(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    main_path = tmp_path / "paper" / "main.tex"
    text = main_path.read_text(encoding="utf-8")
    text = text.replace("gpt-5-mini", "hosted model")
    text = text.replace("hosted gpt-5-mini agent", "hosted agent")
    text = text.replace("gpt-5-mini backend", "hosted backend")
    text = text.replace("response cap", "response limit")
    text = text.replace("temperature 0.0, top_p 1.0, max_tokens 512, ", "")
    text = text.replace("temperature 0.0, top_p 1.0, max_tokens 512, cache ", "cache ")
    text = text.replace("a fixed per-episode token budget, ", "")
    text = text.replace(" under a fixed token budget", " under identical limits")
    text = text.replace("cache keys", "memo keys")
    text = text.replace("cache fingerprint", "request fingerprint")
    text = text.replace("cache policy", "request policy")
    text = text.replace("and a three-retry timeout policy", "and identical limits")
    text = text.replace("route, temperature, response limit, and retry policy", "route and identical limits")
    text = text.replace("cache enabled, fixed seeds where sampling appears in task selection, ", "")
    text = re.sub(r"\bseeds?\b", "ordering", text)
    text = re.sub(r"\bbudget\b", "limit", text)
    _write(main_path, text)

    codes = {issue.code for issue in validate_emnlp_paper_contract(tmp_path)}

    assert "missing_experiment_model_identifier" in codes
    assert "missing_experiment_model_settings" in codes


def test_emnlp_paper_contract_rejects_stale_result_numbers(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_json(
        tmp_path / "experiments" / "full_swe_verified_240" / "summary.json",
        [
            {"method": "repair_memo_gate", "correct": 127, "episodes": 240},
            {"method": "no_verifier", "correct": 67, "episodes": 240},
        ],
    )
    main_path = tmp_path / "paper" / "main.tex"
    text = main_path.read_text(encoding="utf-8").replace(
        "Across 240 scored episodes, SkillGuard improves verified completion by 8 points "
        "over the strongest runnable baseline while reducing unsupported memory admissions.",
        (
            "The repair-memo method reaches 187/240 success versus 127/240 for "
            "the no-verifier control while reducing unsupported memory admissions."
        ),
    )
    _write(main_path, text)

    codes = {issue.code for issue in validate_emnlp_paper_contract(tmp_path)}

    assert "unsupported_result_ratio" in codes
    assert "method_result_number_mismatch" in codes


def test_emnlp_paper_contract_rejects_hosted_model_no_external_contradiction(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_json(
        tmp_path / "experiments" / "full_swe_verified_240" / "manifest.json",
        {
            "methods": ["repair_memo_gate", "hosted_extract"],
            "model_metadata": [
                {"method": "hosted_extract", "model": "gpt-5-mini", "route_name": "engineer"}
            ],
            "status": "done",
        },
    )
    main_path = tmp_path / "paper" / "main.tex"
    text = main_path.read_text(encoding="utf-8").replace(
        "We evaluate SkillGuard with gpt-5-mini as a hosted no-GPU backbone, "
        "deterministic decoding, a fixed response cap, cached prompts, and a shared "
        "request budget across three real benchmark sources: ToolBench, WebArena, and GAIA.",
        (
            "We evaluate SkillGuard in a deterministic harness that makes no "
            "external LLM/model calls while sharing one request budget across "
            "three real benchmark sources: ToolBench, WebArena, and GAIA."
        ),
    )
    _write(main_path, text)

    codes = {issue.code for issue in validate_emnlp_paper_contract(tmp_path)}

    assert "hosted_model_contradicts_no_external_model_claim" in codes


def test_emnlp_paper_contract_rejects_planned_only_benchmark_sources(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write(
        tmp_path / "experiments" / "BENCHMARK_PROVENANCE.md",
        "\n".join(
            [
                "# Benchmark Provenance",
                "",
                "Selected benchmark sources:",
                "| Name | URL/repo | Paper/citation | Version/date | Task count | Split/filtering | License/access | Capability | Rationale | Alternatives |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                "| SWE-bench Verified | https://github.com/swe-bench/SWE-bench | SWE-bench Verified | 2024 | 240 completed scored tasks | verified split | public benchmark release | code repair | main completed source | SWE-bench+ |",
                "| SWE-bench Multimodal | https://huggingface.co/datasets/SWE-bench/SWE-bench_Multimodal | SWE-bench Multimodal | 2024 | 80 diagnostic tasks planned | planned slice | public benchmark release | visual bug fixing | transfer diagnostic | SWE-bench |",
                "| RepoBench-P | https://github.com/Leolty/repobench | RepoBench | 2024 | 80 diagnostic tasks planned | planned slice | public benchmark release | repo completion | transfer diagnostic | CodeSearchNet |",
            ]
        )
        + "\n",
    )

    codes = {issue.code for issue in validate_emnlp_paper_contract(tmp_path)}

    assert "insufficient_executed_benchmark_sources" in codes


def test_emnlp_paper_contract_rejects_same_family_benchmark_components(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write(
        tmp_path / "experiments" / "BENCHMARK_PROVENANCE.md",
        "\n".join(
            [
                "# Benchmark Provenance",
                "",
                "Selected benchmark sources:",
                "| Name | URL/repo | Paper/citation | Version/date | Task count | Split/filtering | License/access | Capability | Rationale | Alternatives |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                "| SWE-bench Verified | https://github.com/swe-bench/SWE-bench | SWE-bench Verified | 2024 | 100 completed scored tasks | verified split | public benchmark release | code repair | main completed source | SWE-bench+ |",
                "| SWE-bench Lite | https://github.com/swe-bench/SWE-bench | SWE-bench Lite | 2024 | 100 completed scored tasks | lite split | public benchmark release | code repair | same-family slice | SWE-bench |",
                "| SWE-bench Multimodal | https://huggingface.co/datasets/SWE-bench/SWE-bench_Multimodal | SWE-bench Multimodal | 2024 | 100 completed scored tasks | multimodal split | public benchmark release | visual bug fixing | same-family slice | SWE-bench |",
            ]
        )
        + "\n",
    )

    codes = {issue.code for issue in validate_emnlp_paper_contract(tmp_path)}

    assert "insufficient_executed_benchmark_sources" in codes


def test_emnlp_paper_contract_rejects_significance_table_after_ethics(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    main_path = tmp_path / "paper" / "main.tex"
    text = main_path.read_text(encoding="utf-8")
    significance_table = (
        "\\begin{table}[t]\n"
        "\\centering\n"
        "\\begin{tabular}{lc}\\toprule Test & p-value \\\\ \\midrule Paired McNemar & 0.01 \\\\ \\bottomrule\\end{tabular}\n"
        "\\caption{Paired McNemar significance remains below p=0.01 across 120 tasks.}\n"
        "\\label{tab:ethics-significance}\n"
        "\\end{table}\n"
    )
    text = text.replace(
        "\\section*{Ethical Considerations}\n"
        "The work uses synthetic tasks and reports safety-relevant limitations.\n"
        "\\bibliography{references}",
        "\\section*{Ethical Considerations}\n"
        "The work uses synthetic tasks and reports safety-relevant limitations.\n"
        + significance_table
        + "\\bibliography{references}",
    )
    _write(main_path, text)

    codes = {issue.code for issue in validate_emnlp_paper_contract(tmp_path)}

    assert "significance_table_after_ethics" in codes


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


def test_paper_format_rejects_newer_root_latex_outputs(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_bytes(tmp_path / "main.pdf", _minimal_pdf_bytes(["newer root pdf"]))
    _write(tmp_path / "main.log", "newer root log\n")
    newer_time = (tmp_path / "paper" / "main.pdf").stat().st_mtime + 10
    os.utime(tmp_path / "main.pdf", (newer_time, newer_time))
    os.utime(tmp_path / "main.log", (newer_time, newer_time))

    codes = {issue.code for issue in validate_paper_format(tmp_path)}

    assert "noncanonical_latex_output_newer" in codes


def test_research_md_format_preflight_accepts_complete_review_paper(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)

    assert validate_research_md_format_preflight(tmp_path) == []


def test_research_md_format_preflight_rejects_forced_break_before_conclusion(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    main_path = tmp_path / "paper" / "main.tex"
    main_text = main_path.read_text(encoding="utf-8").replace(
        "\\section{Conclusion}",
        "\\clearpage\n\\section{Conclusion}",
    )
    _write(main_path, main_text)

    issues = validate_research_md_format_preflight(tmp_path)

    assert "forced_page_break_before_conclusion" in {issue.code for issue in issues}


def test_research_md_manual_page_breaks_accept_clean_reference_break() -> None:
    issues = _validate_research_md_manual_page_breaks(
        "\\section{Conclusion}\nBody conclusion.\n\\clearpage\n\\bibliography{references}"
    )

    assert issues == []


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
    main_path = tmp_path / "paper" / "main.tex"
    main_text = main_path.read_text(encoding="utf-8")
    main_text = re.sub(r"\\citep\{[^{}]+\}", r"\\citep{verifiedref01}", main_text)
    _write(main_path, main_text)

    issues = validate_research_md_format_preflight(tmp_path)

    assert "insufficient_unique_citations" in {issue.code for issue in issues}


def test_research_md_format_preflight_rejects_placeholder_bibtex_authors(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    bib_path = tmp_path / "paper" / "references.bib"
    bib_text = bib_path.read_text(encoding="utf-8").replace(
        "author = {Author, Test 1}",
        "author = {Yucheng Chen and others}",
        1,
    )
    _write(bib_path, bib_text)

    issues = validate_research_md_format_preflight(tmp_path)

    assert "placeholder_bibtex_author_others" in {issue.code for issue in issues}


def test_research_md_format_preflight_rejects_numeric_acl_citations(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    main_path = tmp_path / "paper" / "main.tex"
    main_text = main_path.read_text(encoding="utf-8").replace(
        "\\usepackage[review]{acl}\n",
        "\\usepackage[review]{acl}\n\\setcitestyle{numbers,square}\n",
    )
    _write(main_path, main_text)

    issues = validate_research_md_format_preflight(tmp_path)

    assert "numeric_acl_citation_style" in {issue.code for issue in issues}


def test_research_md_format_preflight_rejects_missing_bibtex_author_metadata(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    bib_path = tmp_path / "paper" / "references.bib"
    bib_text = re.sub(r"\n  author = \{Author, Test 1\},", "", bib_path.read_text(encoding="utf-8"), count=1)
    _write(bib_path, bib_text)

    issues = validate_research_md_format_preflight(tmp_path)

    assert "missing_bibtex_author_metadata" in {issue.code for issue in issues}


def test_research_md_format_preflight_rejects_starter_key_title_mismatch(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    bib_path = tmp_path / "paper" / "references.bib"
    bib_text = bib_path.read_text(encoding="utf-8")
    bib_text += "\n".join(
        [
            "",
            "@misc{amem2025,",
            "  title = {23.8-GHz Acoustic Filter in Periodically Poled Piezoelectric Film Lithium Niobate},",
            "  author = {Cho, Sinwoo and Barrera, Omar},",
            "  year = {2024}",
            "}",
            "",
        ]
    )
    _write(bib_path, bib_text)

    issues = validate_research_md_format_preflight(tmp_path)

    assert "bibtex_key_title_mismatch" in {issue.code for issue in issues}


def test_research_md_format_preflight_rejects_rendered_placeholder_authors(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write(
        tmp_path / "paper" / "main.bbl",
        "\\bibitem[Chen and 1 others(2024)]{badref} Chen and 1 others. Title.\n",
    )

    issues = validate_research_md_format_preflight(tmp_path)

    assert "rendered_placeholder_reference_authors" in {issue.code for issue in issues}


def test_research_md_format_preflight_rejects_citation_dumping(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    keys = _valid_reference_keys()
    main_path = tmp_path / "paper" / "main.tex"
    main_text = re.sub(
        r"Prior benchmark work motivates.*?reporting protocol \\citep\{[^{}]+\}\.",
        lambda _: f"Prior work is summarized in one place \\citep{{{','.join(keys[:30])}}}.",
        main_path.read_text(encoding="utf-8"),
        flags=re.S,
    )
    _write(main_path, main_text)

    codes = {issue.code for issue in validate_research_md_format_preflight(tmp_path)}

    assert "citation_command_dumping" in codes
    assert "citation_paragraph_dumping" in codes


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


def test_research_md_pdf_text_rejects_line_numbered_early_references() -> None:
    issues = _validate_research_md_pdf_text(
        [
            "001 Title\nIntroduction\n",
            "082 Related Work\n",
            "155 Method\n",
            "238 Experimental Setup\n",
            "312 Main Results\n",
            "401 Analysis\nConclusion\nLimitations and Ethical Considerations\n",
            "499 References\nPaper A\nPaper B\n",
            "556 More references\nPaper C\nPaper D\n",
        ]
    )

    codes = {issue.code for issue in issues}
    assert "references_before_full_body" in codes


def test_research_md_pdf_text_rejects_two_column_line_numbered_references() -> None:
    issues = _validate_research_md_pdf_text(
        [
            "001 Title\nIntroduction\n",
            "082 Related Work\n",
            "155 Method\n",
            "238 Experimental Setup\n",
            "312 Main Results\n",
            "401 Analysis\nConclusion\nLimitations and Ethical Considerations\n",
            "499 References        Hyungjoo Chae, Namyoung Kim, and Minju Gwak.\n"
            "500 More reference text\n",
            "556 More references\nPaper C\nPaper D\n",
        ]
    )

    codes = {issue.code for issue in issues}
    assert "references_before_full_body" in codes


def test_research_md_pdf_text_rejects_right_column_references_after_body_text() -> None:
    issues = _validate_research_md_pdf_text(
        [
            "001 Title\nIntroduction\n",
            "082 Related Work\n",
            "155 Method\n",
            "238 Experimental Setup\n",
            "312 Main Results\n",
            "401 Analysis\nMore body text\n",
            "545 where the decision point is insufficient.                  References      593\n"
            "546 7 Conclusion                                                Minju Gwak and others\n"
            "567 8 Limitations and Ethical Considerations\n",
            "More references\nPaper C\nPaper D\n",
        ]
    )

    codes = {issue.code for issue in issues}
    assert "references_before_full_body" in codes
    assert "references_share_page_with_body_sections" in codes


def test_research_md_pdf_text_allows_page_seven_conclusion_when_body_continues() -> None:
    issues = _validate_research_md_pdf_text(
        [
            "Title\nIntroduction\n",
            "Related Work\n",
            "Method\n",
            "Experimental Setup\n",
            "Main Results\n",
            "Analysis\n",
            "Conclusion\nLimitations and Ethical Considerations\n",
            "Body tail\nMore limitations and ethics\n",
            "References\nPaper A\nPaper B\n",
            "More References\nPaper C\nPaper D\n",
        ]
    )

    codes = {issue.code for issue in issues}
    assert "rendered_main_body_underfilled" not in codes
    assert "references_before_full_body" not in codes


def test_research_md_pdf_text_allows_uncapped_references_and_appendix_after_page_8() -> None:
    issues = _validate_research_md_pdf_text(
        [
            "Title\nIntroduction\n",
            "Related Work\n",
            "Method\n",
            "Experimental Setup\n",
            "Main Results\n",
            "Analysis\n",
            "Failure Cases\n",
            "Conclusion\nLimitations and Ethical Considerations\n",
            "References\nPaper A\nPaper B\n",
            "More References\nPaper C\nPaper D\n",
            "Appendix\nReproducibility\n",
            "Extra Appendix\nArtifact notes\n",
            "Supplementary Appendix\nMore artifact notes\n",
        ]
    )

    codes = {issue.code for issue in issues}
    assert "rendered_pdf_exceeds_total_page_limit" not in codes
    assert "appendix_before_page_9" not in codes


def test_research_md_pdf_text_ignores_pdftotext_trailing_form_feed_page() -> None:
    issues = _validate_research_md_pdf_text(
        [
            "Title\nIntroduction\n",
            "Related Work\n",
            "Method\n",
            "Experimental Setup\n",
            "Main Results\n",
            "Analysis\n",
            "Failure Cases\n",
            "Conclusion\nLimitations and Ethical Considerations\n",
            "References\nPaper A\nPaper B\n",
            "More References\nPaper C\nPaper D\n",
            "Appendix\nReproducibility\n",
            "Extra Appendix\nArtifact notes\n",
            "",
        ]
    )

    assert "rendered_pdf_exceeds_total_page_limit" not in {issue.code for issue in issues}


def test_rendered_pdf_page_budget_allows_pdfinfo_total_over_twelve(tmp_path: Path) -> None:
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    (paper_dir / "main.pdf").write_bytes(_minimal_pdf_bytes([f"Page {i}" for i in range(13)]))

    issues = _validate_rendered_pdf_page_budget(tmp_path, 8.0)

    assert "rendered_pdf_exceeds_total_page_limit" not in {issue.code for issue in issues}


def test_research_md_pdf_text_rejects_appendix_before_page_9() -> None:
    issues = _validate_research_md_pdf_text(
        [
            "Title\nAbstract\nIntroduction\n",
            "Related Work\n",
            "Method\n",
            "Experimental Setup\n",
            "Results\n",
            "Analysis\n",
            "Conclusion\n",
            "Appendix\nReproducibility\n",
            "References\nPaper A\nPaper B\n",
            "References\nPaper C\nPaper D\n",
        ]
    )

    assert "appendix_before_page_9" in {issue.code for issue in issues}


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


def test_layout_review_rejects_forged_generator(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_layout_review(tmp_path)
    path = tmp_path / "paper" / "LAYOUT_REVIEW.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["generated_by"] = "code.make_paper"
    _write_json(path, payload)

    codes = {issue.code for issue in validate_layout_review(tmp_path)}

    assert "layout_review_not_skill_generated" in codes


def test_layout_review_rejects_missing_vision_payload(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_layout_review(tmp_path)
    path = tmp_path / "paper" / "LAYOUT_REVIEW.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("vision_review")
    _write_json(path, payload)

    codes = {issue.code for issue in validate_layout_review(tmp_path)}

    assert "missing_layout_review_vision_payload" in codes


def test_layout_review_rejects_pass_contradicting_vision_payload(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_layout_review(tmp_path)
    path = tmp_path / "paper" / "LAYOUT_REVIEW.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["verdict"] = "PASS"
    payload["needs_revision"] = False
    payload["issues"] = []
    payload["revision_directives"] = []
    payload["vision_review"]["pass_or_revise"] = "revise"
    payload["vision_review"]["major_issues"] = [
        {"issue": "page 6 lower half is visibly underfilled"}
    ]
    payload["vision_review"]["revision_directives"] = [
        {
            "action": "rebalance_columns",
            "target": "page 6",
            "rationale": "vision reviewer requested source-level repair",
        }
    ]
    _write_json(path, payload)

    codes = {issue.code for issue in validate_layout_review(tmp_path)}

    assert "pass_layout_review_with_vision_revise" in codes
    assert "pass_layout_review_with_vision_major_issues" in codes
    assert "pass_layout_review_with_vision_revision_directives" in codes


def test_layout_review_rejects_incomplete_page_snapshot_coverage(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_layout_review(tmp_path)
    path = tmp_path / "paper" / "LAYOUT_REVIEW.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["page_snapshots"] = payload["page_snapshots"][:1]
    payload["vision_review"]["reviewed_pages"] = [1]
    _write_json(path, payload)

    codes = {issue.code for issue in validate_layout_review(tmp_path)}

    assert "incomplete_layout_review_snapshot_coverage" in codes


def test_layout_review_classifies_extra_snapshot_pages_as_stale_not_incomplete(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_layout_review(tmp_path)
    path = tmp_path / "paper" / "LAYOUT_REVIEW.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    page_path = tmp_path / "paper" / "layout_review" / "pages" / "page-11.png"
    _write_bytes(page_path, _png_bytes(612, 792))
    payload["page_snapshots"].append({
        "page": 11,
        "path": page_path.relative_to(tmp_path).as_posix(),
        "sha256": _sha256(page_path),
    })
    payload["vision_review"]["reviewed_pages"] = list(range(1, 12))
    _write_json(path, payload)

    codes = {issue.code for issue in validate_layout_review(tmp_path)}

    assert "stale_layout_review_snapshot_coverage" in codes
    assert "incomplete_layout_review_snapshot_coverage" not in codes


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


def test_paper_infrastructure_review_accepts_model_pass_with_fresh_sources(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_paper_infrastructure_review(tmp_path)

    assert validate_paper_infrastructure_review(tmp_path) == []


def test_paper_infrastructure_review_rejects_missing_review(tmp_path: Path) -> None:
    issues = validate_paper_infrastructure_review(tmp_path)

    assert "missing_paper_infrastructure_review" in {issue.code for issue in issues}


def test_paper_infrastructure_review_rejects_model_reported_leak(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_paper_infrastructure_review(
        tmp_path,
        score=3.5,
        verdict="FAIL",
        needs_revision=True,
        leak_free=False,
        major_issues=[
            {
                "issue_code": "local_device_config_in_body",
                "source_path": "paper/main.tex",
                "quote": "CUDA_VISIBLE_DEVICES=6",
                "required_rewrite": "remove the local device assignment from prose",
            }
        ],
        revision_directives=[
            {
                "action": "remove_infrastructure_leak",
                "target": "paper/main.tex",
                "rationale": "local execution details are not paper-facing method facts",
            }
        ],
    )

    codes = {issue.code for issue in validate_paper_infrastructure_review(tmp_path)}
    assert "paper_infrastructure_review_not_pass" in codes
    assert "paper_infrastructure_review_reports_leak" in codes
    assert "paper_infrastructure_review_has_major_issues" in codes


def test_paper_infrastructure_review_rejects_stale_source_hash(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_paper_infrastructure_review(tmp_path)
    _write(
        tmp_path / "paper" / "main.tex",
        "\\documentclass{article}\nchanged infrastructure source\n",
    )

    codes = {issue.code for issue in validate_paper_infrastructure_review(tmp_path)}
    assert "stale_paper_infrastructure_review_source" in codes


def test_paper_infrastructure_review_rejects_pass_contradicting_model_payload(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_paper_infrastructure_review(tmp_path)
    path = tmp_path / "paper" / "PAPER_INFRASTRUCTURE_REVIEW.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["verdict"] = "PASS"
    payload["needs_revision"] = False
    payload["leak_free"] = True
    payload["revision_directives"] = []
    payload["model_review"]["pass_or_revise"] = "revise"
    payload["model_review"]["major_issues"] = [
        {"issue_code": "local_cache_path", "quote": "/root/.cache/huggingface"}
    ]
    payload["model_review"]["revision_directives"] = [
        {
            "action": "remove_infrastructure_leak",
            "target": "paper/main.tex",
            "rationale": "cache path appears in prose",
        }
    ]
    _write_json(path, payload)

    codes = {issue.code for issue in validate_paper_infrastructure_review(tmp_path)}
    assert "pass_paper_infrastructure_review_with_model_revise" in codes
    assert "pass_paper_infrastructure_review_with_model_major_issues" in codes
    assert "pass_paper_infrastructure_review_with_model_revision_directives" in codes


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


def test_academic_language_review_rejects_forged_generator(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_academic_language_review(tmp_path)
    path = tmp_path / "paper" / "ACADEMIC_LANGUAGE_REVIEW.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["generated_by"] = "code.make_paper"
    _write_json(path, payload)

    codes = {issue.code for issue in validate_academic_language_review(tmp_path)}

    assert "academic_language_review_not_skill_generated" in codes


def test_academic_language_review_rejects_missing_model_payload(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_academic_language_review(tmp_path)
    path = tmp_path / "paper" / "ACADEMIC_LANGUAGE_REVIEW.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("model_review")
    _write_json(path, payload)

    codes = {issue.code for issue in validate_academic_language_review(tmp_path)}

    assert "missing_academic_language_model_payload" in codes


def test_academic_language_review_rejects_pass_contradicting_model_payload(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_academic_language_review(tmp_path)
    path = tmp_path / "paper" / "ACADEMIC_LANGUAGE_REVIEW.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["verdict"] = "PASS"
    payload["needs_revision"] = False
    payload["issues"] = []
    payload["revision_directives"] = []
    payload["model_review"]["pass_or_revise"] = "revise"
    payload["model_review"]["major_issues"] = [
        {"issue": "claim framing still overreaches the evidence"}
    ]
    payload["model_review"]["revision_directives"] = [
        {
            "action": "calibrate_claim",
            "target": "paper/main.tex",
            "rationale": "model reviewer requested a substantive revision",
        }
    ]
    _write_json(path, payload)

    codes = {issue.code for issue in validate_academic_language_review(tmp_path)}

    assert "pass_academic_language_review_with_model_revise" in codes
    assert "pass_academic_language_review_with_model_major_issues" in codes
    assert "pass_academic_language_review_with_model_revision_directives" in codes


def test_academic_language_review_rejects_boilerplate_evidence_quote(tmp_path: Path) -> None:
    _write_valid_paper_draft_report(tmp_path)
    _write_valid_academic_language_review(tmp_path)
    path = tmp_path / "paper" / "ACADEMIC_LANGUAGE_REVIEW.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["evidence_spans"][0]["quote"] = "\\documentclass[11pt]{article}"
    payload["model_review"]["evidence_spans"][0]["quote"] = "\\documentclass[11pt]{article}"
    _write_json(path, payload)

    codes = {issue.code for issue in validate_academic_language_review(tmp_path)}

    assert "academic_language_evidence_boilerplate_quote" in codes


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
            "A complete EMNLP-style long paper studies how evidence-calibrated skill",
            "Large language models have achieved remarkable success. We propose SkillCycle. This paper studies how evidence-calibrated skill",
        ),
    )
    _write_valid_academic_language_review(tmp_path)

    codes = {issue.code for issue in validate_academic_language_review(tmp_path)}
    assert "academic_language_generic_llm_success_opening" in codes


def test_academic_language_review_rejects_missing_method_system_basics(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    text = (tmp_path / "paper" / "main.tex").read_text(encoding="utf-8")
    text = re.sub(
        r"\\section\{Method\}.*?\\section\{Experimental Setup\}",
        lambda _match: "\\section{Method}\nThe method improves the reported result.\n\\section{Experimental Setup}",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"\\section\{Experimental Setup\}.*?\\section\{Results\}",
        lambda _match: "\\section{Experimental Setup}\nWe report paired tests in Table~\\ref{tab:significance}.\n\\section{Results}",
        text,
        flags=re.DOTALL,
    )
    _write(
        tmp_path / "paper" / "main.tex",
        text,
    )
    _write_valid_academic_language_review(tmp_path)

    codes = {issue.code for issue in validate_academic_language_review(tmp_path)}
    assert "academic_language_missing_method_framework_or_runtime" in codes


def test_academic_language_review_rejects_missing_model_id_when_models_are_used(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    text = (tmp_path / "paper" / "main.tex").read_text(encoding="utf-8")
    _write(
        tmp_path / "paper" / "main.tex",
        text.replace("gpt-5-mini", "hosted model").replace(
            "The model produces an action or answer under the shared decoding settings.",
            "Each episode calls an external LLM at fixed temperature.",
        ),
    )
    _write_valid_academic_language_review(tmp_path)

    codes = {issue.code for issue in validate_academic_language_review(tmp_path)}
    assert "academic_language_missing_method_model_identifier" in codes


def test_academic_language_review_accepts_named_pairscorer_backend(
    tmp_path: Path,
) -> None:
    _write_valid_paper_draft_report(tmp_path)
    text = (tmp_path / "paper" / "main.tex").read_text(encoding="utf-8")
    text = text.replace("gpt-5-mini", "PairScorer")
    text = text.replace("hosted gpt-5-mini agent", "PairScorer candidate-ranking backend")
    text = text.replace(
        "The model produces an action or answer under the shared decoding settings.",
        "The PairScorer backend ranks each candidate under the shared scoring budget.",
    )
    _write(tmp_path / "paper" / "main.tex", text)
    _write_valid_academic_language_review(tmp_path)

    codes = {issue.code for issue in validate_academic_language_review(tmp_path)}

    assert "academic_language_missing_method_model_identifier" not in codes


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
        re.sub(
            r"\\begin\{abstract\}.*?\\end\{abstract\}",
            lambda _match: bad_abstract,
            text,
            flags=re.DOTALL,
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
        re.sub(
            r"\\begin\{abstract\}.*?\\end\{abstract\}",
            lambda _match: bad_abstract,
            text,
            flags=re.DOTALL,
        ),
    )

    review = generate_academic_language_review(tmp_path, review_mode="heuristic", write=False)

    codes = {issue["code"] for issue in review["issues"]}
    assert "abstract_contains_internal_evidence_comment" in codes
    assert "result_first_abstract" in codes
    assert "over_defensive_abstract" in codes
    assert review["needs_revision"] is True


def test_academic_quantified_claim_accepts_comparator_style_result() -> None:
    text = (
        "In the 240-task SWE-bench Verified slice, ReplayMemo reaches 77.9% "
        "success versus 52.9% for the no-verifier comparator under a "
        "deterministic protocol."
    )

    assert _has_quantified_claim(text)
    assert _has_quantified_claim("The method improves verified completion by 25.0 points.")
    assert not _has_quantified_claim("The method defines a benchmark protocol.")


def test_reader_facing_contribution_allows_scoped_policy_claim() -> None:
    text = (
        "This paper reports ReplayMemo on SWE-bench Verified. The policy reaches "
        "77.9% success versus 52.9% against the no-verifier baseline under a "
        "deterministic protocol, while the current ablation does not isolate "
        "family matching from duplicate rejection."
    )

    assert _has_reader_facing_contribution(text)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _png_bytes(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", width, height)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _minimal_pdf_bytes(page_texts: list[str]) -> bytes:
    objects: list[bytes] = []
    page_object_ids: list[int] = []
    next_object_id = 4
    for text in page_texts:
        page_object_id = next_object_id
        content_object_id = next_object_id + 1
        next_object_id += 2
        page_object_ids.append(page_object_id)
        content = _pdf_page_content_stream(text)
        objects.append(
            (
                f"{page_object_id} 0 obj\n"
                "<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 3 0 R >> >> "
                "/MediaBox [0 0 612 792] /Contents "
                f"{content_object_id} 0 R >>\nendobj\n"
            ).encode("ascii")
        )
        objects.append(
            (
                f"{content_object_id} 0 obj\n"
                f"<< /Length {len(content)} >>\n"
                "stream\n"
            ).encode("ascii")
            + content
            + b"\nendstream\nendobj\n"
        )

    kids = " ".join(f"{object_id} 0 R" for object_id in page_object_ids)
    header_objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        (
            f"2 0 obj\n<< /Type /Pages /Kids [{kids}] /Count {len(page_object_ids)} >>\nendobj\n"
        ).encode("ascii"),
        b"3 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    numbered_objects = header_objects + objects
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in numbered_objects:
        offsets.append(len(pdf))
        pdf.extend(obj)
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        (
            "trailer\n"
            f"<< /Size {len(offsets)} /Root 1 0 R >>\n"
            "startxref\n"
            f"{xref_offset}\n"
            "%%EOF\n"
        ).encode("ascii")
    )
    return bytes(pdf)


def _pdf_page_content_stream(text: str) -> bytes:
    lines = [_pdf_escape_text(line) for line in text.splitlines() if line.strip()]
    commands = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
    for index, line in enumerate(lines):
        if index:
            commands.append("T*")
        commands.append(f"({line}) Tj")
    commands.append("ET")
    return "\n".join(commands).encode("ascii")


def _pdf_escape_text(text: str) -> str:
    safe = "".join(char if 32 <= ord(char) < 127 else " " for char in text)
    return safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_valid_quality_calibration(root: Path) -> None:
    _write_valid_benchmark_provenance(root)
    _write_valid_model_scale_plan(root)
    _write(root / "paper" / "artifacts" / "significance.tsv", "test\tp\nmcnemar\t0.01\n")
    _write(
        root / "paper" / "artifacts" / "results_summary.tsv",
        "\n".join(
            [
                "scope\tsplit_name\tprotocol\tsuccess_rate\tjson_parse_rate\tn_tasks",
                "overall\tmain\tno_skill\t0.500\t1.000\t300",
                "overall\tmain\traw_memory\t0.610\t1.000\t300",
                "overall\tmain\treflexion\t0.850\t1.000\t300",
                "overall\tmain\tstatic_skill_lib\t0.620\t1.000\t300",
                "overall\tmain\tskillcycle\t0.920\t1.000\t300",
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
    _write_valid_benchmark_provenance(root)
    _write_valid_model_scale_plan(root)
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
    full_methods = ["no_skill", "raw_memory", "reflexion", "static_skill_lib", "skillcycle"]
    _write(
        root / "research" / "BASELINE_AND_BENCHMARK_PLAN.md",
        "Required methods: no_skill, raw_memory, reflexion, static_skill_lib, skillcycle\n",
    )
    _write_full_scale_experiment_run(root, methods=full_methods, task_count=300)
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
                "overall\tmain\tno_skill\t0.500\t1.000\t300",
                "overall\tmain\traw_memory\t0.610\t1.000\t300",
                "overall\tmain\treflexion\t0.850\t1.000\t300",
                "overall\tmain\tstatic_skill_lib\t0.620\t1.000\t300",
                "overall\tmain\tskillcycle\t0.920\t1.000\t300",
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
    _write_valid_paper_infrastructure_review(root)
    _write_valid_artifact_manifest(root)
    _write_valid_claim_graph(root)
    _write_valid_figure_table_style_guide(root)
    _write_valid_validation_priority_policy(root)
    _write_valid_artifact_freshness(root)
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
                "paper_infrastructure_review": {"verdict": "PASS"},
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


def _write_full_scale_experiment_run(
    root: Path,
    *,
    methods: list[str],
    task_count: int,
    declared_task_count: int | None = None,
) -> None:
    _write_valid_benchmark_provenance(root)
    run_dir = root / "experiments" / "run_001"
    declared_count = declared_task_count if declared_task_count is not None else task_count
    _write_json(
        run_dir / "manifest.json",
        {
            "task_count": declared_count,
            "methods": methods,
        },
    )
    _write_json(
        run_dir / "status.json",
        {
            "status": "completed",
            "task_count": declared_count,
        },
    )
    rows = []
    for method in methods:
        for index in range(task_count):
            rows.append(
                json.dumps(
                    {
                        "method": method,
                        "task_id": f"task-{index:03d}",
                        "success": index % 2 == 0,
                    }
                )
            )
    _write(run_dir / "results.jsonl", "\n".join(rows) + "\n")
    _write(run_dir / "progress.jsonl", "{}\n")


def _write_valid_benchmark_provenance(root: Path) -> None:
    _write(
        root / "experiments" / "BENCHMARK_PROVENANCE.md",
        "\n".join(
            [
                "# Benchmark Provenance",
                "",
                "Selected benchmark sources:",
                "| Name | URL/repo | Paper/citation | Version/date | Task count | Split/filtering | License/access | Capability | Rationale | Alternatives |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                "| GAIA | https://huggingface.co/datasets/gaia-benchmark/GAIA | GAIA: A Benchmark for General AI Assistants | 2024 | 140 | held-out sampled split | public benchmark release | assistant reasoning | main reasoning benchmark | AgentBench |",
                "| Mind2Web | https://github.com/OSU-NLP-Group/Mind2Web | Mind2Web: Towards a Generalist Agent for the Web | 2023 | 100 | official train/test adaptation | public dataset release | web action selection | web grounding benchmark | WebArena |",
                "| ToolBench | https://github.com/OpenBMB/ToolBench | ToolLLM: Facilitating Large Language Models to Master 16000+ Real-world APIs | 2023 | 120 | sampled tool-use tasks | public benchmark release | API/tool use | tool-use branch of the benchmark mix | API-Bank |",
            ]
        )
        + "\n",
    )


def _write_valid_model_scale_plan(root: Path) -> None:
    _write(
        root / "experiments" / "MODEL_SCALE_PLAN.md",
        "\n".join(
            [
                "# Model Scale Plan",
                "",
                "- Model backbone: 7B instruction model with LoRA adaptation.",
                "- Parameter count: 7B total; trainable parameters: 32M adapter parameters.",
                "- Training data: official benchmark train split plus licensed auxiliary data.",
                "- GPU memory plan: QLoRA on available B200 GPU with gradient checkpointing.",
                "- Expected GPU-hours: 8.",
                "- Checkpoint: experiments/run_001/checkpoint/adapter.pt.",
            ]
        )
        + "\n",
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
    _write_valid_exemplar_suitability(root)


def _write_valid_exemplar_suitability(root: Path) -> None:
    dimensions = {
        "task_type": {"score": 5, "rationale": "The exemplar studies language-agent evaluation with a matching empirical task shape."},
        "method_family": {"score": 4, "rationale": "The method family uses agent control and verifier-style mechanisms similar to this paper."},
        "experiment_shape": {"score": 5, "rationale": "The exemplar reports main results, ablations, robustness, and failure analysis in the same order."},
        "figure_table_density": {"score": 4, "rationale": "The body uses one overview figure and compact result tables matching the planned visual density."},
        "related_work_shape": {"score": 4, "rationale": "Related work is grouped by method gap and evaluation limitation rather than chronology."},
        "page_rhythm": {"score": 5, "rationale": "The page rhythm keeps methods and experiments visually balanced before conclusion."},
    }
    _write_json(
        root / "paper" / "style_ref" / "EXEMPLAR_SUITABILITY.json",
        {
            "suitability_schema_version": 1,
            "verdict": "PASS",
            "primary_exemplar": "emnlp-award",
            "no_prose_copy_attestation": True,
            "candidate_exemplars": [
                {"slug": "emnlp-award", "role": "primary", "suitability": dimensions},
                {"slug": "acl-evaluation", "role": "same-direction", "suitability": dimensions},
            ],
        },
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


def _write_valid_structure_conformance(root: Path) -> None:
    sections = [
        ("Introduction", "introduction"),
        ("Related Work", "related work"),
        ("Method", "method"),
        ("Experimental Setup", "experimental setup"),
        ("Results", "results"),
        ("Conclusion", "conclusion"),
        ("Limitations", "limitations"),
        ("Ethical Considerations", "ethics"),
    ]
    _write(
        root / "paper" / "style_ref" / "STRUCTURE_CONFORMANCE.md",
        "\n\n".join(
            [
                "# Final structure conformance",
                "## Section mapping\n"
                "Every final manuscript section is mapped to the exemplar-derived blueprint and "
                "the map follows the final LaTeX section order rather than an aspirational outline.",
                "## Exemplar lesson\n"
                "The final paper applies structural lessons about motivation-first introduction, "
                "topic-separated related work, evidence-near result tables, and calibrated endings.",
                "## Evidence source\n"
                "Each mapped section names local evidence sources under research/, experiments/, "
                "results/, or paper/artifacts/ so section roles are grounded in project artifacts.",
                "## Deviation rationale\n"
                "Paper-specific deviations are justified where the local benchmark or method needs "
                "a different shape from the exemplars, while filler sections are disallowed.",
                "## No prose copy policy\n"
                "The exemplars were used only for structural style. No prose, examples, terminology, "
                "claims, bibliography text, figure design, or sentence templates were copied.",
            ]
        )
        + "\n"
        + ("Final section mapping evidence note. " * 90),
    )
    _write_json(
        root / "paper" / "style_ref" / "STRUCTURE_CONFORMANCE.json",
        {
            "conformance_schema_version": 1,
            "verdict": "PASS",
            "no_prose_copy_attestation": True,
            "exemplar_lessons": [
                "Put the problem, gap, method, and measured result in the introduction before details.",
                "Keep results and significance evidence near the evaluation narrative.",
            ],
            "section_mappings": [
                {
                    "section": section,
                    "maps_to_exemplar_phase": phase,
                    "evidence_sources": [
                        "paper/artifacts/results_summary.tsv",
                        "research/IDEA_PROVENANCE.json",
                    ],
                    "exemplar_lesson": "Use the exemplar phase role while grounding prose in local artifacts.",
                    "deviation_rationale": (
                        "The local benchmark and method require this section shape while preserving "
                        "the exemplar-derived argument flow and avoiding copied prose."
                    ),
                }
                for section, phase in sections
            ],
        },
    )


def _write_valid_image2_figures(root: Path) -> None:
    _write(root / "paper" / "figures" / "method.prompt.txt", _valid_image2_teaser_prompt())
    _write_bytes(root / "paper" / "figures" / "method.png", _png_bytes(1536, 1024))
    _write_json(
        root / "paper" / "figures" / "method.review.json",
        _valid_image_review_payload(root, "paper/figures/method.png"),
    )
    _write_image2_provenance(
        root,
        "paper/figures/method.prompt.txt",
        "paper/figures/method.png",
        "paper/figures/method.provenance.json",
    )
    _write_image2_inspect(root, "paper/figures/method.png", "paper/figures/method.inspect.json")
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
                    "sidecar_path": "paper/figures/method.provenance.json",
                    "inspect_path": "paper/figures/method.inspect.json",
                    "review_path": "paper/figures/method.review.json",
                    "requested_size": "1536x1024",
                }
            ]
        },
    )


def _valid_image2_teaser_prompt() -> str:
    return """Use case: scientific-educational.
Asset type: Figure 1 teaser / conceptual overview for an EMNLP/ACL academic manuscript.

General style:
- EMNLP/ACL paper method figure, full-width page-width landscape, 1536x1024.
- Clean Figma-style block diagram with rounded cards, neat alignment, soft pastel fills, thin dark-gray borders, and compact information density.
- Polished manuscript figure, not a dashboard, poster, screenshot, or whiteboard sketch.
- Large readable labels, short phrases, balanced hierarchy, no snake_case identifiers in visible text.
- Flat vector-like raster rendering on a warm white background (#fbfaf7).

Pinned content that must appear exactly:
- Title: "SkillCycle Teaser"
- Stage labels: "Task stream", "Skill proposal", "Verifier gate", "Reusable skill card", "Answer with evidence".
- Outcome chips: "reject bad replay", "admit checked skill", "traceable result".
- SPELL EXACTLY the quoted labels above; do not invent extra terminology.

Layout variant: horizontal swimlane with a central verifier gate. Three clean swimlanes show input, admission, and final answer. Use one large central card for the verifier and small output cards on the right.

Negative prompt / Avoid:
- no tiny unreadable text, no paragraphs, no code snippets, no raw paths, no watermark
- no photorealism, no heavy gradients, no glassmorphism, no logo wall
- no messy Excalidraw look, no arbitrary blobs, no decorative clutter
- no inconsistent terminology between figure and paper
"""


def _write_image2_provenance(root: Path, prompt_path: str, output_path: str, provenance_path: str) -> None:
    output = root / output_path
    prompt_text = (root / prompt_path).read_text(encoding="utf-8")
    output_bytes = output.read_bytes()
    width, height = _png_dimensions_from_file(output)
    output_sha = hashlib.sha256(output_bytes).hexdigest()
    _write_json(
        root / provenance_path,
        {
            "generator": "codex-image2",
            "model": "gpt-image-2",
            "tool": "argus_skill.tools.image_tool.generate_image",
            "created_at_unix": 1700000000,
            "duration_seconds": 1.25,
            "prompt_path": prompt_path,
            "prompt": prompt_text,
            "prompt_sha256": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
            "output_path": output_path,
            "output_sha256": output_sha,
            "requested_size": f"{width}x{height}",
            "image": {
                "image": output_path,
                "exists": True,
                "bytes": len(output_bytes),
                "sha256": output_sha,
                "mime": "image/png",
                "width": width,
                "height": height,
            },
            "api": {
                "provider": "openai-compatible",
                "wire_api": "images",
                "endpoint": "/images/generations",
                "base_url_source": "vault",
                "key_source": "vault",
            },
        },
    )


def _write_image2_inspect(root: Path, output_path: str, inspect_path: str) -> None:
    output = root / output_path
    output_bytes = output.read_bytes()
    width, height = _png_dimensions_from_file(output)
    _write_json(
        root / inspect_path,
        {
            "image": output_path,
            "exists": True,
            "bytes": len(output_bytes),
            "sha256": hashlib.sha256(output_bytes).hexdigest(),
            "mime": "image/png",
            "width": width,
            "height": height,
        },
    )


def _valid_image_review_payload(root: Path, output_path: str, *, score: float = 4.0) -> dict[str, object]:
    output = root / output_path
    return {
        "model": "gpt-5.5",
        "endpoint": "/responses",
        "review_method": "hybrid_vision_heuristic",
        "score_1_to_5": score,
        "keep_or_regenerate": "keep",
        "image": {
            "image": output_path,
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest() if output.is_file() else "0" * 64,
        },
    }


def _png_dimensions_from_file(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    return struct.unpack(">II", data[16:24])


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
        _write_bytes(pdf_path, _minimal_pdf_bytes(["Page 1"]))
    page_count = _pdf_page_count(pdf_path) or 1
    page_snapshots = []
    for page_number in range(1, page_count + 1):
        page_path = root / "paper" / "layout_review" / "pages" / f"page-{page_number}.png"
        _write_bytes(page_path, _png_bytes(612, 792))
        page_snapshots.append(
            {
                "page": page_number,
                "path": page_path.relative_to(root).as_posix(),
                "sha256": _sha256(page_path),
            }
        )
    prompt = "Review the EMNLP layout from rendered page snapshots."
    review_input = json.dumps(
        {
            "pdf_sha256": _sha256(pdf_path),
            "page_snapshots": page_snapshots,
        },
        sort_keys=True,
    )
    payload = {
        "schema_version": 1,
        "generated_by": "argus_skill.skills.paper_layout_review",
        "iteration": 1,
        "review_method": review_method,
        "review_policy": {
            "pass_requires_vision": True,
            "minimum_score": 4.0,
        },
        "verdict": verdict,
        "score_1_to_5": score,
        "threshold": 4.0,
        "needs_revision": needs_revision,
        "pdf_path": "paper/main.pdf",
        "pdf_sha256": _sha256(pdf_path),
        "page_snapshots": page_snapshots,
        "vision_review": {
            "model": "gpt-5.5",
            "endpoint": "/responses",
            "reviewed_pages": list(range(1, page_count + 1)),
            "raw_review_text": (
                "The model inspected all rendered pages for EMNLP layout quality, "
                "table readability, float balance, and page flow before passing."
            ),
            "prompt_sha256": _sha256_text(prompt),
            "review_input_sha256": _sha256_text(review_input),
        },
        "criteria_scores": {
            "typography": 4.2,
            "table_readability": 4.1,
            "float_balance": 4.5,
            "page_flow": 4.3,
        },
        "issues": [],
        "blocking_issues": blocking_issues or [],
        "revision_directives": revision_directives or [],
    }
    review_path = root / "paper" / "LAYOUT_REVIEW.json"
    _write_json(review_path, payload)
    _write(
        root / "paper" / "LAYOUT_REVIEW_history.jsonl",
        json.dumps(
            {
                "generated_by": payload["generated_by"],
                "iteration": 1,
                "review_method": review_method,
                "vision_model": "gpt-5.5",
                "vision_endpoint": "/responses",
                "artifact_path": "paper/LAYOUT_REVIEW.json",
                "artifact_sha256": _sha256(review_path),
                "verdict": verdict,
                "score_1_to_5": score,
                "needs_revision": needs_revision,
                "pdf_sha256": payload["pdf_sha256"],
            }
        )
        + "\n",
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
        "method_system_clarity": score,
        "style_and_clarity": score,
    }
    checks = {
        "clear_problem_gap_contribution": True,
        "evidence_aligned_claims": True,
        "five_sentence_abstract_or_equivalent": True,
        "related_work_methodological": True,
        "method_system_readable": True,
        "calibrated_no_hype": True,
        "limitations_scope_present": True,
    }
    if required_checks:
        checks.update(required_checks)
    evidence_spans = _valid_academic_evidence_spans(section_scores)
    prompt = "Review academic writing quality for an EMNLP long paper."
    review_input = json.dumps(
        {
            "source_snapshots": source_snapshots,
            "section_scores": section_scores,
            "required_checks": checks,
        },
        sort_keys=True,
    )
    payload = {
        "schema_version": 1,
        "generated_by": "argus_skill.skills.academic_language_review",
        "iteration": 1,
        "review_method": review_method,
        "review_policy": {
            "pass_requires_model": True,
            "minimum_score": 4.0,
        },
        "verdict": verdict,
        "score_1_to_5": score,
        "threshold": 4.0,
        "needs_revision": needs_revision,
        "source_snapshots": source_snapshots,
        "reviewed_source_count": len(source_snapshots),
        "section_scores": section_scores,
        "required_checks": checks,
        "evidence_spans": evidence_spans,
        "model_review": {
            "model": "gpt-5.5",
            "endpoint": "/responses",
            "score_1_to_5": score,
            "section_scores": section_scores,
            "required_checks": checks,
            "evidence_spans": evidence_spans,
            "raw_review_text": (
                "The model reviewed the paper source for problem framing, evidence alignment, "
                "related-work positioning, method/system clarity, calibrated tone, and "
                "limitations coverage."
            ),
            "prompt_sha256": _sha256_text(prompt),
            "review_input_sha256": _sha256_text(review_input),
        },
        "issues": [],
        "blocking_issues": blocking_issues or [],
        "revision_directives": revision_directives or [],
    }
    review_path = root / "paper" / "ACADEMIC_LANGUAGE_REVIEW.json"
    _write_json(review_path, payload)
    _write(
        root / "paper" / "ACADEMIC_LANGUAGE_REVIEW_history.jsonl",
        json.dumps(
            {
                "generated_by": payload["generated_by"],
                "iteration": 1,
                "review_method": review_method,
                "model": "gpt-5.5",
                "endpoint": "/responses",
                "artifact_path": "paper/ACADEMIC_LANGUAGE_REVIEW.json",
                "artifact_sha256": _sha256(review_path),
                "verdict": verdict,
                "score_1_to_5": score,
                "needs_revision": needs_revision,
                "source_sha256": {
                    entry["path"]: entry["sha256"] for entry in source_snapshots
                },
            }
        )
        + "\n",
    )


def _write_valid_paper_infrastructure_review(
    root: Path,
    *,
    score: float = 4.5,
    verdict: str = "PASS",
    needs_revision: bool = False,
    leak_free: bool = True,
    review_method: str = "llm_text_reviewer",
    blocking_issues: list[dict[str, object]] | None = None,
    major_issues: list[dict[str, object]] | None = None,
    revision_directives: list[dict[str, object]] | None = None,
) -> None:
    main_path = root / "paper" / "main.tex"
    if not main_path.exists():
        _write_valid_paper_draft_report(root)
    references_path = root / "paper" / "references.bib"
    if not references_path.exists():
        _write(references_path, _valid_references_bibtex())
    source_paths = [main_path, references_path]
    source_snapshots = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256(path),
        }
        for path in source_paths
    ]
    checked_scope = ["title", "abstract", "body", "captions", "tables", "appendix"]
    evidence_spans = [
        {
            "section": "body",
            "source_path": "paper/main.tex",
            "line": 1,
            "quote": "The evaluated SkillGuard implementation runs in a deterministic Python benchmark harness",
            "why": "The reviewed setup sentence is paper-facing and omits local device/cache details.",
        }
    ]
    prompt = "Review the EMNLP paper for local infrastructure leakage."
    review_input = json.dumps(
        {
            "source_snapshots": source_snapshots,
            "checked_scope": checked_scope,
            "leak_free": leak_free,
        },
        sort_keys=True,
    )
    payload = {
        "schema_version": 1,
        "generated_by": "argus_skill.skills.paper_infrastructure_review",
        "iteration": 1,
        "review_method": review_method,
        "review_policy": {
            "pass_requires_model": True,
            "minimum_score": 4.0,
            "required_checked_scope": checked_scope,
        },
        "verdict": verdict,
        "score_1_to_5": score,
        "threshold": 4.0,
        "needs_revision": needs_revision,
        "leak_free": leak_free,
        "checked_scope": checked_scope,
        "source_snapshots": source_snapshots,
        "reviewed_source_count": len(source_snapshots),
        "evidence_spans": evidence_spans,
        "model_review": {
            "model": "gpt-5.5",
            "endpoint": "/responses",
            "verdict": verdict,
            "score_1_to_5": score,
            "leak_free": leak_free,
            "checked_scope": checked_scope,
            "evidence_spans": evidence_spans,
            "blocking_issues": blocking_issues or [],
            "major_issues": major_issues or [],
            "revision_directives": revision_directives or [],
            "pass_or_revise": "pass" if verdict == "PASS" and leak_free else "revise",
            "raw_review_text": (
                "The reviewer inspected the manuscript source for local device, cache, "
                "environment, path, route, and paper-generation infrastructure leaks."
            ),
            "prompt_sha256": _sha256_text(prompt),
            "review_input_sha256": _sha256_text(review_input),
        },
        "issues": [],
        "blocking_issues": blocking_issues or [],
        "major_issues": major_issues or [],
        "revision_directives": revision_directives or [],
    }
    review_path = root / "paper" / "PAPER_INFRASTRUCTURE_REVIEW.json"
    _write_json(review_path, payload)
    _write(
        root / "paper" / "PAPER_INFRASTRUCTURE_REVIEW_history.jsonl",
        json.dumps(
            {
                "generated_by": payload["generated_by"],
                "iteration": 1,
                "review_method": review_method,
                "model": "gpt-5.5",
                "endpoint": "/responses",
                "artifact_path": "paper/PAPER_INFRASTRUCTURE_REVIEW.json",
                "artifact_sha256": _sha256(review_path),
                "verdict": verdict,
                "score_1_to_5": score,
                "needs_revision": needs_revision,
                "source_sha256": {
                    entry["path"]: entry["sha256"] for entry in source_snapshots
                },
            }
        )
        + "\n",
    )


def _valid_academic_evidence_spans(section_scores: dict[str, float]) -> list[dict[str, object]]:
    quote_by_section = {
        "abstract": "A complete EMNLP-style long paper studies how evidence-calibrated skill",
        "introduction": "Long-horizon agents increasingly rely on stored traces, reflections, and reusable skills",
        "contribution_framing": "A controller routes skill-memory state through verifier policy checks",
        "evidence_alignment": "Table~\\ref{tab:main} summarizes the main result.",
        "related_work_positioning": "Prior benchmark work motivates the transfer setting",
        "method_system_clarity": "The evaluated SkillGuard implementation runs in a deterministic Python benchmark harness",
        "style_and_clarity": "The paper concludes within the main-page budget.",
    }
    return [
        {
            "section": section,
            "source_path": "paper/main.tex",
            "line": 1,
            "quote": quote_by_section[section],
            "why": f"The quoted prose supports the {section.replace('_', ' ')} assessment.",
        }
        for section in section_scores
    ]


def _write_valid_paper_draft_report(
    root: Path,
    *,
    target_venue: str = "EMNLP",
    scope: str = "long-paper",
    pages: float = 7.8,
) -> None:
    citation_keys = _valid_reference_keys()
    abstract_text = (
        "A complete EMNLP-style long paper studies how evidence-calibrated skill "
        "transfer changes tool-using agent behavior when memory is admitted only "
        "after verifier checks. Existing agent-memory systems often report a "
        "single benchmark or omit the model and runtime settings needed to "
        "interpret the result. We evaluate SkillGuard with gpt-5-mini as a "
        "hosted no-GPU backbone, deterministic decoding, a fixed response cap, "
        "cached prompts, and a shared request budget across three real benchmark "
        "sources: ToolBench, WebArena, and GAIA. Across 240 scored episodes, "
        "SkillGuard improves verified completion by 8 points over the strongest "
        "runnable baseline while reducing unsupported memory admissions. The "
        "result supports a scoped claim about verifier-gated skill transfer "
        "under this benchmark mix, not a general claim that all agent memory "
        "should be compressed in the same way. This framing lets the reader "
        "separate the measured admission policy from broader claims about agent "
        "planning or long-term memory. The paper reports the model, budget, "
        "benchmark provenance, ablations, and residual failures in the body so "
        "the claim can be audited from saved run artifacts. The failure labels "
        "show where the gate still loses."
    )
    introduction_text = (
        "This paper is formatted as a reviewable long paper with "
        "Figure~\\ref{fig:method}. Long-horizon agents increasingly rely on "
        "stored traces, reflections, and reusable skills, but the paper-facing "
        "question is not merely whether memory exists. A reviewer needs to know "
        "which memories are admitted, which task distribution tests reuse, which "
        "model powers the evaluated agent, and whether the same budget applies "
        "to every baseline. Without that information, an apparently positive "
        "agent paper can be a thin systems note with hidden benchmark choices. "
        "Recent tool-agent and memory systems make this risk visible: ReAct-style "
        "execution, Reflexion-style self-feedback, ToolBench-style API tasks, and "
        "WebArena-style interaction benchmarks all depend on explicit state, "
        "actions, and scoring contracts \\citep{"
        f"{citation_keys[0]},{citation_keys[1]},{citation_keys[2]},{citation_keys[3]}"
        "}. Those papers motivate skill reuse, but they also show why a memory "
        "paper must say exactly what is retained and how that retained evidence "
        "is scored. A reader cannot infer that contract from aggregate accuracy "
        "alone. "
        "SkillGuard addresses the narrower problem of admission: after an "
        "episode, the system decides whether a proposed skill should enter the "
        "memory library or remain a transient trace. The central hypothesis is "
        "that verifier-gated admission can preserve reusable evidence while "
        "preventing the library from accumulating duplicate or task-local notes. "
        "This is a practical problem for tool-using agents because downstream "
        "failures often come from stale, over-general, or unrelated traces being "
        "retrieved in later episodes. Prior work on ReAct-style loops, "
        "reflection, and tool benchmarks motivates the ingredients, but it does "
        "not by itself specify a reproducible admission contract for a shared "
        "skill store. We therefore frame the contribution as an evaluation of "
        "one auditable gate rather than as a new universal planner. The "
        "experiments use gpt-5-mini with fixed decoding and a common harness so "
        "that the comparison isolates admission policy instead of changing the "
        "underlying model or benchmark order. The paper makes three "
        "contributions. First, it defines a concrete SkillGuard admission rule "
        "that checks task family and duplicate evidence before storage. Second, "
        "it evaluates the rule on a three-source real benchmark mix with "
        "nontrivial baselines and paired tests. Third, it reports the remaining "
        "failure modes and limitations so the claim stays tied to measured "
        "agent behavior rather than validator-shaped prose. The introduction also "
        "explains why the problem is not solved by simply increasing memory size. "
        "A large unfiltered store can look strong on repeated repository cues while "
        "hurting tasks that require file-local or tool-specific evidence, so the "
        "paper distinguishes storage capacity from admission quality. It previews "
        "the evaluation before the reader reaches the tables: the same model, "
        "task order, budget, and scoring protocol are held fixed, while the memory "
        "admission rule changes. This keeps the narrative close to what a reviewer "
        "can verify. The section closes by naming the scoped claim, the three "
        "benchmark sources, and the reason the remaining problem-digest failures "
        "are analyzed rather than hidden behind the aggregate score. This makes "
        "the first page function like a real conference-paper introduction: it "
        "starts from an observable failure mode, positions the missing admission "
        "contract, states the intervention, previews the empirical comparison, "
        "and tells the reader exactly what evidence would falsify the claim. It "
        "also names the negative control so the result is not mistaken for a "
        "general memory-size comparison. The motivating example is a tool-using "
        "agent that solves one task, stores a generic lesson, and later retrieves "
        "that lesson for a different benchmark source where the relevant evidence "
        "is a file name, web action, or answer normalization rule. If the memory "
        "library accepts that lesson without checking the source family, the next "
        "episode receives confident but misplaced context. SkillGuard is designed "
        "to make that admission decision auditable. The introduction therefore "
        "does not treat memory as a single scalar resource; it separates the "
        "quality of admitted evidence from the amount of stored text, then "
        "previews the paired evaluation that tests this distinction under fixed "
        "model calls, fixed budgets, and saved raw rows. The final paragraph also "
        "sets up the paper organization in reviewer-facing terms. Section 2 "
        "groups the relevant agent-memory and benchmark literature by the role it "
        "plays in the admission problem. Section 3 defines the controller and "
        "verifier. Section 4 describes the three public benchmark sources, the "
        "hosted model route, and the shared decoding budget. Sections 5 and 6 "
        "separate aggregate success from source-level failures, so the reader can "
        "see both the benefit of the gate and the remaining cases where admission "
        "quality is not enough. This roadmap is part of the claim discipline: the "
        "paper tells the reader what evidence appears before asking them to accept "
        "the headline result. It also gives the reviewer enough context to judge "
        "whether a failure should be attributed to retrieval, admission, task "
        "difficulty, or model output quality. The result preview is intentionally "
        "concrete: in the completed 240-episode matrix, SkillGuard improves "
        "verified completion by 8 points over the strongest runnable baseline "
        "under the same gpt-5-mini route, decoding configuration, and task order. "
        "That number is not presented as a universal memory result. It is used "
        "as an empirical anchor for the design question the introduction raises: "
        "whether a verifier should decide which solved episodes become reusable "
        "skills. The rest of the paper is organized around that anchor. Related "
        "Work explains which prior-agent mechanisms supply the controller, "
        "reflection, and benchmark ingredients. Method defines the admission rule "
        "in enough detail to replay it. Experiments document the source mix and "
        "budget. Results and Analysis then separate the aggregate 8-point gain "
        "from source-level errors, duplicate rejections, and cases where the "
        "model output itself remains wrong even when the admitted skill is valid "
        "under identical runtime constraints."
    )
    method_text = (
        "The evaluated SkillGuard implementation runs in a deterministic Python "
        "benchmark harness around a hosted gpt-5-mini agent. A controller routes "
        "skill-memory state through verifier policy checks before each "
        "tool-using agent episode, using temperature 0.0, top_p 1.0, "
        "max_tokens 512, a fixed per-episode token budget, cache keys that "
        "include the full prompt, and a three-retry timeout policy. The method "
        "has three stages. First, the episode runner builds the prompt from the "
        "benchmark task, the current library summary, and the baseline-specific "
        "state. Second, the model produces an action or answer under the shared "
        "decoding settings. Third, after a solved episode, SkillGuard proposes "
        "a candidate skill and admits it only if the verifier can link it to the "
        "task family and show that the same family does not already contain an "
        "equivalent memory. The controller does not change the gold labels, "
        "task order, metrics, or result extraction rule. The no-skill baseline "
        "runs the same agent without a library, raw memory stores recent "
        "episode traces, Reflexion stores failure summaries, and the static "
        "skill library stores candidate memories without the verifier gate. "
        "This decomposition makes the admission rule the primary intervention. "
        "The verifier itself is intentionally simple: it checks source family, "
        "evidence compatibility, duplicate normalized content, and whether the "
        "candidate names a task-local cue that would be unsafe to reuse outside "
        "that family. Rejected candidates remain in raw logs for audit but are "
        "not exposed to later episodes. Accepted skills are serialized with "
        "source ids, family labels, prompt hashes, model id, and the result row "
        "that justified admission. The implementation keeps the verifier outside "
        "the answer generator so that a rejection cannot rewrite the current "
        "episode. It only changes future retrieval state. At retrieval time, the "
        "agent receives at most the accepted family-specific entries and cannot "
        "inspect rejected candidates. This matters for interpretation because a "
        "gain could otherwise come from leaking gold labels, changing the prompt, "
        "or letting the verifier act as a second solver. SkillGuard instead "
        "treats verification as a storage decision with explicit provenance. "
        "Every accepted entry includes the source benchmark, episode id, family "
        "assignment, normalized skill text, duplicate key, and the baseline run "
        "that would have stored or rejected the same candidate. The policy can "
        "therefore be re-run over raw traces without regenerating model outputs."
        " The duplicate key is computed from normalized family labels and skill "
        "content rather than from task ids, so the same lesson cannot be admitted "
        "again just because it appears in a new benchmark source. The family "
        "compatibility check is conservative: if a candidate mixes repository, "
        "tool, and file-local cues in a way the verifier cannot classify, the "
        "candidate is rejected and the episode remains a normal solved row. This "
        "keeps the method auditable and makes the ablation against no-verifier "
        "meaningful. The controller state has four fields that are visible in "
        "the artifact log: the selected benchmark source, the current episode id, "
        "the active baseline condition, and the accepted skill inventory. Prompt "
        "construction reads only these fields plus the task text, so rejected "
        "skills cannot leak into later trials. After the hosted model returns an "
        "answer, the scorer writes the raw answer, normalized answer, gold target, "
        "success bit, retry count, cache fingerprint, model id, and budget fields "
        "before any admission update occurs. The verifier then receives the solved "
        "row and a candidate memory summary. It first checks that the candidate "
        "mentions the same source family as the solved row, then compares a "
        "normalized duplicate key against accepted entries for that family. Only "
        "candidates passing both checks become retrievable skills. This ordering "
        "matters because it prevents the verifier from acting as an answer judge "
        "or a second planner. It is a post-solve storage policy whose inputs and "
        "outputs can be audited from the saved result rows. The implementation "
        "therefore has a simple replay path: given the same raw traces, the same "
        "family labels, and the same duplicate-key function, the admitted library "
        "can be reconstructed without calling the hosted model again. This replay "
        "path is used for ablations that swap the admission rule while preserving "
        "the original answers. The implementation also records a compact event log "
        "for each episode: prompt hash, selected source, active baseline, model "
        "route, retry count, accepted-skill ids, and rejection reason. Those fields "
        "are the only state used by the analysis scripts, which keeps the method "
        "description aligned with the saved artifacts and prevents hidden manual "
        "corrections from entering the reported result."
    )
    setup_text = (
        "Each benchmark run scores 240 task episodes against no-skill, "
        "raw-memory, Reflexion, and static-skill baselines with success rate as "
        "the primary metric under a fixed token budget. We report paired tests "
        "in Table~\\ref{tab:significance}. The benchmark mix contains three "
        "independent real sources: ToolBench for API/tool use, WebArena for "
        "web-agent interaction, and GAIA for multi-step assistant reasoning. "
        "Each source contributes documented public tasks with stable ids, "
        "source URLs, split/filtering notes, and license/access records in "
        "the benchmark provenance files. All methods run on the same "
        "gpt-5-mini backend, temperature 0.0, top_p 1.0, max_tokens 512, cache "
        "enabled, fixed seeds where sampling appears in task selection, and "
        "identical stop/resume rules. The primary metric is exact or official "
        "task success depending on the source, and the secondary metrics are "
        "admitted-skill count, duplicate rejection count, and paired win/loss "
        "against the strongest runnable baseline. The run matrix is complete "
        "only when every baseline and the proposed method have scored rows for "
        "all selected tasks. Failures, timeouts, parse errors, and blocked "
        "source rows are retained as rows rather than silently dropped. ToolBench "
        "episodes are scored with the official tool-use success extraction after "
        "normalizing harmless formatting differences. WebArena tasks use the "
        "benchmark interaction outcome exported by the harness, and GAIA tasks use "
        "the official answer normalization for assistant questions. The analysis "
        "reports paired win/loss counts because every method sees the same ordered "
        "task stream. We also record store size, accepted-skill count, duplicate "
        "rejection count, retry count, and per-source failure labels so that the "
        "paper can separate a genuine admission effect from a formatting, timeout, "
        "or parsing artifact. The configuration table in the final manuscript "
        "mirrors these fields instead of relying on prose alone. Before analysis, "
        "the runner checks that each selected source contributes unique task ids, "
        "that no pilot rows were duplicated with suffixes, and that every method "
        "has a completed row for each required condition. Confidence intervals "
        "and paired tests are computed from the raw result table, not from the "
        "LaTeX table, and every number in the main text is regenerated from that "
        "canonical artifact. Runs that exceed the request budget are marked as "
        "failures with their partial trace preserved for later error analysis. "
        "The no-GPU setting is handled through the approved hosted route rather "
        "than local acceleration: every evaluated agent call uses gpt-5-mini with "
        "the same endpoint class, decoding settings, response cap, and retry "
        "policy. A run is not counted as final evidence until the provenance file "
        "lists three executed sources, the status file records completion, and the "
        "raw result rows contain every required method/source pair. The setup also "
        "records the exact source version or access date, because benchmark drift "
        "would otherwise make a later rerun difficult to interpret. Error labels "
        "distinguish model answer failures, parse failures, timeout failures, "
        "blocked source rows, and verifier rejections. Those labels feed the "
        "failure-analysis section and keep the body from relying on aggregate "
        "accuracy alone. All reported tables are regenerated from the canonical "
        "rows after the run finishes, and the manuscript records the command that "
        "performs this regeneration in the reproducibility appendix. The same "
        "appendix records seeds, cache policy, and source filters for reruns. The "
        "setup treats hosted calls as part of the evaluated system, so a table row "
        "must name the model id, route, temperature, response cap, and retry policy "
        "whenever a method uses model output. This prevents a final paper from "
        "mixing deterministic pilot prose with hosted-agent evidence."
    )
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
                f"\\begin{{abstract}}{abstract_text}\\end{{abstract}}",
                "\\section{Introduction}",
                introduction_text,
                "\\section{Related Work}",
                (
                    "Prior benchmark work motivates the transfer setting "
                    f"\\citep{{{','.join(citation_keys[:6])}}}.\n\n"
                    "Tool-use systems define reusable execution patterns "
                    f"\\citep{{{','.join(citation_keys[6:12])}}}.\n\n"
                    "Agent-memory papers motivate durable state and retrieval "
                    f"\\citep{{{','.join(citation_keys[12:18])}}}.\n\n"
                    "Verifier and self-refinement methods frame the admission gate "
                    f"\\citep{{{','.join(citation_keys[18:24])}}}.\n\n"
                    "Evaluation and hallucination studies shape our reporting protocol "
                    f"\\citep{{{','.join(citation_keys[24:30])}}}."
                ),
                "\\section{Method}",
                "\\begin{figure}[t]",
                "\\centering",
                "\\includegraphics[width=0.82\\linewidth]{figures/method.png}",
                "\\caption{SkillGuard routing improves verified completion by 8 points.}",
                "\\label{fig:method}",
                "\\end{figure}",
                method_text,
                "\\section{Experimental Setup}",
                setup_text,
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
    _write_bytes(root / "paper" / "main.pdf", _minimal_pdf_bytes(_valid_rendered_paper_pages()))
    _write(root / "paper" / "FORMAT_PREFLIGHT.md", "validate-research-md-format: PASS\n")
    _write_valid_benchmark_provenance(root)
    _write_valid_structure_conformance(root)
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


def _valid_rendered_paper_pages() -> list[str]:
    return [
        "Title\nAbstract\nIntroduction\nThis page introduces the long paper.",
        "Related Work\nPrior work motivates the benchmark and transfer setting.",
        "Method\nThe method uses a conservative routing policy.",
        "Experimental Setup\nFigure 1 shows the pipeline and Table 1 defines tasks.",
        "Results\nTable 2 summarizes the main result and Figure 2 shows transfer.",
        "Analysis\nTable 3 reports ablations and Figure 3 shows diagnostics.",
        "Operational Takeaways\nTable 4 summarizes limitations before the conclusion.",
        "Conclusion\nThe final body page explains deployment scope and reproducibility.",
        "References\nReference entries begin here.",
        "References\nMore reference entries continue here.",
    ]


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


def _write_valid_claim_graph(root: Path) -> None:
    _write(root / "paper" / "artifacts" / "result_to_claim.tsv", "result\tclaim\naccuracy\tmain-result\n")
    _write_json(
        root / "paper" / "CLAIM_GRAPH.json",
        {
            "claim_graph_schema_version": 1,
            "claims": [
                {
                    "id": "main-result",
                    "claim": "SkillGuard improves success by 8 points over the baseline.",
                    "section": "Experimental Setup",
                    "status": "supported",
                    "evidence_sources": ["paper/artifacts/results_table.tsv"],
                    "result_artifacts": ["paper/RESULTS_REPORT.md"],
                    "figure_or_table": ["tab:main"],
                    "citations": ["verifiedref01"],
                },
                {
                    "id": "method-routing",
                    "claim": "The method uses a conservative routing policy for skill transfer.",
                    "section": "Method",
                    "status": "supported",
                    "evidence_sources": ["paper/artifacts/result_to_claim.tsv"],
                    "result_artifacts": ["paper/RESULTS_REPORT.md"],
                    "figure_or_table": ["fig:method"],
                    "citations": ["verifiedref02"],
                },
            ],
        },
    )


def _write_valid_figure_table_style_guide(root: Path) -> None:
    _write_json(
        root / "paper" / "FIGURE_TABLE_STYLE_GUIDE.json",
        {
            "style_guide_schema_version": 1,
            "verdict": "PASS",
            "figure_rules": "Use readable page-width academic figures with short labels and no clutter.",
            "table_rules": "Use compact ACL-style tables with footnotesize text and takeaway captions.",
            "body_appendix_policy": "Keep only primary evidence floats in the body; move diagnostics to appendix.",
            "float_inventory": [
                {
                    "id": "fig:method",
                    "type": "figure",
                    "body_or_appendix": "body",
                    "target_section": "Method section",
                    "source_artifact": "paper/figures/method.png",
                    "style_decision": "Overview figure stays readable at column/body width.",
                    "readability_check": "Large labels, balanced cards, no raw artifact paths.",
                },
                {
                    "id": "tab:main",
                    "type": "table",
                    "body_or_appendix": "body",
                    "target_section": "Experimental Setup",
                    "source_artifact": "paper/artifacts/results_table.tsv",
                    "style_decision": "Primary result table is compact and has a numerical caption.",
                    "readability_check": "Footnotesize table with limited columns and a clear winner row.",
                },
                {
                    "id": "tab:significance",
                    "type": "table",
                    "body_or_appendix": "body",
                    "target_section": "Results section",
                    "source_artifact": "paper/artifacts/results_table.tsv",
                    "style_decision": "Significance evidence sits near the result claim.",
                    "readability_check": "Compact p-value table with a direct statistical takeaway.",
                },
            ],
        },
    )


def _write_valid_validation_priority_policy(root: Path) -> None:
    priority_order = [
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
        "paper_infrastructure",
        "artifact_manifest",
    ]
    routing = {
        "freshness": {
            "issue_code_prefixes": ["artifact_freshness", "freshness_", "artifact_stale", "artifact_modified"],
            "repair_mode": "regenerate stale downstream artifacts from current inputs",
        },
        "experiment_evidence": {
            "issue_code_prefixes": [
                "missing_full_scale_experiment_run",
                "incomplete_full_scale_experiment_run",
                "missing_baseline_condition_run",
                "pilot_pdf_without_full_scale_evidence",
                "proposed_result_missing",
                "quality_signal_contradicts_results",
            ],
            "repair_mode": "run more benchmark experiments and complete missing baseline or ablation conditions",
        },
        "claim_graph": {
            "issue_code_prefixes": ["claim_graph", "claim_", "unsupported_claim", "evidence_gap"],
            "repair_mode": "reroute to evidence-gap handling, extra experiments, or claim softening",
        },
        "content_sufficiency": {
            "issue_code_prefixes": [
                "underlength_emnlp_paper",
                "rendered_main_body_underfilled",
                "references_before_full_body",
                "missing_midpaper_visual_pages",
                "draft_not_submission_quality",
            ],
            "repair_mode": (
                "add source-backed framing, method detail, evidence-backed "
                "analysis, ablation, failure study, or run experiments"
            ),
        },
        "exemplar_suitability": {
            "issue_code_prefixes": ["exemplar_suitability", "style_exemplar_suitability"],
            "repair_mode": "reselect or justify the primary exemplar before drafting",
        },
        "exemplar_structure": {
            "issue_code_prefixes": ["style_structure", "structure_", "unmapped_final_section"],
            "repair_mode": "reset paper skeleton and section mapping",
        },
        "figure_table_style": {
            "issue_code_prefixes": ["figure_table_style", "style_guide_", "float_inventory"],
            "repair_mode": "redesign figure/table floats and body-vs-appendix placement",
        },
        "format_layout": {
            "issue_code_prefixes": [
                "severe_overfull_hbox",
                "code_like_display_label",
                "table_caption",
                "body_figure",
                "too_many_body_figures",
            ],
            "repair_mode": "repair LaTeX formatting, captions, labels, and float layout without padding content",
        },
        "layout_vision": {
            "issue_code_prefixes": ["layout_", "stale_layout", "low_layout"],
            "repair_mode": "apply vision guidance to LaTeX floats and rebuild the whole PDF",
        },
        "academic_language": {
            "issue_code_prefixes": ["academic_", "stale_academic", "low_academic"],
            "repair_mode": "revise reader-facing prose after evidence and structure are current",
        },
        "paper_infrastructure": {
            "issue_code_prefixes": ["paper_infrastructure", "stale_paper_infrastructure"],
            "repair_mode": (
                "remove reader-facing local environment, device, and config leaks "
                "and rerun the model reviewer"
            ),
        },
        "artifact_manifest": {
            "issue_code_prefixes": ["artifact_", "manifest_", "generated_artifact"],
            "repair_mode": "refresh manifest entries and canonical source graph",
        },
    }
    _write_json(
        root / "paper" / "VALIDATION_PRIORITY_POLICY.json",
        {
            "priority_policy_schema_version": 1,
            "priority_order": priority_order,
            "failure_routing": routing,
            "reset_policy": {
                "max_non_improving_rounds": 2,
                "actions": ["reset paper skeleton", "rebalance floats", "soften unsupported claims"],
            },
        },
    )


def _write_valid_artifact_freshness(root: Path) -> None:
    def record(path: str, inputs: list[str], *, role: str = "generated") -> dict[str, object]:
        resolved = root / path
        existing_inputs = [input_path for input_path in inputs if (root / input_path).exists()]
        payload: dict[str, object] = {
            "path": path,
            "role": role,
            "inputs": [
                {"path": input_path, "sha256": _sha256(root / input_path)}
                for input_path in existing_inputs
            ],
            "generator": "unit-test",
        }
        if resolved.suffix.lower() != ".pdf":
            payload["sha256"] = _sha256(resolved)
        return payload

    records = [
        record(
            "paper/CLAIM_GRAPH.json",
            ["paper/artifacts/result_to_claim.tsv", "paper/artifacts/results_table.tsv"],
            role="contract",
        ),
        record("paper/FIGURE_TABLE_STYLE_GUIDE.json", ["paper/main.tex"], role="contract"),
        record("paper/VALIDATION_PRIORITY_POLICY.json", ["paper/CLAIM_GRAPH.json"], role="contract"),
        record(
            "paper/main.tex",
            [
                "paper/CLAIM_GRAPH.json",
                "paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md",
                "paper/FIGURE_TABLE_STYLE_GUIDE.json",
                "paper/artifacts/result_to_claim.tsv",
                "paper/artifacts/results_table.tsv",
            ],
            role="paper_source",
        ),
        record("paper/main.pdf", ["paper/main.tex"], role="compiled_pdf"),
        record("paper/RESULTS_REPORT.md", ["paper/artifacts/results_table.tsv"], role="generated_report"),
    ]
    if (root / "paper" / "LAYOUT_REVIEW.json").exists():
        records.append(record("paper/LAYOUT_REVIEW.json", ["paper/main.pdf"], role="review"))
    if (root / "paper" / "ACADEMIC_LANGUAGE_REVIEW.json").exists():
        records.append(record("paper/ACADEMIC_LANGUAGE_REVIEW.json", ["paper/main.tex", "paper/main.pdf"], role="review"))
    if (root / "paper" / "PAPER_INFRASTRUCTURE_REVIEW.json").exists():
        records.append(record("paper/PAPER_INFRASTRUCTURE_REVIEW.json", ["paper/main.tex", "paper/main.pdf"], role="review"))
    _write_json(
        root / "paper" / "ARTIFACT_FRESHNESS.json",
        {
            "freshness_schema_version": 1,
            "records": records,
        },
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
