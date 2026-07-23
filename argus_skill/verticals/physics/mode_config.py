"""Physics vertical run-mode configuration (env-driven, no hardcoded literals).

Controls whether a mission is allowed to succeed as a downgraded paper type
(diagnostic benchmark / reproduction) or must reach an original research article.
Read by the Paper-Type gate, the Novelty-Seeking Loop gate, and the terminal
manuscript contract.

Env vars (all optional; defaults preserve the pre-V5 behaviour):

* ``ARGUS_SKILL_PHYSICS_TARGET_PAPER_TYPE`` — ``auto`` (default) or
  ``original_research_article``.
* ``ARGUS_SKILL_PHYSICS_ALLOW_DOWNGRADE`` — ``true`` (default) / ``false``.

``original-research-required`` mode is: TARGET == original_research_article AND
ALLOW_DOWNGRADE is false. In that mode a downgraded paper type is only an
intermediate result.
"""
from __future__ import annotations

import os

ENV_TARGET_PAPER_TYPE = "ARGUS_SKILL_PHYSICS_TARGET_PAPER_TYPE"
ENV_ALLOW_DOWNGRADE = "ARGUS_SKILL_PHYSICS_ALLOW_DOWNGRADE"

#: Paper types that count as original research (not a downgrade).
ORIGINAL_PAPER_TYPES: frozenset[str] = frozenset(
    {"original research article", "original", "letter", "communication", "new observation"}
)
#: Downgrade paper types — allowed as intermediate, not as a success terminal in
#: original-research-required mode.
DOWNGRADE_PAPER_TYPES: frozenset[str] = frozenset(
    {"diagnostic benchmark", "benchmark", "reproduction", "training report", "report", "methods note"}
)

_TRUE = {"1", "true", "yes", "y", "on"}
_FALSE = {"0", "false", "no", "n", "off"}


def target_paper_type() -> str:
    raw = (os.environ.get(ENV_TARGET_PAPER_TYPE) or "auto").strip().lower()
    return raw or "auto"


def allow_downgrade_as_success() -> bool:
    raw = (os.environ.get(ENV_ALLOW_DOWNGRADE) or "").strip().lower()
    if raw in _FALSE:
        return False
    if raw in _TRUE:
        return True
    return True  # default: downgrade allowed (pre-V5 behaviour)


def is_original_research_required() -> bool:
    """True when the mission must reach original research."""
    return target_paper_type() in {"original_research_article", "original"} and not allow_downgrade_as_success()


def is_downgrade_type(paper_type: str) -> bool:
    p = (paper_type or "").strip().lower()
    if not p:
        return False
    if any(o in p for o in ORIGINAL_PAPER_TYPES):
        return False
    return any(d in p for d in DOWNGRADE_PAPER_TYPES) or "benchmark" in p or "reproduc" in p


def mode_summary() -> dict:
    return {
        "target_paper_type": target_paper_type(),
        "allow_downgrade_as_success": allow_downgrade_as_success(),
        "original_research_required": is_original_research_required(),
    }


__all__ = [
    "ENV_TARGET_PAPER_TYPE",
    "ENV_ALLOW_DOWNGRADE",
    "ORIGINAL_PAPER_TYPES",
    "DOWNGRADE_PAPER_TYPES",
    "target_paper_type",
    "allow_downgrade_as_success",
    "is_original_research_required",
    "is_downgrade_type",
    "mode_summary",
]
