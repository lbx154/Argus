"""Research vertical — the paper-writing domain on top of argus core.

This package is the **single authoritative location** for everything that
assumes the project is producing a research paper:

* the 8 paper-pipeline stages (research → plan → benchmark → run → analysis →
  draft → review → submission), defined in ``stages.py``;
* the **paper-specific quality gates** — the eleven research-paper-only
  reviewers/validators that previously lived alongside the generic skills in
  ``argus_skill.skills`` and now live here as submodules:
  ``academic_language_review``, ``paper_layout_review``,
  ``paper_infrastructure_review``, ``_review_contract_constants``,
  ``draft_outline``, ``paper_structural_minimums``, ``exemplar_grounding``,
  ``experiment_audit_gate``, ``method_differentiation``,
  ``reviewer_simulation``, ``run_evidence_health``.

Submodules are imported directly (e.g.
``from argus_skill.verticals.research import academic_language_review``), and the
most-used public symbols (validators, generators, report dataclasses, path
constants) are re-exported here for callers that want one import site. Each
validator is an agent-callable tool (``python -m ...``); there is no harness
router deciding which one runs at which stage — that judgment is the
Reviewer's. The generic anti-fraud gate ``evidence_chain`` stays in
``argus_skill.skills`` (it is domain-agnostic) and is re-exported here for the
paper pipeline's convenience.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Generic anti-fraud gate (lives in skills/, re-exported for the paper pipeline)
# ---------------------------------------------------------------------------
from ...skills.evidence_chain import (
    ChainIssue,
    ChainReport,
)

# ---------------------------------------------------------------------------
# Shared review-contract constants / helpers
# ---------------------------------------------------------------------------
from ._review_contract_constants import (
    ACADEMIC_LANGUAGE_REVIEW_GENERATED_BY,
    ACADEMIC_LANGUAGE_REVIEW_HISTORY_PATH,
    LAYOUT_REVIEW_GENERATED_BY,
    LAYOUT_REVIEW_HISTORY_PATH,
    PAPER_INFRASTRUCTURE_REVIEW_GENERATED_BY,
    PAPER_INFRASTRUCTURE_REVIEW_HISTORY_PATH,
    REVIEW_INPUT_SHA256_FIELD,
    REVIEW_PROMPT_SHA256_FIELD,
    review_sha256_file,
    review_sha256_json,
    review_sha256_text,
)

# ---------------------------------------------------------------------------
# Model-/vision-backed review generators
# ---------------------------------------------------------------------------
from .academic_language_review import (
    ACADEMIC_LANGUAGE_REVIEW_JSON_PATH,
    ACADEMIC_LANGUAGE_REVIEW_MD_PATH,
    MIN_ACADEMIC_LANGUAGE_SCORE,
    AcademicLanguageReviewError,
    generate_academic_language_review,
)

# ---------------------------------------------------------------------------
# Draft outline contract
# ---------------------------------------------------------------------------
from .draft_outline import (
    DRAFT_OUTLINE_PATH,
    DraftOutline,
    ExperimentPlaceholder,
    FigurePlaceholder,
    OutlineIssue,
    SectionPlaceholder,
    cross_check_figure_ids,
    load_outline,
    parse_outline,
    validate_outline,
)

# ---------------------------------------------------------------------------
# Structural / anti-fabrication gates
# ---------------------------------------------------------------------------
from .exemplar_grounding import (
    GroundingIssue,
    GroundingReport,
    validate_exemplar_grounding,
)
from .experiment_audit_gate import (
    AuditIssue,
    AuditReport,
    validate_experiment_audit,
)
from .method_differentiation import (
    ConditionRun,
    MethodDifferentiationReport,
    PairFinding,
    validate_method_differentiation,
)
from .paper_infrastructure_review import (
    MIN_PAPER_INFRASTRUCTURE_REVIEW_SCORE,
    PAPER_INFRASTRUCTURE_REVIEW_JSON_PATH,
    PAPER_INFRASTRUCTURE_REVIEW_MD_PATH,
    PaperInfrastructureReviewError,
    generate_paper_infrastructure_review,
)
from .paper_layout_review import (
    LAYOUT_REVIEW_JSON_PATH,
    LAYOUT_REVIEW_MD_PATH,
    LAYOUT_REVIEW_PAGE_DIR,
    LayoutReviewError,
    generate_layout_review,
)
from .paper_structural_minimums import (
    StructuralIssue,
    StructuralReport,
    validate_paper_structural_minimums,
)
from .reviewer_simulation import (
    SimulationIssue,
    SimulationReport,
    validate_reviewer_simulation,
)
from .run_evidence_health import (
    BundleHealth,
    HealthIssue,
    RunEvidenceHealthReport,
    validate_run_evidence_health,
)

# ---------------------------------------------------------------------------
# Paper-specific pipeline (stage definitions + checks)
# ---------------------------------------------------------------------------
from .stages import (
    STAGE_ORDER,
    WORKFLOW_MODE,
)

__all__ = [
    # _review_contract_constants
    "ACADEMIC_LANGUAGE_REVIEW_GENERATED_BY",
    "ACADEMIC_LANGUAGE_REVIEW_HISTORY_PATH",
    "LAYOUT_REVIEW_GENERATED_BY",
    "LAYOUT_REVIEW_HISTORY_PATH",
    "PAPER_INFRASTRUCTURE_REVIEW_GENERATED_BY",
    "PAPER_INFRASTRUCTURE_REVIEW_HISTORY_PATH",
    "REVIEW_INPUT_SHA256_FIELD",
    "REVIEW_PROMPT_SHA256_FIELD",
    "review_sha256_file",
    "review_sha256_json",
    "review_sha256_text",
    # academic_language_review
    "ACADEMIC_LANGUAGE_REVIEW_JSON_PATH",
    "ACADEMIC_LANGUAGE_REVIEW_MD_PATH",
    "MIN_ACADEMIC_LANGUAGE_SCORE",
    "AcademicLanguageReviewError",
    "generate_academic_language_review",
    # paper_infrastructure_review
    "MIN_PAPER_INFRASTRUCTURE_REVIEW_SCORE",
    "PAPER_INFRASTRUCTURE_REVIEW_JSON_PATH",
    "PAPER_INFRASTRUCTURE_REVIEW_MD_PATH",
    "PaperInfrastructureReviewError",
    "generate_paper_infrastructure_review",
    # paper_layout_review
    "LAYOUT_REVIEW_JSON_PATH",
    "LAYOUT_REVIEW_MD_PATH",
    "LAYOUT_REVIEW_PAGE_DIR",
    "LayoutReviewError",
    "generate_layout_review",
    # draft_outline
    "DRAFT_OUTLINE_PATH",
    "DraftOutline",
    "ExperimentPlaceholder",
    "FigurePlaceholder",
    "OutlineIssue",
    "SectionPlaceholder",
    "cross_check_figure_ids",
    "load_outline",
    "parse_outline",
    "validate_outline",
    # exemplar_grounding
    "GroundingIssue",
    "GroundingReport",
    "validate_exemplar_grounding",
    # experiment_audit_gate
    "AuditIssue",
    "AuditReport",
    "validate_experiment_audit",
    # method_differentiation
    "ConditionRun",
    "MethodDifferentiationReport",
    "PairFinding",
    "validate_method_differentiation",
    # paper_structural_minimums
    "StructuralIssue",
    "StructuralReport",
    "validate_paper_structural_minimums",
    # reviewer_simulation
    "SimulationIssue",
    "SimulationReport",
    "validate_reviewer_simulation",
    # run_evidence_health
    "BundleHealth",
    "HealthIssue",
    "RunEvidenceHealthReport",
    "validate_run_evidence_health",
    # evidence_chain (generic, from skills/)
    "ChainIssue",
    "ChainReport",
    # pipeline stages
    "STAGE_ORDER",
    "WORKFLOW_MODE",
]
