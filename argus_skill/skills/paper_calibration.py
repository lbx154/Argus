"""Paper-quality calibration cases and blocker detectors.

This module intentionally stores only metadata and quality signals. Published
paper text should be fetched or read only from sources whose license permits the
intended use, and generated papers must never copy exemplar prose.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RESULTS_SUMMARY_PATH = Path("paper/artifacts/results_summary.tsv")
BENCHMARK_PROVENANCE_MD_PATH = Path("experiments/BENCHMARK_PROVENANCE.md")
BENCHMARK_PROVENANCE_JSON_PATH = Path("experiments/BENCHMARK_PROVENANCE.json")
PAPER_DRAFT_REPORT_MD_PATH = Path("paper/PAPER_DRAFT_REPORT.md")
PAPER_DRAFT_REPORT_JSON_PATH = Path("paper/PAPER_DRAFT_REPORT.json")
PAPER_QUALITY_CALIBRATION_JSON_PATH = Path("paper/PAPER_QUALITY_CALIBRATION.json")
MODEL_SCALE_PLAN_PATH = Path("experiments/MODEL_SCALE_PLAN.md")
PAPER_NARRATIVE_PATHS = (
    Path("paper/RESULTS_REPORT.md"),
    Path("paper/main.tex"),
)
MODEL_SCALE_EVIDENCE_GLOBS = (
    "experiments/MODEL_SCALE_PLAN.md",
    "experiments/**/model_card.json",
    "experiments/**/training_metrics.jsonl",
    "experiments/**/manifest.json",
    "research/EXPERIMENT_PLAN.md",
    "research/BASELINE_AND_BENCHMARK_PLAN.md",
)
EXPERIMENT_STATUS_GLOB = "experiments/**/status.json"
EXPERIMENT_PROGRESS_GLOB = "experiments/**/progress.jsonl"
EXPERIMENT_SUMMARY_GLOB = "experiments/**/summary.json"
BENCHMARK_RECORD_GLOBS = (
    "bench/**/*.jsonl",
    "benchmarks/**/*.jsonl",
    "experiments/**/records.jsonl",
    "results/**/records.jsonl",
)

QUALITY_CALIBRATION_VERDICTS = (
    "PASS",
    "WARN",
    "FAIL",
    "BLOCKED",
    "ERROR",
    "NOT_APPLICABLE",
)
READY_VERDICTS = {"PASS", "WARN"}
MIN_PAPER_TASKS = 200
PAPER_TASK_SCALE_TARGET = "3-source final-evidence"
MIN_SELECTED_BENCHMARK_SOURCES = 3
RECOMMENDED_SELECTED_BENCHMARK_SOURCES = 3
KNOWN_BENCHMARK_FAMILY_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bswe[-_\s]?bench\b|\bswebench\b", "swe-bench"),
    (r"\btool[-_\s]?bench\b|\btooleval\b", "toolbench"),
    (r"\bweb[-_\s]?arena\b", "webarena"),
    (r"\bmind2web\b|\bonline[-_\s]?mind2web\b", "mind2web"),
    (r"\bminiwo\b|\bminiwob\b|\bminiwob\+\+\b", "miniwob"),
    (r"\bgaia\b", "gaia"),
    (r"\bagent[-_\s]?bench\b", "agentbench"),
    (r"\balfworld\b", "alfworld"),
    (r"\bmulti[-_\s]?agent[-_\s]?bench\b", "multiagentbench"),
    (r"\blocomo\b", "locomo"),
    (r"\brepo[-_\s]?bench\b", "repobench"),
    (r"\bcode[-_\s]?search[-_\s]?net\b", "codesearchnet"),
    (r"\bthe[-_\s]?agent[-_\s]?company\b", "theagentcompany"),
)
BENCHMARK_VARIANT_TOKENS = {
    "benchmark",
    "bench",
    "verified",
    "lite",
    "multimodal",
    "multi",
    "modal",
    "full",
    "official",
    "release",
    "component",
    "components",
    "source",
    "sources",
    "suite",
    "dataset",
    "tasks",
    "episodes",
    "split",
    "test",
    "train",
    "dev",
    "validation",
    "held",
    "out",
    "sample",
    "sampled",
}
BENCHMARK_TASK_COUNT_FIELDS = {
    "task_count",
    "n_tasks",
    "num_tasks",
    "total_tasks",
    "scored_tasks",
    "episodes",
    "episode_count",
    "n_episodes",
    "num_episodes",
    "total_episodes",
    "sample_count",
    "n_samples",
}
RESULTS_TASK_COUNT_FIELDS = (
    "n_tasks",
    "task_count",
    "tasks",
    "scored_tasks",
    "n_episodes",
    "episodes",
    "episode_count",
    "total_episodes",
)
PLANNED_COUNT_KEY_MARKERS = ("planned", "target", "proposed", "intended", "future")
PLANNED_COUNT_TEXT_MARKERS = (
    "planned",
    "target",
    "proposed",
    "intended",
    "future",
    "will",
)
PLANNED_SOURCE_TEXT_MARKERS = (
    "planned",
    "diagnostic tasks planned",
    "not yet run",
    "not executed",
    "pending",
    "future",
    "todo",
    "nice-to-have",
    "blocked",
)
ACTUAL_COUNT_TEXT_MARKERS = (
    "actual",
    "completed",
    "complete",
    "evaluated",
    "executed",
    "finished",
    "scored",
    "raw rows",
    "released",
    "canonical run",
    "main split",
)
BENCHMARK_SURVEY_JSON_FIELDS = (
    "surveyed_benchmarks",
    "benchmark_survey",
    "surveyed_benchmark_alternatives",
    "public_benchmark_survey",
    "frontier_benchmark_survey",
    "benchmark_sources_considered",
)
SELECTED_BENCHMARK_JSON_FIELDS = (
    "selected_benchmarks",
    "selected_benchmark_sources",
    "benchmark_sources",
    "evaluation_benchmarks",
    "benchmark_mix",
    "benchmark_components",
    "public_benchmarks",
    "source_benchmarks",
)
BENCHMARK_SOURCE_NAME_FIELDS = (
    "name",
    "benchmark",
    "benchmark_name",
    "dataset",
    "suite",
    "source",
    "title",
    "id",
)
BENCHMARK_SOURCE_POINTER_FIELDS = (
    "url",
    "source_url",
    "repo",
    "repository",
    "paper",
    "paper_url",
    "citation",
    "doi",
)
BENCHMARK_SOURCE_DETAIL_FIELDS = (
    "license",
    "version",
    "retrieved_on",
    "task_count",
    "split",
    "rationale",
)
BENCHMARK_SOURCE_POINTER_MARKERS = (
    "http://",
    "https://",
    "doi:",
    "arxiv",
    "acl anthology",
    "github.com",
    "papers with code",
)
FRONTIER_BENCHMARK_TEXT_MARKERS = (
    "toolbench",
    "tooleval",
    "webarena",
    "miniwo",
    "mind2web",
    "gaia",
    "agentbench",
    "alfworld",
    "multiagentbench",
    "swe-bench",
    "locomo",
    "acl anthology",
    "surveyed benchmark",
    "benchmark alternatives",
    "public benchmark survey",
)
SELECTED_BENCHMARK_TEXT_MARKERS = (
    "selected benchmark",
    "selected benchmarks",
    "selected benchmark sources",
    "benchmark source table",
    "benchmark mix",
    "evaluation benchmark mix",
    "public benchmark components",
)
SELECTED_BENCHMARK_COUNT_RE = re.compile(
    r"\b(?:selected benchmark sources?|selected benchmarks?|benchmark sources?|"
    r"benchmark components?|benchmark suites?)\b\D{0,40}(\d{1,3})",
    re.I,
)
COPY_EXPANSION_SUFFIX_RE = re.compile(
    r"(?:[_-](?:r|rep|repeat|copy|dup|duplicate)\d*)+$",
    re.I,
)
RECORD_ID_FIELDS = ("episode_id", "id", "task_id", "sample_id")
TASK_DEFINITION_FIELDS = (
    "prompt",
    "input",
    "question",
    "request",
    "task",
    "task_input",
    "scenario",
    "case",
    "spec",
    "messages",
    "instruction",
)
NON_TASK_SIGNATURE_FIELDS = {
    "episode_id",
    "id",
    "task_id",
    "sample_id",
    "prediction",
    "predicted_answer",
    "success",
    "trace",
    "method",
    "protocol",
    "variant",
    "model",
    "started_at",
    "finished_at",
    "timestamp",
    "prompt_tokens",
    "completion_tokens",
    "latency",
    "cost",
}
BENCHMARK_TASK_COUNT_RE = re.compile(
    r"\b(?:task count|tasks|scored tasks|episodes?|n_tasks|samples?)\b\D{0,24}(\d{1,6})",
    re.I,
)

REQUIRED_QUALITY_SIGNAL_KEYS = (
    "uses_public_benchmark",
    "beats_nontrivial_baseline",
    "proposed_contribution_beats_strong_baseline",
    "statistical_support_for_headline",
    "n_tasks_meets_threshold",
    "parser_schema_confound_cleared",
)
SUBMISSION_QUALITY_ASSESSMENTS = ("ready", "pilot", "not_ready", "blocked")
REQUIRED_PAPER_CONTRIBUTION_FIELDS = (
    "contribution_sentence",
    "proposed_artifact",
    "proposed_protocol",
    "primary_metric",
    "metric_direction",
    "primary_split",
    "primary_baselines",
    "primary_improvement",
    "mechanism",
    "positive_headline_supported",
    "negative_result",
    "statistical_support",
)
METRIC_DIRECTIONS = ("higher_is_better", "lower_is_better")
TRIVIAL_BASELINE_PROTOCOLS = {
    "",
    "baseline",
    "direct",
    "none",
    "no_skill",
    "zero_shot",
}
NEGATIVE_RESULT_MARKERS = (
    "benchmark-focused negative-result paper",
    "does not support the originally planned method-positive thesis",
    "method-positive thesis was softened",
    "negative result for skill libraries",
    "original method-positive thesis was softened",
    "proposed method fails",
    "not as the main positive claim",
)
TINY_MODEL_MARKERS = (
    "bag-of-words",
    "bag of words",
    "hashed token",
    "hashed-token",
    "keyword overlap",
    "lexical baseline",
    "lexical ranker",
    "compact scorer",
    "tiny scorer",
    "small scorer",
    "linear scorer",
    "exact lookahead",
    "exhaustive gold search",
    "oracle policy",
    "prompt-only wrapper",
)
MODEL_SCALE_POSITIVE_MARKERS = (
    "parameter count",
    "trainable parameter",
    "trainable parameters",
    "checkpoint",
    "adapter",
    "lora",
    "qlora",
    "fsdp",
    "deepspeed",
    "accelerate",
    "fine-tune",
    "finetune",
    "trained backbone",
    "model backbone",
    "gpu-hours",
    "gpu hours",
    "gpu memory",
)
SYNTHETIC_BENCHMARK_MARKERS = (
    "synthetic",
    "local",
    "proxy",
    "generated",
    "hand-written",
    "handwritten",
    "oracle graph",
)

_AWARDS_SOURCE = "https://2025.emnlp.org/program/awards/"
_RETRIEVED_ON = "2026-05-23"


@dataclass(frozen=True)
class CalibrationCase:
    """A positive or negative paper-quality calibration case."""

    case_id: str
    polarity: str
    title: str
    venue: str
    year: int
    source_url: str
    retrieved_on: str
    expected_gate: str
    quality_signals: tuple[str, ...]
    authors: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class CalibrationIssue:
    """A stable machine-readable quality blocker."""

    code: str
    path: str
    message: str


CALIBRATION_CASES: tuple[CalibrationCase, ...] = (
    CalibrationCase(
        case_id="negative:fresh-demo-pilot-pattern",
        polarity="negative",
        title="Synthetic checklist self-verification pilot pattern",
        venue="local calibration",
        year=2026,
        source_url="argus-calibration://fresh-demo-pilot-pattern",
        retrieved_on="2026-05-23",
        expected_gate="FAIL_OR_PIVOT",
        quality_signals=(
            "baseline_not_beaten",
            "synthetic_only_benchmark",
            "underpowered_pilot",
            "parser_or_schema_confound",
            "draft_self_reports_not_submission_quality",
        ),
        notes=(
            "Negative gold pattern: a complete-looking ACL-style PDF with artifacts, "
            "but weak pilot evidence and no submission assurance."
        ),
    ),
    CalibrationCase(
        case_id="positive:emnlp2025-best-infini-gram-mini",
        polarity="positive",
        title="Infini-gram mini: Exact n-gram Search at the Internet Scale with FM-Index",
        venue="EMNLP",
        year=2025,
        source_url=_AWARDS_SOURCE,
        retrieved_on=_RETRIEVED_ON,
        expected_gate="QUALITY_SIGNAL_SOURCE",
        authors=(
            "Hao Xu",
            "Jiacheng Liu",
            "Yejin Choi",
            "Noah A. Smith",
            "Hannaneh Hajishirzi",
        ),
        quality_signals=(
            "clear_problem_with_broad_relevance",
            "nontrivial_technical_contribution",
            "scale_or_resource_evidence",
            "practical_audit_use_case",
        ),
    ),
    CalibrationCase(
        case_id="positive:emnlp2025-outstanding-linggym",
        polarity="positive",
        title="LingGym: How Far Are LLMs from Thinking Like Field Linguists?",
        venue="EMNLP",
        year=2025,
        source_url=_AWARDS_SOURCE,
        retrieved_on=_RETRIEVED_ON,
        expected_gate="QUALITY_SIGNAL_SOURCE",
        authors=("Changbing Yang", "Franklin Ma", "Freda Shi", "Jian Zhu"),
        quality_signals=(
            "clear_evaluation_question",
            "benchmark_or_resource_contribution",
            "domain_relevance_beyond_toy_tasks",
        ),
    ),
    CalibrationCase(
        case_id="positive:emnlp2025-outstanding-value-action-gap",
        polarity="positive",
        title="Mind the Value-Action Gap: Do LLMs Act in Alignment with Their Values?",
        venue="EMNLP",
        year=2025,
        source_url=_AWARDS_SOURCE,
        retrieved_on=_RETRIEVED_ON,
        expected_gate="QUALITY_SIGNAL_SOURCE",
        authors=("Hua Shen", "Nicholas Clark", "Tanu Mitra"),
        quality_signals=(
            "clear_behavioral_construct",
            "evaluation_framework",
            "claim_scope_matches_measurement",
        ),
    ),
    CalibrationCase(
        case_id="positive:emnlp2025-outstanding-discosg",
        polarity="positive",
        title=(
            "DiscoSG: Towards Discourse-Level Text Scene Graph Parsing through "
            "Iterative Graph Refinement"
        ),
        venue="EMNLP",
        year=2025,
        source_url=_AWARDS_SOURCE,
        retrieved_on=_RETRIEVED_ON,
        expected_gate="QUALITY_SIGNAL_SOURCE",
        authors=(
            "Shaoqing Lin",
            "Chong Teng",
            "Fei Li",
            "Donghong Ji",
            "Lizhen Qu",
            "Zhuang Li",
        ),
        quality_signals=(
            "task_defines_nontrivial_structure",
            "method_matches_task_difficulty",
            "evaluation_targets_core_claim",
        ),
    ),
    CalibrationCase(
        case_id="positive:emnlp2025-outstanding-generative-discriminative",
        polarity="positive",
        title="Generative or Discriminative? Revisiting Text Classification in the Era of Transformers",
        venue="EMNLP",
        year=2025,
        source_url=_AWARDS_SOURCE,
        retrieved_on=_RETRIEVED_ON,
        expected_gate="QUALITY_SIGNAL_SOURCE",
        authors=(
            "Siva Rajesh Kasa",
            "Karan Gupta",
            "Sumegh Roychowdhury",
            "Ashutosh Kumar",
            "Yaswanth Biruduraju",
            "Santhoh Kumar Kasa",
            "Pattisapu Nikhil Priyatam",
            "Arindam Bhattacharya",
            "Shailendra Agarwal",
            "Vijay Huddar",
        ),
        quality_signals=(
            "strong_baseline_framing",
            "large_scale_comparison",
            "revisits_common_assumption_with_evidence",
        ),
    ),
)


def calibration_cases() -> tuple[CalibrationCase, ...]:
    """Return built-in paper-quality calibration metadata."""

    return CALIBRATION_CASES


def calibration_cases_payload() -> list[dict[str, Any]]:
    """Return JSON-serializable calibration metadata."""

    return [
        {
            "case_id": case.case_id,
            "polarity": case.polarity,
            "title": case.title,
            "venue": case.venue,
            "year": case.year,
            "source_url": case.source_url,
            "retrieved_on": case.retrieved_on,
            "expected_gate": case.expected_gate,
            "quality_signals": list(case.quality_signals),
            "authors": list(case.authors),
            "notes": case.notes,
        }
        for case in CALIBRATION_CASES
    ]


def validate_quality_calibration_file(project_root: Path) -> list[CalibrationIssue]:
    """Validate ``paper/PAPER_QUALITY_CALIBRATION.json``."""

    root = Path(project_root)
    path = root / PAPER_QUALITY_CALIBRATION_JSON_PATH
    if not path.exists():
        return [
            CalibrationIssue(
                "missing_paper_quality_calibration",
                str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                "paper quality calibration JSON is missing",
            )
        ]

    try:
        payload = _read_json_object(path)
    except ValueError as exc:
        return [
            CalibrationIssue(
                "invalid_paper_quality_calibration_json",
                str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                str(exc),
            )
        ]

    issues: list[CalibrationIssue] = []
    verdict = payload.get("verdict")
    if not isinstance(verdict, str) or verdict not in QUALITY_CALIBRATION_VERDICTS:
        issues.append(
            CalibrationIssue(
                "invalid_quality_calibration_verdict",
                str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                f"verdict must be one of {', '.join(QUALITY_CALIBRATION_VERDICTS)}",
            )
        )

    blocking_issues = payload.get("blocking_issues")
    if verdict in READY_VERDICTS and isinstance(blocking_issues, list) and blocking_issues:
        issues.append(
            CalibrationIssue(
                "ready_quality_calibration_with_blocking_issues",
                str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                "ready quality calibration cannot still list blocking_issues",
            )
        )

    quality_signals = payload.get("quality_signals")
    if not isinstance(quality_signals, dict):
        issues.append(
            CalibrationIssue(
                "missing_quality_signals",
                str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                "quality_signals must be an object with explicit booleans",
            )
        )
    else:
        issues.extend(_validate_quality_signals(verdict, quality_signals))

    issues.extend(_validate_paper_contribution(root, verdict, payload))

    negative_regressions = payload.get("negative_case_regressions")
    if not isinstance(negative_regressions, list):
        issues.append(
            CalibrationIssue(
                "missing_negative_case_regressions",
                str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                "negative_case_regressions must be a list, even when empty",
            )
        )
    elif verdict in READY_VERDICTS and _has_hard_negative_match(negative_regressions):
        issues.append(
            CalibrationIssue(
                "ready_verdict_matches_negative_regression",
                str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                "ready verdict cannot match a hard negative calibration pattern",
            )
        )

    positive_signals = payload.get("quality_signals_from_positive_examples")
    if not isinstance(positive_signals, list) or not positive_signals:
        issues.append(
            CalibrationIssue(
                "missing_positive_calibration_signals",
                str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                "record quality signals derived from positive examples, not copied prose",
            )
        )

    return issues


def detect_quality_blockers(project_root: Path) -> list[CalibrationIssue]:
    """Detect quality blockers that should prevent PASS/WARN readiness."""

    root = Path(project_root)
    issues: list[CalibrationIssue] = []
    issues.extend(_quality_signal_blockers(root))
    issues.extend(_results_summary_blockers(root))
    issues.extend(_benchmark_uniqueness_blockers(root))
    issues.extend(_benchmark_provenance_blockers(root))
    issues.extend(_model_scale_blockers(root))
    issues.extend(_draft_report_blockers(root))
    issues.extend(_negative_result_framing_blockers(root))
    return _dedupe_issues(issues)


def _validate_quality_signals(
    verdict: object,
    quality_signals: dict[str, Any],
) -> list[CalibrationIssue]:
    issues: list[CalibrationIssue] = []
    for key in REQUIRED_QUALITY_SIGNAL_KEYS:
        value = quality_signals.get(key)
        if not isinstance(value, bool):
            issues.append(
                CalibrationIssue(
                    "invalid_quality_signal",
                    str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                    f"quality_signals.{key} must be a boolean",
                )
            )
        elif verdict in READY_VERDICTS and not value:
            issues.append(
                CalibrationIssue(
                    "ready_verdict_with_failed_quality_signal",
                    str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                    f"ready verdict cannot have quality_signals.{key}=false",
                )
            )

    assessment = quality_signals.get("submission_quality_self_assessment")
    if not isinstance(assessment, str) or assessment not in SUBMISSION_QUALITY_ASSESSMENTS:
        issues.append(
            CalibrationIssue(
                "invalid_submission_quality_self_assessment",
                str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                "submission_quality_self_assessment must be ready, pilot, not_ready, or blocked",
            )
        )
    elif verdict in READY_VERDICTS and assessment != "ready":
        issues.append(
            CalibrationIssue(
                "ready_verdict_with_not_ready_self_assessment",
                str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                f"ready verdict cannot self-assess as {assessment!r}",
            )
        )
    return issues


def _validate_paper_contribution(
    root: Path,
    verdict: object,
    payload: dict[str, Any],
) -> list[CalibrationIssue]:
    contribution = payload.get("paper_contribution")
    if not isinstance(contribution, (dict, str)) or not contribution:
        if verdict in READY_VERDICTS:
            return [
                CalibrationIssue(
                    "missing_paper_contribution",
                    str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                    (
                        "ready calibration must declare paper_contribution with "
                        "the proposed artifact, protocol, metric, baselines, "
                        "mechanism, and statistical support for the headline claim"
                    ),
                )
            ]
        return []

    # Accept both string (contribution sentence) and structured dict
    if isinstance(contribution, str):
        return []  # string contribution is sufficient

    if verdict not in READY_VERDICTS:
        # Blocked calibrations are allowed to carry a benchmark-local no-go claim
        # without satisfying the ready-paper contribution schema.
        return []

    issues: list[CalibrationIssue] = []
    for field in REQUIRED_PAPER_CONTRIBUTION_FIELDS:
        if field not in contribution:
            issues.append(
                CalibrationIssue(
                    "missing_paper_contribution_field",
                    str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                    f"paper_contribution.{field} is required",
                )
            )

    for field in (
        "contribution_sentence",
        "proposed_artifact",
        "proposed_protocol",
        "primary_metric",
        "primary_split",
        "primary_improvement",
        "mechanism",
    ):
        value = contribution.get(field)
        if not _nonempty_string(value):
            issues.append(
                CalibrationIssue(
                    "invalid_paper_contribution_field",
                    str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                    f"paper_contribution.{field} must be a non-empty string",
                )
            )

    sentence = contribution.get("contribution_sentence")
    if isinstance(sentence, str):
        lowered = sentence.lower()
        if (
            "we propose" not in lowered
            or "we show" not in lowered
            or "improv" not in lowered
            or not any(character.isdigit() for character in sentence)
        ):
            issues.append(
                CalibrationIssue(
                    "invalid_contribution_sentence_shape",
                    str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                    (
                        "contribution_sentence must follow the research.md shape: "
                        "'We propose X. We show X improves Y by Z because W.'"
                    ),
                )
            )

    direction = contribution.get("metric_direction")
    if direction not in METRIC_DIRECTIONS:
        issues.append(
            CalibrationIssue(
                "invalid_metric_direction",
                str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                "paper_contribution.metric_direction must be higher_is_better or lower_is_better",
            )
        )

    baselines = contribution.get("primary_baselines")
    if not (
        isinstance(baselines, list)
        and baselines
        and all(_nonempty_string(item) for item in baselines)
    ):
        issues.append(
            CalibrationIssue(
                "invalid_primary_baselines",
                str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                "paper_contribution.primary_baselines must be a non-empty list of protocol names",
            )
        )

    if contribution.get("positive_headline_supported") is not True:
        issues.append(
            CalibrationIssue(
                "positive_headline_not_supported",
                str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                "paper_contribution.positive_headline_supported must be true for a ready EMNLP paper",
            )
        )

    if contribution.get("negative_result") is not False:
        issues.append(
            CalibrationIssue(
                "ready_verdict_with_negative_result",
                str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                "this final EMNLP objective requires a positive headline, not a negative-result pivot",
            )
        )

    statistical_support = contribution.get("statistical_support")
    if not isinstance(statistical_support, dict):
        issues.append(
            CalibrationIssue(
                "invalid_statistical_support",
                str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                "paper_contribution.statistical_support must be an object with an artifact_path",
            )
        )
    else:
        raw_artifact_path = statistical_support.get("artifact_path")
        if not _nonempty_string(raw_artifact_path):
            issues.append(
                CalibrationIssue(
                    "missing_statistical_support_artifact",
                    str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                    "paper_contribution.statistical_support.artifact_path is required",
                )
            )
        else:
            artifact_path = str(raw_artifact_path)
            if verdict in READY_VERDICTS and not (root / artifact_path).exists():
                issues.append(
                    CalibrationIssue(
                        "missing_statistical_support_artifact",
                        artifact_path,
                        "headline statistical support artifact does not exist",
                    )
            )

    return issues


def _has_hard_negative_match(regressions: list[object]) -> bool:
    for regression in regressions:
        if not isinstance(regression, dict):
            continue
        if regression.get("matched") is True and regression.get("hard_failure") is True:
            return True
    return False


def _quality_signal_blockers(root: Path) -> list[CalibrationIssue]:
    payload = _read_json_object_if_exists(root / PAPER_QUALITY_CALIBRATION_JSON_PATH)
    if payload is None:
        return []
    quality_signals = payload.get("quality_signals")
    if not isinstance(quality_signals, dict):
        return []

    issues: list[CalibrationIssue] = []
    if quality_signals.get("beats_nontrivial_baseline") is False:
        issues.append(
            CalibrationIssue(
                "baseline_not_beaten",
                str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                "candidate claim does not beat a non-trivial baseline",
            )
        )
    if quality_signals.get("proposed_contribution_beats_strong_baseline") is False:
        issues.append(
            CalibrationIssue(
                "proposed_claim_not_supported",
                str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                "proposed contribution does not beat the strongest non-trivial baseline",
            )
        )
    if quality_signals.get("statistical_support_for_headline") is False:
        issues.append(
            CalibrationIssue(
                "headline_lacks_statistical_support",
                str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                "headline improvement lacks explicit statistical support",
            )
        )
    if quality_signals.get("uses_public_benchmark") is False:
        issues.append(
            CalibrationIssue(
                "synthetic_only_benchmark",
                str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                "candidate relies on local/synthetic-only benchmark evidence",
            )
        )
    if quality_signals.get("n_tasks_meets_threshold") is False:
        issues.append(
            CalibrationIssue(
                "underpowered_pilot",
                str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                (
                    f"task count does not meet the minimum full-paper threshold "
                    f"of {MIN_PAPER_TASKS} scored tasks (target scale {PAPER_TASK_SCALE_TARGET})"
                ),
            )
        )
    if quality_signals.get("parser_schema_confound_cleared") is False:
        issues.append(
            CalibrationIssue(
                "parser_or_schema_confound",
                str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                "parser/schema confound remains uncleared",
            )
        )

    assessment = quality_signals.get("submission_quality_self_assessment")
    if isinstance(assessment, str) and assessment != "ready":
        issues.append(
            CalibrationIssue(
                "draft_self_reports_not_submission_quality",
                str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                f"draft self-assessment is {assessment!r}, not ready",
            )
        )
    return issues


def _results_summary_blockers(root: Path) -> list[CalibrationIssue]:
    path = root / RESULTS_SUMMARY_PATH
    rows = _read_tsv_rows(path)
    issues: list[CalibrationIssue] = []
    calibration = _read_json_object_if_exists(root / PAPER_QUALITY_CALIBRATION_JSON_PATH)
    if not rows:
        if _ready_calibration(calibration):
            issues.append(
                CalibrationIssue(
                    "missing_results_summary",
                    str(RESULTS_SUMMARY_PATH),
                    "ready calibration requires a machine-checkable results summary TSV",
                )
            )
        return issues

    contribution = (
        calibration.get("paper_contribution")
        if isinstance(calibration, dict)
        and isinstance(calibration.get("paper_contribution"), dict)
        else None
    )
    quality_signals = (
        calibration.get("quality_signals")
        if isinstance(calibration, dict)
        and isinstance(calibration.get("quality_signals"), dict)
        else {}
    )

    if contribution is not None:
        issues.extend(
            _headline_result_blockers(
                rows,
                contribution=contribution,
                quality_signals=quality_signals,
            )
        )
    else:
        issues.extend(_legacy_baseline_blockers(rows))

    task_counts = _actual_results_task_counts(rows)
    if task_counts and max(task_counts) < MIN_PAPER_TASKS:
        issues.append(
            CalibrationIssue(
                "underpowered_pilot",
                str(RESULTS_SUMMARY_PATH),
                (
                    f"maximum reported overall task count {max(task_counts)} is below "
                    f"the minimum full-paper threshold {MIN_PAPER_TASKS} "
                    f"(target scale {PAPER_TASK_SCALE_TARGET})"
                ),
            )
        )
    elif (
        _ready_calibration(calibration)
        and isinstance(quality_signals, dict)
        and quality_signals.get("n_tasks_meets_threshold") is True
        and not task_counts
    ):
        issues.append(
            CalibrationIssue(
                "missing_scored_task_count",
                str(RESULTS_SUMMARY_PATH),
                (
                    "ready calibration requires protocol-level results_summary.tsv "
                    f"rows with scored task counts >= {MIN_PAPER_TASKS}; planned "
                    "benchmark counts or provenance prose are not sufficient"
                ),
            )
        )

    evidence_capacity = _experiment_scored_task_capacity(root)
    if (
        task_counts
        and evidence_capacity is not None
        and max(task_counts) > evidence_capacity
    ):
        issues.append(
            CalibrationIssue(
                "results_summary_exceeds_run_evidence",
                str(RESULTS_SUMMARY_PATH),
                (
                    f"results_summary.tsv reports up to {max(task_counts)} scored tasks, "
                    f"but experiment run artifacts support at most {evidence_capacity} "
                    "scored tasks for any method after aggregating executed source "
                    "runs; rerun experiments instead of only editing summary artifacts"
                ),
            )
        )

    issues.extend(_parse_rate_blockers(rows))

    return issues


def _legacy_baseline_blockers(rows: list[dict[str, str]]) -> list[CalibrationIssue]:
    overall = {
        _normalize_protocol(row.get("protocol", "")): row
        for row in rows
        if _is_overall_row(row) and row.get("protocol")
    }
    baseline = overall.get("direct") or overall.get("no_skill")
    if not baseline:
        return []

    baseline_success = _float_or_none(baseline.get("success_rate"))
    non_baseline_successes = [
        value
        for protocol, row in overall.items()
        if protocol not in TRIVIAL_BASELINE_PROTOCOLS
        for value in [_float_or_none(row.get("success_rate"))]
        if value is not None
    ]
    if (
        baseline_success is not None
        and non_baseline_successes
        and max(non_baseline_successes) <= baseline_success
    ):
        return [
            CalibrationIssue(
                "baseline_not_beaten",
                str(RESULTS_SUMMARY_PATH),
                "no non-trivial protocol beats the trivial baseline on overall success_rate",
            )
        ]
    return []


def _headline_result_blockers(
    rows: list[dict[str, str]],
    *,
    contribution: dict[str, Any],
    quality_signals: object,
) -> list[CalibrationIssue]:
    proposed_protocol = _string_or_none(contribution.get("proposed_protocol"))
    metric = _string_or_none(contribution.get("primary_metric"))
    direction = _string_or_none(contribution.get("metric_direction"))
    primary_split = _string_or_none(contribution.get("primary_split"))
    if (
        proposed_protocol is None
        or metric is None
        or direction not in METRIC_DIRECTIONS
        or primary_split is None
    ):
        return []

    overall_rows = [row for row in rows if _is_overall_row(row)]
    reported_splits = sorted({_row_split(row) for row in overall_rows})
    if not reported_splits:
        return []

    issues: list[CalibrationIssue] = []
    computed_support = True
    if primary_split in {"all", "all_reported"}:
        required_splits = reported_splits
    elif primary_split not in reported_splits:
        computed_support = False
        required_splits = []
        issues.append(
            CalibrationIssue(
                "unknown_primary_split",
                str(RESULTS_SUMMARY_PATH),
                (
                    f"paper_contribution.primary_split={primary_split!r} is not in "
                    f"reported splits {', '.join(reported_splits)}"
                ),
            )
        )
    else:
        required_splits = [primary_split]

    for heldout_name in ("public_validation", "heldout", "test"):
        if heldout_name in reported_splits and heldout_name not in required_splits:
            required_splits.append(heldout_name)
    baseline_names = _baseline_names(contribution.get("primary_baselines"))
    proposed_norm = _normalize_protocol(proposed_protocol)
    proposed_norm = _normalize_protocol(proposed_protocol)
    for split in required_splits:
        split_rows = [row for row in overall_rows if _row_split(row) == split]
        by_protocol = {
            _normalize_protocol(row.get("protocol", "")): row
            for row in split_rows
            if row.get("protocol")
        }
        proposed_row = by_protocol.get(proposed_norm)
        if proposed_row is None:
            computed_support = False
            issues.append(
                CalibrationIssue(
                    "proposed_result_missing",
                    str(RESULTS_SUMMARY_PATH),
                    f"missing results row for proposed protocol {proposed_protocol!r} on split {split!r}",
                )
            )
            continue

        proposed_score = _float_or_none(proposed_row.get(metric))
        if proposed_score is None:
            computed_support = False
            issues.append(
                CalibrationIssue(
                    "primary_metric_missing",
                    str(RESULTS_SUMMARY_PATH),
                    (
                        f"proposed protocol {proposed_protocol!r} on split {split!r} "
                        f"does not report primary metric {metric!r}"
                    ),
                )
            )
            continue

        missing_declared = sorted(baseline_names - set(by_protocol))
        if missing_declared:
            computed_support = False
            issues.append(
                CalibrationIssue(
                    "missing_declared_baseline_result",
                    str(RESULTS_SUMMARY_PATH),
                    (
                        f"declared baseline result(s) missing on split {split!r}: "
                        f"{', '.join(missing_declared)}"
                    ),
                )
            )

        comparison_rows = [
            (protocol, row, value)
            for protocol, row in by_protocol.items()
            if protocol != proposed_norm
            and protocol not in TRIVIAL_BASELINE_PROTOCOLS
            for value in [_float_or_none(row.get(metric))]
            if value is not None
        ]
        if not comparison_rows:
            computed_support = False
            issues.append(
                CalibrationIssue(
                    "missing_nontrivial_baseline_result",
                    str(RESULTS_SUMMARY_PATH),
                    (
                        f"no non-trivial baseline reports {metric!r} on split {split!r}; "
                        "a trivial direct/no-skill comparison is not enough"
                    ),
                )
            )
            continue

        best_protocol, _, best_score = _best_metric_row(comparison_rows, direction)
        if not _metric_is_better(proposed_score, best_score, direction):
            computed_support = False
            delta = proposed_score - best_score
            if direction == "lower_is_better":
                delta = best_score - proposed_score
            issues.append(
                CalibrationIssue(
                    "proposed_claim_not_supported",
                    str(RESULTS_SUMMARY_PATH),
                    (
                        f"{proposed_protocol}={proposed_score:.3f} on {split}/{metric} "
                        f"does not beat strongest non-trivial baseline "
                        f"{best_protocol}={best_score:.3f} (margin={delta:.3f})"
                    ),
                )
            )

    if (
        not computed_support
        and isinstance(quality_signals, dict)
        and (
            quality_signals.get("beats_nontrivial_baseline") is True
            or quality_signals.get("proposed_contribution_beats_strong_baseline")
            is True
        )
    ):
        issues.append(
            CalibrationIssue(
                "quality_signal_contradicts_results",
                str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                (
                    "quality_signals claim the headline contribution beats a "
                    "strong baseline, but results_summary.tsv does not support it"
                ),
            )
        )

    return issues


def _parse_rate_blockers(rows: list[dict[str, str]]) -> list[CalibrationIssue]:
    overall = {
        _normalize_protocol(row.get("protocol", "")): row
        for row in rows
        if _is_overall_row(row) and row.get("protocol")
    }
    baseline = overall.get("direct") or overall.get("no_skill")
    if baseline is None:
        return []

    baseline_parse = _float_or_none(baseline.get("json_parse_rate"))
    if baseline_parse is None:
        return []

    issues: list[CalibrationIssue] = []
    for protocol, row in overall.items():
        if protocol in TRIVIAL_BASELINE_PROTOCOLS:
            continue
        parse_rate = _float_or_none(row.get("json_parse_rate"))
        if parse_rate is not None and parse_rate <= baseline_parse - 0.10:
            issues.append(
                CalibrationIssue(
                    "parser_or_schema_confound",
                    str(RESULTS_SUMMARY_PATH),
                    (
                        f"{protocol} json_parse_rate={parse_rate:.3f} trails "
                        f"trivial baseline json_parse_rate={baseline_parse:.3f} "
                        "by at least 0.10"
                    ),
                )
            )
            break
    return issues


def _benchmark_provenance_blockers(root: Path) -> list[CalibrationIssue]:
    calibration = _read_json_object_if_exists(root / PAPER_QUALITY_CALIBRATION_JSON_PATH)
    ready = _ready_calibration(calibration)
    json_payload = _read_json_object_if_exists(root / BENCHMARK_PROVENANCE_JSON_PATH)
    if json_payload is not None:
        issues: list[CalibrationIssue] = []
        uses_public = json_payload.get(
            "uses_existing_real_benchmark",
            json_payload.get("uses_public_benchmark"),
        )
        benchmark_type = str(json_payload.get("benchmark_type", "")).lower()
        if uses_public is not True or benchmark_type in {"synthetic", "local", "pilot"}:
            issues.append(
                CalibrationIssue(
                    "synthetic_only_benchmark",
                    str(BENCHMARK_PROVENANCE_JSON_PATH),
                    "structured benchmark provenance must prove existing real benchmark evidence, not synthetic/local/proxy evidence",
                )
            )
            if not _json_benchmark_survey_present(json_payload):
                issues.append(
                    CalibrationIssue(
                        "missing_benchmark_literature_survey",
                        str(BENCHMARK_PROVENANCE_JSON_PATH),
                        (
                            "synthetic/local benchmark provenance must record surveyed "
                            "frontier public benchmark papers/repos and why they were "
                            "not selected"
                        ),
                    )
                )
        task_counts = _benchmark_task_counts(json_payload)
        if _requires_multi_source_benchmark(task_counts, json_payload):
            selected_sources = [
                source
                for source in _selected_benchmark_sources(json_payload)
                if not _benchmark_source_is_planned_only(source)
            ]
            unique_source_count = len(
                {_benchmark_source_identity(source) for source in selected_sources}
                - {None}
            )
            if unique_source_count < MIN_SELECTED_BENCHMARK_SOURCES:
                issues.append(
                    CalibrationIssue(
                        "insufficient_selected_benchmark_sources",
                        str(BENCHMARK_PROVENANCE_JSON_PATH),
                        (
                            "full-paper benchmark provenance must list at least "
                            f"{MIN_SELECTED_BENCHMARK_SOURCES} independent selected "
                            "executed benchmark source families; planned diagnostic "
                            "sources do not count, and same-family slices such as "
                            "SWE-bench Verified/Lite/Multimodal do not count as "
                            "separate sources"
                        ),
                    )
                )
            elif not all(
                _benchmark_source_has_pointer(source) for source in selected_sources
            ):
                issues.append(
                    CalibrationIssue(
                        "incomplete_selected_benchmark_sources",
                        str(BENCHMARK_PROVENANCE_JSON_PATH),
                        (
                            "each selected benchmark source must include provenance "
                            "such as URL/repo, paper/citation/DOI, version/date, "
                            "license/access notes, split/filtering, task count, and "
                            "selection rationale"
                        ),
                    )
                )
            if any(_benchmark_source_looks_synthetic(source) for source in selected_sources):
                issues.append(
                    CalibrationIssue(
                        "synthetic_selected_benchmark_source",
                        str(BENCHMARK_PROVENANCE_JSON_PATH),
                        "selected benchmark sources must be existing real benchmarks or official task/data releases, not synthetic/local/proxy components",
                    )
                )
        if task_counts and max(task_counts) < MIN_PAPER_TASKS:
            issues.append(
                CalibrationIssue(
                    "underpowered_pilot",
                    str(BENCHMARK_PROVENANCE_JSON_PATH),
                    (
                        f"structured benchmark provenance reports at most {max(task_counts)} "
                        f"scored tasks, below the required {MIN_PAPER_TASKS} "
                        f"({PAPER_TASK_SCALE_TARGET} scale)"
                    ),
                )
            )
        return issues

    path = root / BENCHMARK_PROVENANCE_MD_PATH
    if not path.exists():
        if ready:
            return [
                CalibrationIssue(
                    "missing_real_benchmark_provenance",
                    str(BENCHMARK_PROVENANCE_MD_PATH),
                    "ready calibration requires benchmark provenance listing existing real benchmark sources",
                )
            ]
        return []
    raw_text = path.read_text(encoding="utf-8", errors="replace")
    text = raw_text.lower()
    text_issues: list[CalibrationIssue] = []
    if "synthetic" in text or "local pseudo-benchmark" in text or "proxy benchmark" in text:
        text_issues.append(
            CalibrationIssue(
                "synthetic_only_benchmark",
                str(BENCHMARK_PROVENANCE_MD_PATH),
                "benchmark provenance describes synthetic/local/proxy evidence; final paper evidence must use existing real benchmarks",
            )
        )
        if not _text_benchmark_survey_present(text):
            text_issues.append(
                CalibrationIssue(
                    "missing_benchmark_literature_survey",
                    str(BENCHMARK_PROVENANCE_MD_PATH),
                    (
                        "synthetic/local benchmark provenance must cite surveyed "
                        "frontier public benchmarks or official benchmark repos"
                    ),
                )
            )
    text_task_counts: list[int] = []
    text_planned_counts: list[int] = []
    for line in raw_text.splitlines():
        line_counts = [
            count
            for match in BENCHMARK_TASK_COUNT_RE.findall(line)
            for count in [_int_or_none(match)]
            if count is not None
        ]
        if not line_counts:
            continue
        if _planned_only_task_count_line(line):
            text_planned_counts.extend(line_counts)
        else:
            text_task_counts.extend(line_counts)
    text_task_counts = [count for count in text_task_counts if count is not None]
    if text_planned_counts and not text_task_counts:
        text_issues.append(
            CalibrationIssue(
                "planned_benchmark_scale_only",
                str(BENCHMARK_PROVENANCE_MD_PATH),
                (
                    "benchmark provenance reports only planned/target task counts; "
                    "final readiness requires completed scored tasks in results artifacts"
                ),
            )
        )
        if max(text_planned_counts) < MIN_PAPER_TASKS:
            text_issues.append(
                CalibrationIssue(
                    "underpowered_pilot",
                    str(BENCHMARK_PROVENANCE_MD_PATH),
                    (
                        f"benchmark provenance only plans at most {max(text_planned_counts)} "
                        f"tasks, below the required {MIN_PAPER_TASKS} "
                        f"({PAPER_TASK_SCALE_TARGET} scale)"
                    ),
                )
            )
    if text_task_counts and max(text_task_counts) < MIN_PAPER_TASKS:
        text_issues.append(
            CalibrationIssue(
                "underpowered_pilot",
                str(BENCHMARK_PROVENANCE_MD_PATH),
                (
                    f"benchmark provenance reports at most {max(text_task_counts)} scored tasks, "
                    f"below the required {MIN_PAPER_TASKS} ({PAPER_TASK_SCALE_TARGET} scale)"
                ),
            )
        )
    if _requires_multi_source_benchmark(text_task_counts, raw_text):
        selected_source_count = _text_selected_benchmark_source_count(raw_text)
        if selected_source_count < MIN_SELECTED_BENCHMARK_SOURCES:
            text_issues.append(
                CalibrationIssue(
                    "insufficient_selected_benchmark_sources",
                    str(BENCHMARK_PROVENANCE_MD_PATH),
                    (
                        "full-paper benchmark provenance must include a selected "
                        "benchmark source table/list with at least "
                        f"{MIN_SELECTED_BENCHMARK_SOURCES} independent executed real/frontier "
                        "benchmark source families; planned diagnostic rows do not "
                        "count, and same-family slices do not establish broad method "
                        "effectiveness"
                    ),
                )
            )
    if ready and _text_selected_benchmark_source_count(raw_text) < MIN_SELECTED_BENCHMARK_SOURCES:
        text_issues.append(
            CalibrationIssue(
                "insufficient_selected_benchmark_sources",
                str(BENCHMARK_PROVENANCE_MD_PATH),
                (
                    "ready calibration requires a selected benchmark source table/list "
                    f"with at least {MIN_SELECTED_BENCHMARK_SOURCES} existing real benchmark sources"
                ),
            )
        )
    return text_issues


def _benchmark_task_counts(payload: object) -> list[int]:
    counts: list[int] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized_key = str(key).lower().replace("-", "_")
            if any(marker in normalized_key for marker in PLANNED_COUNT_KEY_MARKERS):
                continue
            if normalized_key in BENCHMARK_TASK_COUNT_FIELDS:
                count = _int_or_none(value)
                if count is not None:
                    counts.append(count)
            if isinstance(value, (dict, list)):
                counts.extend(_benchmark_task_counts(value))
    elif isinstance(payload, list):
        for item in payload:
            counts.extend(_benchmark_task_counts(item))
    return counts


def _json_benchmark_survey_present(payload: dict[str, Any]) -> bool:
    for field in BENCHMARK_SURVEY_JSON_FIELDS:
        value = payload.get(field)
        if isinstance(value, list) and value:
            return True
        if isinstance(value, dict) and value:
            return True
        if _nonempty_string(value):
            return True
    return _text_benchmark_survey_present(json.dumps(payload, ensure_ascii=False).lower())


def _text_benchmark_survey_present(text: str) -> bool:
    return any(marker in text for marker in FRONTIER_BENCHMARK_TEXT_MARKERS)


def _requires_multi_source_benchmark(
    task_counts: list[int],
    provenance: dict[str, Any] | str,
) -> bool:
    if task_counts and max(task_counts) >= MIN_PAPER_TASKS:
        return True
    if isinstance(provenance, dict):
        stage_text = json.dumps(provenance, ensure_ascii=False).lower()
    else:
        stage_text = provenance.lower()
    final_markers = (
        "full benchmark",
        "final benchmark",
        "main benchmark",
        "main split",
        "full-paper",
        "full paper",
        "emnlp-ready",
        "submission-ready",
        "final_submission",
    )
    return any(marker in stage_text for marker in final_markers)


def _selected_benchmark_sources(payload: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for field in SELECTED_BENCHMARK_JSON_FIELDS:
        sources.extend(_benchmark_source_entries(payload.get(field)))
    if not sources and _looks_like_benchmark_source(payload):
        sources.append(payload)
    return sources


def _benchmark_source_entries(value: object) -> list[dict[str, Any]]:
    if isinstance(value, list):
        sources: list[dict[str, Any]] = []
        for item in value:
            sources.extend(_benchmark_source_entries(item))
        return sources
    if isinstance(value, dict):
        if _looks_like_benchmark_source(value):
            return [value]
        sources = []
        for name, nested in value.items():
            if isinstance(nested, dict):
                merged = {"name": str(name), **nested}
            else:
                merged = {"name": str(name), "source": nested}
            sources.extend(_benchmark_source_entries(merged))
        return sources
    if _nonempty_string(value):
        return [{"name": str(value).strip()}]
    return []


def _looks_like_benchmark_source(value: dict[str, Any]) -> bool:
    keys = {str(key).lower().replace("-", "_") for key in value}
    return bool(keys & set(BENCHMARK_SOURCE_NAME_FIELDS)) or bool(
        keys & set(BENCHMARK_SOURCE_POINTER_FIELDS)
    )


def _benchmark_source_identity(source: dict[str, Any]) -> str | None:
    family_fields = (
        "benchmark_family",
        "source_family",
        "family",
        "suite",
    )
    for field in family_fields + BENCHMARK_SOURCE_NAME_FIELDS + BENCHMARK_SOURCE_POINTER_FIELDS:
        value = source.get(field)
        if _nonempty_string(value):
            return _benchmark_family_identity(str(value))
    serialized = json.dumps(source, sort_keys=True, ensure_ascii=False)
    if serialized == "{}":
        return None
    return _benchmark_family_identity(serialized)


def _normalize_source_identity(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _benchmark_family_identity(value: str) -> str:
    text = _normalize_source_identity(value).replace("_", "-")
    for pattern, family in KNOWN_BENCHMARK_FAMILY_PATTERNS:
        if re.search(pattern, text, flags=re.I):
            return family
    text = re.sub(r"https?://", " ", text)
    text = re.sub(r"\b(?:github\.com|huggingface\.co|datasets|dataset)\b", " ", text)
    text = re.sub(r"[/#?=&:.,;()]+", " ", text)
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)?", text)
        if token and token not in BENCHMARK_VARIANT_TOKENS
    ]
    if not tokens:
        return _normalize_source_identity(value)
    return " ".join(tokens[:2])


def _benchmark_source_has_pointer(source: dict[str, Any]) -> bool:
    for field in BENCHMARK_SOURCE_POINTER_FIELDS:
        value = source.get(field)
        if _nonempty_string(value):
            return True
    return any(
        marker in json.dumps(source, ensure_ascii=False).lower()
        for marker in BENCHMARK_SOURCE_POINTER_MARKERS
    )


def _benchmark_source_looks_synthetic(source: dict[str, Any]) -> bool:
    text = json.dumps(source, ensure_ascii=False).lower()
    return any(marker in text for marker in SYNTHETIC_BENCHMARK_MARKERS)


def _benchmark_source_is_planned_only(source: dict[str, Any]) -> bool:
    text = json.dumps(source, ensure_ascii=False).lower()
    return _planned_only_text(text, markers=PLANNED_SOURCE_TEXT_MARKERS)


def _text_selected_benchmark_source_count(raw_text: str) -> int:
    table_count = _markdown_benchmark_source_table_count(raw_text)
    if table_count:
        return table_count
    text = raw_text.lower()
    if not any(marker in text for marker in SELECTED_BENCHMARK_TEXT_MARKERS):
        return 0
    explicit_counts = [
        count
        for match in SELECTED_BENCHMARK_COUNT_RE.findall(raw_text)
        for count in [_int_or_none(match)]
        if count is not None
    ]
    if explicit_counts:
        return max(explicit_counts)
    selected_section = _selected_benchmark_text_section(raw_text)
    source_identities: set[str] = set()
    for marker in FRONTIER_BENCHMARK_TEXT_MARKERS:
        if marker in selected_section.lower():
            source_identities.add(_benchmark_family_identity(marker))
    for line in selected_section.splitlines():
        normalized = line.strip().lower()
        if not normalized.startswith(("-", "*", "|")):
            continue
        if any(skip in normalized for skip in ("reject", "infeasible", "alternative")):
            continue
        if _planned_only_text(normalized, markers=PLANNED_SOURCE_TEXT_MARKERS):
            continue
        if any(marker in normalized for marker in BENCHMARK_SOURCE_POINTER_MARKERS):
            source_identities.add(_benchmark_family_identity(normalized))
    return len(source_identities)


def _markdown_benchmark_source_table_count(raw_text: str) -> int:
    """Count selected benchmark sources from a provenance Markdown table.

    Agents often write `experiments/BENCHMARK_PROVENANCE.md` as a single
    table under `# Benchmark Provenance` with columns such as `Name`,
    `URL/repo`, `Paper/citation`, and `Why selected`, without an exact
    "Selected benchmark sources" heading. That is still a structured selected
    source table, so count rows with source pointers instead of forcing a
    brittle heading phrase.
    """
    lines = raw_text.splitlines()
    best_count = 0
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        if index + 1 >= len(lines) or not _looks_like_markdown_separator(lines[index + 1]):
            continue
        headers = _markdown_cells(line)
        normalized_headers = [
            re.sub(r"[^a-z0-9]+", "_", header.strip().lower()).strip("_")
            for header in headers
        ]
        header_text = " ".join(normalized_headers)
        has_source_identity = any(
            token in normalized_headers
            for token in ("name", "benchmark", "benchmark_name", "source", "suite", "dataset")
        )
        has_pointer = any(
            "url" in token
            or "repo" in token
            or "paper" in token
            or "citation" in token
            or token == "doi"
            for token in normalized_headers
        )
        has_selection_rationale = (
            "selected" in header_text
            or "rationale" in header_text
            or "why_selected" in header_text
            or "capability" in header_text
        )
        if not (has_source_identity and has_pointer and has_selection_rationale):
            continue

        identities: set[str] = set()
        for row in lines[index + 2 :]:
            if not row.lstrip().startswith("|"):
                break
            if _looks_like_markdown_separator(row):
                continue
            normalized_row = row.strip().lower()
            if any(skip in normalized_row for skip in ("alternative", "reject", "infeasible")):
                # Skip rows that clearly describe surveyed-but-not-selected sources.
                continue
            if _planned_only_text(normalized_row, markers=PLANNED_SOURCE_TEXT_MARKERS):
                # Planned diagnostic rows are useful provenance, but they are not
                # executed benchmark evidence for final-paper readiness.
                continue
            cells = _markdown_cells(row)
            if len([cell for cell in cells if cell.strip()]) < 3:
                continue
            identity_cell = _markdown_identity_cell(normalized_headers, cells)
            if any(marker in normalized_row for marker in BENCHMARK_SOURCE_POINTER_MARKERS):
                identities.add(_benchmark_family_identity(identity_cell or normalized_row))
                continue
            pointer_cells = [
                cell
                for header, cell in zip(normalized_headers, cells, strict=False)
                if (
                    "url" in header
                    or "repo" in header
                    or "paper" in header
                    or "citation" in header
                    or header == "doi"
                )
            ]
            if any(_nonempty_string(cell) for cell in pointer_cells):
                identities.add(_benchmark_family_identity(identity_cell or normalized_row))
        best_count = max(best_count, len(identities))
    return best_count


def _markdown_identity_cell(headers: list[str], cells: list[str]) -> str | None:
    for header, cell in zip(headers, cells, strict=False):
        if header in {"name", "benchmark", "benchmark_name", "source", "suite", "dataset"}:
            if cell.strip():
                return cell.strip()
    for cell in cells:
        if cell.strip():
            return cell.strip()
    return None


def _looks_like_markdown_separator(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return False
    cells = _markdown_cells(stripped)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def _markdown_cells(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _selected_benchmark_text_section(raw_text: str) -> str:
    lines = raw_text.splitlines()
    start_index: int | None = None
    for index, line in enumerate(lines):
        normalized = line.strip().lower()
        if any(marker in normalized for marker in SELECTED_BENCHMARK_TEXT_MARKERS):
            start_index = index
            break
    if start_index is None:
        return raw_text
    section_lines: list[str] = []
    for line in lines[start_index:]:
        if section_lines and line.lstrip().startswith("#"):
            break
        section_lines.append(line)
    return "\n".join(section_lines)


def _actual_results_task_counts(rows: list[dict[str, str]]) -> list[int]:
    counts: list[int] = []
    for row in rows:
        if not _nonempty_string(row.get("protocol")):
            continue
        # Accept both overall rows and per-benchmark rows with a named scope
        if not _is_overall_row(row) and not _nonempty_string(row.get("scope")):
            continue
        for field in RESULTS_TASK_COUNT_FIELDS:
            count = _int_or_none(row.get(field))
            if count is not None:
                counts.append(count)
                break
    return counts


def _experiment_scored_task_counts(root: Path) -> list[int]:
    counts: list[int] = []
    for path in root.glob(EXPERIMENT_SUMMARY_GLOB):
        payload = _read_json_object_if_exists(path)
        if payload is not None:
            counts.extend(_benchmark_task_counts(payload))
    for path in root.glob(EXPERIMENT_STATUS_GLOB):
        payload = _read_json_object_if_exists(path)
        if payload is not None:
            counts.extend(_benchmark_task_counts(payload))
    for path in root.glob(EXPERIMENT_PROGRESS_GLOB):
        counts.extend(_progress_task_counts(path))
    return counts


def _experiment_scored_task_capacity(root: Path) -> int | None:
    """Maximum supported task count after summing the same method across runs.

    A final paper can legitimately report an aggregate row over several
    independently executed benchmark-source runs, e.g. 240 + 80 + 80 tasks for
    the same protocol. A single small run must still not justify a 300+ row.
    """

    by_run_method: dict[tuple[str, str], int] = {}
    for path in root.glob(EXPERIMENT_SUMMARY_GLOB):
        payload = _read_json_object_if_exists(path)
        if payload is not None:
            _merge_run_method_counts(by_run_method, path.parent, payload)
    for path in root.glob(EXPERIMENT_STATUS_GLOB):
        payload = _read_json_object_if_exists(path)
        if payload is not None:
            _merge_run_method_counts(by_run_method, path.parent, payload)
    for path in root.glob(EXPERIMENT_PROGRESS_GLOB):
        _merge_progress_run_method_counts(by_run_method, path)

    totals_by_method: dict[str, int] = {}
    for (_run_id, method), count in by_run_method.items():
        totals_by_method[method] = totals_by_method.get(method, 0) + count
    if totals_by_method:
        return max(totals_by_method.values())

    counts = _experiment_scored_task_counts(root)
    return max(counts) if counts else None


def _merge_run_method_counts(
    by_run_method: dict[tuple[str, str], int],
    run_dir: Path,
    payload: object,
) -> None:
    run_id = str(run_dir)
    for method, count in _method_task_counts(payload).items():
        key = (run_id, method)
        by_run_method[key] = max(by_run_method.get(key, 0), count)


def _merge_progress_run_method_counts(
    by_run_method: dict[tuple[str, str], int],
    path: Path,
) -> None:
    run_id = str(path.parent)
    for method, count in _progress_task_counts_by_method(path).items():
        key = (run_id, method)
        by_run_method[key] = max(by_run_method.get(key, 0), count)


def _method_task_counts(payload: object) -> dict[str, int]:
    counts: dict[str, int] = {}
    if isinstance(payload, dict):
        method = payload.get("method") or payload.get("protocol") or payload.get("variant")
        if _nonempty_string(method):
            for field in BENCHMARK_TASK_COUNT_FIELDS:
                count = _int_or_none(payload.get(field))
                if count is not None:
                    normalized = _normalize_protocol(str(method))
                    counts[normalized] = max(counts.get(normalized, 0), count)
                    break
        for value in payload.values():
            for nested_method, nested_count in _method_task_counts(value).items():
                counts[nested_method] = max(counts.get(nested_method, 0), nested_count)
    elif isinstance(payload, list):
        for item in payload:
            for nested_method, nested_count in _method_task_counts(item).items():
                counts[nested_method] = max(counts.get(nested_method, 0), nested_count)
    return counts


def _progress_task_counts(path: Path) -> list[int]:
    return list(_progress_task_counts_by_method(path).values())


def _progress_task_counts_by_method(path: Path) -> dict[str, int]:
    by_method: dict[str, set[str]] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        method = event.get("method") or event.get("protocol") or event.get("variant")
        episode = (
            event.get("episode_id")
            or event.get("task_id")
            or event.get("sample_id")
            or event.get("episode_index")
        )
        if method is None or episode is None:
            continue
        by_method.setdefault(_normalize_protocol(str(method)), set()).add(str(episode))
    return {
        method: len(episodes)
        for method, episodes in by_method.items()
        if episodes
    }


def _benchmark_uniqueness_blockers(root: Path) -> list[CalibrationIssue]:
    issues: list[CalibrationIssue] = []
    paths = sorted(
        {
            path
            for pattern in BENCHMARK_RECORD_GLOBS
            for path in root.glob(pattern)
            if path.is_file()
        }
    )
    for path in paths:
        records = _read_jsonl_objects(path)
        if len(records) < MIN_PAPER_TASKS:
            continue
        for records_group in _record_groups(records).values():
            if len(records_group) < MIN_PAPER_TASKS:
                continue
            issue = _duplicate_expansion_issue(root, path, records_group)
            if issue is not None:
                issues.append(issue)
    return issues


def _duplicate_expansion_issue(
    root: Path,
    path: Path,
    records: list[dict[str, Any]],
) -> CalibrationIssue | None:
    identifiers: list[str] = []
    for record in records:
        identifier = _record_identifier(record)
        if identifier is not None:
            identifiers.append(identifier)
    if identifiers:
        normalized_ids = [_normalized_record_identifier(identifier) for identifier in identifiers]
        unique_ids = len(set(normalized_ids))
        if unique_ids < len(identifiers):
            return CalibrationIssue(
                "duplicated_benchmark_expansion",
                _relative_to_root(root, path),
                (
                    f"{len(records)} benchmark/result records collapse to only {unique_ids} "
                    "unique episode IDs after stripping copy/repeat suffixes; copied "
                    f"pilot episodes cannot satisfy the {MIN_PAPER_TASKS}-task final scale"
                ),
            )

    signatures = [_record_task_signature(record) for record in records]
    signatures = [signature for signature in signatures if signature is not None]
    if signatures:
        unique_signatures = len(set(signatures))
        if unique_signatures < len(signatures):
            return CalibrationIssue(
                "duplicated_benchmark_expansion",
                _relative_to_root(root, path),
                (
                    f"{len(records)} benchmark/result records contain only "
                    f"{unique_signatures} unique task definitions; repeated prompts/specs "
                    "with renamed IDs do not count as distinct benchmark episodes"
                ),
            )
    return None


def _record_groups(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        key = _record_group_key(record)
        groups.setdefault(key, []).append(record)
    return groups


def _record_group_key(record: dict[str, Any]) -> str:
    parts = [
        f"{key}={value}"
        for key in ("method", "protocol", "variant")
        if _nonempty_string(value := record.get(key))
    ]
    if not parts:
        return "__all__"
    return "|".join(parts)


def _record_identifier(record: dict[str, Any]) -> str | None:
    for key in RECORD_ID_FIELDS:
        value = record.get(key)
        if _nonempty_string(value):
            return str(value).strip()
    return None


def _normalized_record_identifier(identifier: str) -> str:
    return COPY_EXPANSION_SUFFIX_RE.sub("", identifier.strip())


def _record_task_signature(record: dict[str, Any]) -> str | None:
    if not any(record.get(field) for field in TASK_DEFINITION_FIELDS):
        return None
    task_payload = {
        key: value
        for key, value in record.items()
        if key not in NON_TASK_SIGNATURE_FIELDS and not key.startswith("_")
    }
    return json.dumps(task_payload, sort_keys=True, ensure_ascii=False)


def _read_jsonl_objects(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _planned_only_task_count_line(line: str) -> bool:
    return _planned_only_text(line)


def _planned_only_text(
    text: str,
    *,
    markers: tuple[str, ...] = PLANNED_COUNT_TEXT_MARKERS,
) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers) and not any(
        marker in lowered for marker in ACTUAL_COUNT_TEXT_MARKERS
    )


def _model_scale_blockers(root: Path) -> list[CalibrationIssue]:
    calibration = _read_json_object_if_exists(root / PAPER_QUALITY_CALIBRATION_JSON_PATH)
    ready = _ready_calibration(calibration)
    quality_signals = (
        calibration.get("quality_signals")
        if isinstance(calibration, dict) and isinstance(calibration.get("quality_signals"), dict)
        else {}
    )
    issues: list[CalibrationIssue] = []
    if isinstance(quality_signals, dict) and quality_signals.get("uses_meaningful_trained_model") is False:
        issues.append(
            CalibrationIssue(
                "tiny_model_main_claim",
                str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
                "quality signals say the proposed method does not use a meaningful trained/adapted model",
            )
        )

    evidence_text, evidence_paths = _model_scale_evidence_text(root)
    lower_text = evidence_text.lower()
    has_positive_scale = any(marker in lower_text for marker in MODEL_SCALE_POSITIVE_MARKERS)
    has_tiny_marker = any(marker in lower_text for marker in TINY_MODEL_MARKERS)
    if ready and not evidence_paths:
        issues.append(
            CalibrationIssue(
                "missing_model_scale_plan",
                str(MODEL_SCALE_PLAN_PATH),
                "ready calibration requires model/backbone scale, trainable parameters, GPU plan, and checkpoint/training artifacts",
            )
        )
    elif ready and not has_positive_scale:
        issues.append(
            CalibrationIssue(
                "missing_model_scale_plan",
                ", ".join(evidence_paths[:3]) if evidence_paths else str(MODEL_SCALE_PLAN_PATH),
                "model evidence must record a meaningful trained/adapted backbone, not only a lightweight scorer",
            )
        )
    if has_tiny_marker and not has_positive_scale:
        issues.append(
            CalibrationIssue(
                "tiny_model_main_claim",
                ", ".join(evidence_paths[:3]) if evidence_paths else str(MODEL_SCALE_PLAN_PATH),
                "artifacts describe a tiny scorer/oracle/prompt-only proxy without model-scale training evidence",
            )
        )
    return issues


def _model_scale_evidence_text(root: Path) -> tuple[str, list[str]]:
    chunks: list[str] = []
    paths: list[str] = []
    for pattern in MODEL_SCALE_EVIDENCE_GLOBS:
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            chunks.append(text[:20_000])
            paths.append(rel)
    return "\n".join(chunks), paths


def _draft_report_blockers(root: Path) -> list[CalibrationIssue]:
    json_payload = _read_json_object_if_exists(root / PAPER_DRAFT_REPORT_JSON_PATH)
    if json_payload is not None:
        assessment = json_payload.get("submission_quality_self_assessment")
        if isinstance(assessment, str) and assessment != "ready":
            return [
                CalibrationIssue(
                    "draft_self_reports_not_submission_quality",
                    str(PAPER_DRAFT_REPORT_JSON_PATH),
                    f"draft report self-assesses as {assessment!r}",
                )
            ]
        return []

    path = root / PAPER_DRAFT_REPORT_MD_PATH
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace").lower()
    markers = (
        "pilot-note hybrid",
        "not full emnlp",
        "missing experiments",
        "missing public benchmark",
        "no broader benchmark",
    )
    if any(marker in text for marker in markers):
        return [
            CalibrationIssue(
                "draft_self_reports_not_submission_quality",
                str(PAPER_DRAFT_REPORT_MD_PATH),
                "draft report marks the paper as pilot/not full submission quality",
            )
        ]
    return []


def _negative_result_framing_blockers(root: Path) -> list[CalibrationIssue]:
    issues: list[CalibrationIssue] = []
    for relative_path in PAPER_NARRATIVE_PATHS:
        path = root / relative_path
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        marker = next(
            (candidate for candidate in NEGATIVE_RESULT_MARKERS if candidate in text),
            None,
        )
        if marker is None:
            continue
        issues.append(
            CalibrationIssue(
                "negative_result_framing",
                str(relative_path),
                (
                    "final EMNLP readiness for this objective requires a positive "
                    f"headline contribution; found negative-result framing marker {marker!r}"
                ),
            )
        )
    return issues


def _ready_calibration(payload: dict[str, Any] | None) -> bool:
    return isinstance(payload, dict) and payload.get("verdict") in READY_VERDICTS


def _is_overall_row(row: dict[str, str]) -> bool:
    scope = row.get("scope")
    return scope in {None, "", "overall"}


def _row_split(row: dict[str, str]) -> str:
    for key in ("split_name", "split", "subset"):
        value = row.get(key)
        if _nonempty_string(value):
            return str(value).strip()
    return "overall"


def _normalize_protocol(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _baseline_names(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {_normalize_protocol(item) for item in value if _nonempty_string(item)}


def _best_metric_row(
    rows: list[tuple[str, dict[str, str], float]],
    direction: str,
) -> tuple[str, dict[str, str], float]:
    if direction == "lower_is_better":
        return min(rows, key=lambda item: item[2])
    return max(rows, key=lambda item: item[2])


def _metric_is_better(candidate: float, baseline: float, direction: str) -> bool:
    if direction == "lower_is_better":
        return candidate < baseline
    return candidate > baseline


def _string_or_none(value: object) -> str | None:
    if not _nonempty_string(value):
        return None
    return str(value).strip()


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _relative_to_root(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _read_tsv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path} is not valid UTF-8") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _read_json_object_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return _read_json_object(path)
    except ValueError:
        return None


def _float_or_none(value: object) -> float | None:
    if not isinstance(value, (str, int, float)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: object) -> int | None:
    if not isinstance(value, (str, int, float)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dedupe_issues(issues: list[CalibrationIssue]) -> list[CalibrationIssue]:
    seen: set[tuple[str, str]] = set()
    deduped: list[CalibrationIssue] = []
    for issue in issues:
        key = (issue.code, issue.path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped
