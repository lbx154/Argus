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
import os
import re
import shutil
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .academic_language_review import (
    ACADEMIC_LANGUAGE_REVIEW_JSON_PATH,
    GENERIC_OPENING_PATTERNS,
    MIN_ACADEMIC_LANGUAGE_SCORE,
    collect_latex_source_paths,
    find_reader_hostile_abstract_issues,
)
from .academic_language_review import (
    ALLOWED_DIRECTIVE_ACTIONS as ALLOWED_ACADEMIC_LANGUAGE_ACTIONS,
)
from .academic_language_review import (
    MODEL_REVIEW_METHODS as ACADEMIC_LANGUAGE_MODEL_METHODS,
)
from .academic_language_review import (
    REQUIRED_CHECK_KEYS as REQUIRED_ACADEMIC_LANGUAGE_CHECKS,
)
from .academic_language_review import (
    SECTION_SCORE_KEYS as REQUIRED_ACADEMIC_SECTION_SCORES,
)
from .paper_calibration import (
    PAPER_DRAFT_REPORT_JSON_PATH,
    PAPER_QUALITY_CALIBRATION_JSON_PATH,
    detect_quality_blockers,
    validate_quality_calibration_file,
)

PIPELINE_STATE_PATH = Path("research/PIPELINE_STATE.json")
SUBMISSION_ASSURANCE_JSON_PATH = Path("paper/SUBMISSION_ASSURANCE.json")
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
CLAIMS_EVIDENCE_AUDIT_JSON_PATH = Path("paper/CLAIMS_EVIDENCE_AUDIT.json")
RESULT_TO_CLAIM_TSV_PATH = Path("paper/artifacts/result_to_claim.tsv")
RESULTS_TABLE_TSV_PATH = Path("paper/artifacts/results_table.tsv")
PAPER_MAIN_TEX_PATH = Path("paper/main.tex")
PAPER_MAIN_PDF_PATH = Path("paper/main.pdf")
PAPER_MAIN_LOG_PATH = Path("paper/main.log")
FORMAT_PREFLIGHT_REPORT_PATH = Path("paper/FORMAT_PREFLIGHT.md")

MIN_RECENT_HIGH_QUALITY_PAPERS = 10
MIN_CLASSIC_PAPERS = 3
MIN_IDEA_CANDIDATES = 3
MIN_IDEA_DERIVATION_SOURCES = 2
MIN_STYLE_EXEMPLARS = 2
MIN_STYLE_EXEMPLAR_PDF_BYTES = 4096
MIN_STYLE_EXEMPLAR_TEXT_CHARS = 4000
MIN_STYLE_PROFILE_CHARS = 1800
MIN_STYLE_BLUEPRINT_CHARS = 1200
RECENT_PAPER_YEAR_CUTOFF = 2023
MIN_MAIN_CONTENT_PAGES = 7.5
MAX_MAIN_CONTENT_PAGES = 8.0
SEVERE_OVERFULL_HBOX_PT = 5.0
OVERFULL_HBOX_COUNT_PT = 5.0
MAX_MODERATE_OVERFULL_HBOXES = 0
MAX_RESEARCH_MD_BODY_FIGURES = 5
MAX_RESEARCH_MD_WIDE_FIGURES = 1
RESEARCH_MD_VISUAL_PAGES = {4, 5, 6, 7}
MIN_RENDERED_CONCLUSION_PAGE_FOR_FULL_BODY = 7
MIN_RENDERED_REFERENCES_PAGE_FOR_FULL_BODY = 8
MIN_FINAL_BIBLIOGRAPHY_ENTRIES = 35
MIN_FINAL_UNIQUE_CITATION_KEYS = 30
MIN_RENDERED_REFERENCE_PAGES = 2
MIN_CONCEPTUAL_FIGURE_ASPECT_RATIO = 1.2
MAX_CONCEPTUAL_FIGURE_ASPECT_RATIO = 2.6
MIN_CONCEPTUAL_FIGURE_PIXEL_WIDTH = 1200
MIN_CONCEPTUAL_FIGURE_PIXEL_HEIGHT = 768
MIN_IMAGE_REVIEW_SCORE = 4.0
MIN_IMAGE2_TEASER_PROMPT_CHARS = 900
MIN_LAYOUT_REVIEW_SCORE = 4.0
IMAGE2_RASTER_OUTPUT_SUFFIXES = {".png", ".jpg", ".jpeg"}
IMAGE2_TEASER_PROMPT_REQUIRED_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "academic_teaser_intent",
        (
            "figure 1",
            "teaser",
            "full-width",
            "page-width",
            "emnlp",
            "acl",
            "academic manuscript",
            "paper figure",
        ),
    ),
    (
        "polished_figma_style",
        (
            "figma",
            "rounded card",
            "rounded cards",
            "pastel",
            "soft color",
            "clean block",
            "flat vector",
            "manuscript figure",
        ),
    ),
    (
        "pinned_exact_text",
        (
            "pinned content",
            "spell exactly",
            "label exactly",
            "labels exactly",
            "must appear exactly",
            "must appear verbatim",
        ),
    ),
    (
        "negative_prompt",
        (
            "negative",
            "avoid:",
            "do not include",
            "no watermark",
            "no photorealism",
            "no tiny unreadable text",
        ),
    ),
    (
        "layout_variant",
        (
            "layout variant",
            "variant-specific layout",
            "swimlane",
            "hub-and-spoke",
            "multi-panel",
            "dashboard",
            "pipeline plus gallery",
            "figma wireframe",
        ),
    ),
)
LOCAL_RENDERER_SCAN_WINDOW_CHARS = 5_000
MAX_LOCAL_RENDERER_SOURCE_BYTES = 2_000_000
LOCAL_RENDERER_SOURCE_SUFFIXES = {
    ".py",
    ".ipynb",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".r",
    ".jl",
    ".sh",
    ".html",
    ".svg",
}
LOCAL_RENDERER_EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "site-packages",
    "venv",
}
LOCAL_CONCEPTUAL_RENDER_TOKENS: tuple[tuple[str, str], ...] = (
    ("imagedraw", "PIL/ImageDraw"),
    ("image.new(", "PIL/Image.new"),
    ("imagefont", "PIL/ImageFont"),
    ("from pil import", "PIL"),
    ("import pil", "PIL"),
    ("matplotlib", "matplotlib"),
    ("pyplot", "matplotlib/pyplot"),
    ("plt.", "matplotlib/pyplot"),
    ("fig.savefig", "matplotlib savefig"),
    ("patches.", "matplotlib patches"),
    ("fancybboxpatch", "matplotlib/FancyBboxPatch"),
    ("svgwrite", "SVG writer"),
    ("<svg", "inline SVG"),
    ("tikzpicture", "TikZ"),
    ("graphviz", "Graphviz"),
    ("networkx", "NetworkX graph drawing"),
)
LATEX_GRAPHICS_SUFFIXES = ("", ".png", ".jpg", ".jpeg", ".pdf", ".eps")
CONCEPTUAL_IMAGE_FIGURE_TYPES = {
    "conceptual",
    "method",
    "method_overview",
    "system",
    "architecture",
    "framework",
    "pipeline",
    "workflow",
    "illustration",
    "overview",
    "overall",
    "teaser",
    "graphical_abstract",
}
CONCEPTUAL_FIGURE_PATH_TOKENS = (
    "figure1",
    "fig1",
    "teaser",
    "teaser_figure",
    "teaser-figure",
    "overview",
    "conceptual_overview",
    "conceptual-overview",
    "overall_overview",
    "overall-overview",
    "overall_framework",
    "overall-framework",
    "overall_method",
    "overall-method",
    "overall_pipeline",
    "overall-pipeline",
    "framework",
    "method_overview",
    "method-overview",
    "architecture",
    "pipeline",
    "workflow",
    "schematic",
    "system_diagram",
    "system-diagram",
    "graphical_abstract",
    "graphical-abstract",
)
CONCEPTUAL_FIGURE_LABEL_RE = re.compile(
    r"(?:^|\s)fig:(?:method(?:[-_]?overview)?|overview|"
    r"overall(?:[-_]?(?:overview|framework|method|system|architecture|pipeline|teaser))?|"
    r"framework|architecture|arch|pipeline|workflow|system|schematic|teaser|"
    r"graphical[-_]?abstract|fig(?:ure)?[-_]?1)(?:$|\s)",
    re.I,
)
CONCEPTUAL_FIGURE_CAPTION_RE = re.compile(
    r"\b(?:overview of (?:our|the) (?:method|framework|approach|system|pipeline|architecture)|"
    r"overall (?:method|framework|approach|system|pipeline|architecture|teaser|overview)|"
    r"method overview|framework diagram|architecture diagram|system diagram|pipeline diagram|"
    r"workflow diagram|high-level (?:method|framework|system|pipeline|architecture)|"
    r"proposed (?:method|framework|architecture|system)|graphical abstract|teaser figure|schematic)\b",
    re.I,
)
DATA_PLOT_FIGURE_RE = re.compile(
    r"\b(?:accuracy|success rate|f1|loss|auc|roc|ablation|confidence interval|ci95|"
    r"p-value|bar chart|line chart|scatter|heatmap|histogram|breakdown|per-family|"
    r"per family|results?|curve|axis|axes)\b",
    re.I,
)
MIN_ACADEMIC_LANGUAGE_REVIEW_SCORE = MIN_ACADEMIC_LANGUAGE_SCORE
LAYOUT_REVIEW_VISION_METHODS = {"vision_pdf_pages", "hybrid_vision_heuristic"}
ALLOWED_LAYOUT_REVIEW_ACTIONS = {
    "shorten_section",
    "split_table",
    "merge_tables",
    "move_float",
    "resize_figure",
    "regenerate_figure",
    "replace_code_label",
    "tighten_paragraph",
    "delete_low_value_content",
    "rebalance_columns",
    "fix_overfull_box",
    "fix_bibliography_appendix_order",
}

ALLOWED_LITERATURE_VENUE_TOKENS: tuple[str, ...] = (
    "emnlp",
    "acl",
    "findings",
    "naacl",
    "eacl",
    "coling",
    "tacl",
    "computational linguistics",
    "neurips",
    "iclr",
    "icml",
    "aaai",
    "ijcai",
    "jmlr",
    "arxiv",
    "acl anthology",
    "dataset",
    "benchmark",
)

ALLOWED_STYLE_EXEMPLAR_LICENSES = {
    "cc-by-4.0",
    "cc-by-sa-4.0",
    "cc-by-nc-4.0",
    "acl-anthology-open-access",
    "arxiv-nonexclusive-local-cache",
    "publisher-open-access-local-cache",
}
ALLOWED_STYLE_EXEMPLAR_STORAGE_POLICIES = {
    "redistributable_open_access",
    "local_research_cache_not_redistributed",
}
STYLE_PROFILE_REQUIRED_TOPICS: dict[str, tuple[str, ...]] = {
    "abstract_shape": ("abstract shape", "abstract"),
    "section_page_allocation": ("section/page allocation", "page allocation", "section allocation"),
    "figure_table_inventory": ("figure/table inventory", "figure inventory", "table inventory"),
    "related_work_shape": ("related-work shape", "related work shape", "related work"),
    "evaluation_layout": ("evaluation layout", "experiment layout", "results layout"),
    "formatting_layout_lessons": ("formatting/layout lessons", "layout lessons", "format lessons"),
    "writing_lessons": ("writing lessons", "prose lessons", "paper-writing lessons"),
    "transfer_plan": ("transfer plan", "apply to our paper", "how to apply"),
    "no_prose_copy_policy": ("no prose copy", "no-prose-copy", "structural style only"),
}
STYLE_BLUEPRINT_REQUIRED_TOPICS: dict[str, tuple[str, ...]] = {
    "section_order": ("section order", "section-by-section", "paper scaffold"),
    "page_budget": ("page budget", "page allocation", "section/page allocation"),
    "paragraph_roles": ("paragraph role", "paragraph-level", "paragraph plan"),
    "figure_table_plan": ("figure/table plan", "figure placement", "table placement"),
    "related_work_grouping": (
        "related-work grouping",
        "related work grouping",
        "method family",
    ),
    "evaluation_sequence": ("evaluation sequence", "evaluation layout", "results flow"),
    "local_evidence_mapping": (
        "local evidence mapping",
        "evidence mapping",
        "claims-evidence",
    ),
    "no_prose_copy_policy": ("no prose copy", "structural style only", "do not copy prose"),
}
AWARD_STYLE_EXEMPLAR_TOKENS = ("best", "award", "outstanding", "distinguished")

PIPELINE_STAGES: tuple[str, ...] = (
    "brief",
    "literature",
    "novelty",
    "plan",
    "benchmark",
    "run",
    "analysis",
    "narrative",
    "draft",
    "assurance",
    "revision",
    "submission",
)

PIPELINE_STATUSES: tuple[str, ...] = (
    "missing",
    "pending",
    "ready",
    "running",
    "blocked",
    "pivot",
    "rejected",
    "done",
)

SUCCESS_STATUSES = {"ready", "done"}

LITERATURE_ARTIFACT_PATTERNS: tuple[str, ...] = (
    "research/LITERATURE_REVIEW.md",
    "research/LIT_MATRIX.tsv",
    str(LITERATURE_GROUNDING_JSON_PATH),
    "research/SOURCE_DISCOVERY.md",
    "research/TREND_INSIGHTS.md",
)

NOVELTY_ARTIFACT_PATTERNS: tuple[str, ...] = (
    "research/NOVELTY_REPORT.md",
    "research/NOVELTY_MAP.md",
    str(IDEA_PROVENANCE_JSON_PATH),
    "research/RELATED_WORK_BLOCKERS.md",
)

PLAN_ARTIFACT_PATTERNS: tuple[str, ...] = (
    "research/EXPERIMENT_PLAN.md",
    "research/CLAIMS_TO_TEST.md",
    "research/BASELINE_AND_BENCHMARK_PLAN.md",
    str(CODE_REUSE_PLAN_JSON_PATH),
    "experiments/BENCHMARK_PROVENANCE.md",
)

REQUIRED_ARTIFACT_PATTERNS: dict[str, tuple[str, ...]] = {
    "brief": ("research/RESEARCH_BRIEF.md",),
    "literature": LITERATURE_ARTIFACT_PATTERNS,
    "novelty": NOVELTY_ARTIFACT_PATTERNS,
    "plan": (
        *LITERATURE_ARTIFACT_PATTERNS,
        *NOVELTY_ARTIFACT_PATTERNS,
        *PLAN_ARTIFACT_PATTERNS,
    ),
    "benchmark": ("experiments/BENCHMARK_PROVENANCE.md",),
    "run": (
        "experiments/BENCHMARK_PROVENANCE.md",
        "experiments/**/manifest.json",
        "experiments/**/status.json",
        "experiments/**/progress.jsonl",
    ),
    "analysis": (
        "paper/RESULTS_REPORT.md",
        "paper/artifacts/claims_evidence.tsv",
        str(RESULT_TO_CLAIM_TSV_PATH),
        str(RESULTS_TABLE_TSV_PATH),
    ),
    "narrative": (
        "research/NARRATIVE_REPORT.md",
        "paper/RESULTS_REPORT.md",
    ),
    "draft": (
        "paper/main.tex",
        "paper/PAGE_BUDGET.md",
        "paper/TEMPLATE_SOURCE.md",
        "paper/PAPER_DRAFT_REPORT.md",
        str(PAPER_DRAFT_REPORT_JSON_PATH),
        str(STYLE_PROFILE_PATH),
        str(STYLE_EXEMPLAR_JSON_PATH),
        str(IMAGE2_FIGURES_JSON_PATH),
    ),
    "assurance": (
        "paper/SUBMISSION_ASSURANCE.md",
        "paper/SUBMISSION_ASSURANCE.json",
        str(PAPER_QUALITY_CALIBRATION_JSON_PATH),
        str(LAYOUT_REVIEW_JSON_PATH),
        str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
        "paper/CLAIMS_EVIDENCE_AUDIT.tsv",
        str(CLAIMS_EVIDENCE_AUDIT_JSON_PATH),
    ),
    "submission": (
        "paper/main.pdf",
        "paper/SUBMISSION_ASSURANCE.json",
        str(LAYOUT_REVIEW_JSON_PATH),
        str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
    ),
}

ARTIFACT_MANIFEST_STAGES = {"analysis", "narrative", "draft", "assurance", "submission"}
LITERATURE_GROUNDING_STAGES = {"literature", "novelty", "plan"}
IDEA_PROVENANCE_STAGES = {"novelty", "plan"}
CODE_REUSE_PLAN_STAGES = {"plan"}
EMNLP_PAPER_CONTRACT_STAGES = {"draft", "assurance", "submission"}
LAYOUT_REVIEW_STAGES = {"assurance", "submission"}
ACADEMIC_LANGUAGE_REVIEW_STAGES = {"assurance", "submission"}
FULL_EMNLP_REQUIRED_STAGES: tuple[str, ...] = (
    "brief",
    "literature",
    "novelty",
    "plan",
    "run",
    "analysis",
    "narrative",
    "draft",
    "assurance",
    "submission",
)

ASSURANCE_VERDICTS: tuple[str, ...] = (
    "PASS",
    "WARN",
    "FAIL",
    "BLOCKED",
    "ERROR",
    "NOT_APPLICABLE",
)

BLOCKING_ASSURANCE_VERDICTS = {"FAIL", "BLOCKED", "ERROR"}

