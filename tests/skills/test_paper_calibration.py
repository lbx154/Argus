from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from argus_skill.skills.paper_calibration import (
    calibration_cases,
    detect_quality_blockers,
    validate_quality_calibration_file,
)
from argus_skill.skills.pipeline_contracts import validate_submission_assurance


def test_calibration_cases_include_emnlp_2025_positive_best_paper() -> None:
    cases = {case.case_id: case for case in calibration_cases()}

    best = cases["positive:emnlp2025-best-infini-gram-mini"]

    assert best.polarity == "positive"
    assert best.source_url == "https://2025.emnlp.org/program/awards/"
    assert "scale_or_resource_evidence" in best.quality_signals


def test_fresh_demo_pattern_is_detected_as_quality_blockers(tmp_path: Path) -> None:
    _write_fresh_demo_pattern(tmp_path)

    issues = detect_quality_blockers(tmp_path)

    assert {
        "baseline_not_beaten",
        "underpowered_pilot",
        "parser_or_schema_confound",
        "synthetic_only_benchmark",
        "draft_self_reports_not_submission_quality",
    }.issubset({issue.code for issue in issues})


def test_ready_assurance_cannot_pass_fresh_demo_pattern(tmp_path: Path) -> None:
    _write_fresh_demo_pattern(tmp_path)
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
                "submission_package": {"verdict": "PASS"},
            },
        },
    )
    _write_json(
        tmp_path / "paper" / "PAPER_QUALITY_CALIBRATION.json",
        {
            "verdict": "PASS",
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
                    "signals": [
                        "baseline_not_beaten",
                        "synthetic_only_benchmark",
                        "parser_or_schema_confound",
                    ],
                }
            ],
            "quality_signals_from_positive_examples": [
                {
                    "case_id": "positive:emnlp2025-best-infini-gram-mini",
                    "signals_used": ["scale_or_resource_evidence"],
                }
            ],
        },
    )

    issues = validate_submission_assurance(tmp_path)

    assert "ready_verdict_matches_negative_regression" in {issue.code for issue in issues}
    assert "baseline_not_beaten" in {issue.code for issue in issues}


def test_quality_calibration_requires_positive_signals(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "paper" / "PAPER_QUALITY_CALIBRATION.json",
        {
            "verdict": "FAIL",
            "quality_signals": {
                "uses_public_benchmark": False,
                "beats_nontrivial_baseline": False,
                "proposed_contribution_beats_strong_baseline": False,
                "statistical_support_for_headline": False,
                "n_tasks_meets_threshold": False,
                "parser_schema_confound_cleared": False,
                "submission_quality_self_assessment": "pilot",
            },
            "negative_case_regressions": [],
            "quality_signals_from_positive_examples": [],
        },
    )

    issues = validate_quality_calibration_file(tmp_path)

    assert [issue.code for issue in issues] == ["missing_positive_calibration_signals"]


def test_ready_calibration_requires_structured_positive_contribution(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "paper" / "PAPER_QUALITY_CALIBRATION.json",
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
            "negative_case_regressions": [],
            "quality_signals_from_positive_examples": [
                {"case_id": "positive:emnlp2025-best-infini-gram-mini"}
            ],
        },
    )

    issues = validate_quality_calibration_file(tmp_path)

    assert "missing_paper_contribution" in {issue.code for issue in issues}


def test_proposed_protocol_must_beat_best_nontrivial_baseline(
    tmp_path: Path,
) -> None:
    _write_valid_quality_calibration(tmp_path, proposed_protocol="replay_skill")
    _write(
        tmp_path / "paper" / "artifacts" / "results_summary.tsv",
        "\n".join(
            [
                "scope\tsplit_name\tprotocol\tsuccess_rate\tjson_parse_rate\tn_tasks",
                "overall\tmain\tno_skill\t0.500\t1.000\t240",
                "overall\tmain\traw_memory\t0.500\t1.000\t240",
                "overall\tmain\treflexion\t0.944\t1.000\t240",
                "overall\tmain\tstatic_skill_lib\t0.500\t1.000\t240",
                "overall\tmain\treplay_skill\t0.500\t1.000\t240",
                "overall\tpublic_validation\tno_skill\t0.500\t1.000\t30",
                "overall\tpublic_validation\traw_memory\t0.500\t1.000\t30",
                "overall\tpublic_validation\treflexion\t0.833\t1.000\t30",
                "overall\tpublic_validation\tstatic_skill_lib\t0.500\t1.000\t30",
                "overall\tpublic_validation\treplay_skill\t0.500\t1.000\t30",
            ]
        )
        + "\n",
    )

    issues = detect_quality_blockers(tmp_path)
    codes = {issue.code for issue in issues}

    assert "proposed_claim_not_supported" in codes
    assert "quality_signal_contradicts_results" in codes


