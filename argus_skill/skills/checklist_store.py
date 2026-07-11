"""Per-project, stage-keyed checklist STORE — the Planner-authored override.

Historically the per-stage checklist was a frozen Python constant
(``stage_checklists.STAGE_CHECKLISTS`` + each vertical's ``CHECKLIST_ITEMS``).
That floor is now a *reference seed*: the Planner AUTHORS the checklist for the
current task, per stage, and this module is where those authored items live —
``<project_root>/research/CHECKLISTS.json``.

Read path: :func:`store_items_for_stage` is consulted by
``stage_checklists.format_stage_checklist`` / ``format_full_pipeline_checklist``
BEFORE the seed constants. It returns:

* a tuple of items when the store has an entry for the stage (the Planner has
  authored that stage) — used as the checklist base;
* ``()`` when the stage key is present but the list is empty (the Planner
  deliberately emptied it — honored);
* ``None`` when the stage is absent from the store — the signal to FALL BACK to
  the seed constant. This ``None`` is what preserves byte-identical rendering for
  research/quant/speedrun when no project checklist exists.

Write path: :func:`apply_checklist_ops` is the ONLY mutator, invoked by the
Planner after its verdict is finalized. The Reviewer never writes here — it only
emits ``checklist_feedback`` for the Planner to act on next cycle.

Fail-open everywhere: a missing/corrupt store reads as empty; a write error
leaves the store untouched and the planning cycle continues. ``ChecklistItem``
and the active-vertical seed lookup are late-imported to avoid the module-load
cycle ``stage_checklists`` ↔ this module.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: ``<project_root>/research/CHECKLISTS.json``.
CHECKLISTS_RELPATH = ("research", "CHECKLISTS.json")

VALID_OPS = ("seed", "add", "modify", "remove")

#: Bounds — keep a runaway Planner from ballooning the prompt.
MAX_ITEMS_PER_STAGE = 40
MAX_STATEMENT_LEN = 1600
MAX_EVIDENCE_LEN = 1600


def _store_path(project_root: object) -> Path:
    return Path(str(project_root)).joinpath(*CHECKLISTS_RELPATH)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _load_raw(project_root: object) -> dict[str, Any]:
    """Return ``{"revision": int, "stages": {stage: [item-dict, ...]}}`` fail-open."""
    empty = {"revision": 0, "stages": {}}
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
    return {"revision": int(rev) if isinstance(rev, (int, float)) else 0, "stages": stages}


def _coerce_item(raw: object) -> Any | None:
    """Coerce one stored row into a ``ChecklistItem`` (drop malformed)."""
    if not isinstance(raw, dict):
        return None
    item_id = str(raw.get("id") or "").strip()
    statement = str(raw.get("statement") or "").strip()
    if not item_id or not statement:
        return None
    from .stage_checklists import ChecklistItem  # late import (cycle)

    return ChecklistItem(
        id=item_id,
        statement=statement[:MAX_STATEMENT_LEN],
        evidence_hint=str(raw.get("evidence_hint") or "").strip()[:MAX_EVIDENCE_LEN],
    )


def store_items_for_stage(project_root: object, stage: str) -> "tuple[Any, ...] | None":
    """Return the Planner-authored items for ``stage``, or ``None`` if absent.

    ``None`` ⇒ stage not in the store ⇒ caller falls back to the seed constant.
    ``()`` ⇒ stage present but empty (Planner emptied it) ⇒ honored as empty.
    """
    stage_n = (stage or "").strip().lower()
    if not stage_n:
        return None
    stages = _load_raw(project_root)["stages"]
    if stage_n not in stages:
        return None
    rows = stages.get(stage_n)
    if isinstance(rows, list):
        items = [it for it in (_coerce_item(r) for r in rows) if it is not None]
    else:
        items = []
    # Re-inject the protected anti-fraud floor so no write path (planner add-over,
    # or a direct edit of CHECKLISTS.json) can drop/weaken it from the rendered
    # checklist the reviewer certifies against.
    return tuple(_with_protected_floor(project_root, stage_n, items))


def load_checklist_store(project_root: object) -> dict[str, list[Any]]:
    """Return ``{stage: [ChecklistItem, ...]}`` for every stage present (fail-open)."""
    stages = _load_raw(project_root)["stages"]
    out: dict[str, list[Any]] = {}
    for stage, rows in stages.items():
        if not isinstance(rows, list):
            continue
        out[str(stage).strip().lower()] = [
            it for it in (_coerce_item(r) for r in rows) if it is not None
        ]
    return out


def seed_items_for(project_root: object, stage: str) -> "tuple[Any, ...]":
    """Resolve the ACTIVE vertical's seed (reference) items for ``stage``.

    This is the reference the Planner edits FROM (a ``seed`` op copies these into
    the store). Late-imports the single stage-defs chokepoint so data domains and
    Python verticals resolve identically. Fail-open to ``()``.
    """
    stage_n = (stage or "").strip().lower()
    if not stage_n:
        return ()
    try:
        from .stage_checklists import _active_vertical_checklist_defs

        _order, items = _active_vertical_checklist_defs(project_root)
        return tuple(items.get(stage_n, ()))
    except Exception:  # noqa: BLE001 — seed lookup must never break planning
        return ()


def _paper_gate_protected_ids(project_root: object) -> frozenset[str]:
    """Protected floor ids the Planner may NOT remove/modify on a paper vertical.

    For a data domain (or any non-paper gate) there is no protected floor — the
    Planner has full authority. Fail-open to an empty set so a resolution hiccup
    never blocks a legitimate edit.
    """
    try:
        from ..verticals._base import load_vertical, vertical_completion_gate
        from .harness_overlay import PROTECTED_ITEM_IDS
        from .vertical_select import resolve_vertical

        gate = vertical_completion_gate(
            load_vertical(resolve_vertical(project_root), project_root=project_root)
        )
        return PROTECTED_ITEM_IDS if gate == "full_paper" else frozenset()
    except Exception:  # noqa: BLE001
        return frozenset()


def _with_protected_floor(project_root: object, stage: str, items: list[Any]) -> list[Any]:
    """Re-validate the protected anti-fraud floor for ``stage`` on READ.

    On a paper vertical, force each :data:`PROTECTED_ITEM_IDS` seed item for the
    stage to its canonical seed text (replacing any weakened override copy in
    place) and append any protected floor item the override dropped. The write
    guard in :func:`apply_checklist_ops` only covers the Planner-ops path; this
    read-side re-injection (mirroring ``harness_overlay``'s re-validate-on-read) is
    what makes the floor un-removable against ANY writer — including a direct edit
    of ``research/CHECKLISTS.json`` by the unsandboxed engineer subprocess. No-op
    for a non-paper gate or if seed resolution fails (fail-open).
    """
    try:
        protected = _paper_gate_protected_ids(project_root)
        if not protected:
            return items
        seed_by_id = {
            s.id: s for s in seed_items_for(project_root, stage) if s.id in protected
        }
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


def _row(item_id: str, statement: str, evidence_hint: str) -> dict[str, str]:
    return {
        "id": item_id,
        "statement": statement[:MAX_STATEMENT_LEN],
        "evidence_hint": evidence_hint[:MAX_EVIDENCE_LEN],
    }


def apply_checklist_ops(
    project_root: object,
    ops: list[dict[str, Any]] | None,
    *,
    seed_lookup: Any | None = None,
) -> dict[str, Any]:
    """Apply Planner ``checklist_ops`` to the store (the ONLY write path).

    ``ops`` items: ``{op, stage, id, statement?, evidence_hint?}`` with
    ``op ∈ {seed, add, modify, remove}``:

    * ``seed`` — if the stage has no project entry yet, copy the seed (reference)
      items for that stage into the store as the editable base. No-op if present.
    * ``add`` — append (or replace same-id) a new item; needs ``statement``.
    * ``modify`` — update an existing item's statement/evidence by ``id``.
    * ``remove`` — drop an item by ``id``.

    ``add``/``modify``/``remove`` on a paper-vertical PROTECTED floor id are
    refused (counted as ``skipped``) — the floor is the Planner's read-only base.
    Atomic write, ``revision`` bumped. Fail-soft: any
    error leaves the store untouched. Returns ``{applied, skipped, revision}``.
    """
    if not ops:
        return {"applied": 0, "skipped": 0, "revision": _load_raw(project_root)["revision"]}

    seed_fn = seed_lookup or (lambda stage: seed_items_for(project_root, stage))
    protected = _paper_gate_protected_ids(project_root)

    try:
        raw = _load_raw(project_root)
        stages: dict[str, Any] = dict(raw["stages"])
        # Normalize existing stage lists to plain lists we can mutate.
        for k, v in list(stages.items()):
            stages[k] = list(v) if isinstance(v, list) else []

        applied = 0
        skipped = 0
        for op_raw in ops:
            if not isinstance(op_raw, dict):
                skipped += 1
                continue
            op = str(op_raw.get("op") or "").strip().lower()
            stage = str(op_raw.get("stage") or "").strip().lower()
            item_id = str(op_raw.get("id") or "").strip()
            if op not in VALID_OPS or not stage:
                skipped += 1
                continue

            if op == "seed":
                if stage not in stages:
                    seed_items = seed_fn(stage) or ()
                    stages[stage] = [
                        _row(it.id, it.statement, getattr(it, "evidence_hint", ""))
                        for it in seed_items
                    ][:MAX_ITEMS_PER_STAGE]
                    applied += 1
                else:
                    skipped += 1
                continue

            if not item_id:
                skipped += 1
                continue
            if op in {"add", "modify", "remove"} and item_id in protected:
                skipped += 1
                continue

            bucket = stages.setdefault(stage, [])

            if op == "add":
                statement = str(op_raw.get("statement") or "").strip()
                if not statement:
                    skipped += 1
                    continue
                evidence = str(op_raw.get("evidence_hint") or "").strip()
                bucket = [r for r in bucket if not (isinstance(r, dict) and r.get("id") == item_id)]
                if len(bucket) >= MAX_ITEMS_PER_STAGE:
                    skipped += 1
                    stages[stage] = bucket
                    continue
                bucket.append(_row(item_id, statement, evidence))
                stages[stage] = bucket
                applied += 1
            elif op == "modify":
                found = False
                for r in bucket:
                    if isinstance(r, dict) and r.get("id") == item_id:
                        if str(op_raw.get("statement") or "").strip():
                            r["statement"] = str(op_raw["statement"]).strip()[:MAX_STATEMENT_LEN]
                        if "evidence_hint" in op_raw:
                            r["evidence_hint"] = str(op_raw.get("evidence_hint") or "").strip()[:MAX_EVIDENCE_LEN]
                        found = True
                        break
                applied += 1 if found else 0
                skipped += 0 if found else 1
            elif op == "remove":
                before = len(bucket)
                stages[stage] = [
                    r for r in bucket if not (isinstance(r, dict) and r.get("id") == item_id)
                ]
                if len(stages[stage]) != before:
                    applied += 1
                else:
                    skipped += 1

        revision = int(raw["revision"]) + 1
        _atomic_write_json(_store_path(project_root), {"revision": revision, "stages": stages})
        return {"applied": applied, "skipped": skipped, "revision": revision}
    except Exception:  # noqa: BLE001 — write must never break planning
        log.warning("apply_checklist_ops failed; store left untouched", exc_info=True)
        return {"applied": 0, "skipped": len(ops), "revision": _load_raw(project_root)["revision"]}


__all__ = [
    "CHECKLISTS_RELPATH",
    "VALID_OPS",
    "store_items_for_stage",
    "load_checklist_store",
    "seed_items_for",
    "apply_checklist_ops",
]
