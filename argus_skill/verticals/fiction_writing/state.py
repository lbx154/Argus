"""Shared narrative-state core for fiction_writing (and future creative_writing
modules): the ``story_state`` model and the SAFE ``state_patch`` apply engine.

Why this exists: a drafting agent must NEVER hand-rewrite the whole story_state
JSON — that silently loses prior setup (a dead character quietly resurrected, a
planted foreshadow dropped). Instead each chapter emits a structured, additive/
mutating ``state_patch`` that this engine validates and applies with hard
guarantees:

* **idempotent** — re-applying the same ``patch_id`` is a no-op (no duplicated
  events), so a retried mission cannot double-count;
* **no silent deletion** — there is no op that removes an entity or a section;
  ``update_*`` only sets the fields it names and never drops the rest, so history
  is preserved (a character is marked ``dead``, not erased);
* **referential integrity** — every id an op references (character/item/location/
  thread/foreshadowing) must already exist; an ``add_*`` refuses to overwrite an
  existing id;
* **parseable timeline** — timeline ``order`` values stay unique integers.

Language- and genre-agnostic: characters/world/timeline/threads are shared; only
style/anti-AI guidance is per-language (handled by adapters, not here).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import jsonschema

_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"


def _load_schema(name: str) -> dict[str, Any]:
    with (_SCHEMA_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


STORY_STATE_SCHEMA: dict[str, Any] = _load_schema("story_state.schema.json")
STATE_PATCH_SCHEMA: dict[str, Any] = _load_schema("state_patch.schema.json")


class PatchError(ValueError):
    """Raised when a state_patch is invalid or cannot be safely applied."""


# --------------------------------------------------------------------------- #
# construction / validation
# --------------------------------------------------------------------------- #
def new_state(language: str = "zh", form: str = "fiction", title: str = "") -> dict[str, Any]:
    """A fresh, schema-valid, empty story_state."""
    return {
        "meta": {"language": language, "form": form, "title": title, "revision": 0},
        "characters": {},
        "relationships": [],
        "world_rules": [],
        "locations": {},
        "items": {},
        "timeline": [],
        "open_threads": [],
        "foreshadowing": [],
        "chapter_summaries": [],
        "applied_patches": [],
    }


def validate_state(state: dict[str, Any]) -> None:
    """Raise :class:`PatchError` if ``state`` violates the story_state schema."""
    try:
        jsonschema.validate(state, STORY_STATE_SCHEMA)
    except jsonschema.ValidationError as exc:
        raise PatchError(f"invalid story_state: {exc.message}") from exc


def validate_patch(patch: dict[str, Any]) -> None:
    """Raise :class:`PatchError` if ``patch`` violates the state_patch schema."""
    try:
        jsonschema.validate(patch, STATE_PATCH_SCHEMA)
    except jsonschema.ValidationError as exc:
        raise PatchError(f"invalid state_patch: {exc.message}") from exc


# --------------------------------------------------------------------------- #
# op handlers (each mutates ``s`` in place; raises PatchError on a bad ref)
# --------------------------------------------------------------------------- #
def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise PatchError(msg)


def _op_set_meta(s: dict[str, Any], op: dict[str, Any]) -> None:
    allowed = {"form", "title", "language"}
    for key, val in (op.get("set") or {}).items():
        _require(key in allowed, f"set_meta: cannot set meta field {key!r}")
        s["meta"][key] = val


def _op_add_character(s: dict[str, Any], op: dict[str, Any]) -> None:
    val = op.get("value") or {}
    cid = op.get("id") or val.get("id")
    _require(bool(cid), "add_character: missing id")
    _require(cid not in s["characters"], f"add_character: id {cid!r} already exists (no overwrite)")
    _require(bool(val.get("name")), f"add_character: {cid!r} missing name")
    s["characters"][cid] = {
        "id": cid,
        "name": val["name"],
        "aliases": list(val.get("aliases", [])),
        "status": val.get("status", "alive"),
        "knows": list(val.get("knows", [])),
        "location": val.get("location"),
        "motivation": val.get("motivation", ""),
        "notes": val.get("notes", ""),
    }


def _op_update_character(s: dict[str, Any], op: dict[str, Any]) -> None:
    cid = op.get("id")
    _require(cid in s["characters"], f"update_character: unknown id {cid!r}")
    allowed = {"name", "aliases", "status", "knows", "location", "motivation", "notes"}
    for key, val in (op.get("set") or {}).items():
        _require(key in allowed, f"update_character: cannot set {key!r}")
        s["characters"][cid][key] = val


def _op_add_relationship(s: dict[str, Any], op: dict[str, Any]) -> None:
    val = op.get("value") or {}
    frm, to = val.get("from"), val.get("to")
    _require(frm in s["characters"], f"add_relationship: unknown from {frm!r}")
    _require(to in s["characters"], f"add_relationship: unknown to {to!r}")
    _require(bool(val.get("type")), "add_relationship: missing type")
    s["relationships"].append({
        "from": frm, "to": to, "type": val["type"], "notes": val.get("notes", ""),
    })


def _op_add_world_rule(s: dict[str, Any], op: dict[str, Any]) -> None:
    val = op.get("value") or {}
    rid = val.get("id")
    _require(bool(rid), "add_world_rule: missing id")
    _require(all(r["id"] != rid for r in s["world_rules"]),
             f"add_world_rule: id {rid!r} already exists")
    _require(bool(val.get("statement")), "add_world_rule: missing statement")
    s["world_rules"].append({"id": rid, "statement": val["statement"]})


def _op_add_location(s: dict[str, Any], op: dict[str, Any]) -> None:
    val = op.get("value") or {}
    lid = op.get("id") or val.get("id")
    _require(bool(lid), "add_location: missing id")
    _require(lid not in s["locations"], f"add_location: id {lid!r} already exists")
    _require(bool(val.get("name")), f"add_location: {lid!r} missing name")
    s["locations"][lid] = {"id": lid, "name": val["name"], "notes": val.get("notes", "")}


def _op_add_item(s: dict[str, Any], op: dict[str, Any]) -> None:
    val = op.get("value") or {}
    iid = op.get("id") or val.get("id")
    _require(bool(iid), "add_item: missing id")
    _require(iid not in s["items"], f"add_item: id {iid!r} already exists")
    _require(bool(val.get("name")), f"add_item: {iid!r} missing name")
    holder = val.get("holder")
    location = val.get("location")
    _require(holder is None or holder in s["characters"],
             f"add_item: unknown holder {holder!r}")
    _require(location is None or location in s["locations"],
             f"add_item: unknown location {location!r}")
    s["items"][iid] = {"id": iid, "name": val["name"], "holder": holder,
                       "location": location, "notes": val.get("notes", "")}


def _op_move_item(s: dict[str, Any], op: dict[str, Any]) -> None:
    iid = op.get("id")
    _require(iid in s["items"], f"move_item: unknown item {iid!r}")
    has_holder = "to_holder" in op
    has_location = "to_location" in op
    _require(has_holder != has_location,
             "move_item: provide exactly one of to_holder / to_location")
    if has_holder:
        holder = op.get("to_holder")
        _require(holder is None or holder in s["characters"],
                 f"move_item: unknown holder {holder!r}")
        s["items"][iid]["holder"] = holder
        s["items"][iid]["location"] = None
    else:
        location = op.get("to_location")
        _require(location is None or location in s["locations"],
                 f"move_item: unknown location {location!r}")
        s["items"][iid]["location"] = location
        s["items"][iid]["holder"] = None


def _op_add_timeline(s: dict[str, Any], op: dict[str, Any]) -> None:
    val = op.get("value") or {}
    tid = val.get("id")
    order = val.get("order")
    _require(bool(tid), "add_timeline: missing id")
    _require(isinstance(order, int), "add_timeline: order must be an integer")
    _require(all(t["id"] != tid for t in s["timeline"]),
             f"add_timeline: id {tid!r} already exists")
    _require(all(t["order"] != order for t in s["timeline"]),
             f"add_timeline: order {order} already used (timeline must stay ordered)")
    _require(bool(val.get("label")), "add_timeline: missing label")
    s["timeline"].append({
        "id": tid, "order": order, "label": val["label"], "chapter": val.get("chapter"),
    })
    s["timeline"].sort(key=lambda t: t["order"])


def _op_add_open_thread(s: dict[str, Any], op: dict[str, Any]) -> None:
    val = op.get("value") or {}
    tid = val.get("id")
    _require(bool(tid), "add_open_thread: missing id")
    _require(all(t["id"] != tid for t in s["open_threads"]),
             f"add_open_thread: id {tid!r} already exists")
    _require(bool(val.get("statement")), "add_open_thread: missing statement")
    s["open_threads"].append({"id": tid, "statement": val["statement"], "status": "open"})


def _op_resolve_thread(s: dict[str, Any], op: dict[str, Any]) -> None:
    tid = op.get("id")
    thread = next((t for t in s["open_threads"] if t["id"] == tid), None)
    _require(thread is not None, f"resolve_thread: unknown thread {tid!r}")
    assert thread is not None  # for type-checkers
    thread["status"] = "resolved"


def _op_add_foreshadowing(s: dict[str, Any], op: dict[str, Any]) -> None:
    val = op.get("value") or {}
    fid = val.get("id")
    _require(bool(fid), "add_foreshadowing: missing id")
    _require(all(f["id"] != fid for f in s["foreshadowing"]),
             f"add_foreshadowing: id {fid!r} already exists")
    _require(bool(val.get("statement")), "add_foreshadowing: missing statement")
    s["foreshadowing"].append({
        "id": fid, "statement": val["statement"],
        "planted_chapter": val.get("planted_chapter"),
        "payoff_chapter": val.get("payoff_chapter"),
        "status": val.get("status", "planted"),
    })


def _op_resolve_foreshadowing(s: dict[str, Any], op: dict[str, Any]) -> None:
    fid = op.get("id")
    fore = next((f for f in s["foreshadowing"] if f["id"] == fid), None)
    _require(fore is not None, f"resolve_foreshadowing: unknown id {fid!r}")
    assert fore is not None
    fore["status"] = "paid_off"
    fore["payoff_chapter"] = op.get("payoff_chapter", fore.get("payoff_chapter"))


def _op_add_chapter_summary(s: dict[str, Any], op: dict[str, Any]) -> None:
    chapter = op.get("chapter")
    _require(isinstance(chapter, int), "add_chapter_summary: chapter must be an integer")
    _require(all(c["chapter"] != chapter for c in s["chapter_summaries"]),
             f"add_chapter_summary: chapter {chapter} already summarized")
    s["chapter_summaries"].append({"chapter": chapter, "summary": op.get("summary", "")})


_HANDLERS = {
    "set_meta": _op_set_meta,
    "add_character": _op_add_character,
    "update_character": _op_update_character,
    "add_relationship": _op_add_relationship,
    "add_world_rule": _op_add_world_rule,
    "add_location": _op_add_location,
    "add_item": _op_add_item,
    "move_item": _op_move_item,
    "add_timeline": _op_add_timeline,
    "add_open_thread": _op_add_open_thread,
    "resolve_thread": _op_resolve_thread,
    "add_foreshadowing": _op_add_foreshadowing,
    "resolve_foreshadowing": _op_resolve_foreshadowing,
    "add_chapter_summary": _op_add_chapter_summary,
}


# --------------------------------------------------------------------------- #
# the safe apply engine
# --------------------------------------------------------------------------- #
#: ops that accept the new id at EITHER the op top level OR value.id
_DUAL_ID_OPS = {"add_character", "add_location", "add_item"}


def normalize_ops(patch: dict[str, Any]) -> dict[str, Any]:
    """Deterministically coerce a stringified ``ops`` back to a list BEFORE strict
    validation. Some providers, under a forced tool call, emit ``ops`` as a JSON *string*
    instead of an array. If ``ops`` is a string, ``json.loads`` it ONCE; the decoded value
    MUST be a list, else raise :class:`PatchError`; unparseable JSON raises a
    :class:`PatchError` naming the reason. A list ``ops`` is returned unchanged. No
    operation is invented, no LLM repair loop is started, the schema is untouched, and the
    input is not mutated. Returns a new patch.
    """
    if not isinstance(patch, dict) or not isinstance(patch.get("ops"), str):
        return patch
    try:
        parsed = json.loads(patch["ops"])
    except (json.JSONDecodeError, ValueError) as exc:
        raise PatchError(f"ops is a string but not valid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise PatchError(
            f"ops decoded from a string is {type(parsed).__name__}, not an array")
    return {**patch, "ops": parsed}


def canonicalize_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Deterministically reconcile the model's dual id-placement habit with the strict
    single-id contract, BEFORE :func:`validate_patch`. For the ops that accept an id at
    EITHER the op top level OR ``value.id`` (add_character/add_location/add_item):

    * exactly one id present → unchanged;
    * op-level id and ``value.id`` both present and EQUAL → drop ``value.id`` (canonical
      form is the op-level id);
    * both present but DIFFERENT → raise :class:`PatchError` (conflicting ids).

    No id is ever invented, no other field is touched, the input is not mutated, and the
    v3 schema stays strict — this only removes a redundant, provably-equal duplicate the
    provider does not hard-suppress at generation time. Returns a new patch.
    """
    if not isinstance(patch, dict) or not isinstance(patch.get("ops"), list):
        return patch
    new_ops = []
    for idx, op in enumerate(patch["ops"]):
        val = op.get("value") if isinstance(op, dict) else None
        if (isinstance(op, dict) and op.get("op") in _DUAL_ID_OPS
                and isinstance(val, dict) and "id" in op and "id" in val):
            if op["id"] == val["id"]:
                op = {**op, "value": {k: v for k, v in val.items() if k != "id"}}
            else:
                raise PatchError(
                    f"op[{idx}] ({op.get('op')}): conflicting ids — "
                    f"op-level id {op['id']!r} != value.id {val['id']!r}")
        new_ops.append(op)
    return {**patch, "ops": new_ops}