def test_negative_result_framing_blocks_final_quality(tmp_path: Path) -> None:
    _write(
        tmp_path / "research" / "NARRATIVE_REPORT.md",
        "This supports a benchmark-focused negative-result paper.\n",
    )

    issues = detect_quality_blockers(tmp_path)

    assert "negative_result_framing" in {issue.code for issue in issues}


def test_positive_headline_contribution_can_pass_quality_blocker_scan(
    tmp_path: Path,
) -> None:
    _write_valid_quality_calibration(tmp_path, proposed_protocol="skillcycle")
    _write(
        tmp_path / "paper" / "artifacts" / "results_summary.tsv",
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

    assert validate_quality_calibration_file(tmp_path) == []
    assert detect_quality_blockers(tmp_path) == []


def test_sixty_task_summary_is_underpowered_for_full_paper(tmp_path: Path) -> None:
    _write_valid_quality_calibration(tmp_path, proposed_protocol="skillcycle")
    _write(
        tmp_path / "paper" / "artifacts" / "results_summary.tsv",
        "\n".join(
            [
                "scope\tsplit_name\tprotocol\tsuccess_rate\tjson_parse_rate\tn_tasks",
                "overall\tmain\tno_skill\t0.500\t1.000\t60",
                "overall\tmain\treflexion\t0.850\t1.000\t60",
                "overall\tmain\tstatic_skill_lib\t0.620\t1.000\t60",
                "overall\tmain\tskillcycle\t0.920\t1.000\t60",
            ]
        )
        + "\n",
    )

    issues = detect_quality_blockers(tmp_path)

    underpowered = [issue for issue in issues if issue.code == "underpowered_pilot"]
    assert underpowered
    assert "240" in underpowered[0].message


def test_planned_240_provenance_cannot_substitute_for_scored_results(
    tmp_path: Path,
) -> None:
    _write_valid_quality_calibration(tmp_path, proposed_protocol="skillcycle")
    _write(
        tmp_path / "paper" / "artifacts" / "results_summary.tsv",
        "\n".join(
            [
                "metric\tvalue\tsource",
                "benchmark_scored_tasks_overall\t240\texperiments/BENCHMARK_PROVENANCE.md",
                "canonical_episodes\t60\tresults/transfer60/summary.json",
                "canonical_accuracy\t1.0000\tpaper/artifacts/results_table.tsv",
            ]
        )
        + "\n",
    )
    _write(
        tmp_path / "experiments" / "BENCHMARK_PROVENANCE.md",
        "Planned episodes: 240 total across the full benchmark development corpus.\n",
    )

    issues = detect_quality_blockers(tmp_path)
    codes = {issue.code for issue in issues}

    assert "missing_scored_task_count" in codes
    assert "planned_benchmark_scale_only" in codes


def test_results_summary_cannot_exceed_run_evidence(tmp_path: Path) -> None:
    _write_valid_quality_calibration(tmp_path, proposed_protocol="skillcycle")
    _write(
        tmp_path / "paper" / "artifacts" / "results_summary.tsv",
        "\n".join(
            [
                "scope\tsplit_name\tprotocol\tsuccess_rate\tjson_parse_rate\tn_tasks",
                "overall\tmain\tno_skill\t0.500\t1.000\t240",
                "overall\tmain\traw_memory\t0.610\t1.000\t240",
                "overall\tmain\treflexion\t0.850\t1.000\t240",
                "overall\tmain\tstatic_skill_lib\t0.620\t1.000\t240",
                "overall\tmain\tskillcycle\t0.920\t1.000\t240",
            ]
        )
        + "\n",
    )
    _write_json(
        tmp_path / "experiments" / "transfer60" / "status.json",
        {
            "status": "done",
            "summaries": [
                {"method": "skillcycle", "episodes": 60, "successes": 55},
                {"method": "raw_memory", "episodes": 60, "successes": 36},
            ],
        },
    )

    issues = detect_quality_blockers(tmp_path)

    assert "results_summary_exceeds_run_evidence" in {issue.code for issue in issues}


def test_copied_records_do_not_count_as_240_unique_tasks(tmp_path: Path) -> None:
    _write_valid_quality_calibration(tmp_path, proposed_protocol="skillcycle")
    _write_full_results_summary(tmp_path)
    _write_json(
        tmp_path / "experiments" / "transfer240" / "status.json",
        {"status": "done", "summaries": [{"method": "skillcycle", "episodes": 240}]},
    )
    records = []
    for repeat_index in range(4):
        suffix = "" if repeat_index == 0 else f"_r{repeat_index + 1}"
        for task_index in range(60):
            records.append(
                {
                    "episode_id": f"pilot_task_{task_index:03d}{suffix}",
                    "family": "boundary_skill_cards",
                    "difficulty": "hard",
                    "gold_answer": f"A{task_index}",
                    "prediction": f"ANSWER: A{task_index}",
                    "success": True,
                }
            )
    _write_jsonl(tmp_path / "results" / "transfer240" / "skillcycle" / "records.jsonl", records)

    issues = detect_quality_blockers(tmp_path)

    assert "duplicated_benchmark_expansion" in {issue.code for issue in issues}


def test_renamed_duplicate_task_definitions_are_blocked(tmp_path: Path) -> None:
    records = [
        {
            "id": f"task_{task_index:03d}",
            "family": "constraint_filter_routing",
            "difficulty": "hard",
            "prompt": f"Route these requests for base case {task_index % 120}.",
            "gold_answer": f"A{task_index % 120}",
        }
        for task_index in range(240)
    ]
    _write_jsonl(tmp_path / "bench" / "boundary_trap_bench" / "all.jsonl", records)

    issues = detect_quality_blockers(tmp_path)

    assert "duplicated_benchmark_expansion" in {issue.code for issue in issues}


def test_unique_240_task_records_pass_duplicate_gate(tmp_path: Path) -> None:
    _write_valid_quality_calibration(tmp_path, proposed_protocol="skillcycle")
    _write_full_results_summary(tmp_path)
    records = [
        {
            "episode_id": f"task_{task_index:03d}",
            "family": "constraint_filter_routing",
            "difficulty": "hard",
            "prompt": f"Route unique request set {task_index}.",
            "gold_answer": f"A{task_index}",
            "prediction": f"ANSWER: A{task_index}",
            "success": True,
        }
        for task_index in range(240)
    ]
    _write_jsonl(tmp_path / "results" / "transfer240" / "skillcycle" / "records.jsonl", records)

    issues = detect_quality_blockers(tmp_path)

    assert "duplicated_benchmark_expansion" not in {issue.code for issue in issues}


def test_underpowered_benchmark_provenance_json_is_blocked(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "experiments" / "BENCHMARK_PROVENANCE.json",
        {
            "uses_public_benchmark": True,
            "benchmark_type": "public",
            "task_count": 60,
        },
    )

    issues = detect_quality_blockers(tmp_path)

    assert "underpowered_pilot" in {issue.code for issue in issues}


def test_synthetic_benchmark_requires_frontier_benchmark_survey(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "experiments" / "BENCHMARK_PROVENANCE.json",
        {
            "uses_public_benchmark": False,
            "benchmark_type": "synthetic",
            "task_count": 240,
        },
    )

    issues = detect_quality_blockers(tmp_path)

    assert "missing_benchmark_literature_survey" in {issue.code for issue in issues}


def test_synthetic_benchmark_survey_can_name_frontier_sources(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "experiments" / "BENCHMARK_PROVENANCE.json",
        {
            "uses_public_benchmark": False,
            "benchmark_type": "synthetic",
            "task_count": 240,
            "surveyed_benchmarks": [
                {"name": "ToolBench", "decision": "reject", "reason": "tool APIs mismatch"},
                {"name": "WebArena", "decision": "reject", "reason": "browser stack unavailable"},
            ],
        },
    )

    issues = detect_quality_blockers(tmp_path)

    assert "missing_benchmark_literature_survey" not in {issue.code for issue in issues}


def test_full_benchmark_requires_multiple_selected_sources(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "experiments" / "BENCHMARK_PROVENANCE.json",
        {
            "uses_public_benchmark": True,
            "benchmark_type": "public",
            "task_count": 240,
            "selected_benchmarks": [
                {
                    "name": "ToolBench",
                    "url": "https://github.com/OpenBMB/ToolBench",
                    "task_count": 240,
                }
            ],
        },
    )

    issues = detect_quality_blockers(tmp_path)

    assert "insufficient_selected_benchmark_sources" in {issue.code for issue in issues}


def test_selected_benchmark_sources_need_source_pointers(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "experiments" / "BENCHMARK_PROVENANCE.json",
        {
            "uses_public_benchmark": True,
            "benchmark_type": "hybrid",
            "task_count": 240,
            "selected_benchmarks": [
                {"name": "ToolBench", "task_count": 120},
                {"name": "WebArena", "task_count": 120},
                {"name": "GAIA", "task_count": 120},
            ],
        },
    )

    issues = detect_quality_blockers(tmp_path)

    assert "incomplete_selected_benchmark_sources" in {issue.code for issue in issues}


def test_multi_source_benchmark_provenance_passes_source_gate(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "experiments" / "BENCHMARK_PROVENANCE.json",
        {
            "uses_public_benchmark": True,
            "benchmark_type": "hybrid",
            "task_count": 240,
            "selected_benchmarks": [
                {
                    "name": "ToolBench",
                    "url": "https://github.com/OpenBMB/ToolBench",
                    "paper": "ToolLLM",
                    "version": "official repo snapshot",
                    "license": "recorded in benchmark repo",
                    "split": "sampled tool-use tasks",
                    "task_count": 120,
                    "rationale": "frontier practical tool-use coverage",
                },
                {
                    "name": "WebArena",
                    "url": "https://webarena.dev/",
                    "paper": "WebArena",
                    "version": "official site snapshot",
                    "license": "recorded in benchmark repo",
                    "split": "sampled web-agent tasks",
                    "task_count": 120,
                    "rationale": "realistic web task coverage",
                },
                {
                    "name": "GAIA",
                    "url": "https://huggingface.co/datasets/gaia-benchmark/GAIA",
                    "paper": "GAIA",
                    "version": "official dataset snapshot",
                    "license": "recorded in benchmark card",
                    "split": "sampled assistant tasks",
                    "task_count": 120,
                    "rationale": "multi-step assistant reasoning coverage",
                },
            ],
        },
    )

    issues = detect_quality_blockers(tmp_path)
    source_issue_codes = {
        "insufficient_selected_benchmark_sources",
        "incomplete_selected_benchmark_sources",
    }

    assert source_issue_codes.isdisjoint({issue.code for issue in issues})


def test_markdown_benchmark_provenance_table_counts_selected_sources(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "experiments" / "BENCHMARK_PROVENANCE.md",
        "\n".join(
            [
                "# Benchmark Provenance",
                "",
                "| Name | URL/repo | Paper/citation | Version/date | Unique task count contributed | Split/filtering | License/access | Capability / failure mode | Why selected | Alternatives surveyed |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                "| GAIA | https://huggingface.co/datasets/gaia-benchmark/GAIA | GAIA: A Benchmark for General AI Assistants | 2024 | 450 | sample 100 held-out tasks | public benchmark release | assistant reasoning and retrieval | General-assistant branch of the full benchmark mix. | AgentBench |",
                "| Online-Mind2Web | https://github.com/OSU-NLP-Group/Online-Mind2Web | An Illusion of Progress? Assessing the Current State of Web Agents | 2025 | 300 | sample 80 live tasks | MIT / CC BY 4.0 | live web navigation under drift | Live-web branch of the full benchmark mix. | WebArena |",
                "| TheAgentCompany | https://github.com/TheAgentCompany/TheAgentCompany | TheAgentCompany: Benchmarking LLM Agents on Consequential Real World Tasks | 2025 | 175 | sample 60 stable workflows | MIT code | chained office workflows | Work-branch coverage distinct from browsing. | SWE-bench |",
                "",
                "The default selected mix is GAIA + Online-Mind2Web + TheAgentCompany.",
            ]
        )
        + "\n",
    )

    issues = detect_quality_blockers(tmp_path)

    assert "insufficient_selected_benchmark_sources" not in {
        issue.code for issue in issues
    }


def test_planned_markdown_benchmark_sources_do_not_count_as_selected(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "experiments" / "BENCHMARK_PROVENANCE.md",
        "\n".join(
            [
                "# Benchmark Provenance",
                "",
                "| Name | URL/repo | Paper/citation | Version/date | Unique task count contributed | Split/filtering | License/access | Capability / failure mode | Why selected | Alternatives surveyed |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                "| SWE-bench Verified | https://github.com/swe-bench/SWE-bench | SWE-bench Verified | 2024 | 240 completed scored tasks | verified split | public benchmark release | code repair | Completed main source. | SWE-bench+ |",
                "| SWE-bench Multimodal | https://huggingface.co/datasets/SWE-bench/SWE-bench_Multimodal | SWE-bench Multimodal | 2024 | 80 diagnostic tasks planned | planned split | public benchmark release | visual bug fixing | Planned diagnostic. | SWE-bench |",
                "| RepoBench-P | https://github.com/Leolty/repobench | RepoBench | 2024 | 80 diagnostic tasks planned | planned split | public benchmark release | repo completion | Planned diagnostic. | CodeSearchNet |",
                "",
                "This is the final benchmark package.",
            ]
        )
        + "\n",
    )

    issues = detect_quality_blockers(tmp_path)

    assert "insufficient_selected_benchmark_sources" in {
        issue.code for issue in issues
    }


def test_ready_quality_calibration_cannot_keep_blocking_issues(tmp_path: Path) -> None:
    _write_valid_quality_calibration(tmp_path, proposed_protocol="skillcycle")
    path = tmp_path / "paper" / "PAPER_QUALITY_CALIBRATION.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["blocking_issues"] = [
        {"issue": "layout_review_failed", "required_fix": "revise figure"}
    ]
    _write_json(path, payload)

    issues = validate_quality_calibration_file(tmp_path)

    assert "ready_quality_calibration_with_blocking_issues" in {
        issue.code for issue in issues
    }


