"""Per-project, stage-keyed checklist STORE — the Planner-authored override.

Historically the per-stage checklist was a frozen Python constant in the
research vertical plus each vertical's ``CHECKLIST_ITEMS``.
That floor is now a *reference seed*: the Planner is the sole runtime author of
the current task's checklist, per stage, and this module stores it —
``<project_root>/research/CHECKLISTS.json``.

The file is bound to one explicit ``vertical``. A store authored for research
is ignored after the Manager changes the project to math (and vice versa); stage
names or gates never leak across verticals.

Read path: :func:`store_items_for_stage` is consulted by
``stage_machine.format_stage_checklist`` / ``format_full_pipeline_checklist``
BEFORE the seed constants. It returns:

* a tuple of items when the store vertical matches the committed project
  vertical and has an entry for the stage (the Planner has
  authored that stage) — used as the checklist base;
* ``()`` when the stage key is present but the list is empty (the Planner
  deliberately emptied it — honored);
* ``None`` when the stage is absent from the store — the signal to FALL BACK to
  the seed constant. This ``None`` is what preserves byte-identical rendering for
  research/quant/speedrun when no project checklist exists.

Write path: :func:`apply_checklist_ops` is the ONLY mutator, invoked by the
Planner after its verdict is finalized. The Reviewer never writes here — it only
reports checklist problems in its ordinary verdict reason.

Fail-open everywhere: a missing/corrupt store reads as empty; a write error
leaves the store untouched and the planning cycle continues. ``ChecklistItem``
and the active-vertical seed lookup are late-imported to avoid the module-load
cycle ``stage_machine`` ↔ this module.
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
                disabled[key] = [
                    s for s in (str(i).strip() for i in v if isinstance(i, str))
                    if s
                ]
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

    **Seed-plus-override semantics** (Task 2):

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


def load_checklist_store(project_root: object) -> dict[str, list[Any]]:
    """Return ``{stage: [ChecklistItem, ...]}`` for every stage present (fail-open)."""
    current = _current_vertical(project_root)
    raw = _load_raw(project_root)
    if current is None or raw["vertical"] != current:
        return {}
    stages = raw["stages"]
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
        from .stage_machine import _active_vertical_checklist_defs

        _order, items = _active_vertical_checklist_defs(project_root)
        return tuple(items.get(stage_n, ()))
    except Exception:  # noqa: BLE001 — seed lookup must never break planning
        return ()


def _protected_floor_ids(project_root: object) -> frozenset[str] | None:
    """Protected seed ids the Planner may not remove or modify.

    Verticals may declare ``PROTECTED_ITEM_IDS`` for irreducible Goal/Integrity
    gates. Paper verticals additionally inherit the shared anti-fraud floor.
    ``None`` means protection could not be resolved; writes then fail closed.
    """
    try:
        from ..verticals._base import load_vertical, vertical_completion_gate
        from .harness_overlay import PROTECTED_ITEM_IDS
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
        if vertical_completion_gate(module) == "full_paper":
            return vertical_ids | PROTECTED_ITEM_IDS
        return vertical_ids
    except Exception:  # noqa: BLE001 — unknown protection refuses writes
        return None


def _with_protected_floor(project_root: object, stage: str, items: list[Any]) -> list[Any]:
    """Re-validate the protected anti-fraud floor for ``stage`` on READ.

    Force each protected seed item for the stage to its canonical seed text
    (replacing any weakened override copy in place) and append any protected
    floor item the override dropped. The write
    guard in :func:`apply_checklist_ops` only covers the Planner-ops path; this
    read-side re-injection (mirroring ``harness_overlay``'s re-validate-on-read) is
    what makes the floor un-removable against ANY writer — including a direct edit
    of ``research/CHECKLISTS.json`` by the unsandboxed engineer subprocess. No-op
    when the vertical declares no protected ids or seed resolution fails.
    """
    try:
        protected = _protected_floor_ids(project_root)
        if protected is None or not protected:
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

    * ``seed`` — if the stage has no project entry yet, initialize an empty
      project entry so subsequent ``add``/``modify``/``remove`` ops have a bucket
      to work with.  Seeds are always merged in at read time; ``seed`` does NOT
      copy them into the store (no duplication).  No-op if the stage is already
      present.
    * ``add`` — append (or replace same-id) a new item; needs ``statement``.
      Refused when the authored-row count is already at ``MAX_ITEMS_PER_STAGE``
      (replacement of an existing same-id row is still permitted because the
      old row is stripped before the cap check).  Clears any tombstone for the
      same ID only when the operation succeeds.
    * ``modify`` — update an existing item's statement/evidence by ``id``.
      For seed IDs not yet overridden and below the cap, creates an override
      entry.  Custom IDs that are absent from the store are skipped (modify
      cannot create arbitrary new rows).  Clears any tombstone for the same
      ID only when the operation succeeds.
    * ``remove`` on a **custom ID** — removes its row only.  ``remove`` on a
      **seed ID** — records a tombstone under ``disabled[stage]`` and hides that
      seed from the effective list; does not touch other seeds.

    ``add``/``modify``/``remove`` on a vertical PROTECTED floor id are
    refused (counted as ``skipped``) — the floor is the Planner's read-only base.
    Atomic write, ``revision`` bumped. Fail-soft: any
    error leaves the store untouched. Returns ``{applied, skipped, revision}``.
    """
    current_vertical = _current_vertical(project_root)
    raw_now = _load_raw(project_root)
    if current_vertical is None:
        return {
            "applied": 0,
            "skipped": len(ops or []),
            "revision": raw_now["revision"],
        }
    if not ops:
        return {"applied": 0, "skipped": 0, "revision": raw_now["revision"]}

    seed_fn = seed_lookup or (lambda stage: seed_items_for(project_root, stage))
    protected = _protected_floor_ids(project_root)
    if protected is None:
        return {
            "applied": 0,
            "skipped": len(ops),
            "revision": raw_now["revision"],
        }

    try:
        raw = _load_raw(project_root)
        # A checklist authored for another vertical is unrelated state. Start a
        # clean project checklist for the committed vertical instead of mixing
        # stage names or requirements across domains.
        stages: dict[str, Any] = (
            dict(raw["stages"])
            if raw["vertical"] == current_vertical
            else {}
        )
        disabled: dict[str, list[str]] = (
            {k: list(v) for k, v in raw["disabled"].items()}
            if raw["vertical"] == current_vertical
            else {}
        )
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
                    # Mark stage as explicitly managed; seeds are merged at read time.
                    stages[stage] = []
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
                # Strip same-id existing row (dedup before cap check).
                bucket = [r for r in bucket if not (isinstance(r, dict) and r.get("id") == item_id)]
                if len(bucket) >= MAX_ITEMS_PER_STAGE:
                    skipped += 1
                    stages[stage] = bucket
                    continue
                # Clear tombstone only after we know the add will succeed.
                if item_id in disabled.get(stage, []):
                    disabled[stage] = [i for i in disabled[stage] if i != item_id]
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
                if not found:
                    # Only create an override entry for seed IDs, not arbitrary custom IDs.
                    seed_ids_for_stage = {it.id for it in (seed_fn(stage) or ())}
                    if item_id in seed_ids_for_stage:
                        stmt = str(op_raw.get("statement") or "").strip()
                        ev = str(op_raw.get("evidence_hint") or "").strip()
                        if stmt and len(bucket) < MAX_ITEMS_PER_STAGE:
                            bucket.append(_row(item_id, stmt, ev))
                            stages[stage] = bucket
                            found = True
                # Clear tombstone only after the operation is known to succeed.
                if found and item_id in disabled.get(stage, []):
                    disabled[stage] = [i for i in disabled[stage] if i != item_id]
                applied += 1 if found else 0
                skipped += 0 if found else 1

            elif op == "remove":
                seed_ids_for_stage = {it.id for it in (seed_fn(stage) or ())}
                if item_id in seed_ids_for_stage:
                    # Seed ID: record tombstone to hide this seed from effective list.
                    dis = disabled.setdefault(stage, [])
                    if item_id not in dis:
                        dis.append(item_id)
                        applied += 1
                    else:
                        skipped += 1  # already tombstoned → idempotent
                    # Also remove any override entry so the tombstone is authoritative.
                    stages[stage] = [
                        r for r in bucket
                        if not (isinstance(r, dict) and r.get("id") == item_id)
                    ]
                else:
                    # Custom ID: remove its row only.
                    before = len(bucket)
                    stages[stage] = [
                        r for r in bucket
                        if not (isinstance(r, dict) and r.get("id") == item_id)
                    ]
                    if len(stages[stage]) != before:
                        applied += 1
                    else:
                        skipped += 1

        revision = int(raw["revision"]) + 1
        payload: dict[str, Any] = {
            "revision": revision,
            "vertical": current_vertical,
            "stages": stages,
        }
        # Only persist 'disabled' when it is non-empty (keeps old format for clean stores).
        clean_disabled = {k: v for k, v in disabled.items() if v}
        if clean_disabled:
            payload["disabled"] = clean_disabled
        _atomic_write_json(_store_path(project_root), payload)
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
