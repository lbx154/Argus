"""argus.reviewer — the Reviewer role (split into its own top-level package).

Layer: roles

Historically the Reviewer lived at ``argus.engineer.reviewer`` next to
``SupervisedEngineer``. It is its own role (the single source of truth for
"done / continue / blocked"), so it now lives in its own package:

  * :mod:`._core`    — the ``Reviewer`` agent + ``ReviewerConfig`` and prompt build.
  * :mod:`.tools`    — call-bound native actions with natural-language feedback.

The execution path never parses review prose.
"""
from __future__ import annotations

from ._core import Reviewer, ReviewerConfig

__all__ = [
    "Reviewer",
    "ReviewerConfig",
]
