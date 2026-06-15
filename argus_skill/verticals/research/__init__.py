"""Research vertical — paper-writing domain on top of argus core.

This package is the **single authoritative location** for everything
that assumes the project is producing a research paper:

* the 8 paper-pipeline stages (research → plan → benchmark → run →
  analysis → draft → review → submission),
* the paper-specific artifacts (``paper/main.tex``, ``paper/refs.bib``,
  ``paper/DRAFT_OUTLINE.md``, ``paper/claims_to_evidence.tsv``,
  ``benchmarks/evidence/<bundle>/summary.tsv``, etc.),
* the paper-specific gates (``evidence_chain``,
  ``paper_structural_minimums``, ``draft_outline``,
  ``exemplar_grounding``, ``experiment_audit_gate``,
  ``anti_mediocrity``, ``paper_layout_review``,
  ``academic_language_review``, ``paper_infrastructure_review``,
  ``reviewer_simulation``, ``run_evidence_health``,
  ``run_contract``, ``method_differentiation``, ``venue_profiles``,
  ``pipeline_contracts``, ``pipeline_policy``, ``stage_checklists``,
  ``stage_check``).

At the moment this is a **re-export namespace** — the underlying
modules still physically live under ``argus_skill.skills`` and
``argus_skill.tools``. The re-exports here let new code (and any
follow-up vertical refactor) target a stable import path:

```python
from argus_skill.verticals.research import (
    DraftOutline, validate_outline, cross_check_figure_ids,
    StructuralReport, validate_paper_structural_minimums,
    ChainReport, validate_chain,
    STAGE_ORDER, STAGE_CHECKS, REVIEWER_CHECKLISTS,
)
```

A future commit will physically relocate the source files to
``argus_skill/verticals/research/skills/`` and
``argus_skill/verticals/research/tools/`` and update the in-tree
imports; the public names re-exported here are the stable
contract.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Paper-specific gates
# ---------------------------------------------------------------------------
from ...skills.draft_outline import (
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
from ...skills.evidence_chain import (
    ChainIssue,
    ChainReport,
)
from ...skills.paper_structural_minimums import (
    StructuralIssue,
    StructuralReport,
    validate_paper_structural_minimums,
)

# ---------------------------------------------------------------------------
# Paper-specific pipeline (stage definitions + checks)
# ---------------------------------------------------------------------------
from .stages import (
    REVIEWER_CHECKLISTS,
    STAGE_CHECKS,
    STAGE_ORDER,
)

__all__ = [
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
    # evidence_chain
    "ChainIssue",
    "ChainReport",
    # paper_structural_minimums
    "StructuralIssue",
    "StructuralReport",
    "validate_paper_structural_minimums",
    # pipeline / stage_check
    "REVIEWER_CHECKLISTS",
    "STAGE_CHECKS",
    "STAGE_ORDER",
]
