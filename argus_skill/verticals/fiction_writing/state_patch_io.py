"""Generation-side reliability for ``state_patch`` — the preventive + repair half
of the "生成→校验→闸" chain whose floor is :mod:`.state`.

:mod:`.state` is the SAFE APPLY engine: it validates, atomically applies, and
re-validates a patch, and already coerces two provider quirks deterministically
(``normalize_ops`` for a stringified ``ops``; ``canonicalize_patch`` for the
dual id-placement habit). What it does NOT do — and what a real multi-chapter
run needs — is help the model PRODUCE a valid patch in the first place. Left
unaided, an unconstrained model emits patches that reference a holder that is
not a character, invent an id it never declared, or omit an id entirely; a blind
retry (same prompt, same blind spot) does not reliably fix this.

This module adds the two missing, DETERMINISTIC pieces (no hidden model calls of
its own — the repairer is injected by the caller):

* :func:`valid_reference_inventory` / :func:`build_generation_context` —
  schema-in-prompt grounding: the exact op contract + the *current* set of
  referenceable ids, so generation is constrained to real anchors up front.
* :func:`diagnose_patch` — a NON-raising check that returns the first blocking
  problem together with the valid-id inventory, i.e. structured, actionable
  repair feedback (never a fabricated fix).
* :func:`apply_patch_with_repair` — a bounded validate→repair loop that feeds
  that diagnosis to a caller-supplied ``repair_fn`` (the LLM in production, a
  deterministic function in tests) until the engine accepts the patch or the
  attempt budget is spent. The engine remains the sole gate: a patch that never
  becomes valid still raises, so this can only make a REJECT into an ACCEPT via
  a genuinely-valid revision, never launder a bad patch through.
"""
from __future__ import annotations

from typing import Any, Callable

from .state import _HANDLERS, PatchError, apply_patch

#: The ops the engine understands, surfaced for the generation prompt so the
#: model never invents an op name. Sourced from the engine's own handler table
#: (single source of truth — a new handler shows up here automatically).
ALLOWED_OPS: tuple[str, ...] = tuple(_HANDLERS.keys())

#: Compact required-``value`` shapes per op (the engine rejects a missing one).
#: Kept terse on purpose — this is a prompt anchor, not the schema.
_OP_SHAPES: dict[str, str] = {
    "set_meta": "set:{form?/title?/language?/world_clock?}",
    "add_character": "id, value:{name, aliases?, status?, knows?, location?, birth_year?, age?, motivation?, notes?}",
    "update_character": "id, set:{any of name/aliases/status/knows/location/birth_year/age/motivation/notes}",
    "add_relationship": "value:{from(char id), to(char id), type, notes?}",
    "add_world_rule": "value:{id, statement}",
    "add_location": "id, value:{name, notes?}",
    "add_item": "value:{id, name, holder?(char id), location?(loc id), notes?}",
    "move_item": "id, exactly one of to_holder(char id|null)/to_location(loc id|null)",
    "add_timeline": "value:{id, order(unique int), label, year?, chapter?}",
    "add_open_thread": "value:{id, statement}",
    "resolve_thread": "id(existing thread)",
    "add_foreshadowing": "value:{id, statement, planted_chapter?, payoff_chapter?, status?}",
    "resolve_foreshadowing": "id(existing), payoff_chapter?",
    "add_chapter_summary": "chapter(int), summary",
}


def valid_reference_inventory(state: dict[str, Any] | None) -> dict[str, Any]:
    """Return the set of ids an op may currently reference, per category.

    This is the ground truth a patch must anchor to: ``add_relationship`` /
    ``add_item`` / ``move_item`` / ``update_character`` / ``resolve_*`` all
    reference an EXISTING id, and every ``add_*`` must NOT collide with one. An
    empty state yields empty buckets (a brand-new story references nothing yet).
    """
    s = state or {}
    return {
        "characters": {cid: c.get("name", "") for cid, c in (s.get("characters") or {}).items()},
        "locations": {lid: loc.get("name", "") for lid, loc in (s.get("locations") or {}).items()},
        "items": {iid: it.get("name", "") for iid, it in (s.get("items") or {}).items()},
        "open_threads": [t["id"] for t in (s.get("open_threads") or []) if "id" in t],
        "foreshadowing": [f["id"] for f in (s.get("foreshadowing") or []) if "id" in f],
        "world_rules": [r["id"] for r in (s.get("world_rules") or []) if "id" in r],
        "timeline_orders": sorted(t["order"] for t in (s.get("timeline") or []) if "order" in t),
    }


