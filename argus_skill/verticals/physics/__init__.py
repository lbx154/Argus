"""Built-in physics vertical: scope -> model -> execute -> review -> manuscript.

The five-stage physics vertical ends in a MANDATORY manuscript stage — a
completed physics mission's deliverable is a standard, discipline-agnostic
research-paper package delivered in three layers: a machine-checkable source
layer, a LaTeX-compiled paper layer (MANUSCRIPT/SUPPLEMENT .tex + .pdf), and an
optional presentation layer that never gates (see ``manuscript.py``). There is
no optional paper mode, no marker file, and no environment-variable trigger.
"""
from __future__ import annotations

from .manuscript import (
    CLAIMS_COLUMNS,
    CLAIMS_HEADER,
    FIG_TEX_END_FRACTION,
    FIGURE_CAPTION_HARD_CAP,
    MAIN_TEXT_FORBIDDEN_ALWAYS,
    MAIN_TEXT_FORBIDDEN_PATHS,
    MANUSCRIPT_SECTIONS,
    MIN_CITE_COMMANDS,
    MIN_DISPLAY_EQUATIONS,
    MIN_EQ_CITATIONS,
    MIN_FIGURES,
    MIN_INTRO_WORDS,
    MIN_MAIN_TABLES,
    MIN_REFERENCES,
    MIN_RESULTS_WORDS,
    MIN_SUPP_CITATIONS,
    MIN_SUPP_TABLES,
    MIN_TABLE_CITATIONS,
    PAPER_AUDIT_HEADING,
    PAPER_REQUIRED_FILES,
    RAW_MATH_SIMPLE_MAX,
    REQUIRED_FILES,
    SUPPLEMENT_CONTENT,
    manuscript_review_items,
    verify_all_deliverables,
    verify_manuscript_deliverables,
    verify_paper_style_deliverables,
)
from .stages import (
    CHECKLIST_ITEMS,
    CHECKLIST_STAGE_ORDER,
    REVIEWER_CHECKLISTS,
    STAGE_CHECKS,
    STAGE_ORDER,
    WORKFLOW_MODE,
    completion_gate,
    role_banner,
)

__all__ = [
    "CHECKLIST_ITEMS",
    "CHECKLIST_STAGE_ORDER",
    "REVIEWER_CHECKLISTS",
    "STAGE_CHECKS",
    "STAGE_ORDER",
    "WORKFLOW_MODE",
    "completion_gate",
    "role_banner",
    # mandatory manuscript-stage delivery contract
    "CLAIMS_COLUMNS",
    "CLAIMS_HEADER",
    "MANUSCRIPT_SECTIONS",
    "MIN_FIGURES",
    "MIN_REFERENCES",
    "REQUIRED_FILES",
    "PAPER_REQUIRED_FILES",
    "MIN_CITE_COMMANDS",
    "MIN_DISPLAY_EQUATIONS",
    "MIN_EQ_CITATIONS",
    "MIN_MAIN_TABLES",
    "MIN_SUPP_TABLES",
    "MIN_TABLE_CITATIONS",
    "MIN_SUPP_CITATIONS",
    "FIGURE_CAPTION_HARD_CAP",
    "MIN_INTRO_WORDS",
    "MIN_RESULTS_WORDS",
    "RAW_MATH_SIMPLE_MAX",
    "FIG_TEX_END_FRACTION",
    "PAPER_AUDIT_HEADING",
    "SUPPLEMENT_CONTENT",
    "MAIN_TEXT_FORBIDDEN_ALWAYS",
    "MAIN_TEXT_FORBIDDEN_PATHS",
    "manuscript_review_items",
    "verify_manuscript_deliverables",
    "verify_paper_style_deliverables",
    "verify_all_deliverables",
]
