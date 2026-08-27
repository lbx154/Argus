"""Built-in physics vertical: scope -> model -> execute -> review -> manuscript.

The five-stage physics vertical ends in a manuscript stage. Its only
deterministic paper check is the requested outcome: a current compiled PDF from
the LaTeX source. Scientific quality remains the independent Reviewer's job.
"""
from __future__ import annotations

from .manuscript import MANUSCRIPT_PDF, MANUSCRIPT_SOURCE, verify_compiled_manuscript
from .stages import (
    CHECKLIST_ITEMS,
    CHECKLIST_STAGE_ORDER,
    STAGE_ORDER,
    WORKFLOW_MODE,
    completion_gate,
    role_banner,
)

__all__ = [
    "CHECKLIST_ITEMS",
    "CHECKLIST_STAGE_ORDER",
    "STAGE_ORDER",
    "WORKFLOW_MODE",
    "completion_gate",
    "role_banner",
    "MANUSCRIPT_SOURCE",
    "MANUSCRIPT_PDF",
    "verify_compiled_manuscript",
]