def build_generation_context(state: dict[str, Any] | None, *, language: str = "zh") -> str:
    """A compact prompt block that constrains state_patch GENERATION.

    Injecting this ahead of the model's patch turn is the preventive half: it
    lists the allowed ops with their required ``value`` shapes AND the current
    referenceable ids, so the model reuses real ids, gives every new entity a
    fresh unique id, and never points a holder/location at something that does
    not exist. Deterministic string — no model call.
    """
    inv = valid_reference_inventory(state)
    zh = language != "en"
    lines: list[str] = []
    lines.append("可用的 state_patch 操作(只能用这些 op):" if zh
                 else "Allowed state_patch ops (use ONLY these):")
    for op in ALLOWED_OPS:
        lines.append(f"  - {op}: {_OP_SHAPES.get(op, '')}")
    lines.append("")
    lines.append("当前可引用的 id(引用现有实体必须用下列 id;新实体请起一个未占用的新 id):" if zh
                 else "Currently referenceable ids (reference existing entities by these; "
                      "give every NEW entity a fresh unused id):")
    lines.append(f"  characters: {inv['characters'] or '(none)'}")
    lines.append(f"  locations:  {inv['locations'] or '(none)'}")
    lines.append(f"  items:      {inv['items'] or '(none)'}")
    lines.append(f"  open_threads:  {inv['open_threads'] or '(none)'}")
    lines.append(f"  foreshadowing: {inv['foreshadowing'] or '(none)'}")
    lines.append(f"  used timeline orders: {inv['timeline_orders'] or '(none)'}")
    lines.append("")
    lines.append(
        "硬规则:relationship.from/to、item.holder、move_item 目标必须是上面的 character/location id;"
        "add_* 不能覆盖已存在 id;timeline.order 为未用过的整数;没有删除 op。"
        if zh else
        "Hard rules: relationship.from/to, item.holder, move_item targets MUST be ids "
        "listed above; add_* may not overwrite an existing id; timeline.order is an "
        "unused integer; there is no delete op.")
    return "\n".join(lines)


def diagnose_patch(state: dict[str, Any] | None, patch: dict[str, Any]) -> dict[str, Any]:
    """Non-raising validation: ``{"ok": True}`` or a structured problem report.

    On failure returns ``{"ok": False, "error": <engine message>, "valid":
    <inventory>}``. The engine message already names the offending ``op[idx]``
    and reason; pairing it with the referenceable-id inventory is exactly what a
    grounded repair needs. Never mutates state and never fabricates a fix.
    """
    try:
        apply_patch(state, patch)
    except PatchError as exc:
        return {"ok": False, "error": str(exc), "valid": valid_reference_inventory(state)}
    return {"ok": True, "error": None, "valid": valid_reference_inventory(state)}


RepairFn = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


def apply_patch_with_repair(
    state: dict[str, Any] | None,
    patch: dict[str, Any],
    repair_fn: RepairFn,
    *,
    max_attempts: int = 2,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Apply ``patch`` through the engine, repairing via ``repair_fn`` on failure.

    Loop: try :func:`~.state.apply_patch`; on :class:`~.state.PatchError`, build a
    structured diagnosis (:func:`diagnose_patch`) and pass ``(current_patch,
    diagnosis)`` to ``repair_fn`` to obtain a revised patch, then retry — up to
    ``max_attempts`` repairs. The injected ``repair_fn`` is the LLM in
    production and a deterministic function in tests; this module never calls a
    model itself.

    Returns ``(new_state, result, attempts)`` where ``attempts`` is the list of
    diagnoses that triggered a repair (empty if the first patch applied). The
    ENGINE stays the only gate: if no revision ever validates, the final
    :class:`~.state.PatchError` propagates — a bad patch is never laundered
    through, it can only be turned into an ACCEPT by a genuinely-valid revision.
    """
    if max_attempts < 0:
        raise ValueError("max_attempts must be >= 0")
    current = patch
    attempts: list[dict[str, Any]] = []
    for i in range(max_attempts + 1):
        try:
            new_state, result = apply_patch(state, current)
            return new_state, result, attempts
        except PatchError as exc:
            if i == max_attempts:
                raise
            diagnosis = {"error": str(exc), "valid": valid_reference_inventory(state)}
            attempts.append(diagnosis)
            current = repair_fn(current, diagnosis)
    raise AssertionError("unreachable")  # pragma: no cover


__all__ = [
    "ALLOWED_OPS",
    "valid_reference_inventory",
    "build_generation_context",
    "diagnose_patch",
    "apply_patch_with_repair",
]