def _write_fresh_demo_pattern(tmp_path: Path) -> None:
    _write(
        tmp_path / "paper" / "artifacts" / "results_summary.tsv",
        "\n".join(
            [
                "scope\tprotocol\tn_tasks\tsuccess_rate\tjson_parse_rate",
                "overall\tdirect\t24\t0.7083333333333334\t0.9583333333333334",
                "overall\tvisible_checklist\t24\t0.4166666666666667\t0.7083333333333334",
                "overall\thidden_checklist_final_only\t24\t0.625\t0.9166666666666666",
            ]
        )
        + "\n",
    )
    _write(
        tmp_path / "experiments" / "BENCHMARK_PROVENANCE.md",
        "This is a synthetic pilot. No public benchmark yet.\n",
    )
    _write(
        tmp_path / "paper" / "PAPER_DRAFT_REPORT.md",
        "Draft quality: short-paper / pilot-note hybrid, not full EMNLP quality.\n",
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: object) -> None:
    _write(path, json.dumps(payload) + "\n")


def _write_jsonl(path: Path, payloads: Sequence[object]) -> None:
    _write(path, "".join(json.dumps(payload) + "\n" for payload in payloads))


def _write_full_results_summary(tmp_path: Path) -> None:
    _write(
        tmp_path / "paper" / "artifacts" / "results_summary.tsv",
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


def _write_valid_quality_calibration(
    tmp_path: Path,
    *,
    proposed_protocol: str,
) -> None:
    _write_valid_benchmark_provenance(tmp_path)
    _write_valid_model_scale_plan(tmp_path)
    _write(tmp_path / "paper" / "artifacts" / "significance.tsv", "test\tp\nmcnemar\t0.01\n")
    _write_json(
        tmp_path / "paper" / "PAPER_QUALITY_CALIBRATION.json",
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
                "proposed_protocol": proposed_protocol,
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
                    "signals_used": ["nontrivial_technical_contribution"],
                }
            ],
        },
    )


def _write_valid_benchmark_provenance(tmp_path: Path) -> None:
    _write(
        tmp_path / "experiments" / "BENCHMARK_PROVENANCE.md",
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


def _write_valid_model_scale_plan(tmp_path: Path) -> None:
    _write(
        tmp_path / "experiments" / "MODEL_SCALE_PLAN.md",
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
