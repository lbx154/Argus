"""Research vertical — the paper-writing domain on top of argus core.

This package is the **single authoritative location** for everything that
assumes the project is producing a research paper:

* the four forward-only paper-pipeline stages (idea → experiment →
  paper → review), defined in ``stages.py``;
* the model-/vision-backed review passes used in the Review stage:
  ``academic_language_review``, ``paper_layout_review``,
  ``paper_infrastructure_review``, plus their shared
  ``_review_contract_constants``;
* supporting facilities such as ``method_differentiation``, ``method_freeze``
  and the idea portfolio.

Quality judgment belongs to the Reviewer reading the actual paper, code and
raw results — not to deterministic validators. Submodules are imported
directly (e.g. ``from argus_skill.verticals.research import
academic_language_review``), and the most-used public symbols are re-exported
here for callers that want one import site.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Generic evidence-chain helpers (live in skills/, re-exported for convenience)
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
    AcademicLanguageReviewError,
    generate_academic_language_review,
)
from .method_differentiation import (
    ConditionRun,
    MethodDifferentiationReport,
    PairFinding,
    validate_method_differentiation,
)
from .method_freeze import (
    CONFIRMATION_RESULT_PATH,
    FREEZE_PATH,
    declare_method_freeze,
    record_confirmation_result,
)
from .paper_infrastructure_review import (
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

# ---------------------------------------------------------------------------
# Paper-specific pipeline (stage definitions + checks)
# ---------------------------------------------------------------------------
from .stages import (
    CHECKLIST_ITEMS,
    STAGE_ORDER,
    WORKFLOW_MODE,
    stage_completion_issues,
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
    "AcademicLanguageReviewError",
    "generate_academic_language_review",
    # paper_infrastructure_review
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
    # method_differentiation
    "ConditionRun",
    "MethodDifferentiationReport",
    "PairFinding",
    "validate_method_differentiation",
    # method_freeze process writers
    "CONFIRMATION_RESULT_PATH",
    "FREEZE_PATH",
    "declare_method_freeze",
    "record_confirmation_result",
    # evidence_chain (generic, from skills/)
    "ChainIssue",
    "ChainReport",
    # pipeline stages
    "CHECKLIST_ITEMS",
    "STAGE_ORDER",
    "WORKFLOW_MODE",
    "stage_completion_issues",
]
