"""argus_skill.reviewer — the Reviewer role (split into its own top-level package).

Layer: roles

Historically the Reviewer lived at ``argus_skill.engineer.reviewer`` next to
``SupervisedEngineer``. It is its own role (the single source of truth for
"done / continue / blocked"), so it now lives in its own package:

  * :mod:`._core`    — the ``Reviewer`` agent + ``ReviewerConfig`` and prompt build.
  * :mod:`._parsing` — pure verdict/decision parsers (unit-testable, no runner).

The model-facing verdict is an ordinary reply ending in named lines; the parser
keeps JSON input compatibility only for sessions started by older releases.
"""
from __future__ import annotations

from ._core import Reviewer, ReviewerConfig, _load_wiki_curator_skill_if_present
from ._parsing import (
    _find_decision_in_messages,
    parse_decision_text,
)

__all__ = [
    "Reviewer",
    "ReviewerConfig",
    "parse_decision_text",
    "_find_decision_in_messages",
    "_load_wiki_curator_skill_if_present",
]
