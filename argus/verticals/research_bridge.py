"""The framework's window into the research vertical.

The supervisor's letters, the vertical reset in
``skills/vertical_select.py``, and the Reviewer's research context block are
research-vertical features by design: each caller first confirms it is acting
for the research vertical, then needs one of the entry points below. Framework
packages stay out of ``verticals/research/`` itself; this module is the one
place that names those entry points, so ``tests/test_architecture_invariants.py``
can keep treating every subdirectory of ``verticals/`` as domain-owned.

Everything here is a plain re-export or a thin public wrapper; the behavior
lives with the research vertical.
"""

from __future__ import annotations

from pathlib import Path

from .research.notes import clear_research_notes, read_research_notes
from .research.prompt_policy import active_research_context


def derive_method_card(workdir: Path) -> dict:
    """The research method card with its host-derived evidence, for the Atlas web UI.

    Resolved lazily so the web layer pays for the derivation module only when a
    project actually has a METHOD.md; any failure propagates for the caller to
    turn into a soft error.
    """
    from .research.method_card import derive_method_card as derive

    return derive(workdir)


__all__ = [
    "active_research_context",
    "clear_research_notes",
    "derive_method_card",
    "read_research_notes",
]
