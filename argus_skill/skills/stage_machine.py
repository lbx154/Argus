"""Generic vertical-aware stage state machine and checklist rendering.

Checklist seeds and domain-specific rendering live in each ``verticals/*/stages.py``.
This module owns only state transitions, active-vertical resolution, project checklist
overrides, and prompt rendering.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ChecklistItem:
    """One verifiable item on a stage checklist."""

    id: str
    statement: str
    evidence_hint: str


class ChecklistLoadState(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    NOT_LOADED = "not_loaded"
    EMPTY = "empty"
    LOADED = "loaded"


@dataclass(frozen=True)
class StageChecklistContract:
    stage: str
    state: ChecklistLoadState
    checklist_optional: bool
    items: tuple[ChecklistItem, ...]


def completion_contract_fingerprint(
    project_root: Path | str,
    stage: str,
    *,
    version: int,
) -> str:
    """Hash the active final-stage checklist contract deterministically."""
    contract = resolve_stage_checklist_contract(stage, project_root=project_root)
    payload = {
        "version": int(version),
        "stage": contract.stage,
        "state": contract.state.value,
        "checklist_optional": contract.checklist_optional,
        "items": [
            {
                "id": item.id,
                "statement": item.statement,
                "evidence_hint": item.evidence_hint,
            }
            for item in contract.items
        ],
    }
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def _normalize_stage(stage: str | None) -> str:
    if not stage:
        return ""
    return str(stage).strip().lower()


def _active_vertical_stage_aliases(project_root) -> dict[str, str]:
    import os

    if project_root is None:
        project_root = os.environ.get("ARGUS_SKILL_PROJECT_ROOT") or "."
    try:
        from ..verticals._base import load_vertical, vertical_stage_aliases
        from .vertical_select import resolve_checklist_vertical

        vertical = resolve_checklist_vertical(project_root)
        if vertical is None:
            return {}
        return vertical_stage_aliases(load_vertical(vertical, project_root=project_root))
    except Exception:  # noqa: BLE001
        return {}


def normalize_stage_for_project(
    project_root: Path | str,
    stage: str | None,
    *,
    require_known: bool = False,
) -> str:
    """Canonicalize a stage name using the active vertical's aliases."""
    normalized = _normalize_stage(stage)
    aliases = _active_vertical_stage_aliases(project_root)
    seen: set[str] = set()
    while normalized in aliases and normalized not in seen:
        seen.add(normalized)
        normalized = _normalize_stage(aliases[normalized])
    if require_known:
        order, _items = _active_vertical_checklist_defs(project_root)
        known = {_normalize_stage(item) for item in order}
        if normalized not in known:
            return ""
    return normalized


def current_stage(project_root: Path | str = ".") -> str:
    """Read the project pipeline state and return the current stage.

    The set of valid stages and fallback come from the active vertical's
    ``CHECKLIST_STAGE_ORDER`` / ``CHECKLIST_ITEMS``. Missing, unreadable, or
    invalid state falls back to that vertical's first stage.
    """

    root = Path(project_root)
    state_path = root / "research" / "PIPELINE_STATE.json"
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = None
    order, items = _active_vertical_checklist_defs(project_root)
    fallback = _normalize_stage(order[0]) if order else "research"
    stage = normalize_stage_for_project(
        project_root,
        payload.get("current_stage") if isinstance(payload, dict) else None,
    )
    if stage in {_normalize_stage(s) for s in order}:
        return stage
    return fallback