ASSURANCE_LAYERS: tuple[str, ...] = (
    "experiment_integrity",
    "result_to_claim",
    "paper_claim_audit",
    "idea_provenance_and_code_reuse",
    "literature_and_exemplar_grounding",
    "citation_audit",
    "kill_argument",
    "paper_quality_calibration",
    "research_md_format_preflight",
    "academic_language_review",
    "layout_aesthetic_review",
    "submission_package",
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


def validate_pipeline_state(project_root: Path) -> list[ContractIssue]:
    """Validate ``research/PIPELINE_STATE.json`` and completed-stage artifacts."""

    root = Path(project_root)
    state_path = root / PIPELINE_STATE_PATH
    if not state_path.exists():
        return [
            ContractIssue(
                "missing_pipeline_state",
                str(PIPELINE_STATE_PATH),
                "research pipeline state file is missing",
            )
        ]

    try:
        state = _read_json_object(state_path)
    except ValueError as exc:
        return [ContractIssue("invalid_pipeline_state_json", str(PIPELINE_STATE_PATH), str(exc))]

    issues: list[ContractIssue] = []
    current_stage = state.get("current_stage")
    if not isinstance(current_stage, str):
        issues.append(
            ContractIssue(
                "missing_current_stage",
                str(PIPELINE_STATE_PATH),
                "current_stage must be a string",
            )
        )
    elif current_stage not in PIPELINE_STAGES:
        issues.append(
            ContractIssue(
                "unknown_current_stage",
                str(PIPELINE_STATE_PATH),
                f"current_stage {current_stage!r} is not a known stage",
            )
        )

    stages = state.get("stages")
    if not isinstance(stages, dict):
        issues.append(
            ContractIssue(
                "missing_stages",
                str(PIPELINE_STATE_PATH),
                "stages must be an object keyed by pipeline stage",
            )
        )
        return issues

    manifest_checked = False
    literature_grounding_checked = False
    idea_provenance_checked = False
    code_reuse_plan_checked = False
    style_exemplar_checked = False
    image2_figures_checked = False
    emnlp_paper_contract_checked = False
    layout_review_checked = False
    academic_language_review_checked = False
    for stage, value in stages.items():
        if stage not in PIPELINE_STAGES:
            issues.append(
                ContractIssue(
                    "unknown_stage",
                    str(PIPELINE_STATE_PATH),
                    f"stage {stage!r} is not a known pipeline stage",
                )
            )
            continue
        if not isinstance(value, dict):
            issues.append(
                ContractIssue(
                    "invalid_stage_entry",
                    str(PIPELINE_STATE_PATH),
                    f"stage {stage!r} must map to an object",
                )
            )
            continue
        status = value.get("status")
        if not isinstance(status, str) or status not in PIPELINE_STATUSES:
            issues.append(
                ContractIssue(
                    "invalid_stage_status",
                    str(PIPELINE_STATE_PATH),
                    f"stage {stage!r} has invalid status {status!r}",
                )
            )
            continue
        if status in SUCCESS_STATUSES:
            issues.extend(_missing_artifact_issues(root, stage))
            if stage in LITERATURE_GROUNDING_STAGES and not literature_grounding_checked:
                issues.extend(validate_literature_grounding(root))
                literature_grounding_checked = True
            if stage in IDEA_PROVENANCE_STAGES and not idea_provenance_checked:
                issues.extend(validate_idea_provenance(root))
                idea_provenance_checked = True
            if stage in CODE_REUSE_PLAN_STAGES and not code_reuse_plan_checked:
                issues.extend(validate_code_reuse_plan(root))
                code_reuse_plan_checked = True
            if stage in ARTIFACT_MANIFEST_STAGES and not manifest_checked:
                issues.extend(validate_artifact_manifest(root))
                manifest_checked = True
            if stage in EMNLP_PAPER_CONTRACT_STAGES:
                if not literature_grounding_checked:
                    issues.extend(validate_literature_grounding(root))
                    literature_grounding_checked = True
                if not idea_provenance_checked:
                    issues.extend(validate_idea_provenance(root))
                    idea_provenance_checked = True
                if not code_reuse_plan_checked:
                    issues.extend(validate_code_reuse_plan(root))
                    code_reuse_plan_checked = True
                if not style_exemplar_checked:
                    issues.extend(validate_style_exemplar(root))
                    style_exemplar_checked = True
                if not image2_figures_checked:
                    issues.extend(validate_image2_figures(root))
                    image2_figures_checked = True
                if not emnlp_paper_contract_checked:
                    issues.extend(validate_emnlp_paper_contract(root))
                    emnlp_paper_contract_checked = True
            if stage in LAYOUT_REVIEW_STAGES and not layout_review_checked:
                issues.extend(validate_layout_review(root))
                layout_review_checked = True
            if (
                stage in ACADEMIC_LANGUAGE_REVIEW_STAGES
                and not academic_language_review_checked
            ):
                issues.extend(validate_academic_language_review(root))
                academic_language_review_checked = True
            if stage == "submission":
                issues.extend(validate_submission_readiness(root))

    return issues


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
    """Refresh digests and TSV headers in an existing artifact manifest."""

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

    for section in ("canonical_sources", "generated_artifacts"):
        entries = manifest.get(section)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            normalized = _normalize_manifest_path(entry.get("path"))
            if normalized is None:
                continue
            resolved = _resolve_manifest_path(root, normalized)
            if resolved is None or not resolved.is_file():
                continue
            entry["path"] = normalized
            entry["sha256"] = _sha256_file(resolved)
            if resolved.suffix == ".tsv":
                entry["columns"] = _read_tsv_header(resolved)

    _write_json_object(manifest_path, manifest)
    return validate_artifact_manifest(root)


def validate_literature_grounding(project_root: Path) -> list[ContractIssue]:
    """Validate literature grounding for complete EMNLP-style projects."""

    root = Path(project_root)
    path = root / LITERATURE_GROUNDING_JSON_PATH
    if not path.exists():
        return [
            ContractIssue(
                "missing_literature_grounding",
                str(LITERATURE_GROUNDING_JSON_PATH),
                "literature grounding JSON is missing",
            )
        ]

    try:
        payload = _read_json_object(path)
    except ValueError as exc:
        return [
            ContractIssue(
                "invalid_literature_grounding_json",
                str(LITERATURE_GROUNDING_JSON_PATH),
                str(exc),
            )
        ]

    issues: list[ContractIssue] = []
    issues.extend(
        _validate_reference_entries(
            payload,
            "recent_high_quality_papers",
            min_count=MIN_RECENT_HIGH_QUALITY_PAPERS,
            min_year=RECENT_PAPER_YEAR_CUTOFF,
        )
    )
    issues.extend(
        _validate_reference_entries(
            payload,
            "classic_papers",
            min_count=MIN_CLASSIC_PAPERS,
            min_year=None,
        )
    )
    issues.extend(_validate_trend_sources(payload))
    return _dedupe_contract_issues(issues)


def validate_idea_provenance(project_root: Path) -> list[ContractIssue]:
    """Validate that the research idea is derived from literature/code, not agent brainstorming."""

    root = Path(project_root)
    path = root / IDEA_PROVENANCE_JSON_PATH
    if not path.exists():
        return [
            ContractIssue(
                "missing_idea_provenance",
                str(IDEA_PROVENANCE_JSON_PATH),
                "idea provenance JSON is missing; ideas must be selected after literature/code survey",
            )
        ]

    try:
        payload = _read_json_object(path)
    except ValueError as exc:
        return [
            ContractIssue(
                "invalid_idea_provenance_json",
                str(IDEA_PROVENANCE_JSON_PATH),
                str(exc),
            )
        ]

    issues: list[ContractIssue] = []
    mode = _lower_text(payload.get("idea_generation_mode", payload.get("generation_mode")))
    if mode not in {"literature_grounded", "literature_and_code_grounded", "paper_derived"}:
        issues.append(
            ContractIssue(
                "invalid_idea_generation_mode",
                str(IDEA_PROVENANCE_JSON_PATH),
                "idea_generation_mode must be literature_grounded, literature_and_code_grounded, or paper_derived",
            )
        )
    if "agent" in mode or "brainstorm" in mode:
        issues.append(
            ContractIssue(
                "agent_brainstormed_idea",
                str(IDEA_PROVENANCE_JSON_PATH),
                "agent-generated brainstorming cannot be the source of the paper idea",
            )
        )

    if payload.get("not_agent_brainstorm") is not True and payload.get("agent_generated") is not False:
        issues.append(
            ContractIssue(
                "missing_not_agent_brainstorm_attestation",
                str(IDEA_PROVENANCE_JSON_PATH),
                "idea provenance must attest not_agent_brainstorm=true or agent_generated=false",
            )
        )

    raw_candidates = payload.get("candidate_ideas")
    if not isinstance(raw_candidates, list):
        issues.append(
            ContractIssue(
                "missing_candidate_ideas",
                str(IDEA_PROVENANCE_JSON_PATH),
                "candidate_ideas must list literature-derived candidates before selecting one",
            )
        )
    else:
        if len(raw_candidates) < MIN_IDEA_CANDIDATES:
            issues.append(
                ContractIssue(
                    "insufficient_idea_candidates",
                    str(IDEA_PROVENANCE_JSON_PATH),
                    f"candidate_ideas must contain at least {MIN_IDEA_CANDIDATES} literature-derived candidates",
                )
            )
        for index, raw_candidate in enumerate(raw_candidates):
            candidate_path = f"{IDEA_PROVENANCE_JSON_PATH}:candidate_ideas[{index}]"
            if not isinstance(raw_candidate, dict):
                issues.append(
                    ContractIssue("invalid_idea_candidate", candidate_path, "candidate idea must be an object")
                )
                continue
            if not _is_nonempty_string(raw_candidate.get("title")):
                issues.append(ContractIssue("missing_idea_candidate_title", candidate_path, "candidate needs a title"))
            source_refs = raw_candidate.get("source_refs", raw_candidate.get("derived_from"))
            issues.extend(
                _validate_source_ref_entries(
                    source_refs,
                    candidate_path,
                    min_count=1,
                    require_paper_source=True,
                )
            )

    selected = payload.get("selected_idea")
    if not isinstance(selected, dict):
        issues.append(
            ContractIssue(
                "missing_selected_idea",
                str(IDEA_PROVENANCE_JSON_PATH),
                "selected_idea must be an object derived from surveyed sources",
            )
        )
    else:
        selected_path = f"{IDEA_PROVENANCE_JSON_PATH}:selected_idea"
        for field in ("title", "research_gap", "novelty_delta", "selection_rationale"):
            if not _is_nonempty_string(selected.get(field)):
                issues.append(
                    ContractIssue(
                        "missing_selected_idea_field",
                        selected_path,
                        f"selected_idea must include non-empty {field}",
                    )
                )
        source_refs = selected.get("derived_from", selected.get("source_refs"))
        issues.extend(
            _validate_source_ref_entries(
                source_refs,
                selected_path,
                min_count=MIN_IDEA_DERIVATION_SOURCES,
                require_paper_source=True,
            )
        )

    return _dedupe_contract_issues(issues)


def validate_code_reuse_plan(project_root: Path) -> list[ContractIssue]:
    """Validate source-code survey and reuse decisions for research implementation."""

    root = Path(project_root)
    path = root / CODE_REUSE_PLAN_JSON_PATH
    if not path.exists():
        return [
            ContractIssue(
                "missing_code_reuse_plan",
                str(CODE_REUSE_PLAN_JSON_PATH),
                "code reuse plan is missing; survey paper/open-source code before implementation",
            )
        ]

    try:
        payload = _read_json_object(path)
    except ValueError as exc:
        return [
            ContractIssue(
                "invalid_code_reuse_plan_json",
                str(CODE_REUSE_PLAN_JSON_PATH),
                str(exc),
            )
        ]

    issues: list[ContractIssue] = []
    searched_queries = payload.get("searched_queries")
    if not isinstance(searched_queries, list) or not any(_is_nonempty_string(query) for query in searched_queries):
        issues.append(
            ContractIssue(
                "missing_code_search_queries",
                str(CODE_REUSE_PLAN_JSON_PATH),
                "searched_queries must record paper-code/repository search terms or URLs tried",
            )
        )

    raw_sources = payload.get("code_sources", payload.get("repositories"))
    if not isinstance(raw_sources, list):
        issues.append(
            ContractIssue(
                "missing_code_sources",
                str(CODE_REUSE_PLAN_JSON_PATH),
                "code_sources must list surveyed official paper code, benchmark repos, or reusable libraries",
            )
        )
        return _dedupe_contract_issues(issues)

    if not raw_sources and not _is_nonempty_string(payload.get("no_usable_external_code_reason")):
        issues.append(
            ContractIssue(
                "empty_code_sources_without_justification",
                str(CODE_REUSE_PLAN_JSON_PATH),
                "if no external code is usable, record no_usable_external_code_reason after a real search",
            )
        )

    usable_decisions = {"use", "adapt", "fork", "reference", "baseline"}
    has_usable_source = False
    for index, raw_entry in enumerate(raw_sources):
        entry_path = f"{CODE_REUSE_PLAN_JSON_PATH}:code_sources[{index}]"
        if not isinstance(raw_entry, dict):
            issues.append(ContractIssue("invalid_code_source", entry_path, "code source must be an object"))
            continue
        issues.extend(_validate_code_source_entry(raw_entry, entry_path))
        decision = _lower_text(raw_entry.get("reuse_decision", raw_entry.get("decision")))
        has_usable_source = has_usable_source or decision in usable_decisions

    if raw_sources and not has_usable_source and not _is_nonempty_string(payload.get("from_scratch_justification")):
        issues.append(
            ContractIssue(
                "all_code_sources_rejected_without_justification",
                str(CODE_REUSE_PLAN_JSON_PATH),
                "if all surveyed code is rejected, explain why implementation must be from scratch",
            )
        )

    return _dedupe_contract_issues(issues)


def validate_style_exemplar(project_root: Path) -> list[ContractIssue]:
    """Validate structural exemplar metadata for paper drafting."""

    root = Path(project_root)
    path = root / STYLE_EXEMPLAR_JSON_PATH
    if not path.exists():
        return [
            ContractIssue(
                "missing_style_exemplar",
                str(STYLE_EXEMPLAR_JSON_PATH),
                "every paper project must name at least one excellent structural exemplar",
            )
        ]

    try:
        payload = _read_json_object(path)
    except ValueError as exc:
        return [
            ContractIssue(
                "invalid_style_exemplar_json",
                str(STYLE_EXEMPLAR_JSON_PATH),
                str(exc),
            )
        ]

    if payload.get("exemplar_schema_version") != 2:
        issues = [
            ContractIssue(
                "invalid_style_exemplar_schema_version",
                str(STYLE_EXEMPLAR_JSON_PATH),
                "style exemplar JSON must use exemplar_schema_version: 2 with local PDF/text/hash evidence",
            )
        ]
    else:
        issues = []

    raw_exemplars = payload.get("exemplars")
    if isinstance(raw_exemplars, list):
        exemplars = raw_exemplars
    elif _looks_like_exemplar_entry(payload):
        exemplars = [payload]
    else:
        return [
            ContractIssue(
                "missing_style_exemplars",
                str(STYLE_EXEMPLAR_JSON_PATH),
                "style exemplar JSON must contain an exemplars list or one exemplar object",
            )
        ]

    if not exemplars:
        return [
            ContractIssue(
                "empty_style_exemplars",
                str(STYLE_EXEMPLAR_JSON_PATH),
                "at least one excellent paper exemplar is required",
            )
        ]

    if len(exemplars) < MIN_STYLE_EXEMPLARS:
        issues.append(
            ContractIssue(
                "too_few_style_exemplars",
                str(STYLE_EXEMPLAR_JSON_PATH),
                f"style learning requires at least {MIN_STYLE_EXEMPLARS} downloaded paper exemplars",
            )
        )

    exemplar_venues: set[str] = set()
    exemplar_years: set[int] = set()
    has_award_exemplar = False
    for index, raw_entry in enumerate(exemplars):
        entry_path = f"{STYLE_EXEMPLAR_JSON_PATH}:exemplars[{index}]"
        if not isinstance(raw_entry, dict):
            issues.append(
                ContractIssue("invalid_style_exemplar_entry", entry_path, "exemplar must be an object")
            )
            continue
        issues.extend(_validate_style_exemplar_entry(root, raw_entry, entry_path))
        venue = _lower_text(raw_entry.get("venue"))
        if venue:
            exemplar_venues.add(venue)
        year = _int_or_none(raw_entry.get("year"))
        if year is not None:
            exemplar_years.add(year)
        has_award_exemplar = has_award_exemplar or _is_award_style_exemplar(raw_entry)

    if len(exemplars) >= MIN_STYLE_EXEMPLARS and len(exemplar_venues) < 2 and len(exemplar_years) < 2:
        issues.append(
            ContractIssue(
                "style_exemplars_not_diverse",
                str(STYLE_EXEMPLAR_JSON_PATH),
                "use exemplars from at least two venues or years to avoid copying one paper's quirks",
            )
        )
    if not has_award_exemplar:
        issues.append(
            ContractIssue(
                "missing_award_style_exemplar",
                str(STYLE_EXEMPLAR_JSON_PATH),
                "include at least one recent best/outstanding/award paper exemplar for top-conference calibration",
            )
        )
    issues.extend(_validate_style_structure_blueprint(root))
    return _dedupe_contract_issues(issues)


def validate_image2_figures(project_root: Path) -> list[ContractIssue]:
    """Validate that conceptual raster figures are routed through image-2."""

    root = Path(project_root)
    path = root / IMAGE2_FIGURES_JSON_PATH
    if not path.exists():
        return [
            ContractIssue(
                "missing_image2_figures_manifest",
                str(IMAGE2_FIGURES_JSON_PATH),
                "image-2 figure manifest is missing",
            )
        ]

    try:
        payload = _read_json_object(path)
    except ValueError as exc:
        return [
            ContractIssue(
                "invalid_image2_figures_json",
                str(IMAGE2_FIGURES_JSON_PATH),
                str(exc),
            )
        ]

    raw_figures = payload.get("figures")
    if not isinstance(raw_figures, list) or not raw_figures:
        return [
            ContractIssue(
                "empty_image2_figures",
                str(IMAGE2_FIGURES_JSON_PATH),
                "figures must contain at least one image-2 conceptual figure entry",
            )
        ]

    issues: list[ContractIssue] = []
    has_image2_conceptual_figure = False
    for index, raw_entry in enumerate(raw_figures):
        entry_path = f"{IMAGE2_FIGURES_JSON_PATH}:figures[{index}]"
        if not isinstance(raw_entry, dict):
            issues.append(
                ContractIssue("invalid_image2_figure_entry", entry_path, "figure entry must be an object")
            )
            continue
        entry_issues, is_image2_conceptual = _validate_figure_entry(root, raw_entry, entry_path)
        issues.extend(entry_issues)
        has_image2_conceptual_figure = has_image2_conceptual_figure or is_image2_conceptual

    if not has_image2_conceptual_figure:
        issues.append(
            ContractIssue(
                "missing_image2_conceptual_figure",
                str(IMAGE2_FIGURES_JSON_PATH),
                "at least one conceptual/method/system paper figure must be generated with image-2",
            )
        )
    issues.extend(_validate_body_image2_conceptual_figure_usage(root, raw_figures=raw_figures))
    return _dedupe_contract_issues(issues)


def validate_emnlp_paper_contract(project_root: Path) -> list[ContractIssue]:
    """Validate that the draft is a complete EMNLP-style long paper, not a pilot."""

    root = Path(project_root)
    path = root / PAPER_DRAFT_REPORT_JSON_PATH
    if not path.exists():
        return [
            ContractIssue(
                "missing_paper_draft_report_json",
                str(PAPER_DRAFT_REPORT_JSON_PATH),
                "machine-readable paper draft report is missing",
            )
        ]

    try:
        payload = _read_json_object(path)
    except ValueError as exc:
        return [
            ContractIssue(
                "invalid_paper_draft_report_json",
                str(PAPER_DRAFT_REPORT_JSON_PATH),
                str(exc),
            )
        ]

    issues: list[ContractIssue] = []
    target_venue = payload.get("target_venue")
    target_venue_normalized = str(target_venue).upper() if isinstance(target_venue, str) else ""
    if target_venue_normalized != "EMNLP":
        issues.append(
            ContractIssue(
                "invalid_target_venue",
                str(PAPER_DRAFT_REPORT_JSON_PATH),
                "target_venue must be EMNLP for this paper contract",
            )
        )

    paper_scope = payload.get("paper_scope", payload.get("draft_scope"))
    paper_scope_normalized = str(paper_scope).lower() if isinstance(paper_scope, str) else ""
    if paper_scope_normalized not in {"long-paper", "emnlp-long-paper", "acl-long-paper"}:
        issues.append(
            ContractIssue(
                "not_long_paper_scope",
                str(PAPER_DRAFT_REPORT_JSON_PATH),
                "paper_scope/draft_scope must be long-paper; pilot, short, or workshop scopes cannot pass",
            )
        )

    pages = _float_or_none(payload.get("main_content_pages"))
    if pages is None:
        issues.append(
            ContractIssue(
                "missing_main_content_pages",
                str(PAPER_DRAFT_REPORT_JSON_PATH),
                "main_content_pages must be a number",
            )
        )
    elif pages < MIN_MAIN_CONTENT_PAGES:
        issues.append(
            ContractIssue(
                "underlength_emnlp_paper",
                str(PAPER_DRAFT_REPORT_JSON_PATH),
                f"main content must be at least {MIN_MAIN_CONTENT_PAGES} pages for a full paper",
            )
        )
    elif pages > MAX_MAIN_CONTENT_PAGES:
        issues.append(
            ContractIssue(
                "overlength_emnlp_paper",
                str(PAPER_DRAFT_REPORT_JSON_PATH),
                f"main content exceeds the {MAX_MAIN_CONTENT_PAGES}-page EMNLP long-paper limit",
            )
        )

    if payload.get("official_acl_template") is not True:
        issues.append(
            ContractIssue(
                "missing_official_acl_template",
                str(PAPER_DRAFT_REPORT_JSON_PATH),
                "official_acl_template must be true",
            )
        )

    assessment = payload.get("submission_quality_self_assessment")
    if assessment != "ready":
        issues.append(
            ContractIssue(
                "draft_not_submission_quality",
                str(PAPER_DRAFT_REPORT_JSON_PATH),
                "submission_quality_self_assessment must be ready for full-paper readiness",
            )
        )

    issues.extend(validate_research_md_format_preflight(root))

    return _dedupe_contract_issues(issues)


def validate_paper_format(project_root: Path) -> list[ContractIssue]:
    """Validate source-level paper formatting evidence for reviewable EMNLP output."""

    root = Path(project_root)
    issues: list[ContractIssue] = []
    tex_path = root / PAPER_MAIN_TEX_PATH
    pdf_path = root / PAPER_MAIN_PDF_PATH
    log_path = root / PAPER_MAIN_LOG_PATH
    report_path = root / PAPER_DRAFT_REPORT_JSON_PATH
    report = _try_read_json_object(report_path)
    allowed_labels = _paper_allowed_code_labels(report)

    if not tex_path.is_file():
        return [
            ContractIssue(
                "missing_main_tex",
                str(PAPER_MAIN_TEX_PATH),
                "paper/main.tex is required for paper-format validation",
            )
        ]
    if not pdf_path.is_file():
        issues.append(
            ContractIssue(
                "missing_compiled_pdf",
                str(PAPER_MAIN_PDF_PATH),
                "submission-ready papers must include compiled paper/main.pdf",
            )
        )

    tex_text = tex_path.read_text(encoding="utf-8", errors="replace")
    tex_without_comments = _strip_latex_comments(tex_text)
    issues.extend(_validate_reference_appendix_order(tex_without_comments))
    issues.extend(_validate_display_labels(tex_without_comments, allowed_labels))

    if log_path.is_file():
        issues.extend(_validate_latex_log(log_path))
    else:
        issues.append(
            ContractIssue(
                "missing_latex_log",
                str(PAPER_MAIN_LOG_PATH),
                "submission-ready papers must keep the LaTeX compile log for format checks",
            )
        )

    return _dedupe_contract_issues(issues)


def validate_research_md_format_preflight(project_root: Path) -> list[ContractIssue]:
    """Validate the paper against the stricter research.md EMNLP formatting contract."""

    root = Path(project_root)
    issues = validate_paper_format(root)
    tex_path = root / PAPER_MAIN_TEX_PATH
    if not tex_path.is_file():
        return _dedupe_contract_issues(issues)

    report = _try_read_json_object(root / PAPER_DRAFT_REPORT_JSON_PATH)
    source_texts, missing_sources = _load_transitive_latex_sources(root)
    for rel_path in missing_sources:
        issues.append(
            ContractIssue(
                "missing_transitive_latex_source",
                rel_path,
                "research.md format preflight requires every \\input/\\include/BibTeX source to exist",
            )
        )

    tex_sources = {path: text for path, text in source_texts.items() if path.endswith(".tex")}
    bib_sources = {path: text for path, text in source_texts.items() if path.endswith(".bib")}
    stripped_tex_sources = {path: _strip_latex_comments(text) for path, text in tex_sources.items()}
    combined_tex = "\n".join(stripped_tex_sources.values())
    body_tex = _expanded_latex_body(root) or _latex_before_appendix(combined_tex)

    issues.extend(_validate_research_md_acl_review_source(combined_tex))
    issues.extend(_validate_research_md_anonymous_author(combined_tex, report))
    issues.extend(_validate_research_md_required_sections(combined_tex))
    issues.extend(_validate_research_md_placeholders(stripped_tex_sources))
    issues.extend(_validate_research_md_bibliography_markers(bib_sources))
    issues.extend(_validate_research_md_figure_contract(body_tex))
    if (root / IMAGE2_FIGURES_JSON_PATH).is_file():
        issues.extend(_validate_body_image2_conceptual_figure_usage(root, body_tex=body_tex))
    issues.extend(_validate_research_md_table_contract(body_tex, combined_tex, report))

    pdf_pages = _extract_pdf_text_pages(root / PAPER_MAIN_PDF_PATH)
    if pdf_pages is not None:
        issues.extend(_validate_research_md_pdf_text(pdf_pages))
    issues.extend(_validate_research_md_reference_depth(bib_sources, body_tex, pdf_pages))

    return _dedupe_contract_issues(issues)


def _load_transitive_latex_sources(root: Path) -> tuple[dict[str, str], list[str]]:
    source_paths, missing_sources = collect_latex_source_paths(root)
    source_texts: dict[str, str] = {}
    for rel_path in source_paths:
        source_texts[rel_path] = (root / rel_path).read_text(encoding="utf-8", errors="replace")
    return source_texts, missing_sources


def _latex_before_appendix(tex_text: str) -> str:
    return re.split(r"\\appendix\b", tex_text, maxsplit=1)[0]


def _expanded_latex_body(root: Path) -> str:
    expanded, _ = _expand_latex_source_until_appendix(
        root,
        PAPER_MAIN_TEX_PATH.as_posix(),
        seen=set(),
    )
    return expanded


def _expand_latex_source_until_appendix(
    root: Path,
    rel_path: str,
    *,
    seen: set[str],
) -> tuple[str, bool]:
    if rel_path in seen:
        return "", False
    seen.add(rel_path)
    resolved = root / rel_path
    if not resolved.is_file():
        return "", False
    text = _strip_latex_comments(resolved.read_text(encoding="utf-8", errors="replace"))
    appendix_match = re.search(r"\\appendix\b", text)
    stopped = appendix_match is not None
    if appendix_match is not None:
        text = text[: appendix_match.start()]

    pattern = re.compile(r"\\(?:input|include|subfile)\s*\{([^{}]+)\}")
    chunks: list[str] = []
    cursor = 0
    for match in pattern.finditer(text):
        chunks.append(text[cursor : match.start()])
        child_rel = _resolve_latex_tex_child(rel_path, match.group(1))
        if child_rel is not None:
            child_text, child_stopped = _expand_latex_source_until_appendix(
                root,
                child_rel,
                seen=seen,
            )
            chunks.append(child_text)
            if child_stopped:
                return "".join(chunks), True
        cursor = match.end()
    chunks.append(text[cursor:])
    return "".join(chunks), stopped


def _resolve_latex_tex_child(current_rel: str, raw_child: str) -> str | None:
    raw = raw_child.strip()
    if not raw or raw.startswith(("/", "\\")):
        return None
    child = Path(raw)
    if child.suffix == "":
        child = child.with_suffix(".tex")
    normalized = _normalize_manifest_path((Path(current_rel).parent / child).as_posix())
    if normalized is None or not normalized.endswith(".tex"):
        return None
    return normalized


def _validate_research_md_acl_review_source(tex_text: str) -> list[ContractIssue]:
    if re.search(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{\s*acl\s*\}", tex_text):
        return []
    return [
        ContractIssue(
            "missing_acl_style_package",
            str(PAPER_MAIN_TEX_PATH),
            "research.md requires the official ACL/EMNLP LaTeX style, e.g. \\usepackage[review]{acl}",
        )
    ]


def _validate_research_md_anonymous_author(tex_text: str, report: dict[str, Any]) -> list[ContractIssue]:
    phase = str(report.get("submission_phase", report.get("phase", "review"))).strip().lower()
    if phase in {"camera_ready", "camera-ready", "final", "accepted"}:
        return []
    authors = " ".join(_extract_latex_command_arguments(tex_text, "author"))
    author_plain = _plain_latex_text(authors).lower()
    if "anonymous" in author_plain and "emnlp" in author_plain:
        return []
    return [
        ContractIssue(
            "missing_anonymous_emnlp_author",
            str(PAPER_MAIN_TEX_PATH),
            "review submissions must use an anonymous EMNLP author block before camera-ready mode",
        )
    ]


def _validate_research_md_required_sections(tex_text: str) -> list[ContractIssue]:
    titles = _latex_section_titles(tex_text)
    title_text = "\n".join(titles)
    requirements = {
        "missing_limitations_section": ("limitations", "paper must include a Limitations section"),
        "missing_ethics_section": (
            "ethical considerations|ethics",
            "paper must include an Ethical Considerations/Ethics section",
        ),
        "missing_conclusion_section": ("conclusion", "paper must include a Conclusion section before references"),
        "missing_reproducibility_appendix": (
            "reproducibility",
            "paper must include a reproducibility appendix/section with commands, seeds, and artifacts",
        ),
    }
    issues: list[ContractIssue] = []
    for code, (pattern, message) in requirements.items():
        if not re.search(rf"\b(?:{pattern})\b", title_text):
            issues.append(ContractIssue(code, str(PAPER_MAIN_TEX_PATH), message))
    return issues


def _latex_section_titles(tex_text: str) -> list[str]:
    titles: list[str] = []
    for command in ("section", "subsection", "subsubsection"):
        titles.extend(_plain_latex_text(title).lower() for title in _extract_latex_command_arguments(tex_text, command))
    return titles


def _plain_latex_text(value: str) -> str:
    text = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", " ", value)
    text = re.sub(r"[{}$]", " ", text)
    text = text.replace(r"\&", "&").replace("~", " ")
    return re.sub(r"\s+", " ", text).strip()


def _validate_research_md_placeholders(stripped_tex_sources: dict[str, str]) -> list[ContractIssue]:
    patterns = (
        r"\[(?:PLACEHOLDER|TODO|TBD|VERIFY_CITATION)\]",
        r"\b(?:TODO|TBD|FIXME|VERIFY_CITATION)\b",
        r"待补充|占位符",
    )
    issues: list[ContractIssue] = []
    for rel_path, text in stripped_tex_sources.items():
        for pattern in patterns:
            if re.search(pattern, text, re.I):
                issues.append(
                    ContractIssue(
                        "research_md_placeholder_text",
                        rel_path,
                        "research.md preflight forbids placeholders, TODO/TBD markers, and citation-verification markers",
                    )
                )
                break
    return issues


def _validate_research_md_bibliography_markers(bib_sources: dict[str, str]) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    if not bib_sources:
        issues.append(
            ContractIssue(
                "missing_bib_source",
                str(PAPER_MAIN_TEX_PATH),
                "research.md preflight requires a tracked BibTeX source, not implicit or missing references",
            )
        )
    for rel_path, text in bib_sources.items():
        if re.search(r"%\s*UNVERIFIED|\bUNVERIFIED\b", text, re.I):
            issues.append(
                ContractIssue(
                    "unverified_bib_entry",
                    rel_path,
                    "BibTeX sources must not contain % UNVERIFIED or unverified-reference markers",
                )
            )
    return issues


def _validate_research_md_reference_depth(
    bib_sources: dict[str, str],
    tex_text: str,
    pdf_pages: list[str] | None,
) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    if bib_sources:
        bib_entry_count = sum(_count_bibtex_entries(text) for text in bib_sources.values())
        if bib_entry_count < MIN_FINAL_BIBLIOGRAPHY_ENTRIES:
            issues.append(
                ContractIssue(
                    "insufficient_verified_bibliography_entries",
                    ", ".join(sorted(bib_sources)),
                    (
                        f"final EMNLP readiness requires at least {MIN_FINAL_BIBLIOGRAPHY_ENTRIES} "
                        f"verified BibTeX entries; found {bib_entry_count}. research.md's citation "
                        "hygiene section gives a standard verified citation set as a starting point, "
                        "not as a complete bibliography."
                    ),
                )
            )

    citation_keys = _extract_latex_citation_keys(tex_text)
    if len(citation_keys) < MIN_FINAL_UNIQUE_CITATION_KEYS:
        issues.append(
            ContractIssue(
                "insufficient_unique_citations",
                str(PAPER_MAIN_TEX_PATH),
                (
                    f"final EMNLP readiness requires at least {MIN_FINAL_UNIQUE_CITATION_KEYS} "
                    f"unique cited bibliography keys in the paper body; found {len(citation_keys)}"
                ),
            )
        )

    if pdf_pages is not None:
        rendered_reference_pages = _rendered_reference_page_count(pdf_pages)
        if (
            rendered_reference_pages is not None
            and rendered_reference_pages < MIN_RENDERED_REFERENCE_PAGES
        ):
            issues.append(
                ContractIssue(
                    "insufficient_rendered_reference_pages",
                    str(PAPER_MAIN_PDF_PATH),
                    (
                        "final EMNLP readiness expects references to occupy at least "
                        f"{MIN_RENDERED_REFERENCE_PAGES} rendered pages; detected "
                        f"{rendered_reference_pages}"
                    ),
                )
            )
    return issues


def _count_bibtex_entries(text: str) -> int:
    return sum(
        1
        for match in re.finditer(r"@\s*([a-zA-Z]+)\s*\{", text)
        if match.group(1).lower() not in {"comment", "preamble", "string"}
    )


def _extract_latex_citation_keys(tex_text: str) -> set[str]:
    keys: set[str] = set()
    pattern = re.compile(
        r"\\(?:[Cc]ite(?:t|p|alp|alt|author|year|yearpar|poss)?|citeNP|newcite)"
        r"\*?(?:\s*\[[^\]]*\]){0,2}\s*\{([^{}]+)\}"
    )
    for match in pattern.finditer(_strip_latex_comments(tex_text)):
        for raw_key in match.group(1).split(","):
            key = raw_key.strip()
            if key and key != "*":
                keys.add(key)
    return keys


def _rendered_reference_page_count(pages: list[str]) -> int | None:
    reference_index: int | None = None
    for index, page_text in enumerate(pages):
        if re.search(r"(?m)^\s*(?:References|Bibliography)\s*$", page_text):
            reference_index = index
            break
    if reference_index is None:
        return None

    nonempty_page_count = len([page for page in pages if page.strip()])
    appendix_index = nonempty_page_count
    for index in range(reference_index + 1, nonempty_page_count):
        if re.search(
            r"(?m)^\s*(?:Appendix|[A-Z]\.?\s+Reproducibility|Supplementary Material)\b",
            pages[index],
        ):
            appendix_index = index
            break
    return max(0, appendix_index - reference_index)


def _validate_research_md_figure_contract(body_tex: str) -> list[ContractIssue]:
    figure_envs = _extract_latex_environments(body_tex, "figure")
    wide_figure_envs = _extract_latex_environments(body_tex, "figure*")
    issues: list[ContractIssue] = []
    body_figure_count = len(figure_envs) + len(wide_figure_envs)
    if body_figure_count > MAX_RESEARCH_MD_BODY_FIGURES:
        issues.append(
            ContractIssue(
                "too_many_body_figures",
                str(PAPER_MAIN_TEX_PATH),
                f"research.md allows at most {MAX_RESEARCH_MD_BODY_FIGURES} figures before the appendix",
            )
        )
    if len(wide_figure_envs) > MAX_RESEARCH_MD_WIDE_FIGURES:
        issues.append(
            ContractIssue(
                "too_many_wide_body_figures",
                str(PAPER_MAIN_TEX_PATH),
                f"research.md allows at most {MAX_RESEARCH_MD_WIDE_FIGURES} full-width figure before the appendix",
            )
        )

    ref_labels = _latex_reference_labels(body_tex)
    for index, environment in enumerate(figure_envs + wide_figure_envs):
        labels = _latex_label_arguments(environment)
        if not labels:
            issues.append(
                ContractIssue(
                    "body_figure_missing_label",
                    str(PAPER_MAIN_TEX_PATH),
                    f"body figure {index + 1} must have a \\label so readers can find it",
                )
            )
            continue
        for label in labels:
            if label not in ref_labels:
                issues.append(
                    ContractIssue(
                        "body_figure_not_referenced",
                        str(PAPER_MAIN_TEX_PATH),
                        f"body figure label {label!r} is not referenced with \\ref/\\autoref/\\cref in the main text",
                    )
                )
    return issues


def _validate_body_image2_conceptual_figure_usage(
    root: Path,
    *,
    body_tex: str | None = None,
    raw_figures: list[Any] | None = None,
) -> list[ContractIssue]:
    """Require the body conceptual figure to include the actual image-2 raster output."""

    figure_entries = raw_figures if raw_figures is not None else _read_image2_figure_entries(root)
    if not figure_entries:
        return []

    expanded_body = body_tex if body_tex is not None else _expanded_latex_body(root)
    if not expanded_body.strip():
        return []

    image2_outputs = _image2_conceptual_output_paths(root, figure_entries)
    if not image2_outputs:
        return _validate_body_conceptual_figures_without_image2(expanded_body)

    figure_envs = _extract_latex_figure_environments(expanded_body)
    included_paths: set[str] = set()
    per_figure_paths: list[tuple[int, str, list[str], set[str]]] = []
    for index, environment in enumerate(figure_envs):
        include_args = _extract_latex_command_arguments(environment, "includegraphics")
        normalized_paths: set[str] = set()
        for include_arg in include_args:
            normalized_paths.update(_latex_graphics_path_candidates(root, expanded_body, include_arg))
        included_paths.update(normalized_paths)
        per_figure_paths.append((index, environment, include_args, normalized_paths))

    issues: list[ContractIssue] = []
    if image2_outputs.isdisjoint(included_paths):
        outputs = ", ".join(sorted(image2_outputs))
        issues.append(
            ContractIssue(
                "image2_conceptual_figure_not_included_in_main_tex",
                str(PAPER_MAIN_TEX_PATH),
                "a valid image-2 conceptual figure exists in IMAGE2_FIGURES.json, "
                f"but none of its raster output_path files are included before the appendix: {outputs}",
            )
        )

    for index, environment, include_args, normalized_paths in per_figure_paths:
        if not _body_figure_looks_conceptual(index, environment, include_args):
            continue
        if not image2_outputs.isdisjoint(normalized_paths):
            continue
        includes = ", ".join(arg.strip() for arg in include_args if arg.strip()) or "no includegraphics"
        outputs = ", ".join(sorted(image2_outputs))
        issues.append(
            ContractIssue(
                "conceptual_body_figure_not_image2",
                str(PAPER_MAIN_TEX_PATH),
                f"body figure {index + 1} appears to be a method/framework/overview figure "
                f"but includes {includes}; include the image-2 raster output_path instead ({outputs}) "
                "rather than a matplotlib/TikZ/PDF/vector redraw",
            )
        )
    return issues


def _validate_body_conceptual_figures_without_image2(body_tex: str) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    for index, environment in enumerate(_extract_latex_figure_environments(body_tex)):
        include_args = _extract_latex_command_arguments(environment, "includegraphics")
        if not _body_figure_looks_conceptual(index, environment, include_args):
            continue
        includes = ", ".join(arg.strip() for arg in include_args if arg.strip()) or "no includegraphics"
        issues.append(
            ContractIssue(
                "conceptual_body_figure_not_image2",
                str(PAPER_MAIN_TEX_PATH),
                f"body figure {index + 1} appears to be a method/framework/overview figure "
                f"but includes {includes} and no valid image-2 conceptual raster output exists; "
                "regenerate the overview through image-2/codex-image2 instead of using a local redraw",
            )
        )
    return issues


def _read_image2_figure_entries(root: Path) -> list[Any] | None:
    payload = _try_read_json_object(root / IMAGE2_FIGURES_JSON_PATH)
    figures = payload.get("figures") if isinstance(payload, dict) else None
    return figures if isinstance(figures, list) else None


def _image2_conceptual_output_paths(root: Path, raw_figures: list[Any]) -> set[str]:
    paths: set[str] = set()
    for raw_entry in raw_figures:
        if not isinstance(raw_entry, dict) or not _entry_uses_image2(raw_entry):
            continue
        figure_type = _lower_text(raw_entry.get("figure_type", raw_entry.get("kind")))
        if figure_type not in CONCEPTUAL_IMAGE_FIGURE_TYPES:
            continue
        normalized = _normalize_manifest_path(raw_entry.get("output_path"))
        if normalized is None:
            continue
        resolved = _resolve_manifest_path(root, normalized)
        if resolved is None or resolved.suffix.lower() not in IMAGE2_RASTER_OUTPUT_SUFFIXES:
            continue
        paths.add(_project_relative_path(root, resolved))
    return paths


def _latex_graphics_path_candidates(root: Path, body_tex: str, include_arg: str) -> set[str]:
    raw_path = include_arg.strip()
    if not raw_path or "\\" in raw_path:
        return set()

    suffixes = ("",) if Path(raw_path).suffix else LATEX_GRAPHICS_SUFFIXES
    candidates: set[str] = set()
    base_roots = [root / "paper", root]
    base_roots.extend(root / "paper" / graphics_path for graphics_path in _latex_graphicspaths(body_tex))

    for base_root in base_roots:
        for suffix in suffixes:
            candidate = base_root / f"{raw_path}{suffix}"
            try:
                candidate.resolve(strict=False).relative_to(root.resolve())
            except ValueError:
                continue
            candidates.add(_project_relative_path(root, candidate))
    return candidates


def _latex_graphicspaths(tex_text: str) -> list[Path]:
    paths: list[Path] = []
    for argument in _extract_latex_command_arguments(tex_text, "graphicspath"):
        for raw_path in re.findall(r"\{([^{}]+)\}", argument):
            normalized = _normalize_manifest_path(raw_path.rstrip("/"))
            if normalized is not None:
                paths.append(Path(normalized))
    return paths


def _body_figure_looks_conceptual(index: int, environment: str, include_args: list[str]) -> bool:
    labels = _latex_label_arguments(environment)
    captions = _extract_latex_command_arguments(environment, "caption")
    joined_captions = " ".join(captions)
    joined_labels = " ".join(labels)
    joined_paths = " ".join(include_args).lower().replace("_", "-")
    figure_text = " ".join([joined_labels, joined_captions, joined_paths])

    has_plot_language = DATA_PLOT_FIGURE_RE.search(figure_text) is not None
    path_signal = _conceptual_path_signal(joined_paths, has_plot_language=has_plot_language)
    label_signal = CONCEPTUAL_FIGURE_LABEL_RE.search(joined_labels) is not None
    caption_signal = CONCEPTUAL_FIGURE_CAPTION_RE.search(joined_captions) is not None
    first_figure_method_signal = index == 0 and re.search(r"\bfig:method\b", joined_labels, re.I)

    if has_plot_language:
        return bool(path_signal or label_signal or first_figure_method_signal)
    return bool(path_signal or label_signal or caption_signal or first_figure_method_signal)


def _conceptual_path_signal(joined_paths: str, *, has_plot_language: bool) -> bool:
    if any(token.replace("_", "-") in joined_paths for token in CONCEPTUAL_FIGURE_PATH_TOKENS):
        return True
    if has_plot_language:
        return False
    return re.search(r"(?:^|[/_.-])overall(?:$|[/_.-])", joined_paths, re.I) is not None


def _extract_latex_figure_environments(text: str) -> list[str]:
    pattern = re.compile(
        r"\\begin\s*\{\s*(figure\*?)\s*\}(.*?)\\end\s*\{\s*\1\s*\}",
        re.S,
    )
    return [match.group(2) for match in pattern.finditer(text)]


def _latex_label_arguments(tex_text: str) -> list[str]:
    return [label.strip() for label in _extract_latex_command_arguments(tex_text, "label") if label.strip()]


def _latex_reference_labels(tex_text: str) -> set[str]:
    labels: set[str] = set()
    pattern = re.compile(r"\\(?:ref|autoref|Autoref|cref|Cref|figref|Figref|fref|Fref)\*?\s*\{([^{}]+)\}")
    for match in pattern.finditer(tex_text):
        for raw_label in match.group(1).split(","):
            label = raw_label.strip()
            if label:
                labels.add(label)
    return labels


def _validate_research_md_table_contract(
    body_tex: str,
    combined_tex: str,
    report: dict[str, Any],
) -> list[ContractIssue]:
    table_envs = _extract_latex_environments(body_tex, "table")
    table_envs.extend(_extract_latex_environments(body_tex, "table*"))
    issues: list[ContractIssue] = []
    for index, environment in enumerate(table_envs):
        captions = _extract_latex_command_arguments(environment, "caption")
        if not captions:
            issues.append(
                ContractIssue(
                    "table_missing_caption",
                    str(PAPER_MAIN_TEX_PATH),
                    f"body table {index + 1} must have a caption with a numerical headline",
                )
            )
        elif not any(re.search(r"\d", caption) for caption in captions):
            issues.append(
                ContractIssue(
                    "table_caption_missing_number",
                    str(PAPER_MAIN_TEX_PATH),
                    f"body table {index + 1} caption must include the key numerical result",
                )
            )

    if table_envs and not _has_research_md_table_style(combined_tex, table_envs):
        issues.append(
            ContractIssue(
                "missing_research_md_table_style",
                str(PAPER_MAIN_TEX_PATH),
                "tables must use research.md styling: footnotesize, tight tabcolsep, arraystretch, and row shading/macros",
            )
        )
    if table_envs and not _has_paired_significance_evidence(combined_tex, table_envs, report):
        issues.append(
            ContractIssue(
                "missing_paired_significance_table",
                str(PAPER_MAIN_TEX_PATH),
                "research.md requires a paired-significance table or explicit paired_significance_not_applicable=true",
            )
        )
    return issues


def _has_research_md_table_style(combined_tex: str, table_envs: list[str]) -> bool:
    table_text = "\n".join(table_envs)
    searchable = f"{combined_tex}\n{table_text}"
    has_size = bool(re.search(r"\\(?:footnotesize|small)\b", searchable))
    has_spacing = bool(re.search(r"\\setlength\s*\{\s*\\tabcolsep\s*\}\s*\{\s*[234]\s*pt\s*\}", searchable))
    has_arraystretch = bool(re.search(r"\\renewcommand\s*\{\s*\\arraystretch\s*\}\s*\{\s*1\.(?:1|15|2)", searchable))
    has_shading = bool(re.search(r"\\(?:rowcolor|cellcolor)\b|tabheader|oursrow|tabours", searchable, re.I))
    return has_size and has_spacing and has_arraystretch and has_shading


def _has_paired_significance_evidence(
    combined_tex: str,
    table_envs: list[str],
    report: dict[str, Any],
) -> bool:
    if report.get("paired_significance_not_applicable") is True:
        return True
    for key in ("paired_significance_table", "significance_table_label"):
        if _is_nonempty_string(report.get(key)):
            return True
    table_text = "\n".join(table_envs)
    searchable = f"{combined_tex}\n{table_text}"
    return bool(
        re.search(
            r"\b(?:paired|McNemar|bootstrap|Wilcoxon|permutation)\b|p\s*[<=>]\s*0?\.\d+",
            searchable,
            re.I,
        )
    )


def _extract_pdf_text_pages(pdf_path: Path) -> list[str] | None:
    if not pdf_path.is_file() or shutil.which("pdftotext") is None:
        return None
    try:
        completed = subprocess.run(
            ["pdftotext", "-layout", pdf_path.as_posix(), "-"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.split("\f")


def _validate_research_md_pdf_text(pages: list[str]) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    all_text = "\n".join(pages)
    if "[?]" in all_text:
        issues.append(
            ContractIssue(
                "pdf_unresolved_reference_marker",
                str(PAPER_MAIN_PDF_PATH),
                "rendered PDF still contains [?], so references/citations are unresolved",
            )
        )

    conclusion_page = _first_pdf_page_matching(pages, r"\bConclusion\b")
    if conclusion_page is not None and conclusion_page > MAX_MAIN_CONTENT_PAGES:
        issues.append(
            ContractIssue(
                "conclusion_after_page_8",
                str(PAPER_MAIN_PDF_PATH),
                "research.md requires the main Conclusion to land by page 8",
            )
        )
    if (
        conclusion_page is not None
        and conclusion_page < MIN_RENDERED_CONCLUSION_PAGE_FOR_FULL_BODY
    ):
        issues.append(
            ContractIssue(
                "rendered_main_body_underfilled",
                str(PAPER_MAIN_PDF_PATH),
                (
                    "final EMNLP readiness requires the body to be written out to "
                    f"7.5-8 main-content pages; rendered Conclusion appears on page "
                    f"{conclusion_page}, before page {MIN_RENDERED_CONCLUSION_PAGE_FOR_FULL_BODY}"
                ),
            )
        )

    references_page = _first_pdf_page_matching(pages, r"(?m)^\s*(?:References|Bibliography)\s*$")
    if references_page is not None and references_page < MIN_RENDERED_REFERENCES_PAGE_FOR_FULL_BODY:
        issues.append(
            ContractIssue(
                "references_before_full_body",
                str(PAPER_MAIN_PDF_PATH),
                (
                    "References begin before the main body is visibly full; final EMNLP drafts "
                    f"should keep references no earlier than page {MIN_RENDERED_REFERENCES_PAGE_FOR_FULL_BODY}"
                ),
            )
        )

    if len(pages) >= max(RESEARCH_MD_VISUAL_PAGES):
        visual_pages = {
            page_number
            for page_number, page_text in enumerate(pages, start=1)
            if re.search(r"\b(?:Figure|Table)\s+\d+", page_text)
        }
        missing_visual_pages = sorted(RESEARCH_MD_VISUAL_PAGES - visual_pages)
        if missing_visual_pages:
            issues.append(
                ContractIssue(
                    "missing_midpaper_visual_pages",
                    str(PAPER_MAIN_PDF_PATH),
                    f"research.md expects figures/tables on pages 4-7; missing pages {missing_visual_pages}",
                )
            )
    return issues


def _first_pdf_page_matching(pages: list[str], pattern: str) -> int | None:
    for index, page_text in enumerate(pages, start=1):
        if re.search(pattern, page_text):
            return index
    return None


def _paper_allowed_code_labels(report: dict[str, Any]) -> set[str]:
    raw = report.get("allowed_code_labels", report.get("humanized_label_overrides", []))
    if not isinstance(raw, list):
        return set()
    labels: set[str] = set()
    for item in raw:
        if isinstance(item, str) and item.strip():
            labels.add(_normalize_code_label(item))
        elif isinstance(item, dict):
            for key in ("label", "code_label", "identifier"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    labels.add(_normalize_code_label(value))
    return labels


def _strip_latex_comments(text: str) -> str:
    stripped_lines: list[str] = []
    for line in text.splitlines():
        comment_at: int | None = None
        for index, char in enumerate(line):
            if char != "%":
                continue
            backslashes = 0
            cursor = index - 1
            while cursor >= 0 and line[cursor] == "\\":
                backslashes += 1
                cursor -= 1
            if backslashes % 2 == 0:
                comment_at = index
                break
        stripped_lines.append(line[:comment_at] if comment_at is not None else line)
    return "\n".join(stripped_lines)


def _validate_reference_appendix_order(tex_text: str) -> list[ContractIssue]:
    appendix = re.search(r"\\appendix\b", tex_text)
    bibliography = re.search(
        r"\\(?:bibliography\s*\{|printbibliography\b|begin\s*\{\s*thebibliography\s*\})",
        tex_text,
    )
    issues: list[ContractIssue] = []
    if bibliography is None:
        issues.append(
            ContractIssue(
                "missing_bibliography_command",
                str(PAPER_MAIN_TEX_PATH),
                "main.tex must include a references section before any appendix",
            )
        )
    if appendix is not None and bibliography is not None and appendix.start() < bibliography.start():
        issues.append(
            ContractIssue(
                "appendix_before_references",
                str(PAPER_MAIN_TEX_PATH),
                "references must appear before appendix material in EMNLP submissions",
            )
        )
    return issues


def _validate_latex_log(log_path: Path) -> list[ContractIssue]:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    rel_path = str(PAPER_MAIN_LOG_PATH)
    issues: list[ContractIssue] = []
    overfulls = [
        float(match.group(1))
        for match in re.finditer(r"Overfull \\hbox \(([0-9.]+)pt too wide\)", text)
    ]
    severe = [amount for amount in overfulls if amount > SEVERE_OVERFULL_HBOX_PT]
    if severe:
        issues.append(
            ContractIssue(
                "severe_overfull_hbox",
                rel_path,
                (
                    f"LaTeX log contains overfull hboxes up to {max(severe):.1f}pt; "
                    f"research.md requires no Overfull \\hbox > {SEVERE_OVERFULL_HBOX_PT:g}pt"
                ),
            )
        )
    moderate_count = sum(1 for amount in overfulls if amount > OVERFULL_HBOX_COUNT_PT)
    if not severe and moderate_count > MAX_MODERATE_OVERFULL_HBOXES:
        issues.append(
            ContractIssue(
                "excessive_overfull_hboxes",
                rel_path,
                (
                    f"LaTeX log contains {moderate_count} overfull hboxes >= "
                    f"{OVERFULL_HBOX_COUNT_PT:g}pt; fix table widths and long labels"
                ),
            )
        )
    if re.search(
        r"(?i)(undefined references|undefined citations|there were undefined references|"
        r"there were undefined citations|reference [`'][^`']+['`] .*undefined|"
        r"citation [`'][^`']+['`] .*undefined)",
        text,
    ):
        issues.append(
            ContractIssue(
                "unresolved_latex_references",
                rel_path,
                "LaTeX log reports unresolved citations or references",
            )
        )
    return issues


def _validate_display_labels(tex_text: str, allowed_labels: set[str]) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    reported: set[str] = set()
    for context, content in _iter_display_contexts(tex_text):
        for label in _find_code_like_labels(content):
            normalized = _normalize_code_label(label)
            if normalized in allowed_labels or normalized in reported:
                continue
            reported.add(normalized)
            issues.append(
                ContractIssue(
                    "code_like_display_label",
                    str(PAPER_MAIN_TEX_PATH),
                    (
                        f"display context {context} contains code-like label {label!r}; "
                        "use human-readable paper labels in abstracts, headings, captions, and tables"
                    ),
                )
            )
    return issues


def _iter_display_contexts(tex_text: str) -> list[tuple[str, str]]:
    main_text = tex_text.split(r"\appendix", 1)[0]
    contexts: list[tuple[str, str]] = []
    abstract = re.search(r"\\begin\s*\{\s*abstract\s*\}(.*?)\\end\s*\{\s*abstract\s*\}", main_text, re.S)
    if abstract is not None:
        contexts.append(("abstract", abstract.group(1)))
    for command in ("title", "section", "subsection", "subsubsection", "paragraph", "caption"):
        for index, argument in enumerate(_extract_latex_command_arguments(main_text, command)):
            contexts.append((f"{command}[{index}]", argument))
    for environment_name in ("tabular", "tabularx", "longtable"):
        for index, environment in enumerate(_extract_latex_environments(main_text, environment_name)):
            contexts.append((f"{environment_name}[{index}]", environment))
    return contexts


def _extract_latex_command_arguments(text: str, command: str) -> list[str]:
    arguments: list[str] = []
    pattern = re.compile(rf"\\{re.escape(command)}\*?(?:\[[^\]]*\])?\s*\{{")
    for match in pattern.finditer(text):
        start = match.end() - 1
        argument = _balanced_brace_content(text, start)
        if argument is not None:
            arguments.append(argument)
    return arguments


def _extract_latex_environments(text: str, environment: str) -> list[str]:
    pattern = re.compile(
        rf"\\begin\s*\{{\s*{re.escape(environment)}\s*\}}(.*?)\\end\s*\{{\s*{re.escape(environment)}\s*\}}",
        re.S,
    )
    return [match.group(1) for match in pattern.finditer(text)]


def _balanced_brace_content(text: str, opening_brace: int) -> str | None:
    if opening_brace >= len(text) or text[opening_brace] != "{":
        return None
    depth = 0
    chunks: list[str] = []
    index = opening_brace
    while index < len(text):
        char = text[index]
        if char == "\\":
            if depth > 0 and index + 1 < len(text):
                chunks.append(text[index : index + 2])
                index += 2
                continue
        if char == "{":
            depth += 1
            if depth > 1:
                chunks.append(char)
        elif char == "}":
            depth -= 1
            if depth == 0:
                return "".join(chunks)
            chunks.append(char)
        elif depth > 0:
            chunks.append(char)
        index += 1
    return None


def _find_code_like_labels(text: str) -> set[str]:
    labels: set[str] = set()
    for match in re.finditer(r"\\texttt\s*\{([^{}]*(?:_|\\_)[^{}]*)\}", text):
        labels.add(match.group(1))
    for match in re.finditer(r"\b[A-Za-z][A-Za-z0-9]*(?:\\_)[A-Za-z0-9][A-Za-z0-9\\_]*\b", text):
        labels.add(match.group(0))
    for match in re.finditer(r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9][A-Za-z0-9_]*\b", text):
        labels.add(match.group(0))
    return labels


def _normalize_code_label(value: str) -> str:
    return value.strip().replace(r"\_", "_").lower()


def validate_layout_review(project_root: Path) -> list[ContractIssue]:
    """Validate final PDF layout/aesthetic review evidence."""

    root = Path(project_root)
    path = root / LAYOUT_REVIEW_JSON_PATH
    if not path.exists():
        return [
            ContractIssue(
                "missing_layout_review",
                str(LAYOUT_REVIEW_JSON_PATH),
                "final EMNLP readiness requires paper/LAYOUT_REVIEW.json from the layout review tool",
            )
        ]

    try:
        payload = _read_json_object(path)
    except ValueError as exc:
        return [
            ContractIssue(
                "invalid_layout_review_json",
                str(LAYOUT_REVIEW_JSON_PATH),
                str(exc),
            )
        ]

    issues: list[ContractIssue] = []
    if payload.get("schema_version") != 1:
        issues.append(
            ContractIssue(
                "unknown_layout_review_schema",
                str(LAYOUT_REVIEW_JSON_PATH),
                "layout review schema_version must be 1",
            )
        )

    review_method = payload.get("review_method")
    if not isinstance(review_method, str) or not review_method.strip():
        issues.append(
            ContractIssue(
                "missing_layout_review_method",
                str(LAYOUT_REVIEW_JSON_PATH),
                "layout review must record review_method",
            )
        )
    elif review_method not in LAYOUT_REVIEW_VISION_METHODS:
        issues.append(
            ContractIssue(
                "layout_review_not_visual",
                str(LAYOUT_REVIEW_JSON_PATH),
                "final layout review must use rendered PDF page images and a vision-capable reviewer",
            )
        )

    verdict = payload.get("verdict")
    if verdict != "PASS":
        issues.append(
            ContractIssue(
                "layout_review_not_pass",
                str(LAYOUT_REVIEW_JSON_PATH),
                f"layout review verdict must be PASS, got {verdict!r}",
            )
        )

    score = _float_or_none(payload.get("score_1_to_5"))
    threshold = _float_or_none(payload.get("threshold"))
    if score is None:
        issues.append(
            ContractIssue(
                "missing_layout_review_score",
                str(LAYOUT_REVIEW_JSON_PATH),
                "layout review must include numeric score_1_to_5",
            )
        )
    if threshold is None:
        issues.append(
            ContractIssue(
                "missing_layout_review_threshold",
                str(LAYOUT_REVIEW_JSON_PATH),
                "layout review must include numeric threshold",
            )
        )
    elif threshold < MIN_LAYOUT_REVIEW_SCORE:
        issues.append(
            ContractIssue(
                "layout_review_threshold_too_low",
                str(LAYOUT_REVIEW_JSON_PATH),
                f"layout review threshold must be at least {MIN_LAYOUT_REVIEW_SCORE:g}",
            )
        )
    if score is not None and threshold is not None and score < max(threshold, MIN_LAYOUT_REVIEW_SCORE):
        issues.append(
            ContractIssue(
                "low_layout_review_score",
                str(LAYOUT_REVIEW_JSON_PATH),
                f"layout review score {score:g} is below required threshold {max(threshold, MIN_LAYOUT_REVIEW_SCORE):g}",
            )
        )

    if payload.get("needs_revision") is not False:
        issues.append(
            ContractIssue(
                "layout_review_needs_revision",
                str(LAYOUT_REVIEW_JSON_PATH),
                "layout review must set needs_revision=false before submission readiness",
            )
        )

    blocking_issues = payload.get("blocking_issues")
    if not isinstance(blocking_issues, list):
        issues.append(
            ContractIssue(
                "invalid_layout_review_blockers",
                str(LAYOUT_REVIEW_JSON_PATH),
                "layout review blocking_issues must be a list",
            )
        )
    elif blocking_issues:
        issues.append(
            ContractIssue(
                "layout_review_has_blockers",
                str(LAYOUT_REVIEW_JSON_PATH),
                "layout review still reports blocking issues",
            )
        )

    pdf_path = _normalize_manifest_path(payload.get("pdf_path"))
    if pdf_path is None:
        issues.append(
            ContractIssue(
                "invalid_layout_review_pdf_path",
                str(LAYOUT_REVIEW_JSON_PATH),
                "layout review pdf_path must be a relative project path",
            )
        )
    else:
        resolved_pdf = _resolve_manifest_path(root, pdf_path)
        if resolved_pdf is None or not resolved_pdf.is_file():
            issues.append(
                ContractIssue(
                    "missing_layout_review_pdf",
                    pdf_path,
                    "layout review pdf_path does not exist",
                )
            )
        else:
            issues.extend(_validate_layout_review_hash(payload, "pdf_sha256", resolved_pdf, pdf_path))

    issues.extend(_validate_layout_review_snapshots(root, payload.get("page_snapshots")))
    issues.extend(_validate_layout_review_directives(payload))
    return _dedupe_contract_issues(issues)


def _validate_layout_review_hash(
    payload: dict[str, Any],
    field: str,
    resolved_path: Path,
    rel_path: str,
) -> list[ContractIssue]:
    expected_hash = payload.get(field)
    if not isinstance(expected_hash, str) or not _is_sha256_hex(expected_hash):
        return [
            ContractIssue(
                f"missing_layout_review_{field}",
                str(LAYOUT_REVIEW_JSON_PATH),
                f"layout review must record a valid {field} for {rel_path}",
            )
        ]
    actual_hash = _sha256_file(resolved_path)
    if expected_hash != actual_hash:
        return [
            ContractIssue(
                "stale_layout_review_artifact",
                rel_path,
                f"layout review hash for {rel_path} does not match the current file",
            )
        ]
    return []


def _validate_layout_review_snapshots(root: Path, raw_snapshots: object) -> list[ContractIssue]:
    if not isinstance(raw_snapshots, list) or not raw_snapshots:
        return [
            ContractIssue(
                "missing_layout_review_snapshots",
                str(LAYOUT_REVIEW_JSON_PATH),
                "layout review must include rendered page_snapshots with hashes",
            )
        ]
    issues: list[ContractIssue] = []
    for index, raw_snapshot in enumerate(raw_snapshots):
        entry_path = f"{LAYOUT_REVIEW_JSON_PATH}:page_snapshots[{index}]"
        if not isinstance(raw_snapshot, dict):
            issues.append(
                ContractIssue(
                    "invalid_layout_review_snapshot",
                    entry_path,
                    "page snapshot entry must be an object",
                )
            )
            continue
        rel_path = _normalize_manifest_path(raw_snapshot.get("path"))
        if rel_path is None:
            issues.append(
                ContractIssue(
                    "invalid_layout_review_snapshot_path",
                    entry_path,
                    "page snapshot path must be relative to the project",
                )
            )
            continue
        resolved = _resolve_manifest_path(root, rel_path)
        if resolved is None or not resolved.is_file():
            issues.append(
                ContractIssue(
                    "missing_layout_review_snapshot_file",
                    rel_path,
                    "rendered page snapshot is missing",
                )
            )
            continue
        expected_hash = raw_snapshot.get("sha256")
        if not isinstance(expected_hash, str) or not _is_sha256_hex(expected_hash):
            issues.append(
                ContractIssue(
                    "missing_layout_review_snapshot_hash",
                    entry_path,
                    "page snapshot must include a valid sha256 hash",
                )
            )
            continue
        actual_hash = _sha256_file(resolved)
        if actual_hash != expected_hash:
            issues.append(
                ContractIssue(
                    "stale_layout_review_snapshot",
                    rel_path,
                    "page snapshot hash does not match the current file",
                )
            )
    return issues


def _validate_layout_review_directives(payload: dict[str, Any]) -> list[ContractIssue]:
    directives = payload.get("revision_directives")
    if not isinstance(directives, list):
        return [
            ContractIssue(
                "invalid_layout_review_directives",
                str(LAYOUT_REVIEW_JSON_PATH),
                "layout review revision_directives must be a list",
            )
        ]
    issues: list[ContractIssue] = []
    if payload.get("verdict") == "PASS" and directives:
        issues.append(
            ContractIssue(
                "pass_layout_review_with_directives",
                str(LAYOUT_REVIEW_JSON_PATH),
                "PASS layout review cannot leave active revision directives",
            )
        )
    for index, raw_directive in enumerate(directives):
        entry_path = f"{LAYOUT_REVIEW_JSON_PATH}:revision_directives[{index}]"
        if not isinstance(raw_directive, dict):
            issues.append(
                ContractIssue(
                    "invalid_layout_review_directive",
                    entry_path,
                    "revision directive must be an object",
                )
            )
            continue
        action = raw_directive.get("action")
        if action not in ALLOWED_LAYOUT_REVIEW_ACTIONS:
            issues.append(
                ContractIssue(
                    "invalid_layout_review_directive_action",
                    entry_path,
                    "revision directive action must be from the approved layout action vocabulary",
                )
            )
    return issues


def validate_academic_language_review(project_root: Path) -> list[ContractIssue]:
    """Validate final academic-language review evidence."""

    root = Path(project_root)
    path = root / ACADEMIC_LANGUAGE_REVIEW_JSON_PATH
    if not path.exists():
        return [
            ContractIssue(
                "missing_academic_language_review",
                str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
                (
                    "final EMNLP readiness requires paper/ACADEMIC_LANGUAGE_REVIEW.json "
                    "from the academic-language review tool"
                ),
            )
        ]

    try:
        payload = _read_json_object(path)
    except ValueError as exc:
        return [
            ContractIssue(
                "invalid_academic_language_review_json",
                str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
                str(exc),
            )
        ]

    issues: list[ContractIssue] = []
    if payload.get("schema_version") != 1:
        issues.append(
            ContractIssue(
                "unknown_academic_language_review_schema",
                str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
                "academic-language review schema_version must be 1",
            )
        )

    review_method = payload.get("review_method")
    if not isinstance(review_method, str) or not review_method.strip():
        issues.append(
            ContractIssue(
                "missing_academic_language_review_method",
                str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
                "academic-language review must record review_method",
            )
        )
    elif review_method not in ACADEMIC_LANGUAGE_MODEL_METHODS:
        issues.append(
            ContractIssue(
                "academic_language_review_not_model_backed",
                str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
                "final academic-language review must use a model-backed text reviewer",
            )
        )

    verdict = payload.get("verdict")
    if verdict != "PASS":
        issues.append(
            ContractIssue(
                "academic_language_review_not_pass",
                str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
                f"academic-language review verdict must be PASS, got {verdict!r}",
            )
        )

    score = _float_or_none(payload.get("score_1_to_5"))
    threshold = _float_or_none(payload.get("threshold"))
    if score is None:
        issues.append(
            ContractIssue(
                "missing_academic_language_review_score",
                str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
                "academic-language review must include numeric score_1_to_5",
            )
        )
    if threshold is None:
        issues.append(
            ContractIssue(
                "missing_academic_language_review_threshold",
                str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
                "academic-language review must include numeric threshold",
            )
        )
    elif threshold < MIN_ACADEMIC_LANGUAGE_REVIEW_SCORE:
        issues.append(
            ContractIssue(
                "academic_language_review_threshold_too_low",
                str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
                (
                    "academic-language review threshold must be at least "
                    f"{MIN_ACADEMIC_LANGUAGE_REVIEW_SCORE:g}"
                ),
            )
        )
    if (
        score is not None
        and threshold is not None
        and score < max(threshold, MIN_ACADEMIC_LANGUAGE_REVIEW_SCORE)
    ):
        issues.append(
            ContractIssue(
                "low_academic_language_review_score",
                str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
                (
                    f"academic-language review score {score:g} is below required "
                    f"threshold {max(threshold, MIN_ACADEMIC_LANGUAGE_REVIEW_SCORE):g}"
                ),
            )
        )

    if payload.get("needs_revision") is not False:
        issues.append(
            ContractIssue(
                "academic_language_review_needs_revision",
                str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
                "academic-language review must set needs_revision=false before submission readiness",
            )
        )

    blocking_issues = payload.get("blocking_issues")
    if not isinstance(blocking_issues, list):
        issues.append(
            ContractIssue(
                "invalid_academic_language_review_blockers",
                str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
                "academic-language review blocking_issues must be a list",
            )
        )
    elif blocking_issues:
        issues.append(
            ContractIssue(
                "academic_language_review_has_blockers",
                str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
                "academic-language review still reports blocking issues",
            )
        )

    snapshot_paths, snapshot_issues = _validate_academic_source_snapshots(
        root,
        payload.get("source_snapshots"),
    )
    issues.extend(snapshot_issues)
    issues.extend(_validate_academic_review_source_set(root, snapshot_paths))
    issues.extend(_validate_academic_section_scores(payload))
    issues.extend(_validate_academic_required_checks(payload))
    issues.extend(_validate_academic_evidence_spans(root, payload, snapshot_paths))
    issues.extend(_validate_academic_language_directives(payload))
    issues.extend(_academic_static_source_issues(root))
    return _dedupe_contract_issues(issues)


def _validate_academic_source_snapshots(
    root: Path,
    raw_snapshots: object,
) -> tuple[set[str], list[ContractIssue]]:
    if not isinstance(raw_snapshots, list) or not raw_snapshots:
        return set(), [
            ContractIssue(
                "missing_academic_language_source_snapshots",
                str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
                "academic-language review must include source_snapshots with hashes",
            )
        ]
    issues: list[ContractIssue] = []
    paths: set[str] = set()
    for index, raw_snapshot in enumerate(raw_snapshots):
        entry_path = f"{ACADEMIC_LANGUAGE_REVIEW_JSON_PATH}:source_snapshots[{index}]"
        if not isinstance(raw_snapshot, dict):
            issues.append(
                ContractIssue(
                    "invalid_academic_language_source_snapshot",
                    entry_path,
                    "source snapshot entry must be an object",
                )
            )
            continue
        rel_path = _normalize_manifest_path(raw_snapshot.get("path"))
        if rel_path is None:
            issues.append(
                ContractIssue(
                    "invalid_academic_language_source_path",
                    entry_path,
                    "source snapshot path must be relative to the project",
                )
            )
            continue
        paths.add(rel_path)
        resolved = _resolve_manifest_path(root, rel_path)
        if resolved is None or not resolved.is_file():
            issues.append(
                ContractIssue(
                    "missing_academic_language_source_file",
                    rel_path,
                    "reviewed LaTeX source snapshot is missing",
                )
            )
            continue
        expected_hash = raw_snapshot.get("sha256")
        if not isinstance(expected_hash, str) or not _is_sha256_hex(expected_hash):
            issues.append(
                ContractIssue(
                    "missing_academic_language_source_hash",
                    entry_path,
                    "source snapshot must include a valid sha256 hash",
                )
            )
            continue
        actual_hash = _sha256_file(resolved)
        if actual_hash != expected_hash:
            issues.append(
                ContractIssue(
                    "stale_academic_language_review_source",
                    rel_path,
                    "academic-language review hash does not match the current source file",
                )
            )
    if PAPER_MAIN_TEX_PATH.as_posix() not in paths:
        issues.append(
            ContractIssue(
                "missing_main_tex_academic_language_snapshot",
                str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
                "academic-language review must include paper/main.tex in source_snapshots",
            )
        )
    return paths, issues


def _validate_academic_review_source_set(root: Path, snapshot_paths: set[str]) -> list[ContractIssue]:
    current_paths, missing_paths = collect_latex_source_paths(root)
    issues: list[ContractIssue] = []
    for rel_path in missing_paths:
        issues.append(
            ContractIssue(
                "missing_academic_language_latex_source",
                rel_path,
                "paper/main.tex references a LaTeX source that cannot be reviewed",
            )
        )
    current_set = set(current_paths)
    for rel_path in sorted(current_set - snapshot_paths):
        issues.append(
            ContractIssue(
                "unreviewed_academic_language_source",
                rel_path,
                "current LaTeX source is not covered by academic-language review",
            )
        )
    for rel_path in sorted(snapshot_paths - current_set):
        issues.append(
            ContractIssue(
                "stale_academic_language_source_set",
                rel_path,
                "academic-language review includes a source no longer referenced by paper/main.tex",
            )
        )
    return issues


def _validate_academic_section_scores(payload: dict[str, Any]) -> list[ContractIssue]:
    raw_scores = payload.get("section_scores")
    if not isinstance(raw_scores, dict):
        return [
            ContractIssue(
                "missing_academic_language_section_scores",
                str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
                "academic-language review must include section_scores",
            )
        ]
    issues: list[ContractIssue] = []
    threshold = _float_or_none(payload.get("threshold")) or MIN_ACADEMIC_LANGUAGE_REVIEW_SCORE
    required = max(threshold, MIN_ACADEMIC_LANGUAGE_REVIEW_SCORE)
    for key in REQUIRED_ACADEMIC_SECTION_SCORES:
        score = _float_or_none(raw_scores.get(key))
        if score is None:
            issues.append(
                ContractIssue(
                    "missing_academic_language_section_score",
                    str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
                    f"section_scores.{key} must be numeric",
                )
            )
        elif score < required:
            issues.append(
                ContractIssue(
                    "low_academic_language_section_score",
                    str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
                    f"section_scores.{key}={score:g} is below {required:g}",
                )
            )
    return issues


def _validate_academic_required_checks(payload: dict[str, Any]) -> list[ContractIssue]:
    raw_checks = payload.get("required_checks")
    if not isinstance(raw_checks, dict):
        return [
            ContractIssue(
                "missing_academic_language_required_checks",
                str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
                "academic-language review must include required_checks",
            )
        ]
    issues: list[ContractIssue] = []
    for key in REQUIRED_ACADEMIC_LANGUAGE_CHECKS:
        if raw_checks.get(key) is not True:
            issues.append(
                ContractIssue(
                    "failed_academic_language_required_check",
                    str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
                    f"required_checks.{key} must be true before submission readiness",
                )
            )
    return issues


def _validate_academic_evidence_spans(
    root: Path,
    payload: dict[str, Any],
    snapshot_paths: set[str],
) -> list[ContractIssue]:
    raw_spans = payload.get("evidence_spans")
    if not isinstance(raw_spans, list) or not raw_spans:
        return [
            ContractIssue(
                "missing_academic_language_evidence_spans",
                str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
                "model-backed review must include quote evidence spans for every rubric section",
            )
        ]
    issues: list[ContractIssue] = []
    covered_sections: set[str] = set()
    source_cache: dict[str, str] = {}
    for index, raw_span in enumerate(raw_spans):
        entry_path = f"{ACADEMIC_LANGUAGE_REVIEW_JSON_PATH}:evidence_spans[{index}]"
        if not isinstance(raw_span, dict):
            issues.append(
                ContractIssue(
                    "invalid_academic_language_evidence_span",
                    entry_path,
                    "evidence span must be an object",
                )
            )
            continue
        section = raw_span.get("section")
        if section not in REQUIRED_ACADEMIC_SECTION_SCORES:
            issues.append(
                ContractIssue(
                    "invalid_academic_language_evidence_section",
                    entry_path,
                    "evidence span section must match an academic-language section score key",
                )
            )
        else:
            covered_sections.add(str(section))
        source_path = _normalize_manifest_path(raw_span.get("source_path"))
        if source_path is None or source_path not in snapshot_paths:
            issues.append(
                ContractIssue(
                    "invalid_academic_language_evidence_source",
                    entry_path,
                    "evidence span source_path must reference a reviewed source snapshot",
                )
            )
            continue
        if not source_path.endswith(".tex"):
            issues.append(
                ContractIssue(
                    "invalid_academic_language_evidence_source",
                    entry_path,
                    "evidence span source_path must be a reviewed .tex file",
                )
            )
            continue
        quote = raw_span.get("quote")
        why = raw_span.get("why")
        if not _is_nonempty_string(quote) or not _is_nonempty_string(why):
            issues.append(
                ContractIssue(
                    "invalid_academic_language_evidence_span",
                    entry_path,
                    "evidence span must include non-empty quote and why fields",
                )
            )
            continue
        if source_path not in source_cache:
            source_cache[source_path] = (root / source_path).read_text(
                encoding="utf-8",
                errors="replace",
            )
        if not _quote_in_source(str(quote), source_cache[source_path]):
            issues.append(
                ContractIssue(
                    "academic_language_evidence_quote_not_found",
                    entry_path,
                    "evidence span quote is not present in the current reviewed source",
                )
            )
    for section in REQUIRED_ACADEMIC_SECTION_SCORES:
        if section not in covered_sections:
            issues.append(
                ContractIssue(
                    "missing_academic_language_evidence_section",
                    str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
                    f"evidence_spans must cover section {section!r}",
                )
            )
    return issues


def _validate_academic_language_directives(payload: dict[str, Any]) -> list[ContractIssue]:
    directives = payload.get("revision_directives")
    if not isinstance(directives, list):
        return [
            ContractIssue(
                "invalid_academic_language_review_directives",
                str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
                "academic-language review revision_directives must be a list",
            )
        ]
    issues: list[ContractIssue] = []
    if payload.get("verdict") == "PASS" and directives:
        issues.append(
            ContractIssue(
                "pass_academic_language_review_with_directives",
                str(ACADEMIC_LANGUAGE_REVIEW_JSON_PATH),
                "PASS academic-language review cannot leave active revision directives",
            )
        )
    for index, raw_directive in enumerate(directives):
        entry_path = f"{ACADEMIC_LANGUAGE_REVIEW_JSON_PATH}:revision_directives[{index}]"
        if not isinstance(raw_directive, dict):
            issues.append(
                ContractIssue(
                    "invalid_academic_language_review_directive",
                    entry_path,
                    "revision directive must be an object",
                )
            )
            continue
        action = raw_directive.get("action")
        if action not in ALLOWED_ACADEMIC_LANGUAGE_ACTIONS:
            issues.append(
                ContractIssue(
                    "invalid_academic_language_directive_action",
                    entry_path,
                    (
                        "revision directive action must be from the approved "
                        "academic-language action vocabulary"
                    ),
                )
            )
    return issues


def _academic_static_source_issues(root: Path) -> list[ContractIssue]:
    main_path = root / PAPER_MAIN_TEX_PATH
    if not main_path.is_file():
        return []
    raw_text = main_path.read_text(encoding="utf-8", errors="replace")
    text = _strip_latex_comments_for_contract(raw_text)
    opening = _latex_to_plain_for_contract(text)[:1800]
    issues: list[ContractIssue] = []
    for code, pattern in GENERIC_OPENING_PATTERNS:
        if re.search(pattern, opening, re.I):
            issues.append(
                ContractIssue(
                    f"academic_language_{code}",
                    str(PAPER_MAIN_TEX_PATH),
                    (
                        "paper opening still contains generic template prose; "
                        "rerun academic-language revision"
                    ),
                )
            )
    for code, message in find_reader_hostile_abstract_issues(raw_text):
        issues.append(
            ContractIssue(
                f"academic_language_{code}",
                str(PAPER_MAIN_TEX_PATH),
                f"{message}; rerun academic-language revision",
            )
        )
    return issues


def _strip_latex_comments_for_contract(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        escaped = False
        out: list[str] = []
        for char in line:
            if char == "%" and not escaped:
                break
            out.append(char)
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
        lines.append("".join(out))
    return "\n".join(lines)


def _latex_to_plain_for_contract(text: str) -> str:
    text = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", " ", text)
    text = text.replace("{", " ").replace("}", " ")
    return re.sub(r"\s+", " ", text).strip()


def _quote_in_source(quote: str, source_text: str) -> bool:
    normalized_quote = re.sub(r"\s+", " ", quote).strip()
    normalized_source = re.sub(r"\s+", " ", source_text).strip()
    return bool(normalized_quote) and normalized_quote in normalized_source


def validate_submission_assurance(project_root: Path) -> list[ContractIssue]:
    """Validate ``paper/SUBMISSION_ASSURANCE.json`` verdict and layer results."""

    root = Path(project_root)
    assurance_path = root / SUBMISSION_ASSURANCE_JSON_PATH
    if not assurance_path.exists():
        return [
            ContractIssue(
                "missing_submission_assurance",
                str(SUBMISSION_ASSURANCE_JSON_PATH),
                "submission assurance JSON is missing",
            )
        ]

    try:
        assurance = _read_json_object(assurance_path)
    except ValueError as exc:
        return [
            ContractIssue(
                "invalid_submission_assurance_json",
                str(SUBMISSION_ASSURANCE_JSON_PATH),
                str(exc),
            )
        ]

    issues: list[ContractIssue] = []
    verdict = assurance.get("verdict")
    if not isinstance(verdict, str) or verdict not in ASSURANCE_VERDICTS:
        issues.append(
            ContractIssue(
                "invalid_assurance_verdict",
                str(SUBMISSION_ASSURANCE_JSON_PATH),
                f"verdict must be one of {', '.join(ASSURANCE_VERDICTS)}",
            )
        )

    blocking_issues = assurance.get("blocking_issues")
    if blocking_issues is not None and not isinstance(blocking_issues, list):
        issues.append(
            ContractIssue(
                "invalid_blocking_issues",
                str(SUBMISSION_ASSURANCE_JSON_PATH),
                "blocking_issues must be a list when present",
            )
        )
    if verdict == "PASS" and isinstance(blocking_issues, list) and blocking_issues:
        issues.append(
            ContractIssue(
                "pass_with_blocking_issues",
                str(SUBMISSION_ASSURANCE_JSON_PATH),
                "PASS verdict cannot include blocking issues",
            )
        )

    layers = assurance.get("layers")
    if not isinstance(layers, dict):
        issues.append(
            ContractIssue(
                "missing_assurance_layers",
                str(SUBMISSION_ASSURANCE_JSON_PATH),
                "layers must be an object keyed by assurance layer",
            )
        )
    else:
        for layer in ASSURANCE_LAYERS:
            entry = layers.get(layer)
            if not isinstance(entry, dict):
                issues.append(
                    ContractIssue(
                        "missing_assurance_layer",
                        str(SUBMISSION_ASSURANCE_JSON_PATH),
                        f"layer {layer!r} is missing or not an object",
                    )
                )
                continue
            layer_verdict = entry.get("verdict")
            if not isinstance(layer_verdict, str) or layer_verdict not in ASSURANCE_VERDICTS:
                issues.append(
                    ContractIssue(
                        "invalid_assurance_layer_verdict",
                        str(SUBMISSION_ASSURANCE_JSON_PATH),
                        f"layer {layer!r} has invalid verdict {layer_verdict!r}",
                    )
                )
                continue
            if verdict == "PASS" and layer_verdict in BLOCKING_ASSURANCE_VERDICTS:
                issues.append(
                    ContractIssue(
                        "pass_with_blocking_layer",
                        str(SUBMISSION_ASSURANCE_JSON_PATH),
                        f"PASS verdict cannot include {layer_verdict} layer {layer!r}",
                    )
                )

    issues.extend(_contract_issues(validate_quality_calibration_file(root)))
    if verdict in {"PASS", "WARN"}:
        issues.extend(validate_literature_grounding(root))
        issues.extend(validate_idea_provenance(root))
        issues.extend(validate_code_reuse_plan(root))
        issues.extend(validate_style_exemplar(root))
        issues.extend(validate_image2_figures(root))
        issues.extend(validate_emnlp_paper_contract(root))
        if not (root / FORMAT_PREFLIGHT_REPORT_PATH).is_file():
            issues.append(
                ContractIssue(
                    "missing_format_preflight_report",
                    str(FORMAT_PREFLIGHT_REPORT_PATH),
                    "PASS/WARN submission assurance requires paper/FORMAT_PREFLIGHT.md from the EMNLP Format Preflight skill",
                )
            )
        issues.extend(validate_layout_review(root))
        issues.extend(validate_academic_language_review(root))
        issues.extend(_contract_issues(detect_quality_blockers(root)))
        issues.extend(validate_artifact_manifest(root))

    return issues


def validate_submission_readiness(project_root: Path) -> list[ContractIssue]:
    """Validate that the project can be marked as submission-stage ready/done."""

    root = Path(project_root)
    issues = validate_submission_assurance(root)
    assurance_path = root / SUBMISSION_ASSURANCE_JSON_PATH
    if not assurance_path.exists():
        return issues

    try:
        assurance = _read_json_object(assurance_path)
    except ValueError:
        return issues

    verdict = assurance.get("verdict")
    if verdict not in {"PASS", "WARN"}:
        issues.append(
            ContractIssue(
                "submission_not_ready_verdict",
                str(SUBMISSION_ASSURANCE_JSON_PATH),
                f"submission stage cannot be ready/done with assurance verdict {verdict!r}",
            )
        )
    return issues


def validate_full_emnlp_readiness(project_root: Path) -> list[ContractIssue]:
    """Validate final EMNLP readiness without trusting stage self-reporting."""

    root = Path(project_root)
    issues = validate_pipeline_state(root)
    for stage in FULL_EMNLP_REQUIRED_STAGES:
        issues.extend(_missing_artifact_issues(root, stage))

    issues.extend(validate_literature_grounding(root))
    issues.extend(validate_idea_provenance(root))
    issues.extend(validate_code_reuse_plan(root))
    issues.extend(validate_style_exemplar(root))
    issues.extend(validate_image2_figures(root))
    issues.extend(validate_emnlp_paper_contract(root))
    issues.extend(validate_layout_review(root))
    issues.extend(validate_academic_language_review(root))
    issues.extend(_contract_issues(detect_quality_blockers(root)))
    issues.extend(validate_submission_readiness(root))
    issues.extend(validate_artifact_manifest(root))

    state = _try_read_json_object(root / PIPELINE_STATE_PATH)
    stages = state.get("stages") if isinstance(state, dict) else None
    submission = stages.get("submission") if isinstance(stages, dict) else None
    submission_status = submission.get("status") if isinstance(submission, dict) else None
    if submission_status not in SUCCESS_STATUSES:
        issues.append(
            ContractIssue(
                "submission_stage_not_successful",
                str(PIPELINE_STATE_PATH),
                "full EMNLP readiness requires the submission stage to be ready or done",
            )
        )

    return _dedupe_contract_issues(issues)


def _contract_issues(issues: list[Any]) -> list[ContractIssue]:
    return [
        ContractIssue(
            code=str(issue.code),
            path=str(issue.path),
            message=str(issue.message),
        )
        for issue in issues
    ]


def _validate_reference_entries(
    payload: dict[str, Any],
    list_key: str,
    *,
    min_count: int,
    min_year: int | None,
) -> list[ContractIssue]:
    raw_entries = payload.get(list_key)
    if not isinstance(raw_entries, list):
        return [
            ContractIssue(
                f"missing_{list_key}",
                str(LITERATURE_GROUNDING_JSON_PATH),
                f"{list_key} must be a list",
            )
        ]

    issues: list[ContractIssue] = []
    if len(raw_entries) < min_count:
        issues.append(
            ContractIssue(
                f"insufficient_{list_key}",
                str(LITERATURE_GROUNDING_JSON_PATH),
                f"{list_key} must contain at least {min_count} entries",
            )
        )

    for index, raw_entry in enumerate(raw_entries):
        entry_path = f"{LITERATURE_GROUNDING_JSON_PATH}:{list_key}[{index}]"
        if not isinstance(raw_entry, dict):
            issues.append(
                ContractIssue("invalid_literature_entry", entry_path, "literature entry must be an object")
            )
            continue
        issues.extend(_validate_literature_entry(raw_entry, entry_path, min_year=min_year))
    return issues


def _validate_literature_entry(
    entry: dict[str, Any],
    entry_path: str,
    *,
    min_year: int | None,
) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    for field in ("title", "url", "venue_or_status", "relevance"):
        if not _is_nonempty_string(entry.get(field)):
            issues.append(
                ContractIssue(
                    "missing_literature_entry_field",
                    entry_path,
                    f"literature entry must include non-empty {field}",
                )
            )

    url = entry.get("url")
    if isinstance(url, str) and not url.startswith(("https://", "http://")):
        issues.append(
            ContractIssue(
                "invalid_literature_url",
                entry_path,
                "literature URL must be an HTTP(S) source, not copied local prose",
            )
        )

    year = _int_or_none(entry.get("year"))
    if year is None:
        issues.append(
            ContractIssue("invalid_literature_year", entry_path, "literature entry year must be an integer")
        )
    elif min_year is not None and year < min_year:
        issues.append(
            ContractIssue(
                "stale_recent_paper",
                entry_path,
                f"recent high-quality papers must be from {min_year} or later",
            )
        )

    venue = entry.get("venue_or_status")
    if (
        isinstance(venue, str)
        and not _is_allowed_literature_venue(venue)
        and not _is_nonempty_string(entry.get("venue_justification"))
    ):
        issues.append(
            ContractIssue(
                "unvetted_literature_source",
                entry_path,
                "venue_or_status must be a known strong venue/status or include venue_justification",
            )
        )
    return issues


def _validate_trend_sources(payload: dict[str, Any]) -> list[ContractIssue]:
    raw_entries = payload.get("trend_sources")
    if not isinstance(raw_entries, list) or not raw_entries:
        return [
            ContractIssue(
                "missing_trend_sources",
                str(LITERATURE_GROUNDING_JSON_PATH),
                "trend_sources must record at least one news/lab/industry discovery source",
            )
        ]

    issues: list[ContractIssue] = []
    for index, raw_entry in enumerate(raw_entries):
        entry_path = f"{LITERATURE_GROUNDING_JSON_PATH}:trend_sources[{index}]"
        if not isinstance(raw_entry, dict):
            issues.append(
                ContractIssue("invalid_trend_source", entry_path, "trend source must be an object")
            )
            continue
        if not (
            _is_nonempty_string(raw_entry.get("name"))
            or _is_nonempty_string(raw_entry.get("source_name"))
        ):
            issues.append(
                ContractIssue("missing_trend_source_name", entry_path, "trend source needs a name")
            )
        for field in ("url", "accessed_on"):
            if not _is_nonempty_string(raw_entry.get(field)):
                issues.append(
                    ContractIssue(
                        "missing_trend_source_field",
                        entry_path,
                        f"trend source must include non-empty {field}",
                    )
                )
        signals = raw_entry.get("signals")
        if not isinstance(signals, list) or not signals:
            issues.append(
                ContractIssue(
                    "missing_trend_source_signals",
                    entry_path,
                    "trend source must list discovery signals",
                )
            )
    return issues


def _validate_source_ref_entries(
    raw_entries: object,
    entry_path: str,
    *,
    min_count: int,
    require_paper_source: bool,
) -> list[ContractIssue]:
    if not isinstance(raw_entries, list):
        return [
            ContractIssue(
                "missing_source_refs",
                entry_path,
                "source_refs/derived_from must list surveyed papers, benchmarks, code, or trend-backed sources",
            )
        ]

    issues: list[ContractIssue] = []
    if len(raw_entries) < min_count:
        issues.append(
            ContractIssue(
                "insufficient_source_refs",
                entry_path,
                f"source_refs/derived_from must contain at least {min_count} source references",
            )
        )

    has_paper_source = False
    for index, raw_ref in enumerate(raw_entries):
        ref_path = f"{entry_path}:source_refs[{index}]"
        if not isinstance(raw_ref, dict):
            issues.append(ContractIssue("invalid_source_ref", ref_path, "source reference must be an object"))
            continue
        source_type = _lower_text(raw_ref.get("type", raw_ref.get("source_type")))
        has_paper_source = has_paper_source or source_type in {"paper", "recent_paper", "classic_paper"}
        if source_type not in {
            "paper",
            "recent_paper",
            "classic_paper",
            "benchmark",
            "code",
            "official_project",
            "trend_backed_by_paper",
        }:
            issues.append(
                ContractIssue(
                    "invalid_source_ref_type",
                    ref_path,
                    "source reference type must be paper, classic_paper, benchmark, code, official_project, or trend_backed_by_paper",
                )
            )
        for field in ("title", "url"):
            if not _is_nonempty_string(raw_ref.get(field)):
                issues.append(
                    ContractIssue("missing_source_ref_field", ref_path, f"source reference needs {field}")
                )
        url = raw_ref.get("url")
        if isinstance(url, str) and not _is_http_url(url):
            issues.append(
                ContractIssue("invalid_source_ref_url", ref_path, "source reference URL must be HTTP(S)")
            )

    if require_paper_source and not has_paper_source:
        issues.append(
            ContractIssue(
                "source_refs_missing_paper",
                entry_path,
                "idea provenance must derive from at least one surveyed paper, not only agent speculation or news",
            )
        )
    return issues


def _validate_code_source_entry(entry: dict[str, Any], entry_path: str) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    for field in ("url", "source_type", "paper_or_project", "license_or_terms"):
        if not _is_nonempty_string(entry.get(field)):
            issues.append(
                ContractIssue("missing_code_source_field", entry_path, f"code source must include non-empty {field}")
            )
    if not (
        _is_nonempty_string(entry.get("reuse_decision"))
        or _is_nonempty_string(entry.get("decision"))
    ):
        issues.append(
            ContractIssue(
                "missing_code_source_field",
                entry_path,
                "code source must include non-empty reuse_decision",
            )
        )

    url = entry.get("url")
    if isinstance(url, str) and not _is_http_url(url):
        issues.append(ContractIssue("invalid_code_source_url", entry_path, "code source URL must be HTTP(S)"))

    source_type = _lower_text(entry.get("source_type"))
    if source_type and source_type not in {
        "official_paper_code",
        "benchmark_repo",
        "library",
        "baseline_repo",
        "dataset_repo",
        "project_repo",
    }:
        issues.append(
            ContractIssue(
                "invalid_code_source_type",
                entry_path,
                "source_type must identify official paper code, benchmark repo, library, baseline repo, dataset repo, or project repo",
            )
        )

    decision = _lower_text(entry.get("reuse_decision", entry.get("decision")))
    if decision and decision not in {"use", "adapt", "fork", "reference", "baseline", "reject"}:
        issues.append(
            ContractIssue(
                "invalid_code_reuse_decision",
                entry_path,
                "reuse_decision must be use, adapt, fork, reference, baseline, or reject",
            )
        )

    if decision in {"use", "adapt", "fork"} and not _is_nonempty_string(entry.get("attribution")):
        issues.append(
            ContractIssue(
                "missing_code_reuse_attribution",
                entry_path,
                "reused or adapted external code must record attribution",
            )
        )
    return issues


def _looks_like_exemplar_entry(payload: dict[str, Any]) -> bool:
    return any(key in payload for key in ("title", "url", "venue", "structural_profile"))


def _validate_style_exemplar_entry(
    root: Path,
    entry: dict[str, Any],
    entry_path: str,
) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    for field in (
        "title",
        "url",
        "venue",
        "source_type",
        "structural_profile",
        "local_pdf",
        "text_extract",
        "pdf_sha256",
        "license",
        "pdf_storage_policy",
    ):
        if not _is_nonempty_string(entry.get(field)):
            issues.append(
                ContractIssue(
                    "missing_style_exemplar_field",
                    entry_path,
                    f"style exemplar must include non-empty {field}",
                )
            )

    url = entry.get("url")
    if isinstance(url, str) and not url.startswith("https://"):
        issues.append(
            ContractIssue(
                "invalid_style_exemplar_url",
                entry_path,
                "style exemplar URL must point to an HTTPS open scholarly source",
            )
        )

    if _int_or_none(entry.get("year")) is None:
        issues.append(
            ContractIssue("invalid_style_exemplar_year", entry_path, "style exemplar year must be an integer")
        )

    if entry.get("open_access") is not True:
        issues.append(
            ContractIssue(
                "style_exemplar_not_open_access",
                entry_path,
                "style exemplar must be open-access metadata/source",
            )
        )
    if entry.get("usage") != "structural_style_only":
        issues.append(
            ContractIssue(
                "invalid_style_exemplar_usage",
                entry_path,
                "style exemplar usage must be structural_style_only",
            )
        )
    if entry.get("no_prose_copy") is not True:
        issues.append(
            ContractIssue(
                "missing_no_prose_copy_attestation",
                entry_path,
                "style exemplar must attest that prose, claims, examples, and figures are not copied",
            )
        )

    license_name = _lower_text(entry.get("license"))
    if license_name and license_name not in ALLOWED_STYLE_EXEMPLAR_LICENSES:
        issues.append(
            ContractIssue(
                "style_exemplar_license_not_allowed",
                entry_path,
                "style exemplar license must allow open-access or documented local research-cache use",
            )
        )

    storage_policy = _lower_text(entry.get("pdf_storage_policy"))
    if storage_policy and storage_policy not in ALLOWED_STYLE_EXEMPLAR_STORAGE_POLICIES:
        issues.append(
            ContractIssue(
                "invalid_style_exemplar_storage_policy",
                entry_path,
                "pdf_storage_policy must be redistributable_open_access or local_research_cache_not_redistributed",
            )
        )

    pdf_issues, pdf_path = _validate_style_exemplar_local_pdf(root, entry, entry_path)
    issues.extend(pdf_issues)
    if pdf_path is not None:
        expected_sha = _lower_text(entry.get("pdf_sha256"))
        if expected_sha and not _is_sha256_hex(expected_sha):
            issues.append(
                ContractIssue(
                    "invalid_style_exemplar_pdf_sha256",
                    entry_path,
                    "pdf_sha256 must be a lowercase SHA-256 hex digest",
                )
            )
        elif expected_sha and _sha256_file(pdf_path) != expected_sha:
            issues.append(
                ContractIssue(
                    "style_exemplar_pdf_hash_mismatch",
                    _project_relative_path(root, pdf_path),
                    "local exemplar PDF hash does not match pdf_sha256",
                )
            )

    issues.extend(_validate_style_exemplar_text_extract(root, entry, entry_path))
    issues.extend(_validate_style_exemplar_profile(root, entry, entry_path))
    return issues


def _is_award_style_exemplar(entry: dict[str, Any]) -> bool:
    text = " ".join(
        str(entry.get(field, ""))
        for field in ("title", "venue", "source_type", "award_status", "url")
    ).lower()
    return any(token in text for token in AWARD_STYLE_EXEMPLAR_TOKENS)


def _validate_style_exemplar_local_pdf(
    root: Path,
    entry: dict[str, Any],
    entry_path: str,
) -> tuple[list[ContractIssue], Path | None]:
    issues: list[ContractIssue] = []
    issues.extend(
        _validate_relative_file_field(
            root,
            entry,
            entry_path,
            field="local_pdf",
            code_prefix="style_exemplar_pdf",
        )
    )
    normalized, resolved = _resolve_relative_entry_file(
        root,
        entry,
        entry_path,
        field="local_pdf",
        code_prefix="style_exemplar_pdf",
    )
    if resolved is None:
        return issues, None
    if resolved.suffix.lower() != ".pdf":
        issues.append(
            ContractIssue(
                "style_exemplar_pdf_not_pdf",
                normalized or entry_path,
                "local_pdf must point to the downloaded exemplar PDF",
            )
        )
    try:
        size = resolved.stat().st_size
    except OSError:
        size = 0
    if size < MIN_STYLE_EXEMPLAR_PDF_BYTES:
        issues.append(
            ContractIssue(
                "style_exemplar_pdf_too_small",
                normalized or entry_path,
                f"local exemplar PDF must be a nontrivial paper file >= {MIN_STYLE_EXEMPLAR_PDF_BYTES} bytes",
            )
        )
    return issues, resolved


def _validate_style_exemplar_text_extract(
    root: Path,
    entry: dict[str, Any],
    entry_path: str,
) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    issues.extend(
        _validate_relative_file_field(
            root,
            entry,
            entry_path,
            field="text_extract",
            code_prefix="style_exemplar_text",
        )
    )
    normalized, resolved = _resolve_relative_entry_file(
        root,
        entry,
        entry_path,
        field="text_extract",
        code_prefix="style_exemplar_text",
    )
    if resolved is None:
        return issues
    if resolved.suffix.lower() not in {".txt", ".md"}:
        issues.append(
            ContractIssue(
                "style_exemplar_text_invalid_suffix",
                normalized or entry_path,
                "text_extract must be a text or markdown extraction of the downloaded PDF",
            )
        )
    try:
        text = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        issues.append(
            ContractIssue(
                "style_exemplar_text_not_utf8",
                normalized or entry_path,
                "text_extract must be UTF-8 text",
            )
        )
        return issues
    except OSError as exc:
        issues.append(
            ContractIssue(
                "style_exemplar_text_unreadable",
                normalized or entry_path,
                f"text_extract could not be read: {exc}",
            )
        )
        return issues
    if len(text.strip()) < MIN_STYLE_EXEMPLAR_TEXT_CHARS:
        issues.append(
            ContractIssue(
                "style_exemplar_text_too_thin",
                normalized or entry_path,
                f"text_extract must contain enough extracted paper text for style learning ({MIN_STYLE_EXEMPLAR_TEXT_CHARS}+ chars)",
            )
        )
    return issues


def _validate_style_exemplar_profile(
    root: Path,
    entry: dict[str, Any],
    entry_path: str,
) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    issues.extend(
        _validate_relative_file_field(
            root,
            entry,
            entry_path,
            field="structural_profile",
            code_prefix="style_exemplar_profile",
        )
    )
    normalized, resolved = _resolve_relative_entry_file(
        root,
        entry,
        entry_path,
        field="structural_profile",
        code_prefix="style_exemplar_profile",
    )
    if resolved is None:
        return issues
    try:
        text = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [
            ContractIssue(
                "style_exemplar_profile_not_utf8",
                normalized or entry_path,
                "structural_profile must be UTF-8 text",
            )
        ]
    except OSError as exc:
        return [
            ContractIssue(
                "style_exemplar_profile_unreadable",
                normalized or entry_path,
                f"structural_profile could not be read: {exc}",
            )
        ]
    if len(text.strip()) < MIN_STYLE_PROFILE_CHARS:
        issues.append(
            ContractIssue(
                "style_exemplar_profile_too_thin",
                normalized or entry_path,
                f"structural_profile must be a thick exemplar-learning profile ({MIN_STYLE_PROFILE_CHARS}+ chars)",
            )
        )
    lowered = text.lower()
    missing_topics = [
        topic
        for topic, alternatives in STYLE_PROFILE_REQUIRED_TOPICS.items()
        if not any(alternative in lowered for alternative in alternatives)
    ]
    if missing_topics:
        issues.append(
            ContractIssue(
                "style_exemplar_profile_missing_topics",
                normalized or entry_path,
                "structural_profile is missing required topics: " + ", ".join(missing_topics),
            )
        )
    if re.search(r"\b(?:todo|tbd|placeholder)\b", lowered):
        issues.append(
            ContractIssue(
                "style_exemplar_profile_has_placeholder",
                normalized or entry_path,
                "structural_profile must not contain TODO/TBD/placeholder markers",
            )
        )
    return issues


def _validate_style_structure_blueprint(root: Path) -> list[ContractIssue]:
    """Validate the project-specific outline derived from exemplar structure."""

    path = root / STYLE_STRUCTURE_BLUEPRINT_PATH
    if not path.exists():
        return [
            ContractIssue(
                "missing_style_structure_blueprint",
                str(STYLE_STRUCTURE_BLUEPRINT_PATH),
                "write a project-specific paper structure blueprint from the exemplar profile before drafting prose",
            )
        ]
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [
            ContractIssue(
                "style_structure_blueprint_not_utf8",
                str(STYLE_STRUCTURE_BLUEPRINT_PATH),
                "paper structure blueprint must be UTF-8 text",
            )
        ]
    except OSError as exc:
        return [
            ContractIssue(
                "style_structure_blueprint_unreadable",
                str(STYLE_STRUCTURE_BLUEPRINT_PATH),
                f"paper structure blueprint could not be read: {exc}",
            )
        ]

    stripped = text.strip()
    issues: list[ContractIssue] = []
    if len(stripped) < MIN_STYLE_BLUEPRINT_CHARS:
        issues.append(
            ContractIssue(
                "style_structure_blueprint_too_thin",
                str(STYLE_STRUCTURE_BLUEPRINT_PATH),
                f"paper structure blueprint must be thick enough to guide drafting ({MIN_STYLE_BLUEPRINT_CHARS}+ chars)",
            )
        )
    lowered = stripped.lower()
    missing_topics = [
        topic
        for topic, alternatives in STYLE_BLUEPRINT_REQUIRED_TOPICS.items()
        if not any(alternative in lowered for alternative in alternatives)
    ]
    if missing_topics:
        issues.append(
            ContractIssue(
                "style_structure_blueprint_missing_topics",
                str(STYLE_STRUCTURE_BLUEPRINT_PATH),
                "paper structure blueprint is missing required topics: "
                + ", ".join(missing_topics),
            )
        )
    if re.search(r"\b(?:todo|tbd|placeholder)\b", lowered):
        issues.append(
            ContractIssue(
                "style_structure_blueprint_has_placeholder",
                str(STYLE_STRUCTURE_BLUEPRINT_PATH),
                "paper structure blueprint must not contain TODO/TBD/placeholder markers",
            )
        )
    return issues


def _resolve_relative_entry_file(
    root: Path,
    entry: dict[str, Any],
    entry_path: str,
    *,
    field: str,
    code_prefix: str,
) -> tuple[str | None, Path | None]:
    normalized = _normalize_manifest_path(entry.get(field))
    if normalized is None:
        return None, None
    resolved = _resolve_manifest_path(root, normalized)
    if resolved is None:
        return normalized, None
    if not resolved.is_file():
        return normalized, None
    return normalized, resolved


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


def _validate_figure_entry(
    root: Path,
    entry: dict[str, Any],
    entry_path: str,
) -> tuple[list[ContractIssue], bool]:
    figure_type = _lower_text(entry.get("figure_type", entry.get("kind")))
    source = _lower_text(entry.get("source"))
    uses_image2 = _entry_uses_image2(entry)
    is_conceptual = figure_type in CONCEPTUAL_IMAGE_FIGURE_TYPES
    is_vector_or_data = figure_type in {
        "data_plot",
        "plot",
        "chart",
        "table",
        "tikz_diagram",
        "pgfplots",
        "matplotlib",
    } or source in {"tikz", "pgfplots", "matplotlib", "latex", "script"}
    is_raster_generated = source in {"raster", "ai", "generated", "png", "jpg", "jpeg", "image"}

    issues: list[ContractIssue] = []
    if not _is_nonempty_string(entry.get("figure_id", entry.get("id"))):
        issues.append(ContractIssue("missing_figure_id", entry_path, "figure entry needs figure_id"))
    if not figure_type:
        issues.append(ContractIssue("missing_figure_type", entry_path, "figure entry needs figure_type"))

    if is_conceptual and not uses_image2:
        issues.append(
            ContractIssue(
                "conceptual_figure_not_image2",
                entry_path,
                "conceptual/method/overview/teaser/framework figures must be generated with image-2/codex-image2; do not self-draw them with matplotlib, TikZ, scripts, or manual vector tools",
            )
        )
    if is_raster_generated and not uses_image2 and not is_vector_or_data:
        issues.append(
            ContractIssue(
                "raster_figure_not_image2",
                entry_path,
                "AI/raster generated paper figures must use image-2/codex-image2",
            )
        )

    if uses_image2:
        for field in ("prompt_path", "output_path"):
            issues.extend(
                _validate_relative_file_field(
                    root,
                    entry,
                    entry_path,
                    field=field,
                    code_prefix="image2_figure",
                )
            )

    if is_conceptual and uses_image2:
        issues.extend(_validate_conceptual_image2_figure(root, entry, entry_path))

    return issues, is_conceptual and uses_image2


def _validate_conceptual_image2_figure(
    root: Path,
    entry: dict[str, Any],
    entry_path: str,
) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    output_path = _normalize_manifest_path(entry.get("output_path"))
    if output_path is not None and Path(output_path).suffix.lower() not in IMAGE2_RASTER_OUTPUT_SUFFIXES:
        issues.append(
            ContractIssue(
                "image2_conceptual_output_not_raster",
                entry_path,
                "image-2 conceptual figures must keep the generated PNG/JPEG raster as output_path, not a PDF/vector redraw",
            )
        )

    requested_size = _figure_requested_size(root, entry)
    if requested_size == (1024, 1024):
        issues.append(
            ContractIssue(
                "disallowed_square_image_request",
                entry_path,
                "conceptual paper figures must not request 1024x1024 square output",
            )
        )

    dimensions = _figure_dimensions(root, entry)
    if dimensions is None:
        issues.append(
            ContractIssue(
                "missing_figure_dimensions",
                entry_path,
                "image-2 conceptual figures must record or expose actual width and height",
            )
        )
    else:
        width, height = dimensions
        if width <= 0 or height <= 0:
            issues.append(
                ContractIssue(
                    "invalid_figure_dimensions",
                    entry_path,
                    "image-2 conceptual figure dimensions must be positive",
                )
            )
        else:
            ratio = width / height
            has_waiver = _has_aspect_ratio_waiver(entry)
            if not has_waiver and 0.9 <= ratio <= 1.1:
                issues.append(
                    ContractIssue(
                        "square_conceptual_figure",
                        entry_path,
                        (
                            f"conceptual paper figure is near-square ({width}x{height}); "
                            "use a landscape/adaptive academic figure instead"
                        ),
                    )
                )
            if not has_waiver and (
                ratio < MIN_CONCEPTUAL_FIGURE_ASPECT_RATIO
                or ratio > MAX_CONCEPTUAL_FIGURE_ASPECT_RATIO
            ):
                issues.append(
                    ContractIssue(
                        "bad_conceptual_figure_aspect_ratio",
                        entry_path,
                        (
                            f"conceptual paper figure aspect ratio {ratio:.2f} is outside "
                            f"{MIN_CONCEPTUAL_FIGURE_ASPECT_RATIO:.1f}-"
                            f"{MAX_CONCEPTUAL_FIGURE_ASPECT_RATIO:.1f}"
                        ),
                    )
                )

    issues.extend(_validate_image2_output_integrity(root, entry, entry_path))
    issues.extend(_validate_image2_teaser_prompt_quality(root, entry, entry_path))
    issues.extend(_validate_image_review(root, entry, entry_path))
    issues.extend(_validate_image2_generation_provenance(root, entry, entry_path))
    issues.extend(_detect_local_conceptual_figure_generation(root, entry, entry_path))
    return issues


def _validate_image2_teaser_prompt_quality(
    root: Path,
    entry: dict[str, Any],
    entry_path: str,
) -> list[ContractIssue]:
    prompt_path = _normalize_manifest_path(entry.get("prompt_path"))
    prompt_file = _optional_manifest_file(root, prompt_path)
    if prompt_path is None or prompt_file is None or not prompt_file.is_file():
        return []

    try:
        prompt = prompt_file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        prompt = prompt_file.read_text(encoding="utf-8", errors="replace")
    stripped = prompt.strip()
    normalized = re.sub(r"\s+", " ", stripped.lower())

    issues: list[ContractIssue] = []
    if len(stripped) < MIN_IMAGE2_TEASER_PROMPT_CHARS:
        issues.append(
            ContractIssue(
                "thin_image2_teaser_prompt",
                _project_relative_path(root, prompt_file),
                (
                    "image-2 Figure 1/teaser prompts must use the full teaser scaffold "
                    "with style, pinned-content, negative-prompt, and layout-variant blocks; "
                    f"found only {len(stripped)} characters"
                ),
            )
        )

    missing_groups = [
        group_name
        for group_name, tokens in IMAGE2_TEASER_PROMPT_REQUIRED_GROUPS
        if not any(token in normalized for token in tokens)
    ]
    if missing_groups:
        issues.append(
            ContractIssue(
                "incomplete_image2_teaser_prompt_scaffold",
                _project_relative_path(root, prompt_file),
                (
                    "image-2 Figure 1/teaser prompt is missing scaffold blocks: "
                    + ", ".join(missing_groups)
                ),
            )
        )
    return issues


def _figure_requested_size(root: Path, entry: dict[str, Any]) -> tuple[int, int] | None:
    for field in ("requested_size", "size", "generation_size"):
        value = entry.get(field)
        if isinstance(value, str):
            match = re.fullmatch(r"\s*(\d{2,5})x(\d{2,5})\s*", value.lower())
            if match:
                return int(match.group(1)), int(match.group(2))
    sidecar = _optional_manifest_file(root, entry.get("sidecar_path"))
    if sidecar is not None and sidecar.is_file() and sidecar.suffix.lower() == ".json":
        payload = _try_read_json_object(sidecar)
        if payload is not None:
            for field in ("requested_size", "size", "generation_size"):
                value = payload.get(field)
                if isinstance(value, str):
                    match = re.fullmatch(r"\s*(\d{2,5})x(\d{2,5})\s*", value.lower())
                    if match:
                        return int(match.group(1)), int(match.group(2))
    return None


def _figure_dimensions(root: Path, entry: dict[str, Any]) -> tuple[int, int] | None:
    output = _optional_manifest_file(root, entry.get("output_path"))
    if output is not None and output.is_file():
        dimensions = _image_file_dimensions(output)
        if dimensions is not None:
            return dimensions
    direct = _dimensions_from_mapping(entry)
    if direct is not None:
        return direct
    dimensions = entry.get("dimensions")
    if isinstance(dimensions, dict):
        direct = _dimensions_from_mapping(dimensions)
        if direct is not None:
            return direct
    for field in ("sidecar_path", "review_path", "inspect_path"):
        resolved = _optional_manifest_file(root, entry.get(field))
        if resolved is None or not resolved.is_file():
            continue
        if resolved.suffix.lower() == ".json":
            payload = _try_read_json_object(resolved)
            if payload is None:
                continue
            direct = _dimensions_from_mapping(payload)
            if direct is not None:
                return direct
            image = payload.get("image")
            if isinstance(image, dict):
                direct = _dimensions_from_mapping(image)
                if direct is not None:
                    return direct
    return None


def _validate_image2_output_integrity(
    root: Path,
    entry: dict[str, Any],
    entry_path: str,
) -> list[ContractIssue]:
    output_path = _normalize_manifest_path(entry.get("output_path"))
    output = _optional_manifest_file(root, output_path)
    if output_path is None or output is None or not output.is_file():
        return []

    actual_dimensions = _image_file_dimensions(output)
    if actual_dimensions is None:
        return []

    issues: list[ContractIssue] = []
    width, height = actual_dimensions
    if width < MIN_CONCEPTUAL_FIGURE_PIXEL_WIDTH or height < MIN_CONCEPTUAL_FIGURE_PIXEL_HEIGHT:
        issues.append(
            ContractIssue(
                "low_resolution_image2_conceptual_output",
                _project_relative_path(root, output),
                (
                    f"image-2 conceptual output is only {width}x{height}; preserve a real "
                    "page-width image-2 raster instead of a cropped/downsampled/local redraw"
                ),
            )
        )

    requested_size = _figure_requested_size(root, entry)
    if requested_size is not None and actual_dimensions != requested_size:
        issues.append(
            ContractIssue(
                "image2_output_dimensions_mismatch_requested_size",
                entry_path,
                (
                    f"manifest/provenance requested {requested_size[0]}x{requested_size[1]} "
                    f"but output_path is {width}x{height}; do not crop, resave, or replace the "
                    "generated image-2 raster after provenance is written"
                ),
            )
        )

    for source, recorded_dimensions in _recorded_image2_dimensions(root, entry):
        if recorded_dimensions == actual_dimensions:
            continue
        issues.append(
            ContractIssue(
                "image2_recorded_dimensions_mismatch_output",
                source,
                (
                    f"recorded dimensions {recorded_dimensions[0]}x{recorded_dimensions[1]} "
                    f"do not match output_path {output_path} ({width}x{height}); refresh the "
                    "image-2 artifact/sidecars together instead of relabeling a local replacement"
                ),
            )
        )
    return issues


def _recorded_image2_dimensions(root: Path, entry: dict[str, Any]) -> list[tuple[str, tuple[int, int]]]:
    records: list[tuple[str, tuple[int, int]]] = []
    direct = _dimensions_from_mapping(entry)
    if direct is not None:
        records.append(("IMAGE2_FIGURES.json entry", direct))

    dimensions = entry.get("dimensions")
    if isinstance(dimensions, dict):
        direct = _dimensions_from_mapping(dimensions)
        if direct is not None:
            records.append(("IMAGE2_FIGURES.json entry dimensions", direct))

    seen_paths: set[Path] = set()
    for field in (
        "sidecar_path",
        "generation_provenance_path",
        "provenance_path",
        "review_path",
        "inspect_path",
    ):
        resolved = _optional_manifest_file(root, entry.get(field))
        if resolved is None or not resolved.is_file() or resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        if resolved.suffix.lower() != ".json":
            continue
        payload = _try_read_json_object(resolved)
        if payload is None:
            continue
        direct = _dimensions_from_mapping(payload)
        if direct is None:
            image = payload.get("image")
            if isinstance(image, dict):
                direct = _dimensions_from_mapping(image)
        if direct is not None:
            records.append((_project_relative_path(root, resolved), direct))
    return records


def _dimensions_from_mapping(payload: dict[str, Any]) -> tuple[int, int] | None:
    width = _int_or_none(payload.get("width"))
    height = _int_or_none(payload.get("height"))
    if width is not None and height is not None:
        return width, height
    return None


def _optional_manifest_file(root: Path, value: object) -> Path | None:
    normalized = _normalize_manifest_path(value)
    if normalized is None:
        return None
    return _resolve_manifest_path(root, normalized)


def _image_file_dimensions(path: Path) -> tuple[int, int] | None:
    data = path.read_bytes()[:65536]
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data.startswith(b"\xff\xd8"):
        return _jpeg_dimensions(data)
    return None


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data):
            return None
        segment_length = int.from_bytes(data[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(data):
            return None
        if 0xC0 <= marker <= 0xC3 and segment_length >= 7:
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return width, height
        index += segment_length
    return None


def _has_aspect_ratio_waiver(entry: dict[str, Any]) -> bool:
    waiver = entry.get("aspect_ratio_waiver")
    if not isinstance(waiver, dict):
        return False
    return (
        _is_nonempty_string(waiver.get("rationale"))
        and _is_nonempty_string(waiver.get("waiver_reviewed_by"))
    )


def _validate_image_review(
    root: Path,
    entry: dict[str, Any],
    entry_path: str,
) -> list[ContractIssue]:
    review_path = _optional_manifest_file(root, entry.get("review_path"))
    if review_path is None:
        return [
            ContractIssue(
                "missing_image_review_path",
                entry_path,
                "image-2 conceptual figures must include a review_path sidecar",
            )
        ]
    if not review_path.is_file():
        return [
            ContractIssue(
                "missing_image_review_file",
                entry_path,
                "image-2 conceptual figure review_path file is missing",
            )
        ]
    review = _try_read_json_object(review_path)
    if review is None:
        return [
            ContractIssue(
                "invalid_image_review_json",
                str(review_path.relative_to(root)),
                "image review sidecar must be valid JSON",
            )
        ]
    score = _image_review_score(review)
    issues: list[ContractIssue] = []
    if score is None:
        issues.append(
            ContractIssue(
                "missing_image_review_score",
                str(review_path.relative_to(root)),
                "image review must include score_1_to_5",
            )
        )
    elif score < MIN_IMAGE_REVIEW_SCORE:
        issues.append(
            ContractIssue(
                "low_image_review_score",
                str(review_path.relative_to(root)),
                f"image review score {score:g} is below {MIN_IMAGE_REVIEW_SCORE:g}",
            )
        )
    keep_or_regenerate = _image_review_keep_or_regenerate(review)
    if keep_or_regenerate == "regenerate":
        issues.append(
            ContractIssue(
                "image_review_requested_regeneration",
                str(review_path.relative_to(root)),
                "image review requested regeneration, so the figure is not final-ready",
            )
        )
    return issues


def _validate_image2_generation_provenance(
    root: Path,
    entry: dict[str, Any],
    entry_path: str,
) -> list[ContractIssue]:
    raw_path = entry.get("generation_provenance_path") or entry.get("provenance_path")
    provenance_path = _optional_manifest_file(root, raw_path)
    if provenance_path is None:
        return [
            ContractIssue(
                "missing_image2_generation_provenance",
                entry_path,
                "image-2 conceptual figures must include generation_provenance_path/provenance_path",
            )
        ]
    if not provenance_path.is_file():
        return [
            ContractIssue(
                "missing_image2_generation_provenance_file",
                _project_relative_path(root, provenance_path),
                "image-2 generation provenance file is missing",
            )
        ]

    provenance = _try_read_json_object(provenance_path)
    if provenance is None:
        return [
            ContractIssue(
                "invalid_image2_generation_provenance_json",
                _project_relative_path(root, provenance_path),
                "image-2 generation provenance must be a valid JSON object",
            )
        ]

    issues: list[ContractIssue] = []
    provenance_text = " ".join(
        str(provenance.get(field, ""))
        for field in ("model", "generator", "generator_model", "renderer", "tool", "backend", "provider")
    ).lower()
    if not any(token in provenance_text for token in ("image-2", "codex-image2", "gpt-image-2")):
        issues.append(
            ContractIssue(
                "image2_generation_provenance_not_image2",
                _project_relative_path(root, provenance_path),
                "generation provenance must identify image-2/codex-image2 as the model or tool",
            )
        )

    for field in ("prompt_path", "output_path"):
        expected = _normalize_manifest_path(entry.get(field))
        actual = _normalize_manifest_path(provenance.get(field))
        if actual is None:
            issues.append(
                ContractIssue(
                    f"missing_image2_provenance_{field}",
                    _project_relative_path(root, provenance_path),
                    f"generation provenance must record {field}",
                )
            )
        elif expected is not None and actual != expected:
            issues.append(
                ContractIssue(
                    f"mismatched_image2_provenance_{field}",
                    _project_relative_path(root, provenance_path),
                    f"generation provenance {field}={actual!r} does not match manifest {expected!r}",
                )
            )

    output_sha = _lower_text(provenance.get("output_sha256"))
    output_file = _optional_manifest_file(root, entry.get("output_path"))
    if not output_sha:
        issues.append(
            ContractIssue(
                "missing_image2_provenance_output_sha256",
                _project_relative_path(root, provenance_path),
                "generation provenance must record output_sha256 for the generated raster",
            )
        )
    elif not _is_sha256_hex(output_sha):
        issues.append(
            ContractIssue(
                "invalid_image2_provenance_output_sha256",
                _project_relative_path(root, provenance_path),
                "generation provenance output_sha256 must be a lowercase SHA-256 hex digest",
            )
        )
    elif output_file is not None and output_file.is_file() and _sha256_file(output_file) != output_sha:
        issues.append(
            ContractIssue(
                "mismatched_image2_provenance_output_sha256",
                _project_relative_path(root, provenance_path),
                "generation provenance output_sha256 does not match the raster output_path",
            )
        )

    for file_field, hash_field in (
        ("prompt_path", "prompt_sha256"),
        ("output_path", "output_sha256"),
        ("review_path", "review_sha256"),
    ):
        issues.extend(
            _validate_optional_manifest_file_hash(
                root,
                entry,
                entry_path,
                file_field=file_field,
                hash_field=hash_field,
                code_prefix="image2_figure",
            )
        )
    return issues


def _validate_optional_manifest_file_hash(
    root: Path,
    entry: dict[str, Any],
    entry_path: str,
    *,
    file_field: str,
    hash_field: str,
    code_prefix: str,
) -> list[ContractIssue]:
    expected = _lower_text(entry.get(hash_field))
    if not expected:
        return []
    if not _is_sha256_hex(expected):
        return [
            ContractIssue(
                f"{code_prefix}_invalid_{hash_field}",
                entry_path,
                f"{hash_field} must be a lowercase SHA-256 hex digest",
            )
        ]
    resolved = _optional_manifest_file(root, entry.get(file_field))
    if resolved is None or not resolved.is_file():
        return []
    actual = _sha256_file(resolved)
    if actual != expected:
        return [
            ContractIssue(
                f"{code_prefix}_mismatched_{hash_field}",
                entry_path,
                f"{hash_field} does not match {file_field}",
            )
        ]
    return []


def _detect_local_conceptual_figure_generation(
    root: Path,
    entry: dict[str, Any],
    entry_path: str,
) -> list[ContractIssue]:
    output_path = _normalize_manifest_path(entry.get("output_path"))
    if output_path is None:
        return []

    output = Path(output_path)
    needles = {output_path.lower(), output.name.lower()}
    if output_path.startswith("paper/"):
        needles.add(output_path.removeprefix("paper/").lower())

    for source_path in _iter_local_renderer_source_files(root):
        try:
            raw = source_path.read_bytes()
        except OSError:
            continue
        if len(raw) > MAX_LOCAL_RENDERER_SOURCE_BYTES:
            continue
        text = raw.decode("utf-8", errors="ignore").lower()
        renderer = _local_renderer_near_any_needle(text, needles)
        if renderer is None:
            renderer = _local_renderer_named_for_conceptual_output(text, output_path, entry)
        if renderer is None:
            continue
        return [
            ContractIssue(
                "local_conceptual_figure_generation_detected",
                _project_relative_path(root, source_path),
                f"{_project_relative_path(root, source_path)} references {output_path!r} near "
                f"{renderer} rendering code; Figure 1/overview must be a real image-2 raster, "
                "not a local PIL/matplotlib/TikZ/SVG/HTML redraw mislabeled as image-2",
            )
        ]
    return []


def _local_renderer_named_for_conceptual_output(
    text: str,
    output_path: str,
    entry: dict[str, Any],
) -> str | None:
    aliases = _conceptual_renderer_aliases(output_path, entry)
    if not aliases:
        return None
    for alias in aliases:
        pattern = re.compile(
            rf"\bdef\s+(?:render|draw|make|generate|create)_[a-z0-9_]*{re.escape(alias)}[a-z0-9_]*\s*\("
        )
        for match in pattern.finditer(text):
            next_function = text.find("\ndef ", match.end())
            end = next_function if next_function != -1 else match.start() + LOCAL_RENDERER_SCAN_WINDOW_CHARS
            renderer = _local_renderer_label(text[match.start() : end])
            if renderer is not None:
                return renderer
    return None


def _conceptual_renderer_aliases(output_path: str, entry: dict[str, Any]) -> set[str]:
    raw_aliases = {
        Path(output_path).stem,
        _lower_text(entry.get("figure_id", "")).replace("-", "_"),
        _lower_text(entry.get("name", "")).replace("-", "_"),
    }
    aliases: set[str] = set()
    for alias in raw_aliases:
        normalized = re.sub(r"[^a-z0-9_]+", "_", alias.strip().lower()).strip("_")
        if len(normalized) >= 6:
            aliases.add(normalized)
    return aliases


def _iter_local_renderer_source_files(root: Path) -> list[Path]:
    root_resolved = root.resolve()
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root_resolved):
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if dirname not in LOCAL_RENDERER_EXCLUDED_DIRS and not dirname.startswith(".tmp")
        ]
        directory = Path(dirpath)
        for filename in filenames:
            path = directory / filename
            if path.suffix.lower() in LOCAL_RENDERER_SOURCE_SUFFIXES:
                files.append(path)
    return files


def _local_renderer_near_any_needle(text: str, needles: set[str]) -> str | None:
    for needle in needles:
        start = 0
        while True:
            index = text.find(needle, start)
            if index == -1:
                break
            window = text[
                max(0, index - LOCAL_RENDERER_SCAN_WINDOW_CHARS) : index
                + len(needle)
                + LOCAL_RENDERER_SCAN_WINDOW_CHARS
            ]
            renderer = _local_renderer_label(window)
            if renderer is not None:
                return renderer
            start = index + len(needle)
    return None


def _local_renderer_label(text: str) -> str | None:
    for token, label in LOCAL_CONCEPTUAL_RENDER_TOKENS:
        if token in text:
            return label
    return None


def _image_review_score(review: dict[str, Any]) -> float | None:
    direct = _float_or_none(review.get("score_1_to_5"))
    if direct is not None:
        return direct
    nested = _parsed_review_payload(review)
    if nested is not None:
        return _float_or_none(nested.get("score_1_to_5"))
    review_text = review.get("review")
    if isinstance(review_text, str):
        match = re.search(r"score_1_to_5[\"']?\s*[:=]\s*([1-5](?:\.\d+)?)", review_text)
        if match:
            return float(match.group(1))
    return None


def _image_review_keep_or_regenerate(review: dict[str, Any]) -> str:
    direct = review.get("keep_or_regenerate")
    if isinstance(direct, str):
        return direct.strip().lower()
    nested = _parsed_review_payload(review)
    if nested is not None:
        value = nested.get("keep_or_regenerate")
        if isinstance(value, str):
            return value.strip().lower()
    return ""


def _parsed_review_payload(review: dict[str, Any]) -> dict[str, Any] | None:
    value = review.get("review")
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _entry_uses_image2(entry: dict[str, Any]) -> bool:
    text = " ".join(
        str(entry.get(field, ""))
        for field in ("model", "generator", "generator_model", "renderer")
    ).lower()
    return any(token in text for token in ("image-2", "codex-image2", "gpt-image-2"))


def _validate_relative_file_field(
    root: Path,
    entry: dict[str, Any],
    entry_path: str,
    *,
    field: str,
    code_prefix: str,
) -> list[ContractIssue]:
    normalized = _normalize_manifest_path(entry.get(field))
    if normalized is None:
        return [
            ContractIssue(
                f"{code_prefix}_invalid_{field}",
                entry_path,
                f"{field} must be a POSIX relative path inside the project",
            )
        ]
    resolved = _resolve_manifest_path(root, normalized)
    if resolved is None:
        return [
            ContractIssue(
                f"{code_prefix}_unsafe_{field}",
                normalized,
                f"{field} resolves outside the project root",
            )
        ]
    if not resolved.is_file():
        return [
            ContractIssue(
                f"{code_prefix}_missing_{field}",
                normalized,
                f"{field} file is missing",
            )
        ]
    return []


def _is_allowed_literature_venue(value: str) -> bool:
    text = value.lower()
    return any(token in text for token in ALLOWED_LITERATURE_VENUE_TOKENS)


def _is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_http_url(value: str) -> bool:
    return value.startswith(("https://", "http://"))


def _lower_text(value: object) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _missing_artifact_issues(root: Path, stage: str) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    for pattern in REQUIRED_ARTIFACT_PATTERNS.get(stage, ()):
        if _artifact_exists(root, pattern):
            continue
        issues.append(
            ContractIssue(
                "missing_stage_artifact",
                pattern,
                f"stage {stage!r} is ready/done but required artifact is missing",
            )
        )
    return issues


def _artifact_exists(root: Path, pattern: str) -> bool:
    if any(ch in pattern for ch in "*?["):
        return any(path.exists() for path in root.glob(pattern))
    return (root / pattern).exists()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m argus_skill.skills.pipeline_contracts",
        description="Validate or refresh argus-skill research pipeline contracts.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (
        ("validate-pipeline", "validate research/PIPELINE_STATE.json and gated artifacts"),
        ("validate-manifest", "validate paper/ARTIFACT_MANIFEST.json"),
        ("refresh-manifest", "recompute manifest digests and TSV headers, then validate"),
        ("validate-grounding", "validate research/LITERATURE_GROUNDING.json"),
        ("validate-idea-provenance", "validate literature-derived idea provenance"),
        ("validate-code-reuse", "validate external source-code survey and reuse plan"),
        ("validate-exemplar", "validate paper/style_ref/EXEMPLAR.json"),
        ("validate-image2-figures", "validate paper/figures/IMAGE2_FIGURES.json"),
        ("validate-paper-contract", "validate full EMNLP long-paper draft contract"),
        ("validate-paper-format", "validate LaTeX/PDF reviewability and formatting evidence"),
        ("validate-research-md-format", "validate strict research.md EMNLP format preflight"),
        ("validate-layout-review", "validate final paper layout/aesthetic review score"),
        ("validate-academic-language-review", "validate final academic-language review score"),
        ("validate-submission", "validate submission readiness gates"),
        ("validate-full-emnlp", "validate complete EMNLP long-paper readiness"),
    ):
        command_parser = subcommands.add_parser(command, help=help_text)
        command_parser.add_argument(
            "--project-root",
            type=Path,
            default=Path.cwd(),
            help="project root containing research/, experiments/, and paper/",
        )

    args = parser.parse_args(list(argv) if argv is not None else None)
    project_root = Path(args.project_root)
    if args.command == "validate-pipeline":
        issues = validate_pipeline_state(project_root)
    elif args.command == "validate-manifest":
        issues = validate_artifact_manifest(project_root)
    elif args.command == "refresh-manifest":
        issues = refresh_artifact_manifest(project_root)
    elif args.command == "validate-grounding":
        issues = validate_literature_grounding(project_root)
    elif args.command == "validate-idea-provenance":
        issues = validate_idea_provenance(project_root)
    elif args.command == "validate-code-reuse":
        issues = validate_code_reuse_plan(project_root)
    elif args.command == "validate-exemplar":
        issues = validate_style_exemplar(project_root)
    elif args.command == "validate-image2-figures":
        issues = validate_image2_figures(project_root)
    elif args.command == "validate-paper-contract":
        issues = validate_emnlp_paper_contract(project_root)
    elif args.command == "validate-paper-format":
        issues = validate_paper_format(project_root)
    elif args.command == "validate-research-md-format":
        issues = validate_research_md_format_preflight(project_root)
    elif args.command == "validate-layout-review":
        issues = validate_layout_review(project_root)
    elif args.command == "validate-academic-language-review":
        issues = validate_academic_language_review(project_root)
    elif args.command == "validate-submission":
        issues = validate_submission_readiness(project_root)
    else:
        issues = validate_full_emnlp_readiness(project_root)

    for issue in issues:
        print(f"{issue.code}\t{issue.path}\t{issue.message}")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
