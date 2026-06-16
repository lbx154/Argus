"""Shared vertical contract — loader + optional-hook accessors.

Main's vertical packages (``argus_skill.verticals.<name>.stages``) ship a
3-name contract: ``STAGE_ORDER``, ``STAGE_CHECKS``, ``REVIEWER_CHECKLISTS``
(consumed by the System-(A) shell-check runner in
``argus_skill.tools.stage_check``). This module extends that contract with the
OPTIONAL hooks System (B) — the markdown stage checklists in
``argus_skill.skills.stage_checklists`` — needs to become vertical-aware:

* ``CHECKLIST_STAGE_ORDER: tuple[str, ...]`` — the stage order System (B)
  iterates (default: research's ``CANONICAL_STAGE_ORDER``).
* ``CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]]`` — the per-stage
  markdown checklist items (default: research's ``STAGE_CHECKLISTS``).
* ``role_banner(role: str) -> str`` — top-of-prompt framing for
  planner/reviewer/engineer (default ``""``).
* ``completion_gate: str`` — ``"full_emnlp"`` (research) | ``"metric"``
  (speedrun) | ``"none"`` (default ``"full_emnlp"``).

A vertical that does not declare a hook gets the safe default, so the
``research`` vertical (which re-exports its checklist defs) stays byte-identical
to today and a partially-specified new vertical never crashes prompt building.

``load_vertical(name)`` is the single resolver: it imports
``argus_skill.verticals.<name>.stages``, strips a trailing ``-needed`` sentinel
(main wrote a ``"speedrun-needed"`` placeholder before the writer existed), and
on ANY import failure falls back to the ``research`` vertical rather than
raising — vertical resolution must never break the running loop.
"""
from __future__ import annotations

import importlib
import logging
from types import ModuleType

log = logging.getLogger(__name__)

#: The safe fallback vertical: its stages module always imports.
DEFAULT_VERTICAL = "research"


def _normalize_vertical_name(name: object) -> str:
    """Lower/strip a vertical name and drop a trailing ``-needed`` sentinel."""
    if not isinstance(name, str):
        return DEFAULT_VERTICAL
    cleaned = name.strip().lower()
    if cleaned.endswith("-needed"):
        cleaned = cleaned[: -len("-needed")]
    return cleaned or DEFAULT_VERTICAL


def load_vertical(name: object) -> ModuleType:
    """Return the ``stages`` module for vertical ``name``.

    Imports ``argus_skill.verticals.<name>.stages`` (after normalizing the name
    and stripping a trailing ``-needed`` sentinel). On any ``ImportError`` (or
    other import-time failure) it falls back to the ``research`` vertical's
    stages module — never raises — so a typo, a stale ``-needed`` placeholder,
    or a half-built vertical degrades to the safe paper pipeline instead of
    crashing the planner/reviewer loop.
    """
    cleaned = _normalize_vertical_name(name)
    try:
        return importlib.import_module(f"argus_skill.verticals.{cleaned}.stages")
    except Exception:  # noqa: BLE001 — fail-open: any import failure → research
        if cleaned != DEFAULT_VERTICAL:
            log.warning(
                "load_vertical(%r): import failed, falling back to %r",
                name,
                DEFAULT_VERTICAL,
                exc_info=True,
            )
        return importlib.import_module(
            f"argus_skill.verticals.{DEFAULT_VERTICAL}.stages"
        )


# --- optional-hook accessors (safe defaults) -------------------------------


def _research_defaults() -> tuple[tuple[str, ...], dict]:
    """Return research's ``(CANONICAL_STAGE_ORDER, STAGE_CHECKLISTS)`` defaults.

    Late import to avoid a module-load cycle with ``stage_checklists`` (which
    late-imports this module).
    """
    from ..skills.stage_checklists import CANONICAL_STAGE_ORDER, STAGE_CHECKLISTS

    return CANONICAL_STAGE_ORDER, STAGE_CHECKLISTS


def vertical_checklist_stage_order(mod: ModuleType) -> tuple[str, ...]:
    """Return ``mod.CHECKLIST_STAGE_ORDER`` or research's canonical order."""
    order = getattr(mod, "CHECKLIST_STAGE_ORDER", None)
    if order:
        return tuple(order)
    return _research_defaults()[0]


def vertical_checklist_items(mod: ModuleType) -> dict:
    """Return ``mod.CHECKLIST_ITEMS`` or research's ``STAGE_CHECKLISTS``."""
    items = getattr(mod, "CHECKLIST_ITEMS", None)
    if isinstance(items, dict):
        return items
    return _research_defaults()[1]


def vertical_role_banner(mod: ModuleType, role: str) -> str:
    """Return ``mod.role_banner(role)`` or ``""``.

    Fail-open: a vertical with no ``role_banner`` (or one that raises) yields no
    banner, so prompt building never breaks on a missing/buggy hook.
    """
    fn = getattr(mod, "role_banner", None)
    if not callable(fn):
        return ""
    try:
        result = fn(role)
    except Exception:  # noqa: BLE001 — banner must never break prompt building
        return ""
    return result if isinstance(result, str) else ""


def vertical_completion_gate(mod: ModuleType) -> str:
    """Return ``mod.completion_gate`` or the default ``"full_emnlp"``."""
    gate = getattr(mod, "completion_gate", None)
    if isinstance(gate, str) and gate.strip():
        return gate.strip().lower()
    return "full_emnlp"


__all__ = [
    "DEFAULT_VERTICAL",
    "load_vertical",
    "vertical_checklist_stage_order",
    "vertical_checklist_items",
    "vertical_role_banner",
    "vertical_completion_gate",
]
