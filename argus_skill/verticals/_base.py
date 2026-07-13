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
* ``completion_gate: str`` — ``"full_paper"`` (research) | ``"metric"``
  (speedrun) | ``"none"`` (default ``"full_paper"``).

A vertical that does not declare a hook gets the safe default, so the
``research`` vertical (which re-exports its checklist defs) stays byte-identical
to today and a partially-specified new vertical never crashes prompt building.

``load_vertical(name)`` is the single resolver: it imports
``argus_skill.verticals.<name>.stages``, strips a trailing ``-needed`` sentinel
(main wrote a ``"speedrun-needed"`` placeholder before the writer existed). A
genuinely-missing / typo'd / half-built name falls back to the ``research``
vertical (resolution must never break the loop on a bad name); but a REAL named
vertical whose ``stages.py`` exists yet fails to import raises LOUDLY rather than
silently degrading a metric mission into the paper pipeline.
"""
from __future__ import annotations

import importlib
import logging
import os
from types import ModuleType
from typing import TypeAlias

from ._data_domain import DataDomain, load_data_domain

log = logging.getLogger(__name__)

#: The safe fallback vertical: its stages module always imports.
DEFAULT_VERTICAL = "research"
VerticalDefinition: TypeAlias = ModuleType | DataDomain


def _normalize_vertical_name(name: object) -> str:
    """Lower/strip a vertical name and drop a trailing ``-needed`` sentinel."""
    if not isinstance(name, str):
        return DEFAULT_VERTICAL
    cleaned = name.strip().lower()
    if cleaned.endswith("-needed"):
        cleaned = cleaned[: -len("-needed")]
    return cleaned or DEFAULT_VERTICAL


def load_vertical(name: object, project_root: object = None) -> VerticalDefinition:
    """Return the ``stages`` module (or a ``DataDomain`` shim) for vertical ``name``.

    Resolution order:

    1. A real on-disk Python package ``argus_skill.verticals.<name>.stages`` —
       imported via importlib (after normalizing the name and stripping a trailing
       ``-needed`` sentinel). A REAL named vertical whose module exists but fails
       to import raises LOUDLY (never silently degrades a metric mission into the
       paper pipeline).
    2. When ``project_root`` is given and ``name`` is NOT a Python package, a
       project-local DATA domain (``research/DOMAINS/<name>.json``) — returned as
       a duck-typed :class:`~argus_skill.verticals._data_domain.DataDomain` shim
       the optional-hook accessors below consume unchanged.
    3. The ``research`` vertical's stages module (the safe fallback) — so a typo,
       a stale ``-needed`` placeholder, or a half-built vertical degrades to the
       paper pipeline instead of crashing the loop.

    ``project_root=None`` preserves today's behavior exactly (no data-domain
    branch), so every existing call site stays byte-identical until it opts in.
    Package import is tried FIRST so that after a data domain is PROMOTED to a
    real ``verticals/<name>/`` package, resolution converges on the package and
    the data domain becomes inert.
    """
    cleaned = _normalize_vertical_name(name)
    try:
        return importlib.import_module(f"argus_skill.verticals.{cleaned}.stages")
    except Exception as exc:  # noqa: BLE001
        # Distinguish a genuinely-missing / typo'd / half-built vertical (safe
        # fallback) from a REAL named vertical whose stages module errored. The
        # latter must NOT be hidden: silently degrading e.g. nanochat → research
        # turns a metric optimizer into the paper pipeline with only a log line.
        stages_path = os.path.join(os.path.dirname(__file__), cleaned, "stages.py")
        if cleaned != DEFAULT_VERTICAL and os.path.isfile(stages_path):
            raise RuntimeError(
                f"load_vertical({name!r}): the vertical exists ({stages_path}) but "
                f"importing its stages module failed — refusing to silently fall "
                f"back to {DEFAULT_VERTICAL!r} (that would turn this mission into the "
                f"paper pipeline). Fix the vertical."
            ) from exc
        # No Python package: try a project-local DATA domain before falling back.
        if project_root is not None and cleaned != DEFAULT_VERTICAL:
            try:
                domain = load_data_domain(cleaned, project_root)
                if domain is not None:
                    return domain
            except Exception:  # noqa: BLE001 — data-domain load must never break
                log.debug("load_vertical(%r): data-domain probe failed", name, exc_info=True)
        if cleaned != DEFAULT_VERTICAL:
            log.warning(
                "load_vertical(%r): unknown/half-built vertical (%s), falling back to %r",
                name,
                type(exc).__name__,
                DEFAULT_VERTICAL,
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


def vertical_checklist_stage_order(mod: VerticalDefinition) -> tuple[str, ...]:
    """Return ``mod.CHECKLIST_STAGE_ORDER`` or research's canonical order."""
    order = getattr(mod, "CHECKLIST_STAGE_ORDER", None)
    if order:
        return tuple(order)
    return _research_defaults()[0]


