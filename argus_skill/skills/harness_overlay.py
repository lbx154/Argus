"""Per-project prompt-rule self-evolution overlay.

Role-specific prompt rules may be adapted per project. Checklist ownership is
separate and unambiguous: framework/vertical code provides the seed floor, the
Planner is the only runtime editor through ``checklist_ops``, and the Reviewer
is feedback-only.

    <project_root>/.argus/harness/
        active.json     # applied to prompts (read fresh every prompt build)
        pending.json    # proposed, NOT applied until promoted
        journal.jsonl   # append-only audit trail (recover / diff / debug)

Design contract (operator: each project owns its own interface so it can both
*recover* and *apply*):

* Legacy ``checklist_items`` rows are ignored and cannot be added or promoted.
  Project checklist edits belong exclusively to the Planner store.
* Reads are **fail-open**: a missing/corrupt overlay is ignored (the floor still
  renders) and the corruption is recorded in the journal.
* Writes are **atomic** (tmp+rename) and carry a monotonically increasing
  ``revision`` so a stale concurrent writer can be detected.
* Routing: engineer-targeted changes (which only *add obligations*) go to
  ``active.json``; reviewer / planner rule changes and any ``supersede`` go to
  ``pending.json`` until explicitly promoted — so the agent cannot silently
  bias its own judge.

This module is intentionally free of any import from
:mod:`argus_skill.skills.stage_machine` (which imports *this* module for the
merge) to avoid a circular import. The merge/render lives in stage_machine;
this module owns the data, IO, validation and protected-floor policy only.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
from pathlib import Path
from typing import Any

# --- Layout -----------------------------------------------------------------

OVERLAY_DIRNAME = ".argus"
HARNESS_SUBDIR = "harness"
ACTIVE_FILE = "active.json"
PENDING_FILE = "pending.json"
JOURNAL_FILE = "journal.jsonl"

VALID_ROLES = ("engineer", "reviewer", "planner", "critic")
VALID_OPS = ("add", "amend", "supersede")

# Roles whose changes are safe to ACTIVATE immediately: they can only add
# obligations to the engineer, never relax a judge. Everything else lands in
# pending until promoted.
AUTO_ACTIVE_ROLES = ("engineer",)

# Bounds — keep self-modification from ballooning the prompt or running away.
MAX_ITEMS = 40
MAX_RULES = 25
MAX_STATEMENT_LEN = 1200
MAX_RULE_LEN = 1200

# Core scientific-integrity / done-criteria floor. The overlay may *strengthen*
# these (annotate with a stricter project note) but may never weaken, delete, or
# supersede them.
PROTECTED_ITEM_IDS = frozenset({
    "benchmark.evaluator_authentic",
    "run.score_variance",
    "run.method_diagnosis_recall",
    "analysis.claims",
    "review.placeholders",
    "submission.assurance",
    "submission.anonymous",
    "submission.upstream",
})


class OverlayValidationError(ValueError):
    """Raised when a proposed overlay mutation violates schema or floor policy."""


# --- Project-root resolution ------------------------------------------------

def resolve_project_root(explicit: Path | str | None = None) -> Path:
    """Resolve the project root robustly.

    Priority: explicit argument -> ``ARGUS_SKILL_PROJECT_ROOT`` env (set by the
    daemon's in-process env block, so the in-process reviewer/planner resolve the
    same root the engineer subprocess sees) -> current working directory.

    Bare :func:`Path.cwd` is the *last* resort because the daemon builds the
    reviewer/planner prompts in-process where cwd may not be the project root.
    """

    if explicit is not None:
        return Path(explicit)
    env = os.environ.get("ARGUS_SKILL_PROJECT_ROOT")
    if env:
        return Path(env)
    return Path.cwd()


def harness_dir(project_root: Path | str | None = None) -> Path:
    root = resolve_project_root(project_root)
    return root / OVERLAY_DIRNAME / HARNESS_SUBDIR


def _state_path(project_root: Path | str | None, *, state: str) -> Path:
    fname = ACTIVE_FILE if state == "active" else PENDING_FILE
    return harness_dir(project_root) / fname


def _journal_path(project_root: Path | str | None) -> Path:
    return harness_dir(project_root) / JOURNAL_FILE


# --- Journal ----------------------------------------------------------------

def journal(project_root: Path | str | None, event: str, **fields: Any) -> None:
    """Append one audit line. Best-effort; never raises into the caller."""

    try:
        path = _journal_path(project_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "event": event,
            **fields,
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
    except Exception:  # noqa: BLE001 - audit logging must never break a mission
        pass


# --- Empty / default overlay ------------------------------------------------

def _empty_overlay() -> dict[str, Any]:
    return {"revision": 0, "checklist_items": [], "prompt_rules": []}


def _coerce_overlay(data: Any) -> dict[str, Any]:
    """Normalize a parsed overlay into the canonical shape, dropping junk."""

    if not isinstance(data, dict):
        raise OverlayValidationError("overlay root is not an object")
    out = _empty_overlay()
    rev = data.get("revision", 0)
    out["revision"] = int(rev) if isinstance(rev, (int, float)) else 0
    items = data.get("checklist_items")
    rules = data.get("prompt_rules")
    out["checklist_items"] = list(items) if isinstance(items, list) else []
    out["prompt_rules"] = list(rules) if isinstance(rules, list) else []
    return out


# --- Read path (fail-open) --------------------------------------------------

def load_overlay(
    project_root: Path | str | None = None,
    *,
    state: str = "active",
) -> dict[str, Any]:
    """Load an overlay file. Fail-open: returns an empty overlay on any error
    and records the corruption in the journal so it is visible, never silent."""

    path = _state_path(project_root, state=state)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty_overlay()
    except OSError:
        journal(project_root, "overlay_unreadable", state=state, path=str(path))
        return _empty_overlay()
    try:
        return _coerce_overlay(json.loads(raw))
    except (json.JSONDecodeError, OverlayValidationError, ValueError) as exc:
        journal(
            project_root,
            "overlay_invalid_ignored",
            state=state,
            path=str(path),
            error=str(exc),
        )
        return _empty_overlay()


def active_checklist_items(
    project_root: Path | str | None,
    *,
    stage: str,
    role: str,
) -> list[dict[str, Any]]:
    """Legacy API: checklist overlays are disabled; Planner owns edits."""

    _ = (project_root, stage, role)
    return []


def active_prompt_rules(
    project_root: Path | str | None,
    *,
    role: str,
) -> list[dict[str, Any]]:
    """Active house-rule entries that apply to ``role``."""

    overlay = load_overlay(project_root, state="active")
    role_n = (role or "").strip().lower()
    out: list[dict[str, Any]] = []
    for r in overlay.get("prompt_rules", []):
        if not isinstance(r, dict):
            continue
        if (r.get("role") or "engineer").strip().lower() == role_n:
            out.append(r)
    return out


# --- Write path -------------------------------------------------------------

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


def snapshot(project_root: Path | str | None) -> dict[str, bytes | None]:
    """Capture the raw bytes of active+pending so a failed mutation can be
    rolled back to the EXACT prior state (not by id — that would also delete a
    previous good entry with the same id)."""

    snap: dict[str, bytes | None] = {}
    for state in ("active", "pending"):
        path = _state_path(project_root, state=state)
        try:
            snap[state] = path.read_bytes()
        except OSError:
            snap[state] = None
    return snap


def restore(project_root: Path | str | None, snap: dict[str, bytes | None]) -> None:
    """Restore a snapshot captured by :func:`snapshot`."""

    for state in ("active", "pending"):
        path = _state_path(project_root, state=state)
        data = snap.get(state)
        if data is None:
            try:
                path.unlink()
            except OSError:
                pass
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)


def _validate_item(
    item: dict[str, Any],
    *,
    known_stages: frozenset[str],
    known_item_ids: frozenset[str],
) -> None:
    op = (item.get("op") or "").strip().lower()
    if op not in VALID_OPS:
        raise OverlayValidationError(f"unknown op {op!r}; expected one of {VALID_OPS}")
    stage = (item.get("stage") or "").strip().lower()
    if stage not in known_stages:
        raise OverlayValidationError(f"unknown stage {stage!r}")
    role = (item.get("role") or "engineer").strip().lower()
    if role not in VALID_ROLES:
        raise OverlayValidationError(f"unknown role {role!r}")
    item_id = (item.get("id") or "").strip()
    if not item_id:
        raise OverlayValidationError("item id is required")

    if op == "add":
        statement = (item.get("statement") or "").strip()
        if not statement:
            raise OverlayValidationError("add requires a non-empty statement")
        if len(statement) > MAX_STATEMENT_LEN:
            raise OverlayValidationError("statement too long")
        if not (item.get("evidence_hint") or "").strip():
            raise OverlayValidationError("add requires evidence_hint")
        if item_id in known_item_ids:
            raise OverlayValidationError(
                f"id {item_id!r} collides with a framework floor item; use amend"
            )
    else:  # amend / supersede target an existing floor item
        if item_id not in known_item_ids:
            raise OverlayValidationError(
                f"{op} target {item_id!r} is not a framework floor item"
            )
        if item_id in PROTECTED_ITEM_IDS and op == "supersede":
            raise OverlayValidationError(
                f"{item_id!r} is a protected floor item and cannot be superseded"
            )
        if op == "amend":
            note = (item.get("note") or "").strip()
            if not note:
                raise OverlayValidationError("amend requires a non-empty note")
            if len(note) > MAX_STATEMENT_LEN:
                raise OverlayValidationError("note too long")
        if op == "supersede":
            statement = (item.get("statement") or "").strip()
            if not statement:
                raise OverlayValidationError("supersede requires a statement")
            if len(statement) > MAX_STATEMENT_LEN:
                raise OverlayValidationError("statement too long")
    if not (item.get("reason") or "").strip():
        raise OverlayValidationError("a reason is required for every change")


def _validate_rule(rule: dict[str, Any]) -> None:
    role = (rule.get("role") or "engineer").strip().lower()
    if role not in VALID_ROLES:
        raise OverlayValidationError(f"unknown role {role!r}")
    if not (rule.get("id") or "").strip():
        raise OverlayValidationError("rule id is required")
    text = (rule.get("text") or "").strip()
    if not text:
        raise OverlayValidationError("rule text is required")
    if len(text) > MAX_RULE_LEN:
        raise OverlayValidationError("rule text too long")
    if not (rule.get("reason") or "").strip():
        raise OverlayValidationError("a reason is required for every change")


def route_state_for_change(*, role: str, op: str | None = None) -> str:
    """Return 'active' or 'pending' for a proposed change.

    Engineer additive changes activate immediately (they can only add work, never
    relax a judge). Reviewer/planner/critic changes and any ``supersede`` go to
    pending until promoted.
    """

    role_n = (role or "engineer").strip().lower()
    if (op or "").strip().lower() == "supersede":
        return "pending"
    return "active" if role_n in AUTO_ACTIVE_ROLES else "pending"


def add_checklist_item(
    project_root: Path | str | None,
    *,
    item: dict[str, Any],
    known_stages: frozenset[str],
    known_item_ids: frozenset[str],
) -> dict[str, Any]:
    """Checklist overlays are retired; use Planner ``checklist_ops``."""

    _ = (project_root, item, known_stages, known_item_ids)
    raise OverlayValidationError(
        "checklist ownership belongs to the Planner; use reviewer "
        "Planner checklist_ops"
    )


def add_prompt_rule(
    project_root: Path | str | None,
    *,
    rule: dict[str, Any],
) -> dict[str, Any]:
    _validate_rule(rule)
    state = route_state_for_change(role=rule.get("role") or "engineer")
    overlay = load_overlay(project_root, state=state)
    rules = overlay["prompt_rules"]
    if len(rules) >= MAX_RULES:
        raise OverlayValidationError(f"overlay rule cap reached ({MAX_RULES})")

    entry = dict(rule)
    entry["role"] = (rule.get("role") or "engineer").strip().lower()
    entry["created_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    entry["state"] = state
    rules = [r for r in rules if not (isinstance(r, dict) and r.get("id") == entry["id"])]
    rules.append(entry)
    overlay["prompt_rules"] = rules
    overlay["revision"] = int(overlay.get("revision", 0)) + 1
    _atomic_write_json(_state_path(project_root, state=state), overlay)
    journal(project_root, "prompt_rule_added", state=state, id=entry["id"], revision=overlay["revision"])
    return entry


def _find_and_remove(overlay: dict[str, Any], entry_id: str) -> bool:
    removed = False
    for key in ("checklist_items", "prompt_rules"):
        before = len(overlay.get(key, []))
        overlay[key] = [
            e for e in overlay.get(key, [])
            if not (isinstance(e, dict) and e.get("id") == entry_id)
        ]
        if len(overlay[key]) != before:
            removed = True
    return removed


def revert(project_root: Path | str | None, *, entry_id: str) -> bool:
    """Remove a single overlay entry (by id) from both active and pending."""

    any_removed = False
    for state in ("active", "pending"):
        overlay = load_overlay(project_root, state=state)
        if _find_and_remove(overlay, entry_id):
            overlay["revision"] = int(overlay.get("revision", 0)) + 1
            _atomic_write_json(_state_path(project_root, state=state), overlay)
            any_removed = True
    if any_removed:
        journal(project_root, "reverted", id=entry_id)
    return any_removed


def reset(project_root: Path | str | None, *, stage: str | None = None) -> int:
    """Clear the active+pending overlay. If ``stage`` is given, clear only that
    stage's checklist items (rules are stageless and left intact). Returns the
    number of entries removed."""

    removed = 0
    stage_n = (stage or "").strip().lower() or None
    for state in ("active", "pending"):
        overlay = load_overlay(project_root, state=state)
        if stage_n is None:
            removed += len(overlay.get("checklist_items", [])) + len(overlay.get("prompt_rules", []))
            overlay["checklist_items"] = []
            overlay["prompt_rules"] = []
        else:
            kept = [e for e in overlay.get("checklist_items", []) if (isinstance(e, dict) and (e.get("stage") or "").strip().lower() != stage_n)]
            removed += len(overlay.get("checklist_items", [])) - len(kept)
            overlay["checklist_items"] = kept
        overlay["revision"] = int(overlay.get("revision", 0)) + 1
        _atomic_write_json(_state_path(project_root, state=state), overlay)
    journal(project_root, "reset", stage=stage_n, removed=removed)
    return removed


def promote(project_root: Path | str | None, *, entry_id: str) -> bool:
    """Move a single entry from pending to active (gated adoption)."""

    pending = load_overlay(project_root, state="pending")
    moved: tuple[str, dict[str, Any]] | None = None
    for key in ("checklist_items", "prompt_rules"):
        for e in pending.get(key, []):
            if isinstance(e, dict) and e.get("id") == entry_id:
                moved = (key, e)
                break
        if moved is not None:
            break
    if moved is None:
        return False
    key, entry = moved
    if key == "checklist_items":
        return False
    # Active-first then pending-remove: a crash between the two writes leaves a
    # harmless active+pending duplicate rather than losing the entry entirely.
    active = load_overlay(project_root, state="active")
    new_entry = dict(entry)
    new_entry["state"] = "active"
    new_entry["promoted_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    active[key] = [e for e in active.get(key, []) if not (isinstance(e, dict) and e.get("id") == entry_id)]
    active[key].append(new_entry)
    active["revision"] = int(active.get("revision", 0)) + 1
    _atomic_write_json(_state_path(project_root, state="active"), active)

    pending[key] = [e for e in pending.get(key, []) if not (isinstance(e, dict) and e.get("id") == entry_id)]
    pending["revision"] = int(pending.get("revision", 0)) + 1
    _atomic_write_json(_state_path(project_root, state="pending"), pending)
    journal(project_root, "promoted", id=entry_id)
    return True