def apply_patch(
    state: dict[str, Any] | None, patch: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply ``patch`` to ``state`` and return ``(new_state, result)``.

    ``state`` may be ``None`` (a brand-new story is initialized from the patch's
    language). The input ``state`` is never mutated — a deep copy is returned.

    Guarantees: idempotent by ``patch_id``; atomic (a bad op raises
    :class:`PatchError` and NOTHING is applied — the returned state equals the
    input); no op can delete prior state; all id references must resolve; the
    final state is re-validated against the schema.

    ``result`` = ``{"applied": bool, "patch_id": str, "revision": int,
    "reason": str}``. ``applied=False`` with reason ``"duplicate"`` means the
    patch_id was already applied (no-op).
    """
    patch = normalize_ops(patch)
    patch = canonicalize_patch(patch)
    validate_patch(patch)

    if state is None:
        working = new_state(language=patch.get("language", "zh"))
    else:
        validate_state(state)  # reject a corrupt inbound state up front
        working = copy.deepcopy(state)

    patch_id = patch["patch_id"]
    if patch_id in working["applied_patches"]:
        return working, {
            "applied": False, "patch_id": patch_id,
            "revision": working["meta"]["revision"], "reason": "duplicate",
        }

    # Apply to a scratch copy so a mid-list failure leaves the original intact.
    scratch = copy.deepcopy(working)
    for idx, op in enumerate(patch["ops"]):
        handler = _HANDLERS.get(op["op"])
        _require(handler is not None, f"op[{idx}]: unknown op {op['op']!r}")
        assert handler is not None
        try:
            handler(scratch, op)
        except PatchError as exc:
            raise PatchError(f"op[{idx}] ({op['op']}): {exc}") from exc

    scratch["meta"]["revision"] = working["meta"]["revision"] + 1
    scratch["applied_patches"].append(patch_id)

    validate_state(scratch)
    return scratch, {
        "applied": True, "patch_id": patch_id,
        "revision": scratch["meta"]["revision"], "reason": "ok",
    }