def vertical_checklist_items(mod: VerticalDefinition) -> dict:
    """Return ``mod.CHECKLIST_ITEMS`` or research's ``STAGE_CHECKLISTS``."""
    items = getattr(mod, "CHECKLIST_ITEMS", None)
    if isinstance(items, dict):
        return items
    return _research_defaults()[1]


def vertical_role_banner(mod: VerticalDefinition, role: str) -> str:
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


def vertical_completion_gate(mod: VerticalDefinition) -> str:
    """Return ``mod.completion_gate`` or the default ``"full_paper"``."""
    gate = getattr(mod, "completion_gate", None)
    if isinstance(gate, str) and gate.strip():
        return gate.strip().lower()
    return "full_paper"


def vertical_research_target_levels(mod: VerticalDefinition) -> tuple[str, ...]:
    """Return the research target levels supported by this vertical."""
    raw = getattr(mod, "RESEARCH_TARGET_LEVELS", ())
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(
        level
        for value in raw
        if (level := str(value or "").strip().lower())
    )


def vertical_workflow_mode(mod: VerticalDefinition) -> str:
    """Return the vertical's supported workflow mode."""
    mode = str(getattr(mod, "WORKFLOW_MODE", "") or "").strip().lower()
    return mode if mode in {"direct", "proportional"} else "staged"


def vertical_search_altitude(mod: VerticalDefinition, project_root: object) -> str:
    """Return ``mod.search_altitude_context(project_root)`` or ``""``.

    Optional hook: a vertical may surface a NO-VERDICT 'where is the search
    now' fact block (live floor / distance-to-target / consecutive
    non-improving attempts / recombined levers) so the planner & reviewer can
    judge saturation instead of re-deriving it each cycle. Fail-open: a vertical
    with no hook (or one that raises) yields no block, so prompt building never
    breaks on a missing/buggy hook — same posture as ``vertical_role_banner``.
    """
    fn = getattr(mod, "search_altitude_context", None)
    if not callable(fn):
        return ""
    try:
        result = fn(project_root)
    except Exception:  # noqa: BLE001 — visibility hook must never break prompts
        return ""
    return result if isinstance(result, str) else ""


def vertical_search_altitude_facts(
    mod: VerticalDefinition, project_root: object
) -> dict:
    """Return ``mod.search_altitude_facts(project_root)`` or ``{}``.

    Structured twin of :func:`vertical_search_altitude` for the meta-control
    layer: a vertical may expose the live floor / since-improve / per-attempt
    records as DATA (not rendered prose) so the cross-vertical meta layer detects
    saturation without re-implementing the vertical's metric parsing (keeps the
    harness metric-blind). Fail-open: missing/buggy hook → ``{}``.
    """
    fn = getattr(mod, "search_altitude_facts", None)
    if not callable(fn):
        return {}
    try:
        result = fn(project_root)
    except Exception:  # noqa: BLE001 — detection hook must never break prompts
        return {}
    return result if isinstance(result, dict) else {}


def vertical_strategy_pool(mod: VerticalDefinition, project_root: object) -> str:
    """Return ``mod.strategy_pool(project_root)`` or ``""``.

    Optional hook: the regime strategy pool a vertical offers when the meta layer
    convenes a JUMP (the axes menu + coverage + diverse inspirations). Fail-open:
    missing/buggy hook → no pool, so jump framing degrades gracefully.
    """
    fn = getattr(mod, "strategy_pool", None)
    if not callable(fn):
        return ""
    try:
        result = fn(project_root)
    except Exception:  # noqa: BLE001 — strategy hook must never break prompts
        return ""
    return result if isinstance(result, str) else ""


__all__ = [
    "DEFAULT_VERTICAL",
    "VerticalDefinition",
    "load_vertical",
    "vertical_checklist_stage_order",
    "vertical_checklist_items",
    "vertical_role_banner",
    "vertical_completion_gate",
    "vertical_research_target_levels",
    "vertical_workflow_mode",
    "vertical_search_altitude",
    "vertical_search_altitude_facts",
    "vertical_strategy_pool",
]
