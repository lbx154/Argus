"""Lightweight contracts for auto-research pipeline artifacts.

The bundled research skills are Markdown playbooks, but the artifacts they
produce should still be machine-checkable.  This module validates the shared
state and submission-assurance files used by those playbooks so future CLI
surfaces or tests can gate progress without re-encoding the contract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .academic_language_review import (
    ACADEMIC_LANGUAGE_REVIEW_JSON_PATH,
)
from .paper_infrastructure_review import (
    PAPER_INFRASTRUCTURE_REVIEW_JSON_PATH,
)
from .pipeline_policy import (
    DEFAULT_VALIDATION_ISSUE_PREFIXES,
    VALIDATION_FAILURE_CLASSES,
    VALIDATION_PRIORITY_EXPECTED_ORDER,
    VALIDATION_REPAIR_MODE_REQUIRED_TOKENS,
)

ARTIFACT_MANIFEST_PATH = Path("paper/ARTIFACT_MANIFEST.json")
LITERATURE_GROUNDING_JSON_PATH = Path("research/LITERATURE_GROUNDING.json")
IDEA_PROVENANCE_JSON_PATH = Path("research/IDEA_PROVENANCE.json")
CODE_REUSE_PLAN_JSON_PATH = Path("research/CODE_REUSE_PLAN.json")
STYLE_EXEMPLAR_JSON_PATH = Path("paper/style_ref/EXEMPLAR.json")
STYLE_PROFILE_PATH = Path("paper/style_ref/STYLE_PROFILE.md")
STYLE_STRUCTURE_BLUEPRINT_PATH = Path("paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md")
IMAGE2_FIGURES_JSON_PATH = Path("paper/figures/IMAGE2_FIGURES.json")
LAYOUT_REVIEW_JSON_PATH = Path("paper/LAYOUT_REVIEW.json")
ACADEMIC_LANGUAGE_REVIEW_PATH = ACADEMIC_LANGUAGE_REVIEW_JSON_PATH
PAPER_INFRASTRUCTURE_REVIEW_PATH = PAPER_INFRASTRUCTURE_REVIEW_JSON_PATH
CLAIM_GRAPH_JSON_PATH = Path("paper/CLAIM_GRAPH.json")
FIGURE_TABLE_STYLE_GUIDE_JSON_PATH = Path("paper/FIGURE_TABLE_STYLE_GUIDE.json")
VALIDATION_PRIORITY_POLICY_JSON_PATH = Path("paper/VALIDATION_PRIORITY_POLICY.json")
ARTIFACT_FRESHNESS_JSON_PATH = Path("paper/ARTIFACT_FRESHNESS.json")
CLAIMS_EVIDENCE_AUDIT_JSON_PATH = Path("paper/CLAIMS_EVIDENCE_AUDIT.json")
RESULT_TO_CLAIM_TSV_PATH = Path("paper/artifacts/result_to_claim.tsv")
RESULTS_TABLE_TSV_PATH = Path("paper/artifacts/results_table.tsv")
PAPER_MAIN_TEX_PATH = Path("paper/main.tex")
PAPER_MAIN_PDF_PATH = Path("paper/main.pdf")

# EMNLP/ACL page limits apply to the main body only. References and appendix
# pages are intentionally uncapped, but must not start before page 9.
RENDERED_HEADING_LINE_NUMBER_PREFIX = r"(?:\d{1,5}\s+)?"


FRESHNESS_ALWAYS_REQUIRED_PATHS: tuple[Path, ...] = (
    CLAIM_GRAPH_JSON_PATH,
    FIGURE_TABLE_STYLE_GUIDE_JSON_PATH,
    VALIDATION_PRIORITY_POLICY_JSON_PATH,
    PAPER_MAIN_TEX_PATH,
    PAPER_MAIN_PDF_PATH,
    LAYOUT_REVIEW_JSON_PATH,
    ACADEMIC_LANGUAGE_REVIEW_PATH,
    PAPER_INFRASTRUCTURE_REVIEW_PATH,
)
FRESHNESS_REQUIRED_INPUTS: dict[Path, tuple[Path, ...]] = {
    PAPER_MAIN_TEX_PATH: (
        CLAIM_GRAPH_JSON_PATH,
        STYLE_STRUCTURE_BLUEPRINT_PATH,
        FIGURE_TABLE_STYLE_GUIDE_JSON_PATH,
        RESULT_TO_CLAIM_TSV_PATH,
        RESULTS_TABLE_TSV_PATH,
    ),
    PAPER_MAIN_PDF_PATH: (PAPER_MAIN_TEX_PATH,),
    LAYOUT_REVIEW_JSON_PATH: (PAPER_MAIN_PDF_PATH,),
    ACADEMIC_LANGUAGE_REVIEW_PATH: (PAPER_MAIN_TEX_PATH, PAPER_MAIN_PDF_PATH),
    PAPER_INFRASTRUCTURE_REVIEW_PATH: (PAPER_MAIN_TEX_PATH, PAPER_MAIN_PDF_PATH),
}
MANIFEST_DISCOVERY_EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
}
MANIFEST_DISCOVERY_SUFFIXES = {
    ".bib",
    ".csv",
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".tex",
    ".tsv",
    ".txt",
}
MANIFEST_CANONICAL_PREFIXES = (
    "bench/",
    "benchmarks/",
    "experiments/",
    "results/",
    "runs/",
)
MANIFEST_GENERATED_PREFIXES = (
    "paper/",
    "research/",
)
MANIFEST_GENERATED_EXCLUDED_PATHS = {
    ARTIFACT_MANIFEST_PATH.as_posix(),
    ARTIFACT_FRESHNESS_JSON_PATH.as_posix(),
}
MANIFEST_CANONICAL_RESEARCH_PATHS = {
    "research/RESEARCH_BRIEF.md",
    "research/LITERATURE_REVIEW.md",
    "research/LIT_MATRIX.tsv",
    "research/LITERATURE_GROUNDING.json",
    "research/SOURCE_DISCOVERY.md",
    "research/TREND_INSIGHTS.md",
    "research/IDEA_PROVENANCE.json",
    "research/CODE_REUSE_PLAN.json",
    "research/EXPERIMENT_PLAN.md",
    "research/CLAIMS_TO_TEST.md",
    "research/BASELINE_AND_BENCHMARK_PLAN.md",
    "research/NOVELTY_REPORT.md",
    "research/NOVELTY_MAP.md",
    "research/RELATED_WORK_BLOCKERS.md",
    "research/PIPELINE_STATE.json",
}
MANIFEST_SOURCE_PREFERENCES: dict[str, tuple[str, ...]] = {
    "paper/CLAIM_GRAPH.json": (
        "paper/artifacts/result_to_claim.tsv",
        "paper/artifacts/claims_evidence.tsv",
        "paper/artifacts/results_table.tsv",
    ),
    "paper/EVIDENCE_GAPS.json": (
        "paper/CLAIM_GRAPH.json",
        "paper/artifacts/result_to_claim.tsv",
        "paper/artifacts/claims_evidence.tsv",
    ),
    "paper/FIGURE_TABLE_STYLE_GUIDE.json": (
        "paper/figures/IMAGE2_FIGURES.json",
        "paper/artifacts/results_table.tsv",
        "paper/artifacts/result_to_claim.tsv",
    ),
    "paper/figures/IMAGE2_FIGURES.json": (),
    "paper/VALIDATION_PRIORITY_POLICY.json": (
        "paper/CLAIM_GRAPH.json",
        "paper/EVIDENCE_GAPS.json",
        "paper/PAPER_QUALITY_CALIBRATION.json",
    ),
    "paper/PAPER_QUALITY_CALIBRATION.json": (
        "paper/RESULTS_REPORT.md",
        "paper/CLAIM_GRAPH.json",
        "paper/artifacts/results_table.tsv",
    ),
    "paper/PAPER_QUALITY_CALIBRATION.md": (
        "paper/PAPER_QUALITY_CALIBRATION.json",
    ),
    "paper/PAPER_DRAFT_REPORT.json": (
        "paper/main.tex",
        "paper/main.pdf",
    ),
    "paper/PAPER_DRAFT_REPORT.md": (
        "paper/PAPER_DRAFT_REPORT.json",
    ),
    "paper/FORMAT_PREFLIGHT.md": (
        "paper/main.tex",
        "paper/main.pdf",
    ),
    "paper/ACADEMIC_LANGUAGE_REVIEW.json": (
        "paper/main.tex",
        "paper/main.pdf",
    ),
    "paper/PAPER_INFRASTRUCTURE_REVIEW.json": (
        "paper/main.tex",
        "paper/main.pdf",
    ),
    "paper/LAYOUT_REVIEW.json": (
        "paper/main.pdf",
    ),
    "paper/SUBMISSION_ASSURANCE.json": (
        "paper/main.pdf",
        "paper/FORMAT_PREFLIGHT.md",
        "paper/ACADEMIC_LANGUAGE_REVIEW.json",
        "paper/PAPER_INFRASTRUCTURE_REVIEW.json",
        "paper/LAYOUT_REVIEW.json",
        "paper/PAPER_QUALITY_CALIBRATION.json",
    ),
    "paper/SUBMISSION_ASSURANCE.md": (
        "paper/SUBMISSION_ASSURANCE.json",
    ),
    "paper/main.tex": (
        "paper/RESULTS_REPORT.md",
        "paper/CLAIM_GRAPH.json",
        "paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md",
        "paper/FIGURE_TABLE_STYLE_GUIDE.json",
        "paper/figures/IMAGE2_FIGURES.json",
    ),
    "paper/main.pdf": (
        "paper/main.tex",
    ),
}
MANIFEST_RECOMPUTE_SOURCE_PATHS = {
    *MANIFEST_SOURCE_PREFERENCES,
    "paper/RESULTS_REPORT.md",
}
DEFAULT_VALIDATION_REPAIR_MODES: dict[str, str] = {
    "freshness": "regenerate stale generated artifacts and refresh recorded input hashes",
    "experiment_evidence": "run full-scale benchmark experiments and required baselines",
    "claim_graph": "repair claim graph evidence bindings or soften unsupported claims",
    "artifact_manifest": "refresh artifact manifest schemas, sources, digests, and TSV columns",
}


LITERATURE_ARTIFACT_PATTERNS: tuple[str, ...] = (
    "research/LITERATURE_REVIEW.md",
    "research/LIT_MATRIX.tsv",
    str(LITERATURE_GROUNDING_JSON_PATH),
    "research/SOURCE_DISCOVERY.md",
    "research/TREND_INSIGHTS.md",
)


PLAN_ARTIFACT_PATTERNS: tuple[str, ...] = (
    "research/EXPERIMENT_PLAN.md",
    "research/CLAIMS_TO_TEST.md",
    "research/BASELINE_AND_BENCHMARK_PLAN.md",
    str(CODE_REUSE_PLAN_JSON_PATH),
    "experiments/BENCHMARK_PROVENANCE.md",
)


@dataclass(frozen=True)
class ContractIssue:
    """A single machine-checkable contract violation."""

    code: str
    path: str
    message: str


@dataclass(frozen=True)
class _ManifestEntry:
    section: str
    path: str
    resolved_path: Path
    sha256: str
    sources: tuple[str, ...]


def validate_artifact_manifest(project_root: Path) -> list[ContractIssue]:
    """Validate the paper artifact provenance and digest manifest.

    The manifest is the machine-checkable guard against stale research packages:
    canonical result tables and generated prose/manuscripts must be listed
    together, every listed file must match its recorded digest, and generated
    artifacts must trace back to at least one canonical source.
    """

    root = Path(project_root)
    manifest_path = root / ARTIFACT_MANIFEST_PATH
    if not manifest_path.exists():
        return [
            ContractIssue(
                "missing_artifact_manifest",
                str(ARTIFACT_MANIFEST_PATH),
                "paper artifact manifest is missing",
            )
        ]

    try:
        manifest = _read_json_object(manifest_path)
    except ValueError as exc:
        return [
            ContractIssue(
                "invalid_artifact_manifest_json",
                str(ARTIFACT_MANIFEST_PATH),
                str(exc),
            )
        ]

    issues: list[ContractIssue] = []
    if manifest.get("version") != 1:
        issues.append(
            ContractIssue(
                "unknown_artifact_manifest_version",
                str(ARTIFACT_MANIFEST_PATH),
                "artifact manifest version must be 1",
            )
        )

    canonical_entries, canonical_issues = _collect_manifest_entries(
        root,
        manifest.get("canonical_sources"),
        section="canonical_sources",
        require_sources=False,
    )
    generated_entries, generated_issues = _collect_manifest_entries(
        root,
        manifest.get("generated_artifacts"),
        section="generated_artifacts",
        require_sources=True,
    )
    issues.extend(canonical_issues)
    issues.extend(generated_issues)

    all_entries = [*canonical_entries, *generated_entries]
    entry_by_path: dict[str, _ManifestEntry] = {}
    for entry in all_entries:
        if entry.path in entry_by_path:
            issues.append(
                ContractIssue(
                    "duplicate_artifact_manifest_path",
                    str(ARTIFACT_MANIFEST_PATH),
                    f"artifact {entry.path!r} is listed more than once",
                )
            )
            continue
        entry_by_path[entry.path] = entry

    canonical_paths = {entry.path for entry in canonical_entries}
    generated_paths = {entry.path for entry in generated_entries}
    if generated_entries and not canonical_paths:
        issues.append(
            ContractIssue(
                "generated_artifacts_without_canonical_sources",
                str(ARTIFACT_MANIFEST_PATH),
                "generated artifacts must trace to at least one canonical source",
            )
        )

    source_graph: dict[str, tuple[str, ...]] = {}
    for entry in generated_entries:
        source_graph[entry.path] = entry.sources
        for source in entry.sources:
            if source == entry.path:
                issues.append(
                    ContractIssue(
                        "generated_artifact_source_cycle",
                        entry.path,
                        "generated artifact cannot list itself as a source",
                    )
                )
            if source not in entry_by_path:
                issues.append(
                    ContractIssue(
                        "unknown_generated_artifact_source",
                        entry.path,
                        f"source {source!r} must also be listed in the artifact manifest",
                    )
                )
    issues.extend(_manifest_source_graph_issues(source_graph, canonical_paths, generated_paths))
    return _dedupe_contract_issues(issues)


def refresh_artifact_manifest(project_root: Path) -> list[ContractIssue]:
    """Normalize and refresh the artifact manifest from files on disk.

    Older projects often contain partially hand-written manifests with bare
    string paths or generated artifacts that omit their source graph.  Treat
    this command as the canonical repair path: preserve existing intent when
    possible, coerce legacy entries to objects, fill digests/TSV headers, and
    add conservative source links from generated paper artifacts to current
    canonical experiment/result artifacts.
    """

    root = Path(project_root)
    manifest_path = root / ARTIFACT_MANIFEST_PATH
    if manifest_path.exists():
        try:
            manifest = _read_json_object(manifest_path)
        except ValueError as exc:
            return [
                ContractIssue(
                    "invalid_artifact_manifest_json",
                    str(ARTIFACT_MANIFEST_PATH),
                    str(exc),
                )
            ]
    else:
        manifest = {"version": 1, "canonical_sources": [], "generated_artifacts": []}

    manifest["version"] = 1
    canonical_entries = _coerce_manifest_entries(
        root,
        manifest.get("canonical_sources"),
        section="canonical_sources",
    )
    generated_entries = _coerce_manifest_entries(
        root,
        manifest.get("generated_artifacts"),
        section="generated_artifacts",
    )
    canonical_entries, generated_entries = _normalize_manifest_entry_sections(
        canonical_entries,
        generated_entries,
    )
    _add_discovered_manifest_entries(root, canonical_entries, generated_entries)
    _add_missing_source_entries(root, canonical_entries, generated_entries)
    _fill_generated_manifest_sources(root, canonical_entries, generated_entries)
    manifest["canonical_sources"] = canonical_entries
    manifest["generated_artifacts"] = generated_entries
    _write_json_object(manifest_path, manifest)
    return validate_artifact_manifest(root)


def write_validation_priority_policy(project_root: Path) -> list[ContractIssue]:
    """Write the standard validation-priority policy used by final EMNLP gates."""

    root = Path(project_root)
    path = root / VALIDATION_PRIORITY_POLICY_JSON_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "priority_policy_schema_version": 1,
        "priority_order": list(VALIDATION_PRIORITY_EXPECTED_ORDER),
        "failure_routing": {
            failure_class: {
                "issue_code_prefixes": list(
                    DEFAULT_VALIDATION_ISSUE_PREFIXES.get(failure_class, ())
                ),
                "repair_mode": DEFAULT_VALIDATION_REPAIR_MODES.get(
                    failure_class,
                    f"repair {failure_class} blockers from canonical sources",
                ),
            }
            for failure_class in VALIDATION_FAILURE_CLASSES
        },
        "reset_policy": {
            "max_non_improving_rounds": 2,
            "actions": [
                "repeated non-improving paper/layout/prose edits",
                "underfilled body without new evidence-backed analysis",
                "stale generated artifacts after upstream result or draft edits",
                "reset paper skeleton or float plan before further cosmetic edits",
            ],
            "default_action": (
                "route backward to the owning evidence, structure, figure/table, "
                "or manifest stage before cosmetic micro-edits"
            ),
        },
    }
    _write_json_object(path, payload)
    return validate_validation_priority_policy(root)


def refresh_artifact_freshness(project_root: Path) -> list[ContractIssue]:
    """Refresh ARTIFACT_FRESHNESS from the current manifest/source graph.

    This is intended for use after downstream artifacts have already been
    regenerated from their canonical sources. It records current hashes; it
    does not prove that a stale artifact was semantically regenerated.
    """

    root = Path(project_root)
    path = root / ARTIFACT_FRESHNESS_JSON_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = _try_read_json_object(path) or {}
    existing_records: dict[str, dict[str, Any]] = {}
    raw_existing_records = existing.get("records", existing.get("artifacts"))
    if isinstance(raw_existing_records, list):
        for raw_record in raw_existing_records:
            if not isinstance(raw_record, dict):
                continue
            normalized = _normalize_manifest_path(raw_record.get("path"))
            if normalized and normalized not in MANIFEST_GENERATED_EXCLUDED_PATHS:
                existing_records[normalized] = raw_record

    manifest = _try_read_json_object(root / ARTIFACT_MANIFEST_PATH) or {}
    canonical_paths: set[str] = set()
    generated_sources: dict[str, list[str]] = {}
    for raw_entry in manifest.get("canonical_sources", []):
        if not isinstance(raw_entry, dict):
            continue
        normalized = _normalize_manifest_path(raw_entry.get("path"))
        if normalized:
            canonical_paths.add(normalized)
    for raw_entry in manifest.get("generated_artifacts", []):
        if not isinstance(raw_entry, dict):
            continue
        normalized = _normalize_manifest_path(raw_entry.get("path"))
        if not normalized:
            continue
        generated_sources[normalized] = _normalized_path_list(raw_entry.get("sources"))

    paths = {
        path.as_posix()
        for path in _required_freshness_paths(root)
        if (root / path).is_file()
    }
    paths.update(canonical_paths)
    paths.update(generated_sources)
    paths.update(path for path in existing_records if path not in MANIFEST_GENERATED_EXCLUDED_PATHS)

    records: list[dict[str, Any]] = []
    for normalized in sorted(paths):
        resolved = _resolve_manifest_path(root, normalized)
        if resolved is None or not resolved.is_file():
            continue
        role = "canonical" if normalized in canonical_paths else "generated"
        record: dict[str, Any] = {
            "path": normalized,
            "role": role,
            "sha256": _sha256_file(resolved),
        }
        if role != "canonical":
            inputs = _freshness_sources_for_path(
                root,
                normalized,
                generated_sources,
                existing_records,
            )
            if inputs:
                record["inputs"] = inputs
        records.append(record)

    _write_json_object(
        path,
        {
            "freshness_schema_version": 1,
            "records": records,
        },
    )
    return validate_artifact_freshness(root)


def repair_emnlp_contract_artifacts(project_root: Path) -> list[ContractIssue]:
    """Repair the machine-checkable paper contract files that commonly drift.

    This command intentionally does not rewrite scientific content.  It only
    normalizes the manifest, writes the standard validation routing policy, and
    refreshes freshness records from the current source graph after upstream
    artifacts have stabilized.
    """

    root = Path(project_root)
    refresh_artifact_manifest(root)
    write_validation_priority_policy(root)
    refresh_artifact_manifest(root)
    refresh_artifact_freshness(root)
    return _dedupe_contract_issues(
        [
            *validate_artifact_manifest(root),
            *validate_validation_priority_policy(root),
            *validate_artifact_freshness(root),
        ]
    )


def validate_validation_priority_policy(project_root: Path) -> list[ContractIssue]:
    """Validate the repair routing and priority policy used after gates fail."""

    root = Path(project_root)
    path = root / VALIDATION_PRIORITY_POLICY_JSON_PATH
    if not path.exists():
        return [
            ContractIssue(
                "missing_validation_priority_policy",
                str(VALIDATION_PRIORITY_POLICY_JSON_PATH),
                "write VALIDATION_PRIORITY_POLICY.json so daemons repair blockers in a stable order",
            )
        ]

    try:
        payload = _read_json_object(path)
    except ValueError as exc:
        return [
            ContractIssue(
                "invalid_validation_priority_policy_json",
                str(VALIDATION_PRIORITY_POLICY_JSON_PATH),
                str(exc),
            )
        ]

    issues: list[ContractIssue] = []
    if payload.get("priority_policy_schema_version", payload.get("schema_version")) != 1:
        issues.append(
            ContractIssue(
                "invalid_validation_priority_policy_schema_version",
                str(VALIDATION_PRIORITY_POLICY_JSON_PATH),
                "VALIDATION_PRIORITY_POLICY.json must use priority_policy_schema_version: 1",
            )
        )

    raw_order = payload.get("priority_order")
    order = [str(item).strip() for item in raw_order] if isinstance(raw_order, list) else []
    if not order:
        issues.append(
            ContractIssue(
                "missing_validation_priority_order",
                str(VALIDATION_PRIORITY_POLICY_JSON_PATH),
                "priority_order must list failure classes from highest to lowest repair priority",
            )
        )
    else:
        missing = [failure_class for failure_class in VALIDATION_PRIORITY_EXPECTED_ORDER if failure_class not in order]
        if missing:
            issues.append(
                ContractIssue(
                    "incomplete_validation_priority_order",
                    str(VALIDATION_PRIORITY_POLICY_JSON_PATH),
                    "priority_order is missing failure classes: " + ", ".join(missing),
                )
            )

    routing = payload.get("failure_routing")
    if not isinstance(routing, dict):
        issues.append(
            ContractIssue(
                "missing_validation_failure_routing",
                str(VALIDATION_PRIORITY_POLICY_JSON_PATH),
                "failure_routing must map each failure class to issue prefixes and repair mode",
            )
        )
        return _dedupe_contract_issues(issues)

    for failure_class in VALIDATION_FAILURE_CLASSES:
        entry = routing.get(failure_class)
        entry_path = f"{VALIDATION_PRIORITY_POLICY_JSON_PATH}:failure_routing.{failure_class}"
        if not isinstance(entry, dict):
            issues.append(
                ContractIssue(
                    "missing_validation_failure_route",
                    entry_path,
                    f"failure_routing must define route {failure_class!r}",
                )
            )
            continue
        prefixes = _string_list(entry.get("issue_code_prefixes", entry.get("prefixes")))
        if not prefixes:
            issues.append(
                ContractIssue(
                    "validation_failure_route_missing_prefixes",
                    entry_path,
                    "each failure route must list issue_code_prefixes",
                )
            )
        repair_mode = str(entry.get("repair_mode", entry.get("owner", ""))).strip()
        if len(repair_mode) < 5:
            issues.append(
                ContractIssue(
                    "validation_failure_route_missing_repair_mode",
                    entry_path,
                    "each failure route must name a concrete repair mode/owner",
                )
            )
        required_tokens = VALIDATION_REPAIR_MODE_REQUIRED_TOKENS.get(failure_class)
        if required_tokens and not any(token in repair_mode.lower() for token in required_tokens):
            issues.append(
                ContractIssue(
                    "validation_failure_route_bad_repair_mode",
                    entry_path,
                    (
                        f"failure route {failure_class!r} must route to a concrete repair mode "
                        "that mentions one of: " + ", ".join(required_tokens)
                    ),
                )
            )

    reset_policy = payload.get("reset_policy")
    if not isinstance(reset_policy, dict):
        issues.append(
            ContractIssue(
                "missing_validation_reset_policy",
                str(VALIDATION_PRIORITY_POLICY_JSON_PATH),
                "reset_policy must define when to stop micro-patching and reset skeleton/floats",
            )
        )
    else:
        rounds = _int_or_none(reset_policy.get("max_non_improving_rounds"))
        if rounds is None or rounds < 1 or rounds > 3:
            issues.append(
                ContractIssue(
                    "invalid_validation_reset_policy_rounds",
                    str(VALIDATION_PRIORITY_POLICY_JSON_PATH),
                    "reset_policy.max_non_improving_rounds must be 1, 2, or 3",
                )
            )
        actions = _string_list(reset_policy.get("actions", reset_policy.get("reset_actions")))
        if not actions:
            issues.append(
                ContractIssue(
                    "missing_validation_reset_policy_actions",
                    str(VALIDATION_PRIORITY_POLICY_JSON_PATH),
                    "reset_policy must list reset actions such as skeleton reset or float rebalance",
                )
            )
    return _dedupe_contract_issues(issues)


def validate_artifact_freshness(project_root: Path) -> list[ContractIssue]:
    """Validate input-hash freshness for generated paper artifacts."""

    root = Path(project_root)
    path = root / ARTIFACT_FRESHNESS_JSON_PATH
    if not path.exists():
        return [
            ContractIssue(
                "missing_artifact_freshness",
                str(ARTIFACT_FRESHNESS_JSON_PATH),
                "write ARTIFACT_FRESHNESS.json with input hashes for generated paper artifacts",
            )
        ]

    try:
        payload = _read_json_object(path)
    except ValueError as exc:
        return [
            ContractIssue(
                "invalid_artifact_freshness_json",
                str(ARTIFACT_FRESHNESS_JSON_PATH),
                str(exc),
            )
        ]

    issues: list[ContractIssue] = []
    if payload.get("freshness_schema_version", payload.get("schema_version")) != 1:
        issues.append(
            ContractIssue(
                "invalid_artifact_freshness_schema_version",
                str(ARTIFACT_FRESHNESS_JSON_PATH),
                "ARTIFACT_FRESHNESS.json must use freshness_schema_version: 1",
            )
        )

    raw_records = payload.get("records", payload.get("artifacts"))
    if not isinstance(raw_records, list) or not raw_records:
        issues.append(
            ContractIssue(
                "missing_artifact_freshness_records",
                str(ARTIFACT_FRESHNESS_JSON_PATH),
                "freshness records must list generated paper artifacts and their input hashes",
            )
        )
        return _dedupe_contract_issues(issues)

    records_by_path: dict[str, dict[str, Any]] = {}
    for index, raw_record in enumerate(raw_records):
        entry_path = f"{ARTIFACT_FRESHNESS_JSON_PATH}:records[{index}]"
        if not isinstance(raw_record, dict):
            issues.append(
                ContractIssue("invalid_artifact_freshness_record", entry_path, "freshness records must be objects")
            )
            continue
        normalized = _normalize_manifest_path(raw_record.get("path"))
        if normalized is None:
            issues.append(
                ContractIssue(
                    "invalid_artifact_freshness_path",
                    entry_path,
                    "freshness record path must be a POSIX relative project path",
                )
            )
            continue
        if normalized in records_by_path:
            issues.append(
                ContractIssue(
                    "duplicate_artifact_freshness_record",
                    entry_path,
                    f"freshness has duplicate records for {normalized!r}",
                )
            )
        records_by_path[normalized] = raw_record
        resolved = _resolve_manifest_path(root, normalized)
        if resolved is None or not resolved.exists():
            issues.append(
                ContractIssue(
                    "missing_artifact_freshness_artifact",
                    normalized,
                    "freshness record refers to a missing artifact",
                )
            )
            continue
        if resolved.is_file() and resolved.suffix.lower() != ".pdf":
            expected_sha = _lower_text(raw_record.get("sha256"))
            if not _is_sha256_hex(expected_sha):
                issues.append(
                    ContractIssue(
                        "invalid_artifact_freshness_sha256",
                        normalized,
                        "non-PDF freshness records must include the artifact sha256",
                    )
                )
            elif _sha256_file(resolved) != expected_sha:
                issues.append(
                    ContractIssue(
                        "artifact_modified_after_freshness_recorded",
                        normalized,
                        "artifact sha256 no longer matches ARTIFACT_FRESHNESS.json; refresh downstream records",
                    )
                )
        issues.extend(_validate_freshness_inputs(root, raw_record, normalized))

    for required in _required_freshness_paths(root):
        if required.as_posix() not in records_by_path:
            issues.append(
                ContractIssue(
                    "missing_required_artifact_freshness_record",
                    str(ARTIFACT_FRESHNESS_JSON_PATH),
                    f"freshness records must cover generated artifact {required.as_posix()!r}",
                )
            )

    for artifact, required_inputs in FRESHNESS_REQUIRED_INPUTS.items():
        artifact_path = artifact.as_posix()
        if artifact_path not in records_by_path:
            continue
        actual_inputs = {
            normalized
            for normalized, _ in _freshness_input_records(records_by_path[artifact_path].get("inputs"))
        }
        for required_input in required_inputs:
            if not (root / required_input).exists():
                continue
            if required_input.as_posix() not in actual_inputs:
                issues.append(
                    ContractIssue(
                        "artifact_freshness_missing_required_input",
                        artifact_path,
                        f"{artifact_path} freshness must include input {required_input.as_posix()}",
                    )
                )
    return _dedupe_contract_issues(issues)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        strings: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                strings.append(item.strip())
            elif isinstance(item, dict):
                for key in ("path", "label", "id", "key", "citation_key"):
                    raw = item.get(key)
                    if isinstance(raw, str) and raw.strip():
                        strings.append(raw.strip())
                        break
        return strings
    return []


def _required_freshness_paths(root: Path) -> set[Path]:
    required = {path for path in FRESHNESS_ALWAYS_REQUIRED_PATHS if (root / path).exists()}
    manifest = _try_read_json_object(root / ARTIFACT_MANIFEST_PATH)
    raw_generated = manifest.get("generated_artifacts") if isinstance(manifest, dict) else None
    if isinstance(raw_generated, list):
        for raw_entry in raw_generated:
            if not isinstance(raw_entry, dict):
                continue
            normalized = _normalize_manifest_path(raw_entry.get("path"))
            if normalized and (root / normalized).exists():
                required.add(Path(normalized))
    return required


def _normalized_path_list(raw_paths: Any) -> list[str]:
    if not isinstance(raw_paths, list):
        return []
    paths: list[str] = []
    seen: set[str] = set()
    for raw_path in raw_paths:
        if isinstance(raw_path, dict):
            normalized = _normalize_manifest_path(raw_path.get("path", raw_path.get("input_path")))
        else:
            normalized = _normalize_manifest_path(raw_path)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        paths.append(normalized)
    return paths


def _freshness_sources_for_path(
    root: Path,
    artifact_path: str,
    generated_sources: Mapping[str, list[str]],
    existing_records: Mapping[str, dict[str, Any]],
) -> list[dict[str, str]]:
    raw_sources = list(generated_sources.get(artifact_path, []))
    if not raw_sources:
        raw_sources.extend(
            input_path
            for input_path, _ in _freshness_input_records(
                existing_records.get(artifact_path, {}).get("inputs")
            )
        )
    for required_input in FRESHNESS_REQUIRED_INPUTS.get(Path(artifact_path), ()):
        raw_sources.append(required_input.as_posix())

    inputs: list[dict[str, str]] = []
    seen: set[str] = set()
    for source in raw_sources:
        normalized = _normalize_manifest_path(source)
        if not normalized or normalized == artifact_path or normalized in seen:
            continue
        resolved = _resolve_manifest_path(root, normalized)
        if resolved is None or not resolved.is_file():
            continue
        seen.add(normalized)
        inputs.append({"path": normalized, "sha256": _sha256_file(resolved)})
    return inputs


def _freshness_input_records(raw_inputs: Any) -> list[tuple[str, str]]:
    if not isinstance(raw_inputs, list):
        return []
    records: list[tuple[str, str]] = []
    for raw_input in raw_inputs:
        if isinstance(raw_input, str):
            normalized = _normalize_manifest_path(raw_input)
            if normalized:
                records.append((normalized, ""))
            continue
        if not isinstance(raw_input, dict):
            continue
        normalized = _normalize_manifest_path(raw_input.get("path", raw_input.get("input_path")))
        if normalized is None:
            continue
        sha256 = _lower_text(raw_input.get("sha256", raw_input.get("input_sha256")))
        records.append((normalized, sha256))
    return records


def _validate_freshness_inputs(root: Path, record: dict[str, Any], artifact_path: str) -> list[ContractIssue]:
    role = _lower_text(record.get("role", "generated"))
    input_records = _freshness_input_records(record.get("inputs", record.get("generated_from")))
    if not input_records and role != "canonical":
        return [
            ContractIssue(
                "artifact_freshness_missing_inputs",
                artifact_path,
                "generated freshness records must list inputs with sha256 values",
            )
        ]
    issues: list[ContractIssue] = []
    seen_inputs: set[str] = set()
    for input_path, expected_sha in input_records:
        if input_path in seen_inputs:
            issues.append(
                ContractIssue(
                    "duplicate_artifact_freshness_input",
                    artifact_path,
                    f"freshness input {input_path!r} is listed more than once",
                )
            )
        seen_inputs.add(input_path)
        resolved = _resolve_manifest_path(root, input_path)
        if resolved is None or not resolved.is_file():
            issues.append(
                ContractIssue(
                    "missing_artifact_freshness_input",
                    artifact_path,
                    f"freshness input {input_path!r} is missing",
                )
            )
            continue
        if not _is_sha256_hex(expected_sha):
            issues.append(
                ContractIssue(
                    "artifact_freshness_input_missing_sha256",
                    artifact_path,
                    f"freshness input {input_path!r} must record its sha256 at generation time",
                )
            )
            continue
        if _sha256_file(resolved) != expected_sha:
            issues.append(
                ContractIssue(
                    "artifact_stale_vs_inputs",
                    artifact_path,
                    f"freshness input {input_path!r} has changed; regenerate {artifact_path}",
                )
            )
    return issues


def _project_relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _lower_text(value: object) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_manifest_entries(
    root: Path,
    raw_entries: object,
    *,
    section: str,
) -> list[dict[str, Any]]:
    if not isinstance(raw_entries, list):
        return []

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_entry in raw_entries:
        if isinstance(raw_entry, dict):
            normalized = _normalize_manifest_path(raw_entry.get("path"))
            entry: dict[str, Any] = dict(raw_entry)
        else:
            normalized = _normalize_manifest_path(raw_entry)
            entry = {}
        if normalized is None or normalized in seen:
            continue
        if normalized in MANIFEST_GENERATED_EXCLUDED_PATHS:
            continue
        resolved = _resolve_manifest_path(root, normalized)
        if resolved is None or not resolved.is_file():
            continue
        seen.add(normalized)
        entry["path"] = normalized
        _refresh_manifest_entry_file_fields(entry, resolved)
        if section == "generated_artifacts":
            sources = _normalized_path_list(entry.get("sources"))
            if sources:
                entry["sources"] = sources
        elif "sources" in entry:
            entry.pop("sources", None)
        entries.append(entry)
    return entries


def _normalize_manifest_entry_sections(
    canonical_entries: list[dict[str, Any]],
    generated_entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    normalized_canonical: list[dict[str, Any]] = []
    normalized_generated: list[dict[str, Any]] = []
    seen_canonical: set[str] = set()
    seen_generated: set[str] = set()

    for section, entries in (
        ("canonical_sources", canonical_entries),
        ("generated_artifacts", generated_entries),
    ):
        for entry in entries:
            path = _normalize_manifest_path(entry.get("path"))
            if path is None or path in MANIFEST_GENERATED_EXCLUDED_PATHS:
                continue
            target_section = _manifest_discovery_section(path) or section
            if target_section == "canonical_sources":
                if path in seen_canonical:
                    continue
                entry.pop("sources", None)
                normalized_canonical.append(entry)
                seen_canonical.add(path)
                continue
            if path in seen_generated:
                continue
            normalized_generated.append(entry)
            seen_generated.add(path)
    return normalized_canonical, normalized_generated


def _refresh_manifest_entry_file_fields(entry: dict[str, Any], resolved: Path) -> None:
    entry["sha256"] = _sha256_file(resolved)
    if resolved.suffix == ".tsv":
        entry["columns"] = _read_tsv_header(resolved)
    else:
        entry.pop("columns", None)


def _add_discovered_manifest_entries(
    root: Path,
    canonical_entries: list[dict[str, Any]],
    generated_entries: list[dict[str, Any]],
) -> None:
    canonical_paths = _manifest_entry_path_set(canonical_entries)
    generated_paths = _manifest_entry_path_set(generated_entries)
    for normalized, resolved, section in _discover_manifest_artifact_files(root):
        if normalized in canonical_paths or normalized in generated_paths:
            continue
        entry: dict[str, Any] = {"path": normalized}
        _refresh_manifest_entry_file_fields(entry, resolved)
        if section == "canonical_sources":
            canonical_entries.append(entry)
            canonical_paths.add(normalized)
        else:
            generated_entries.append(entry)
            generated_paths.add(normalized)


def _discover_manifest_artifact_files(root: Path) -> list[tuple[str, Path, str]]:
    candidates: list[tuple[str, Path, str]] = []
    for base in ("bench", "benchmarks", "experiments", "results", "runs", "paper", "research"):
        base_path = root / base
        if not base_path.is_dir():
            continue
        for path in sorted(base_path.rglob("*")):
            if not path.is_file() or _is_excluded_manifest_discovery_path(path):
                continue
            if path.suffix.lower() not in MANIFEST_DISCOVERY_SUFFIXES:
                continue
            normalized = _project_relative_path(root, path)
            if normalized in MANIFEST_GENERATED_EXCLUDED_PATHS:
                continue
            section = _manifest_discovery_section(normalized)
            if section is None:
                continue
            candidates.append((normalized, path, section))
    return candidates


def _is_excluded_manifest_discovery_path(path: Path) -> bool:
    return any(part in MANIFEST_DISCOVERY_EXCLUDED_DIRS for part in path.parts)


def _manifest_discovery_section(path: str) -> str | None:
    if path.startswith(MANIFEST_CANONICAL_PREFIXES):
        return "canonical_sources"
    if path.startswith("paper/artifacts/"):
        return "canonical_sources"
    if path in MANIFEST_CANONICAL_RESEARCH_PATHS:
        return "canonical_sources"
    if path.startswith(MANIFEST_GENERATED_PREFIXES):
        return "generated_artifacts"
    return None


def _add_missing_source_entries(
    root: Path,
    canonical_entries: list[dict[str, Any]],
    generated_entries: list[dict[str, Any]],
) -> None:
    canonical_paths = _manifest_entry_path_set(canonical_entries)
    generated_paths = _manifest_entry_path_set(generated_entries)
    all_paths = canonical_paths | generated_paths
    for entry in list(generated_entries):
        for source in _normalized_path_list(entry.get("sources")):
            if source in all_paths:
                continue
            resolved = _resolve_manifest_path(root, source)
            if resolved is None or not resolved.is_file():
                continue
            source_entry: dict[str, Any] = {"path": source}
            _refresh_manifest_entry_file_fields(source_entry, resolved)
            canonical_entries.append(source_entry)
            canonical_paths.add(source)
            all_paths.add(source)


def _fill_generated_manifest_sources(
    root: Path,
    canonical_entries: list[dict[str, Any]],
    generated_entries: list[dict[str, Any]],
) -> None:
    canonical_paths = _manifest_entry_path_set(canonical_entries)
    generated_paths = _manifest_entry_path_set(generated_entries)
    all_paths = canonical_paths | generated_paths
    canonical_fallbacks = tuple(sorted(canonical_paths))
    for entry in generated_entries:
        normalized = _normalize_manifest_path(entry.get("path"))
        if normalized is None:
            continue
        sources = [
            source
            for source in _normalized_path_list(entry.get("sources"))
            if source != normalized and source in all_paths
        ]
        default_sources: list[str] = []
        if not sources or normalized in MANIFEST_RECOMPUTE_SOURCE_PATHS:
            default_sources = _default_manifest_sources_for_generated_path(
                root,
                normalized,
                all_paths,
                canonical_fallbacks,
            )
        if default_sources:
            sources = default_sources
        if sources:
            entry["sources"] = sources


def _default_manifest_sources_for_generated_path(
    root: Path,
    generated_path: str,
    all_paths: set[str],
    canonical_fallbacks: tuple[str, ...],
) -> list[str]:
    preferred: list[str] = []
    preferred.extend(MANIFEST_SOURCE_PREFERENCES.get(generated_path, ()))
    if generated_path.startswith("paper/artifacts/"):
        preferred.extend(
            path
            for path in canonical_fallbacks
            if path.startswith(("experiments/", "results/", "bench/", "benchmarks/"))
        )
    if generated_path.startswith("paper/figures/") or generated_path.startswith("paper/style_ref/"):
        preferred.extend(
            path
            for path in canonical_fallbacks
            if path.startswith(("experiments/", "results/", "paper/artifacts/"))
        )
    if generated_path == IMAGE2_FIGURES_JSON_PATH.as_posix():
        preferred.extend(
            sorted(
                path
                for path in all_paths
                if path.startswith("paper/figures/")
                and path != generated_path
                and path != PAPER_MAIN_TEX_PATH.as_posix()
            )
        )
    if generated_path == "paper/RESULTS_REPORT.md":
        preferred.extend(sorted(path for path in all_paths if path.startswith("paper/artifacts/")))
        preferred.extend(
            path
            for path in canonical_fallbacks
            if path.startswith(("experiments/", "results/"))
        )
    if generated_path.startswith("research/"):
        preferred.extend(
            path
            for path in canonical_fallbacks
            if path.startswith(("research/", "experiments/", "results/", "benchmarks/"))
        )

    sources: list[str] = []
    seen: set[str] = set()
    for source in [*preferred, *canonical_fallbacks]:
        normalized = _normalize_manifest_path(source)
        if (
            normalized is None
            or normalized == generated_path
            or normalized in seen
            or normalized not in all_paths
        ):
            continue
        resolved = _resolve_manifest_path(root, normalized)
        if resolved is None or not resolved.is_file():
            continue
        seen.add(normalized)
        sources.append(normalized)
        if len(sources) >= 12:
            break
    return sources


def _manifest_entry_path_set(entries: Sequence[dict[str, Any]]) -> set[str]:
    paths: set[str] = set()
    for entry in entries:
        normalized = _normalize_manifest_path(entry.get("path"))
        if normalized:
            paths.add(normalized)
    return paths


def _collect_manifest_entries(
    root: Path,
    raw_entries: object,
    *,
    section: str,
    require_sources: bool,
) -> tuple[list[_ManifestEntry], list[ContractIssue]]:
    if not isinstance(raw_entries, list):
        return [], [
            ContractIssue(
                "invalid_artifact_manifest_section",
                str(ARTIFACT_MANIFEST_PATH),
                f"{section} must be a non-empty list",
            )
        ]
    if not raw_entries:
        return [], [
            ContractIssue(
                "empty_artifact_manifest_section",
                str(ARTIFACT_MANIFEST_PATH),
                f"{section} must contain at least one artifact",
            )
        ]

    entries: list[_ManifestEntry] = []
    issues: list[ContractIssue] = []
    for index, raw_entry in enumerate(raw_entries):
        entry_path = f"{ARTIFACT_MANIFEST_PATH}:{section}[{index}]"
        if not isinstance(raw_entry, dict):
            issues.append(
                ContractIssue(
                    "invalid_artifact_manifest_entry",
                    entry_path,
                    "manifest entry must be an object",
                )
            )
            continue

        normalized_path = _normalize_manifest_path(raw_entry.get("path"))
        if normalized_path is None:
            issues.append(
                ContractIssue(
                    "invalid_artifact_manifest_path",
                    entry_path,
                    "artifact path must be a POSIX relative path inside the project",
                )
            )
            continue

        resolved_path = _resolve_manifest_path(root, normalized_path)
        if resolved_path is None:
            issues.append(
                ContractIssue(
                    "unsafe_artifact_manifest_path",
                    normalized_path,
                    "artifact path resolves outside the project root",
                )
            )
            continue

        sha256 = raw_entry.get("sha256")
        if not isinstance(sha256, str) or not _is_sha256_hex(sha256):
            issues.append(
                ContractIssue(
                    "invalid_artifact_sha256",
                    normalized_path,
                    "artifact sha256 must be a lowercase 64-character hex digest",
                )
            )
            sha256 = ""

        if not resolved_path.exists():
            issues.append(
                ContractIssue(
                    "missing_manifest_artifact",
                    normalized_path,
                    "artifact listed in manifest is missing",
                )
            )
        elif not resolved_path.is_file():
            issues.append(
                ContractIssue(
                    "manifest_artifact_not_file",
                    normalized_path,
                    "artifact listed in manifest must be a file",
                )
            )
        elif sha256 and _sha256_file(resolved_path) != sha256:
            issues.append(
                ContractIssue(
                    "artifact_digest_mismatch",
                    normalized_path,
                    "artifact digest does not match the manifest; regenerate downstream artifacts",
                )
            )

        if resolved_path.suffix == ".tsv":
            issues.extend(_validate_tsv_manifest_schema(raw_entry, resolved_path, normalized_path))

        sources: tuple[str, ...] = ()
        if require_sources:
            parsed_sources, source_issues = _parse_generated_sources(raw_entry, normalized_path)
            sources = parsed_sources
            issues.extend(source_issues)

        entries.append(
            _ManifestEntry(
                section=section,
                path=normalized_path,
                resolved_path=resolved_path,
                sha256=sha256,
                sources=sources,
            )
        )
    return entries, issues


def _parse_generated_sources(
    raw_entry: dict[str, Any],
    generated_path: str,
) -> tuple[tuple[str, ...], list[ContractIssue]]:
    raw_sources = raw_entry.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        return (), [
            ContractIssue(
                "generated_artifact_missing_sources",
                generated_path,
                "generated artifact must list one or more source artifacts",
            )
        ]

    sources: list[str] = []
    issues: list[ContractIssue] = []
    for source in raw_sources:
        normalized_source = _normalize_manifest_path(source)
        if normalized_source is None:
            issues.append(
                ContractIssue(
                    "invalid_generated_artifact_source",
                    generated_path,
                    "generated artifact sources must be POSIX relative project paths",
                )
            )
            continue
        sources.append(normalized_source)
    return tuple(sources), issues


def _validate_tsv_manifest_schema(
    raw_entry: dict[str, Any],
    path: Path,
    display_path: str,
) -> list[ContractIssue]:
    columns = raw_entry.get("columns")
    if not isinstance(columns, list) or not columns or not all(
        isinstance(column, str) and column for column in columns
    ):
        return [
            ContractIssue(
                "missing_tsv_schema",
                display_path,
                "TSV artifacts in the manifest must declare their exact columns",
            )
        ]
    if not path.exists() or not path.is_file():
        return []
    header = _read_tsv_header(path)
    if header != columns:
        return [
            ContractIssue(
                "tsv_schema_mismatch",
                display_path,
                f"TSV header {header!r} does not match manifest columns {columns!r}",
            )
        ]
    return []


def _manifest_source_graph_issues(
    source_graph: dict[str, tuple[str, ...]],
    canonical_paths: set[str],
    generated_paths: set[str],
) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    cycle_paths: set[str] = set()
    visited: dict[str, bool] = {}

    def reaches_canonical(path: str, stack: tuple[str, ...]) -> bool:
        if path in canonical_paths:
            return True
        if path in visited:
            return visited[path]
        if path in stack:
            cycle = " -> ".join((*stack, path))
            if cycle not in cycle_paths:
                cycle_paths.add(cycle)
                issues.append(
                    ContractIssue(
                        "generated_artifact_source_cycle",
                        path,
                        f"generated artifact source cycle: {cycle}",
                    )
                )
            return False
        sources = source_graph.get(path, ())
        found = False
        for source in sources:
            if source in canonical_paths:
                found = True
            elif source in generated_paths and reaches_canonical(source, (*stack, path)):
                found = True
        visited[path] = found
        return found

    for generated_path in generated_paths:
        if reaches_canonical(generated_path, ()):
            continue
        issues.append(
            ContractIssue(
                "generated_artifact_without_canonical_source",
                generated_path,
                "generated artifact source graph does not reach a canonical source",
            )
        )
    return issues


def _normalize_manifest_path(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or "\\" in text:
        return None
    path = Path(text)
    if path.is_absolute():
        return None
    parts = path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    return Path(*parts).as_posix()


def _resolve_manifest_path(root: Path, path: str) -> Path | None:
    root_resolved = root.resolve()
    resolved = (root_resolved / path).resolve(strict=False)
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        return None
    return resolved


def _is_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _read_tsv_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        first_line = handle.readline()
    return first_line.rstrip("\r\n").split("\t") if first_line else []


def _dedupe_contract_issues(issues: list[ContractIssue]) -> list[ContractIssue]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[ContractIssue] = []
    for issue in issues:
        key = (issue.code, issue.path, issue.message)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped


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


def _try_read_json_object(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return _read_json_object(path)
    except ValueError:
        return None


def _write_json_object(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


_PipelineContractsHandler = Callable[[Path], list[ContractIssue]]


def cli_command_specs() -> tuple[tuple[str, str, _PipelineContractsHandler], ...]:
    """Return the public CLI command surface for pipeline contracts.

    The ~25 historical ``validate-*`` quality *gates* are intentionally
    absent: the agent surface was replaced with
    :mod:`argus_skill.skills.stage_checklists` so the L2 reviewer rules
    against a markdown checklist rather than chasing brittle JSON gates.
    Those validator *functions* are still importable from Python so the
    supervisor / harness can use them internally for project-done
    detection — they just are no longer reachable via the CLI.

    The artifact *build/repair* utilities below are NOT quality gates;
    they construct the manifest/freshness/policy artifacts that the agent
    must produce (skills forbid hand-editing those JSON files), so they
    remain on the CLI.
    """

    return (
        (
            "refresh-manifest",
            "repair and refresh paper/ARTIFACT_MANIFEST.json",
            refresh_artifact_manifest,
        ),
        (
            "refresh-artifact-freshness",
            "refresh paper/ARTIFACT_FRESHNESS.json",
            refresh_artifact_freshness,
        ),
        (
            "write-validation-priority-policy",
            "write paper/VALIDATION_PRIORITY_POLICY.json",
            write_validation_priority_policy,
        ),
        (
            "repair-emnlp-contract-artifacts",
            "repair manifest, validation-priority policy, and freshness records",
            repair_emnlp_contract_artifacts,
        ),
    )


def cli_command_handlers() -> dict[str, _PipelineContractsHandler]:
    """Return command -> handler mapping for the public CLI."""

    return {command: handler for command, _help, handler in cli_command_specs()}


_AGENT_VALIDATOR_CLI_NOTICE = (
    "argus-skill: the `validate-*` CLI subcommands have been retired in favour\n"
    "of the per-stage reviewer checklists. The L2 reviewer now reads the\n"
    "current stage's checklist from `argus_skill.skills.stage_checklists` and\n"
    "rules against artifacts directly. Validator functions are still importable\n"
    "from `argus_skill.skills.pipeline_contracts` for internal harness use.\n"
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m argus_skill.skills.pipeline_contracts",
        description="Validate or refresh argus-skill research pipeline contracts.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default=None,
        help="(retired) historical validator subcommand — use stage checklists instead",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="project root containing research/, experiments/, and paper/",
    )

    args = parser.parse_args(list(argv) if argv is not None else None)
    handlers = cli_command_handlers()
    if not handlers or args.command is None or args.command not in handlers:
        # Be loud but exit cleanly so reviewer/engineer rounds that still
        # shell out to an old validator command do not collapse the round
        # on a non-zero exit code that does not actually mean a quality
        # blocker.
        print(_AGENT_VALIDATOR_CLI_NOTICE)
        return 0
    issues = handlers[args.command](Path(args.project_root))
    for issue in issues:
        print(f"{issue.code}\t{issue.path}\t{issue.message}")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