def _set_stage(
    project_root: Path | str,
    *,
    target_stage: str,
    reason: str,
    by: str,
    direction: str,
    mark_current_done: bool = False,
    completion_contract_version: int = 0,
    completion_contract_sha256: str = "",
    downgrade_downstream: bool = False,
    legacy_rollback_history: bool = False,
) -> str:
    """Single vertical-aware read-modify-write of the pipeline stage state.

    The ONE primitive behind :func:`advance_stage` and :func:`rollback_stage`.
    Resolves the active vertical's stage order + items via
    ``_active_vertical_checklist_defs`` (fails open to the default vertical, so the
    research/paper path stays byte-identical), validates ``target_stage`` against
    them, then writes the shared pipeline-state sidecar:

    * ``current_stage`` -> ``target_stage``
    * if ``mark_current_done``: the *previous* stage's ``status`` -> ``done``
      (the advance case stamps the stage just completed);
    * if ``downgrade_downstream``: every stage strictly AFTER ``target_stage``
      with status in {done, ready, in_progress} -> ``pending`` (the rollback
      case, so the planner does not skip back over them);
    * appends one entry to ``stage_history`` (the unified transition log):
      ``{at, from_stage, to_stage, direction, reason, by}``;
    * if ``legacy_rollback_history``: ALSO appends the legacy ``rollback_history``
      entry (``{at, from_stage, to_stage, reason, rolled_back_by: by}``) so
      existing rollback consumers/tests stay green.

    ``direction`` is ``"advance"`` (target strictly later) or ``"rollback"``
    (target strictly earlier). Atomic write (sibling tmp file + ``os.replace``),
    ``indent=2, sort_keys=True`` + trailing newline. Raises
    ``ValueError`` on an unknown target or one that violates ``direction``.
    """
    import datetime as _dt

    root = Path(project_root)
    state_path = root / "research" / "PIPELINE_STATE.json"
    raw_order, items = _active_vertical_checklist_defs(project_root)
    order = [_normalize_stage(s) for s in raw_order]
    target = normalize_stage_for_project(project_root, target_stage)
    # Stage EXISTENCE is governed by STAGE_ORDER, not CHECKLIST_ITEMS: a
    # Manager-authored data domain has a full stage `order` but an EMPTY items dict
    # (the Planner authors per-stage items into the project checklist store, which is
    # not merged here), so validating against `items` would ValueError on every
    # transition and pin the mission to stage 1 forever. Built-in verticals key
    # every stage, so order-membership == items-membership for them (unchanged).
    if target not in order:
        raise ValueError(f"unknown stage {target_stage!r}")

    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    fallback_prev = order[0] if order else "research"
    previous = normalize_stage_for_project(
        project_root,
        payload.get("current_stage") or fallback_prev,
    )
    if previous not in order:
        previous = fallback_prev

    p_idx = order.index(previous)
    t_idx = order.index(target)
    if direction == "advance" and t_idx <= p_idx:
        raise ValueError(
            f"advance target {target!r} must be strictly later than current "
            f"stage {previous!r}"
        )
    if direction == "rollback" and t_idx >= p_idx:
        raise ValueError(
            f"rollback target {target!r} must be strictly earlier than current "
            f"stage {previous!r}"
        )
    # ``reset`` is reserved for a Manager-confirmed replacement objective. It
    # may legally land on the same first stage to clear stale completion state.

    payload["current_stage"] = target

    stages = payload.get("stages")
    if not isinstance(stages, dict):
        stages = {}
        payload["stages"] = stages

    if mark_current_done:
        prev_record = stages.get(previous)
        if not isinstance(prev_record, dict):
            prev_record = {}
            stages[previous] = prev_record
        prev_record["status"] = "done"
        if completion_contract_version > 0 and completion_contract_sha256:
            prev_record["completion_contract_version"] = completion_contract_version
            prev_record["completion_contract_sha256"] = completion_contract_sha256

    if downgrade_downstream:
        for stage_name in order[t_idx + 1:]:
            stage_record = stages.get(stage_name)
            if not isinstance(stage_record, dict):
                stage_record = {}
                stages[stage_name] = stage_record
            status = str(stage_record.get("status") or "").lower()
            if status in {"done", "ready", "in_progress"}:
                stage_record["status"] = "pending"

    # LIVENESS INVARIANT: the stage we just landed on must always be actionable.
    # A transition — most commonly a rollback ONTO an already-completed stage
    # (e.g. an open-ended reconcile rolling report -> setup while setup.status is
    # still "done") — that leaves ``current_stage`` with a terminal status
    # produces a hard deadlock: the Planner cannot dispatch work for a "done"
    # stage, and only the Manager may advance, so the mission spins forever
    # emitting ``planner_waiting``. Force the target stage back to an actionable
    # status so there is ALWAYS a legal next move. The Manager still owns the
    # DECISION of where to go (policy); the harness only guarantees the landing
    # state is workable (invariant), never overriding a still-actionable status.
    # EXCLUDES ``complete``: completing the final stage deliberately stamps it
    # ``done`` in place (project reads as complete) and must NOT be reopened.
    if direction != "complete":
        target_record = stages.get(target)
        if direction == "reset":
            if not isinstance(target_record, dict):
                target_record = {}
                stages[target] = target_record
            target_record["status"] = "in_progress"
        elif (
            isinstance(target_record, dict)
            and str(target_record.get("status") or "").lower() == "done"
        ):
            target_record["status"] = "in_progress"

    # Canonical RFC3339 UTC.  ``datetime.isoformat()`` emits ``+00:00``;
    # execution packets and cross-language verifiers commonly require the
    # equivalent but canonical trailing ``Z`` representation.  Existing
    # history remains valid and untouched; every new Manager transition uses Z.
    now_iso = (
        _dt.datetime.now(_dt.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    history = payload.get("stage_history")
    if not isinstance(history, list):
        history = []
        payload["stage_history"] = history
    history_entry = {
        "at": now_iso,
        "from_stage": previous,
        "to_stage": target,
        "direction": direction,
        "reason": reason,
        "by": by,
    }
    history.append(history_entry)

    if legacy_rollback_history:
        rb_history = payload.get("rollback_history")
        if not isinstance(rb_history, list):
            rb_history = []
            payload["rollback_history"] = rb_history
        rb_history.append({
            "at": now_iso,
            "from_stage": previous,
            "to_stage": target,
            "reason": reason,
            "rolled_back_by": by,
        })

    state_path.parent.mkdir(parents=True, exist_ok=True)
    # ATOMIC write: render to a sibling temp file then os.replace() (atomic on
    # POSIX). A crash mid-write (OOM / pod eviction / mission restart) must never
    # leave PIPELINE_STATE.json empty or half-written — every reader fail-opens
    # to the floor stage, silently resetting the whole project back to research.
    import os as _os
    _tmp = state_path.with_suffix(state_path.suffix + f".tmp.{_os.getpid()}")
    _tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _os.replace(_tmp, state_path)
    return str(state_path)


def advance_stage(
    project_root: Path | str,
    *,
    target_stage: str,
    reason: str,
    advanced_by: str = "manager",
) -> str:
    """Move the pipeline state machine **forward** to the next stage.

    ``target_stage`` must be the IMMEDIATE next stage in the active vertical's
    order (no skipping). Stamps the just-completed (previous) stage
    ``status=done`` and sets ``current_stage=target_stage``. Returns the written
    state-file path. Raises ``ValueError`` if the target is unknown or is not the
    immediate next stage.

    After initial state creation, ``advance_stage`` / ``rollback_stage`` are the ONLY mutators
    of ``current_stage`` — both invoked solely by the Manager, which owns stage
    authority (reviewer/planner only advise; the engineer never edits stage
    state).
    """
    raw_order, _items = _active_vertical_checklist_defs(project_root)
    order = [_normalize_stage(s) for s in raw_order]
    target = normalize_stage_for_project(project_root, target_stage)
    if target not in order:
        raise ValueError(f"unknown stage {target_stage!r}")
    cur_norm = _normalize_stage(current_stage(project_root))
    if cur_norm in order:
        nxt_idx = order.index(cur_norm) + 1
        if nxt_idx >= len(order) or order[nxt_idx] != target:
            raise ValueError(
                f"advance target {target!r} must be the immediate next stage "
                f"after {cur_norm!r}"
            )
    return _set_stage(
        project_root,
        target_stage=target,
        reason=reason,
        by=advanced_by,
        direction="advance",
        mark_current_done=True,
    )


def rollback_stage(
    project_root: Path | str,
    *,
    target_stage: str,
    reason: str,
    rolled_back_by: str = "reviewer",
) -> str:
    """Move the pipeline state machine **backward** to an earlier stage.

    Use this when reviewing stage N exposes a missing or unreliable upstream
    artifact. The next round gets the earlier stage's checklist and repairs the
    defect before advancing again.

    Behavior:
    - ``current_stage`` is set to ``target_stage``.
    - Every stage strictly between ``target_stage`` (exclusive) and the
      previous ``current_stage`` (inclusive) is downgraded from
      ``done``/``ready`` to ``pending`` so the planner does not skip
      back over them on the way up.
    - A ``rollback_history`` array is appended with the timestamp,
      previous stage, target stage, reason, and ``rolled_back_by`` so
      the journal carries an audit trail.

    Returns the rendered JSON file path written. Raises ``ValueError``
    if ``target_stage`` is unknown or not strictly earlier than the
    current stage.

    Thin wrapper over :func:`_set_stage` (the shared primitive): a rollback is a
    backward ``_set_stage`` that downgrades downstream stages and also appends
    the legacy ``rollback_history`` entry for back-compat. The unified
    ``stage_history`` log is written too.
    """

    return _set_stage(
        project_root,
        target_stage=target_stage,
        reason=reason,
        by=rolled_back_by,
        direction="rollback",
        downgrade_downstream=True,
        legacy_rollback_history=True,
    )


def reset_stage_for_replacement_intent(
    project_root: Path | str,
    *,
    target_stage: str,
    reason: str,
    reset_by: str = "manager",
) -> str:
    """Restart a staged pipeline for a Manager-confirmed replacement objective.

    Unlike an evidence rollback, this may target the current stage itself. The
    superseded objective's downstream statuses are downgraded and the target is
    made actionable immediately.
    """
    return _set_stage(
        project_root,
        target_stage=target_stage,
        reason=reason,
        by=reset_by,
        direction="reset",
        downgrade_downstream=True,
        legacy_rollback_history=True,
    )


def complete_final_stage(
    project_root: Path | str,
    *,
    reason: str,
    completed_by: str = "manager",
) -> str:
    """Mark the FINAL pipeline stage ``done`` without moving ``current_stage``.

    Used by the Manager when the reviewer certifies the final-submission stage:
    the project stays on its last stage (e.g. ``submission``) but that stage's
    ``status`` is stamped ``done`` so the project reads as complete. This is the
    terminal counterpart to :func:`advance_stage` / :func:`rollback_stage` and,
    like them, is a Manager-owned mutation (gated by the stage-transition
    authority context).

    Raises ``ValueError`` if ``current_stage`` is not the last stage in the
    active vertical's order (it is illegal to "complete" a non-final stage —
    advance to it first).
    """
    raw_order, _items = _active_vertical_checklist_defs(project_root)
    order = [_normalize_stage(s) for s in raw_order]
    cur = _normalize_stage(current_stage(project_root))
    if not order or cur != order[-1]:
        raise ValueError(
            f"complete target must be the final stage {order[-1] if order else '?'!r}; "
            f"current stage is {cur!r}"
        )
    from ..verticals._base import (
        load_vertical,
        vertical_completion_contract_version,
    )
    from .vertical_select import resolve_vertical

    try:
        vertical = resolve_vertical(project_root)
        completion_contract_version = vertical_completion_contract_version(
            load_vertical(vertical, project_root=project_root)
        )
    except Exception as exc:  # noqa: BLE001 — completion authority fails closed
        raise ValueError("completion contract unavailable") from exc
    completion_contract_sha256 = ""
    if completion_contract_version > 0:
        try:
            completion_contract_sha256 = completion_contract_fingerprint(
                project_root,
                cur,
                version=completion_contract_version,
            )
        except Exception as exc:  # noqa: BLE001 — completion must fail closed
            raise ValueError("completion contract fingerprint unavailable") from exc
    return _set_stage(
        project_root,
        target_stage=cur,
        reason=reason,
        by=completed_by,
        direction="complete",
        mark_current_done=True,
        completion_contract_version=completion_contract_version,
        completion_contract_sha256=completion_contract_sha256,
    )


def _render_items(
    items: Iterable[ChecklistItem],
    annotations: dict[str, list[str]] | None = None,
) -> str:
    annotations = annotations or {}
    lines: list[str] = []
    for item in items:
        lines.append(f"- [ ] **{item.id}** — {item.statement}")
        lines.append(f"      _evidence to look at:_ `{item.evidence_hint}`")
        for note in annotations.get(item.id, ()):  # project self-authored notes
            lines.append(f"      _project note (self-authored, revertible):_ {note}")
    return "\n".join(lines)


_FLOOR_STATEMENT = (
    "## Harness floor (non-negotiable)\n"
    "The project-authored checklist items above are ADDITIVE. They may "
    "tighten but never relax the framework: they cannot waive evidence-binding, "
    "permit fabricated or placeholder results, or lower the done criteria. On any conflict, the framework checklist wins."
)


def _augment(body: str, role: str, project_root, *, overlay_present: bool = False) -> str:
    """Append the floor whenever project checklist items were added."""
    _ = role, project_root
    parts = [body]
    if overlay_present:
        parts.append(_FLOOR_STATEMENT)
    return "\n\n".join(parts)


def _research_checklist_defs():
    from ..verticals.research.stages import (
        CANONICAL_STAGE_ORDER,
        STAGE_CHECKLISTS,
    )

    return CANONICAL_STAGE_ORDER, STAGE_CHECKLISTS


def _active_vertical_checklist_defs(project_root):
    """Return ``(stage_order, items_dict)`` for the ACTIVE vertical.

    Resolves the active vertical via ``vertical_select.resolve_vertical`` +
    ``verticals._base.load_vertical`` and returns that vertical's
    ``CHECKLIST_STAGE_ORDER`` + ``CHECKLIST_ITEMS``. ``project_root`` may be
    None (resolved from env/cwd, matching how the overlay/venue resolution
    locate the project). An entirely undecided legacy/empty project keeps the
    historical research seed; once the Manager persists a vertical, that
    committed value is authoritative and cannot be replaced by a stale env.

    Late imports keep this free of a module-load cycle: ``stage_machine`` is imported (top-level) by the vertical ``stages`` modules, so it must not
    import them at top level.
    """
    import os

    if project_root is None:
        project_root = os.environ.get("ARGUS_SKILL_PROJECT_ROOT") or "."
    try:
        from ..verticals._base import (
            load_vertical,
            vertical_checklist_items,
            vertical_checklist_stage_order,
        )
        from .vertical_select import resolve_checklist_vertical

        vertical = resolve_checklist_vertical(project_root)
        if vertical is None:
            return _research_checklist_defs()
        mod = load_vertical(vertical, project_root=project_root)
        return (
            vertical_checklist_stage_order(mod),
            vertical_checklist_items(mod),
        )
    except Exception:  # noqa: BLE001 - vertical resolution must never break prompts
        return _research_checklist_defs()


def _active_vertical_optional_stages(project_root) -> frozenset[str]:
    import os

    if project_root is None:
        project_root = os.environ.get("ARGUS_SKILL_PROJECT_ROOT") or "."
    try:
        from ..verticals._base import (
            load_vertical,
            vertical_checklist_optional_stages,
        )
        from .vertical_select import resolve_checklist_vertical

        vertical = resolve_checklist_vertical(project_root)
        if vertical is None:
            return frozenset()
        mod = load_vertical(vertical, project_root=project_root)
        return vertical_checklist_optional_stages(mod)
    except Exception:  # noqa: BLE001
        return frozenset()


def _resolve_project_root_for_store(project_root):
    """Resolve a concrete project root for the checklist-store read (never None)."""
    import os

    if project_root is None:
        return os.environ.get("ARGUS_SKILL_PROJECT_ROOT") or "."
    return project_root


def _domain_floor_items(
    project_root,
    stage: str,
) -> tuple[ChecklistItem, ...]:
    """Return mandatory checklist additions from the active built-in domain."""
    from ..domains import domain_checklist_items, load_domain
    from .vertical_select import resolve_domain_if_decided

    domain = resolve_domain_if_decided(
        _resolve_project_root_for_store(project_root)
    )
    if not domain:
        return ()
    return tuple(domain_checklist_items(load_domain(domain)).get(stage, ()))


def _append_domain_floor(
    items: tuple[ChecklistItem, ...],
    project_root,
    stage: str,
) -> tuple[ChecklistItem, ...]:
    """Append domain items without allowing an id to shadow workflow items."""
    additions = _domain_floor_items(project_root, stage)
    if not additions:
        return items
    seen = {item.id for item in items}
    duplicates = [item.id for item in additions if item.id in seen]
    if duplicates:
        raise ValueError(
            f"domain checklist duplicates workflow item ids: {', '.join(duplicates)}"
        )
    return (*items, *additions)


def _store_or_seed_items(project_root, vert_items, stage):
    """Base checklist items for ``stage`` BEFORE the additive overlay.

    The per-project, Planner-authored checklist store
    The project checklist store is the source of truth for any stage the
    Planner has authored: when ``store_items_for_stage`` returns non-``None`` it
    REPLACES the seed for that stage (including a deliberately-emptied list). When
    it returns ``None`` (the stage is absent from the store) the active vertical's
    seed constant is used — byte-identical to the historical floor. Fail-open to
    the seed on any store error so prompt building never breaks.
    """
    try:
        from .checklist_store import store_items_for_stage

        override = store_items_for_stage(
            _resolve_project_root_for_store(project_root), stage
        )
        if override is not None:
            return _append_domain_floor(tuple(override), project_root, stage)
    except Exception:  # noqa: BLE001 — store read must never break prompt building
        pass
    return _append_domain_floor(
        tuple(vert_items.get(stage, ())),
        project_root,
        stage,
    )


def resolve_stage_checklist_contract(
    stage: str,
    *,
    role: str = "reviewer",
    project_root=None,
) -> StageChecklistContract:
    """Resolve checklist provenance without treating an empty list as success."""
    stage_norm = _normalize_stage(stage)
    _stage_order, vertical_items = _active_vertical_checklist_defs(project_root)
    optional = stage_norm in _active_vertical_optional_stages(project_root)
    override = None
    try:
        from .checklist_store import store_items_for_stage

        override = store_items_for_stage(
            _resolve_project_root_for_store(project_root),
            stage_norm,
        )
    except Exception:  # noqa: BLE001
        override = None
    if override is not None:
        items = tuple(override)
        state = ChecklistLoadState.LOADED if items else ChecklistLoadState.EMPTY
    elif stage_norm in vertical_items:
        items = tuple(vertical_items.get(stage_norm, ()))
        state = ChecklistLoadState.LOADED if items else ChecklistLoadState.EMPTY
    else:
        items = ()
        state = ChecklistLoadState.NOT_LOADED
    items = _append_domain_floor(items, project_root, stage_norm)
    if items:
        state = ChecklistLoadState.LOADED
    if optional and not items:
        state = ChecklistLoadState.NOT_APPLICABLE
    return StageChecklistContract(
        stage=stage_norm,
        state=state,
        checklist_optional=optional,
        items=items,
    )


def _apply_vertical_rendering(
    body: str,
    *,
    project_root,
    role: str,
    stage: str | None = None,
) -> str:
    import os

    if project_root is None:
        project_root = os.environ.get("ARGUS_SKILL_PROJECT_ROOT") or "."
    try:
        from ..verticals._base import load_vertical
        from .vertical_select import resolve_checklist_vertical

        vertical = resolve_checklist_vertical(project_root)
        if vertical is None:
            from ..verticals.research import stages as module
        else:
            module = load_vertical(vertical, project_root=project_root)
        hook_name = (
            "render_stage_checklist_body"
            if stage is not None
            else "render_full_checklist_body"
        )
        hook = getattr(module, hook_name, None)
        if not callable(hook):
            return body
        return str(
            hook(
                body,
                project_root=project_root,
                role=role,
                **({"stage": stage} if stage is not None else {}),
            )
        )
    except Exception:  # noqa: BLE001 - vertical rendering must not break prompts
        return body


def format_stage_checklist(
    stage: str,
    *,
    role: str = "engineer",
    project_root=None,
    scope: str = "",
) -> str:
    """Render the checklist for ``stage`` as prompt-injectable markdown.

    ``role`` controls the framing line at the top:

    * ``engineer`` — "produce evidence the reviewer can tick off"
    * ``reviewer`` — "tick these items off based on artifacts you read"
    * ``critic`` / ``planner`` — "use this to decide whether more rounds add value"

    ``project_root`` locates the per-project harness overlay (``.argus/harness/``);
    when ``None`` it is resolved from ``ARGUS_SKILL_PROJECT_ROOT`` / cwd. The
    overlay is read fresh on every call so agent edits hot-reload with no daemon
    restart.

    An unknown stage or role still renders a usable block (empty body
    with a one-line note) so the caller does not need to special-case.
    """

    stage_norm = _normalize_stage(stage)
    # Vertical-aware: render the ACTIVE vertical's checklist items for this
    # stage. The generic renderer does not inspect or specialize their content.
    role_norm = (role or "engineer").strip().lower()
    contract = resolve_stage_checklist_contract(
        stage_norm,
        role=role_norm,
        project_root=project_root,
    )
    items = contract.items
    annotations: dict[str, list[str]] = {}
    if not items:
        if contract.checklist_optional:
            return (
                f"## Stage checklist ({stage_norm or 'unknown'})\n"
                "Checklist not applicable: this stage explicitly declares "
                "`checklist_optional`."
            )
        state = contract.state.value.replace("_", " ")
        return (
            f"## Stage checklist ({stage_norm or 'unknown'})\n"
            f"Configuration error: this required checklist is {state}. "
            "Do not mark the stage complete until required checklist items load."
        )

    scope_norm = (scope or "").strip().lower().replace("-", "_")
    if role_norm == "reviewer" and scope_norm == "bounded":
        framing = (
            "You are the L2 reviewer for a bounded mission. Verify the mission's "
            "explicit acceptance criteria and only the checklist items materially "
            "touched by this mission. Unrelated open items belong to later bounded "
            "missions: report them honestly, but do not use them to keep this "
            "mission running. Reply `done` when this bounded objective is satisfied; "
            "the Manager separately keeps the project stage on HOLD until every "
            "stage item is certified. Do not run any `validate-*` shell command — "
            "there isn't one. Read the relevant artifacts yourself."
        )
    elif role_norm == "reviewer":
        framing = (
            "You are the L2 reviewer. Verify each item by reading the cited "
            "evidence. Reply `continue` if any item is unmet; reply `done` only "
            "when every item is satisfied. Do not run any `validate-*` shell "
            "command — there isn't one. Read the artifacts yourself."
        )
    elif role_norm in ("critic", "planner"):
        framing = (
            "Use this checklist to judge whether another engineer round on this "
            "stage adds real value. The reviewer will rule against this list, so "
            "additional polish that does not move an unchecked item to checked "
            "is wasted budget."
        )
    else:
        framing = (
            "The L2 reviewer will tick these items against your artifacts. "
            "Produce the evidence each item names; do not look for a "
            "`validate-*` CLI — the agent surface has none. The reviewer "
            "reads files directly."
        )

    body = (
        f"## Stage checklist ({stage_norm})\n"
        f"{framing}\n\n"
        f"{_render_items(items, annotations)}"
    )
    body = _apply_vertical_rendering(
        body,
        project_root=project_root,
        role=role_norm,
        stage=stage_norm,
    )
    return _augment(
        body,
        role_norm,
        project_root,
        overlay_present=False,
    )


def _full_pipeline_title(project_root) -> str:
    """Vertical-aware title line for the full-pipeline checklist header.

    Paper-shaped verticals use ``final submission gate`` wording. Other
    verticals name themselves. Title resolution never blocks prompt building.
    """
    import os

    if project_root is None:
        project_root = os.environ.get("ARGUS_SKILL_PROJECT_ROOT") or "."
    try:
        from ..verticals._base import load_vertical, vertical_completion_gate
        from .vertical_select import resolve_checklist_vertical

        vertical = resolve_checklist_vertical(project_root)
        if vertical is None:
            return "## Full pipeline checklist (final submission gate)\n"
        if vertical_completion_gate(
            load_vertical(vertical, project_root=project_root)
        ) != "full_paper":
            return f"## Full pipeline checklist ({vertical})\n"
    except Exception:  # noqa: BLE001 — title must never break prompt building
        pass
    return "## Full pipeline checklist (final submission gate)\n"


def format_full_pipeline_checklist(
    *,
    role: str = "reviewer",
    project_root=None,
) -> str:
    """Render every stage's checklist concatenated, for final submission review."""

    title = _full_pipeline_title(project_root)
    role_norm = (role or "reviewer").strip().lower()
    if role_norm == "reviewer":
        header = (
            title
            + "Verify every stage's items end-to-end. Reply `done` only when every "
            "item across every stage is satisfied. There is no `validate-*` "
            "command to run — read the artifacts directly."
        )
    else:
        header = (
            title
            + "Every item below must be true before the project can be marked done."
        )

    blocks = [header]
    overlay_present = False
    # Iterate the active vertical's stage order and render its items.
    stage_order, vert_items = _active_vertical_checklist_defs(project_root)
    for stage in stage_order:
        annotations: dict[str, list[str]] = {}
        items = _store_or_seed_items(project_root, vert_items, stage)
        if not items:
            continue
        blocks.append(f"### {stage}\n{_render_items(items, annotations)}")
    body = "\n\n".join(blocks)
    body = _apply_vertical_rendering(
        body,
        project_root=project_root,
        role=role_norm,
    )
    return _augment(body, role_norm, project_root, overlay_present=overlay_present)
