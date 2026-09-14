"""The framework's window into the research vertical.

The supervisor's letters and second reading, the vertical reset in
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

from .research.notes import clear_research_notes, read_research_notes
from .research.prompt_policy import (
    _query_local_gpus,
    active_research_context,
)
from .research.second_reading import (
    build_second_reading_prompt,
    insert_second_reading_into_notes,
    note_reconsider_signal,
    parse_second_reading,
    record_second_reading,
    second_reading_due,
)


def local_gpu_lines() -> list[str]:
    """One line per local GPU, empty when none can be queried."""
    return list(_query_local_gpus())


__all__ = [
    "active_research_context",
    "build_second_reading_prompt",
    "clear_research_notes",
    "insert_second_reading_into_notes",
    "local_gpu_lines",
    "note_reconsider_signal",
    "parse_second_reading",
    "read_research_notes",
    "record_second_reading",
    "second_reading_due",
]
