"""Read legacy per-project checklist overrides.

Current Planner verdicts no longer carry or apply ``checklist_ops``. This module
keeps the read path for projects that already have
``<project_root>/research/CHECKLISTS.json`` and re-injects each vertical's
protected floor against direct or historical edits.

The file is bound to one explicit ``vertical``. A store authored for research
is ignored after the Manager changes the project to math (and vice versa); stage
names or gates never leak across verticals.

Read path: :func:`store_items_for_stage` is consulted by
``stage_machine.format_stage_checklist`` / ``format_full_pipeline_checklist``
BEFORE the seed constants. It returns:

* a tuple of items when the store vertical matches the committed project
  vertical and has an entry for the stage — used as the checklist base;
* ``()`` when the historical stage key is present but its effective list is
  empty;
* ``None`` when the stage is absent from the store — the signal to FALL BACK to
  the seed constant. This ``None`` is what preserves byte-identical rendering for
  research/quant/speedrun when no project checklist exists.

A missing/corrupt store reads as empty. ``ChecklistItem`` and the
active-vertical seed lookup are late-imported to avoid the module-load cycle
``stage_machine`` ↔ this module.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

_SHARED_PROTECTED_ITEM_IDS = frozenset(
    {
        "benchmark.evaluator_authentic",
        "run.score_variance",
        "run.method_diagnosis_recall",
        "analysis.claims",
        "review.placeholders",
        "submission.assurance",
        "submission.anonymous",
        "submission.upstream",
    }
)

log = logging.getLogger(__name__)

#: ``<project_root>/research/CHECKLISTS.json``.
CHECKLISTS_RELPATH = ("research", "CHECKLISTS.json")

#: Bounds keep historical rows from ballooning the prompt.
MAX_STATEMENT_LEN = 1600
MAX_EVIDENCE_LEN = 1600


def _store_path(project_root: object) -> Path:
    return Path(str(project_root)).joinpath(*CHECKLISTS_RELPATH)


def _current_vertical(project_root: object) -> str | None:
    try:
        from .vertical_select import resolve_checklist_vertical

        return resolve_checklist_vertical(project_root)
    except Exception:  # noqa: BLE001
        return None


def _load_raw(project_root: object) -> dict[str, Any]:
    """Return the vertical-bound checklist store, fail-open."""
    empty: dict[str, Any] = {
        "revision": 0,
        "vertical": "",
        "stages": {},
        "disabled": {},
    }
    try:
        payload = json.loads(_store_path(project_root).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty
    except Exception:  # noqa: BLE001 — corrupt/unreadable → empty (logged)
        log.debug("checklist store unreadable/corrupt; ignoring", exc_info=True)
        return empty
    if not isinstance(payload, dict):
        return empty
    stages = payload.get("stages")
    if not isinstance(stages, dict):
        stages = {}
    rev = payload.get("revision", 0)
    # Parse optional 'disabled' field: {stage: [item_id, ...]}
    disabled_raw = payload.get("disabled")
    disabled: dict[str, list[str]] = {}
    if isinstance(disabled_raw, dict):
        for k, v in disabled_raw.items():
            key = str(k).strip().lower()
            if isinstance(v, list):
                disabled[key] = [s for s in (str(i).strip() for i in v if isinstance(i, str)) if s]
    return {
        "revision": int(rev) if isinstance(rev, (int, float)) else 0,
        "vertical": str(payload.get("vertical") or "").strip(),
        "stages": stages,
        "disabled": disabled,
    }


def _coerce_item(raw: object) -> Any | None:
    """Coerce one stored row into a ``ChecklistItem`` (drop malformed)."""
    if not isinstance(raw, dict):
        return None
    item_id = str(raw.get("id") or "").strip()
    statement = str(raw.get("statement") or "").strip()
    if not item_id or not statement:
        return None
    from .stage_machine import ChecklistItem  # late import (cycle)

    return ChecklistItem(
        id=item_id,
        statement=statement[:MAX_STATEMENT_LEN],
        evidence_hint=str(raw.get("evidence_hint") or "").strip()[:MAX_EVIDENCE_LEN],
    )


def store_items_for_stage(project_root: object, stage: str) -> "tuple[Any, ...] | None":
    """Return the effective checklist items for ``stage``, or ``None`` if absent.

    **Historical seed-plus-override semantics**:

    * Effective = active-vertical seed keyed by ID, overlaid with project rows.
      A project row whose ``id`` matches a seed ID overrides that seed item in
      place; rows with custom IDs are appended after the seeds.
    * Tombstoned seed IDs (stored under the top-level ``disabled`` key) are
      hidden from the effective list.
    * ``None`` ⇒ stage not in the store AND no tombstones ⇒ caller falls back to
      the seed constant unchanged (byte-identical to pre-Task-2 for untouched
      stages).
    * ``()`` ⇒ stage is managed by the project (either in ``stages`` or has
      tombstones) but the effective list is empty (all seeds tombstoned, no
      custom items) ⇒ honored as empty by ``resolve_stage_checklist_contract``.
    * Non-empty tuple ⇒ effective items in seed order (with overrides and custom
      items appended).
    """
    stage_n = (stage or "").strip().lower()
    if not stage_n:
        return None
    current = _current_vertical(project_root)
    if current == "research":
        # Four-stage research keeps its live contract in framework source and
        # direct evidence. Preserve legacy CHECKLISTS.json on disk, but never
        # load its obsolete eight-stage overrides into current research.
        return None
    raw = _load_raw(project_root)
    if current is None or raw["vertical"] != current:
        return None
    stages = raw["stages"]
    disabled_map = raw["disabled"]
    stage_in_store = stage_n in stages
    tombstoned: set[str] = set(disabled_map.get(stage_n, []))
    if not stage_in_store and not tombstoned:
        # No project management of this stage → caller falls back to seed constant.
        return None

    # Resolve seed items for this stage from the active vertical.
    seed_items = seed_items_for(project_root, stage_n)  # tuple[ChecklistItem, ...]
    seed_ids: set[str] = {it.id for it in seed_items}

    # Split project rows into overrides (for seed IDs) and custom (new IDs).
    project_rows = stages.get(stage_n, []) if stage_in_store else []
    override_map: dict[str, Any] = {}  # seed_id → ChecklistItem
    custom_items: list[Any] = []
    for r in project_rows:
        item = _coerce_item(r)
        if item is None:
            continue
        if item.id in seed_ids:
            override_map[item.id] = item
        else:
            custom_items.append(item)

    # Build effective list: seeds (minus tombstoned, with overrides applied) + custom items.
    effective: list[Any] = []
    for seed_item in seed_items:
        if seed_item.id in tombstoned:
            continue  # hidden by tombstone
        effective.append(override_map.get(seed_item.id, seed_item))
    effective.extend(custom_items)

    # Re-validate protected floor on read (guards against direct store edits).
    return tuple(_with_protected_floor(project_root, stage_n, effective))


def seed_items_for(project_root: object, stage: str) -> "tuple[Any, ...]":
    """Resolve the ACTIVE vertical's seed (reference) items for ``stage``.

    Late-imports the single stage-defs chokepoint so data domains and Python
    verticals resolve identically. Fail-open to ``()``.
    """
    stage_n = (stage or "").strip().lower()
    if not stage_n:
        return ()
    try:
        from .stage_machine import _active_vertical_checklist_defs

        _order, items = _active_vertical_checklist_defs(project_root)
        return tuple(items.get(stage_n, ()))
    except Exception:  # noqa: BLE001 — seed lookup must never break planning
        return ()


def _protected_floor_ids(project_root: object) -> frozenset[str] | None:
    """Protected seed ids restored against historical or direct edits.

    Verticals may declare ``PROTECTED_ITEM_IDS`` for irreducible Goal/Integrity
    gates. Paper verticals additionally inherit the shared anti-fraud floor.
    ``None`` means protection could not be resolved.
    """
    try:
        from ..verticals._base import load_vertical, vertical_completion_gate
        from .vertical_select import resolve_vertical

        module = load_vertical(
            resolve_vertical(project_root),
            project_root=project_root,
        )
        vertical_ids = frozenset(
            str(item_id).strip()
            for item_id in getattr(module, "PROTECTED_ITEM_IDS", ())
            if str(item_id).strip()
        )
        if vertical_completion_gate(module) == "certified":
            return vertical_ids | _SHARED_PROTECTED_ITEM_IDS
        return vertical_ids
    except Exception:  # noqa: BLE001 — unknown protection fails open on read
        return None


def _with_protected_floor(project_root: object, stage: str, items: list[Any]) -> list[Any]:
    """Re-validate the protected anti-fraud floor for ``stage`` on READ.

    Force each protected seed item for the stage to its canonical seed text
    (replacing any weakened override copy in place) and append any protected
    floor item the override dropped. This read-side re-injection makes the floor
    un-removable against any writer,
    including a direct or historical edit
    of ``research/CHECKLISTS.json`` by the unsandboxed engineer subprocess. No-op
    when the vertical declares no protected ids or seed resolution fails.
    """
    try:
        protected = _protected_floor_ids(project_root)
        if protected is None or not protected:
            return items
        seed_by_id = {s.id: s for s in seed_items_for(project_root, stage) if s.id in protected}
        if not seed_by_id:
            return items
        out: list[Any] = []
        seen: set[str] = set()
        for it in items:
            iid = getattr(it, "id", None)
            if iid in seed_by_id:
                out.append(seed_by_id[iid])  # canonical floor text, in place
                seen.add(iid)
            else:
                out.append(it)
        for iid, seed_item in seed_by_id.items():
            if iid not in seen:
                out.append(seed_item)  # protected floor item the override dropped
        return out
    except Exception:  # noqa: BLE001 — re-injection must never break prompt building
        return items


__all__ = ["CHECKLISTS_RELPATH", "store_items_for_stage", "seed_items_for"]
