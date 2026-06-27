"""argus.reviewer — the L2 Reviewer agent (split into its own top-level package).

Historically the Reviewer lived at ``argus_skill.engineer.reviewer`` next to the
L1 ``SupervisedEngineer``. It is its own agent layer (the single source of truth
for "done / continue / blocked"), so it now lives in its own package:

  * :mod:`._core`    — the ``Reviewer`` agent + ``ReviewerConfig`` and prompt build.
  * :mod:`._parsing` — pure verdict/decision parsers (unit-testable, no runner).

The JSON contract is ``reviewer_schema.json`` (resolved relative to ``_core.py``).
The public API — and the previously-public test/internal helpers — are re-exported
here so existing ``from argus_skill.reviewer import ...`` callers keep working.
"""
from __future__ import annotations

from ._core import (
    SCHEMA_PATH,
    Reviewer,
    ReviewerConfig,
    _load_wiki_curator_skill_if_present,
)
from ._parsing import (
    _find_decision_in_messages,
    _parse_checkpoint,
    _parse_planner_report,
    _parse_step_back,
    parse_decision_text,
)

__all__ = [
    "Reviewer",
    "ReviewerConfig",
    "SCHEMA_PATH",
    "parse_decision_text",
    "_find_decision_in_messages",
    "_load_wiki_curator_skill_if_present",
    "_parse_checkpoint",
    "_parse_planner_report",
    "_parse_step_back",
]
