"""Research vertical — the paper-writing domain on top of argus core.

This package is the **single authoritative location** for everything that
assumes the project is producing a research paper:

* the four forward-only paper-pipeline stages (idea → experiment →
  paper → review), defined in ``stages.py``;
* supporting facilities such as ``method_differentiation``, ``method_freeze``
  and the idea portfolio.

Quality judgment belongs to the Reviewer reading the actual paper, code and
raw results — not to deterministic validators; ``figure_lint`` is an advisory
tool that lists mechanical figure defects for that judgment. Submodules are imported
directly (e.g. ``from argus.verticals.research import
figure_lint``), and the most-used public symbols are re-exported
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
# The host's log of the Engineer's round registers its round-evidence provider
# on import.
# ---------------------------------------------------------------------------
from . import round_log  # noqa: F401

# ---------------------------------------------------------------------------
# Advisory figure checks
# ---------------------------------------------------------------------------
from .figure_lint import figure_lint_issues
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

# ---------------------------------------------------------------------------
# Per-mission task brief (the vertical's ``prepare_mission`` hook)
# ---------------------------------------------------------------------------
from .mission_brief import prepare_mission

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
    # mission_brief
    "prepare_mission",
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
    # advisory figure checks
    "figure_lint_issues",
]
