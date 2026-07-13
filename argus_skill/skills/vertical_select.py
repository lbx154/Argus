"""Vertical selection for the auto-research loop.

The loop runs ONE of several *verticals*, selected by a single ``vertical``
field in ``research/PIPELINE_STATE.json``:

* ``"research"`` — the full eight-stage research-paper pipeline
  (research → ... → submission). This is the default and the safe fallback
  whenever intent is unclear: producing a paper subsumes the optimize work,
  so over-running is never a correctness hazard, only a cost one.
* ``"speedrun"`` — the lean numeric-optimization vertical (setup → optimize →
  measure → report). No literature review, no draft, no reviewer simulation,
  no submission packaging. Used when the objective is "make this number go the
  right way on this script" rather than "write me a paper". This is the
  nanochat-autoresearch / GPU-kernel-speedrun shape.

Two sides of the selector live here (the DECIDE side is no longer here — the
Manager AGENT chooses the vertical; see ``manager/_core.py`` ``decide_vertical``
and ``manager/domain_author.py``):

* the **read side** (``resolve_vertical``) is cheap, deterministic, and
  LLM-free. It reads the vertical the Manager already decided and persisted. It
  is FAIL-HARD: if nothing valid is resolvable it RAISES
  ``VerticalResolutionError`` rather than silently defaulting to ``"research"``.
* the **write side** (``persist_vertical``) writes the chosen vertical into the
  pipeline state and seeds ``current_stage`` to the vertical's first stage. It
  validates the name (``require_vertical``) and RAISES on an unknown vertical or
  a corrupt state file — no swallowed errors.

Precedence for the resolved vertical (read side):

    persisted project-local DATA domain  >  explicit non-default env
    ``ARGUS_SKILL_VERTICAL``  >  persisted built-in ``vertical`` in
    ``research/PIPELINE_STATE.json``  >  RAISE.

A Manager-authored DATA domain must remain authoritative after it is persisted.
The daemon may still carry the broader built-in vertical selected before the
Manager authored that domain; allowing that inherited env value to win would
silently replace the domain's stage contract with the built-in one.

There are NO keyword classifiers and NO fallbacks: an objective is never mapped
to a vertical by matching words, and a missing/corrupt state is never quietly
coerced to ``"research"``. The Manager decides; the harness only validates,
persists, and reads back — loudly.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


# --- constants -------------------------------------------------------------

#: Known verticals. ``"research"`` is first and is the canonical default.
#: ``"quant"`` is the finance factor-research vertical — a REPORT peer of
#: ``research`` (it produces a reviewer-certified factor report, not a numeric
#: metric), so it is NOT an optimize vertical and is never routed under speedrun.
#: ``"speedrun"`` is the generic numeric-optimization vertical; the three
#: per-task verticals below are the distinct Recursive "First Steps" tasks,
#: each optimizing its OWN metric (so they are never conflated under speedrun):
#:   nanochat         — Task 1: minimize val_bpb (300s, 1 GPU)
#:   nanogpt_speedrun — Task 2: minimize wall-time to val_loss<=3.28 (8xH100)
#:   kernelbench      — Task 3: maximize SOL score (B200 kernels)
VERTICALS: tuple[str, ...] = (
    "direct", "research", "math", "quant", "speedrun",
    "nanochat", "nanogpt_speedrun", "kernelbench",
    "learning", "ale_last_exam", "fiction_writing",
)

#: One-line purpose per built-in vertical, handed to the Manager's vertical
#: decision prompt so the agent can PREFER an existing built-in (which ships
#: expert per-stage reviewer checklists) over authoring a fresh, checklist-less
#: data domain. Keys must stay in sync with ``VERTICALS``.
VERTICAL_PURPOSES: dict[str, str] = {
    "direct": "bounded one-off deliverable that an Engineer can produce and a "
    "Reviewer can judge in one mission, without a staged research lifecycle "
    "(creative composition, focused edits, small standalone artifacts)",
    "research": "full multi-stage research-PAPER pipeline (literature review → "
    "experiments → draft → submission); the default when the goal is a written paper",
    "math": "mathematical conjectures, proofs, and open research problems; dynamically "
    "choose background retrieval, examples/counterexamples, computation, natural-language "
    "proof, and Lean formalization as appropriate; not a paper pipeline or a "
    "metric-optimization vertical",
    "quant": "finance factor-research REPORT — mine/evaluate equity factors "
    "(IC/ICIR, backtest, Sharpe) into a reviewer-certified factor report; not a metric loop",
    "speedrun": "generic single-metric optimize loop on a script/benchmark under a "
    "wall-clock budget (setup → optimize → measure → report); no paper",
    "nanochat": "minimize val_bpb on the nanochat train.py (bits-per-byte, ~300s, 1 GPU)",
    "nanogpt_speedrun": "minimize wall-clock time to reach val_loss<=3.28 on modded-nanogpt (8xH100)",
    "kernelbench": "maximize SOL score / speedup for GPU kernels (CUDA/Triton/CUTLASS, "
    "B200, SOL-ExecBench/KernelBench) against a correctness-checked reference",
    "learning": "ingest operator-provided learning material and update the skill/wiki "
    "libraries (produce a change plan: create/update/archive skills)",
    "ale_last_exam": "complete one Agents' Last Exam long-horizon professional "
    "workflow in a real computer sandbox; hidden-reference, artifact-first GUI+CLI delivery",
    "fiction_writing": "creative FICTION authoring (zh/en) — write a short story or "
    "chapter from a brief, OR continue an existing work, holding characters/world/"
    "timeline consistent via a structured story_state; intake→plan→draft→state_update"
    "→review→revise. NOT a research paper and NOT a 'literature review' — this "
    "produces original narrative prose, not a survey of prior work",
}

#: The safe default vertical when intent is unclear or state is missing.
DEFAULT_VERTICAL: str = "research"

#: Environment override consulted first by ``resolve_vertical``.
ENV_VERTICAL: str = "ARGUS_SKILL_VERTICAL"

_STATE_RELPATH = ("research", "PIPELINE_STATE.json")


class VerticalResolutionError(RuntimeError):
    """Raised by ``resolve_vertical`` when no vertical can be resolved.

    The Manager DECIDES and PERSISTS the vertical at mission bootstrap; once it
    has, ``research/PIPELINE_STATE.json`` names it and this never fires. If it
    DOES fire, a read happened before the decision was persisted, or the state
    is corrupt — a real invariant violation, surfaced loudly instead of silently
    defaulting to ``research``.
    """


class UnknownVerticalError(ValueError):
    """Raised when a value is required to name a known vertical but does not."""



# --- normalization / read side --------------------------------------------


def _strip_needed(value: str) -> str:
    """Drop a trailing ``-needed`` sentinel (main's pre-writer placeholder)."""
    cleaned = value.strip().lower()
    if cleaned.endswith("-needed"):
        cleaned = cleaned[: -len("-needed")]
    return cleaned


def _known_vertical(value: object, project_root: object = None) -> str | None:
    """Return the normalized vertical name if known, else ``None``.

    Strips whitespace/case and a trailing ``-needed`` sentinel. A value that
    names a built-in vertical (the ``VERTICALS`` tuple) is always accepted.
    When ``project_root`` is given, a value that names an existing project-local
    DATA domain (``research/DOMAINS/<name>.json``) is ALSO accepted — this is how
    a Manager-authored data domain flows through the same resolution path as the
    built-in verticals. Returns ``None`` for non-strings, junk, or any value that
    is neither a built-in vertical nor an existing data domain, so the caller can
    fall through to the next precedence source.
    """
    if not isinstance(value, str):
        return None
    cleaned = _strip_needed(value)
    if cleaned in VERTICALS:
        return cleaned
    if project_root is not None and cleaned:
        try:
            from ..verticals._data_domain import data_domain_exists  # late (cycle)

            if data_domain_exists(cleaned, project_root):
                return cleaned
        except Exception:  # noqa: BLE001 — data-domain probe must never raise here
            return None
    return None


def explicit_builtin_vertical() -> str | None:
    """Return the built-in vertical explicitly selected by the environment.

    Project-local data domains are intentionally excluded: this signal is the
    operator choosing a stable built-in capability, not the Manager recovering a
    previously-authored project route.
    """
    return _known_vertical(os.environ.get(ENV_VERTICAL))


def require_vertical(value: object, project_root: object = None) -> str:
    """Return the known vertical named by ``value`` or raise ``UnknownVerticalError``.

    Replaces the old ``normalize_vertical``, which silently defaulted unknowns to
    ``"research"``. The operator's contract is fail-hard: an unknown vertical is
    an error, never a silent coercion.
    """
    known = _known_vertical(value, project_root)
    if known is None:
        raise UnknownVerticalError(
            f"{value!r} is not a known vertical "
            f"(built-ins: {', '.join(VERTICALS)}) nor an existing project data domain"
        )
    return known


def _normalize_stage(stage: object) -> str:
    if not isinstance(stage, str):
        return ""
    return stage.strip().lower()


def _state_path(project_root: object) -> Path:
    return Path(str(project_root)).joinpath(*_STATE_RELPATH)


def _persisted_vertical(project_root: object) -> str | None:
    """Return the persisted ``vertical`` from PIPELINE_STATE.json, or ``None``.

    ``None`` only for the legitimate "not decided yet" case: the state file does
    not exist, OR it exists but carries no (known) ``vertical`` key. A present
    but CORRUPT file (bad JSON / non-dict payload) is a real fault of
    Manager-owned state and RAISES ``VerticalResolutionError`` — we do not
    silently treat corruption as "fresh" and fall through to research.
    """
    try:
        raw = _state_path(project_root).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VerticalResolutionError(
            f"PIPELINE_STATE.json at {_state_path(project_root)} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise VerticalResolutionError(
            f"PIPELINE_STATE.json at {_state_path(project_root)} is not a JSON object"
        )
    return _known_vertical(payload.get("vertical"), project_root)


def _is_project_data_domain(value: str | None, project_root: object) -> bool:
    """Whether ``value`` names a project-local DATA domain, not a built-in vertical."""
    if value is None or value in VERTICALS:
        return False
    try:
        from ..verticals._data_domain import data_domain_exists  # late (cycle)

        return data_domain_exists(value, project_root)
    except Exception:  # noqa: BLE001 — resolver must remain fail-open
        return False


def resolve_vertical(project_root: object = ".") -> str:
    """Resolve the active vertical (cheap, deterministic, no LLM).

    Precedence:

        1. A persisted project-local DATA domain. It is the Manager's committed
           task contract and wins over a broader built-in env value inherited
           from mission bootstrap.
        2. env ``ARGUS_SKILL_VERTICAL`` — only if it names a known vertical
           (a trailing ``-needed`` sentinel is stripped first).
        3. A persisted built-in ``vertical``.

    FAIL-SOFT: if neither yields a known vertical, WARN and return
    ``DEFAULT_VERTICAL`` rather than raising — a rigid rule must never hard-crash
    a mission. The Manager is still the decider (its persisted value wins above);
    the default only catches the un-decided edge (a read that raced the persist,
    or a conversational mission). Still deterministic, never spends a token, and
    never mutates state.
    """
    env = _known_vertical(os.environ.get(ENV_VERTICAL), project_root)
    persisted = _persisted_vertical(project_root)
    if _is_project_data_domain(persisted, project_root):
        return persisted
    if env is not None:
        return env
    if persisted is not None:
        return persisted
    # FAIL-SOFT (was fail-hard): the Manager is meant to DECIDE and PERSIST a
    # vertical at mission bootstrap, but a rigid rule must never hard-crash a
    # whole mission (and block the daemon) just because a read raced ahead of the
    # persist, or a conversational/trivial mission never armed one. Fall back to
    # the safe general default and WARN (visible, not silent) — the same thing
    # the CLI already does (apps/cli/_core.py catches this and defaults to
    # "research"). Real research missions still get the Manager-decided vertical
    # via the persisted value above; only the un-decided edge lands here.
    log.warning(
        "no vertical resolved for %r (ARGUS_SKILL_VERTICAL unset/invalid and "
        "research/PIPELINE_STATE.json has no known 'vertical'); falling back to %s. "
        "The Manager should decide + persist a vertical at mission bootstrap.",
        project_root, DEFAULT_VERTICAL,
    )
    return DEFAULT_VERTICAL


# --- persistence (write side) ---------------------------------------------


def _vertical_first_stage(vertical: str, project_root: object = None) -> str | None:
    """Return the active vertical's first System-(B) checklist stage, if any.

    Late import to avoid a module-load cycle (``_base`` ↔ ``stage_checklists``).
    ``project_root`` is threaded so a project-local DATA domain resolves to its
    own first stage. Fail-open: any error yields ``None`` so persistence never
    breaks bootstrap.
    """
    try:
        from ..verticals._base import (
            load_vertical,
            vertical_checklist_stage_order,
        )

        order = vertical_checklist_stage_order(load_vertical(vertical, project_root=project_root))
        return _normalize_stage(order[0]) if order else None
    except Exception:  # noqa: BLE001 — best-effort: never break persistence
        return None


def persist_vertical(project_root: object, vertical: str) -> None:
    """Persist the chosen ``vertical`` into ``research/PIPELINE_STATE.json``.

    Validates ``vertical`` against the known built-ins + existing project data
    domains; an unknown name RAISES ``UnknownVerticalError`` (no silent coercion
    to ``research``). A corrupt existing state file RAISES. IO errors PROPAGATE —
    persisting the Manager's decision is load-bearing, not best-effort.

    STAGE AUTHORITY — the harness must NOT control ``current_stage``; only the
    reviewer agent moves it (advance via its verdict, or roll back via
    ``stage_checklists.rollback_stage``). So this function SEEDS the vertical's
    first stage only when no stage exists yet (bootstrap of a fresh state
    file); it NEVER overwrites or resets an existing stage. A stale stage left
    by a vertical change is real progress — clobbering it to the first stage is
    an unauthorized rollback that destroys evidence. It is left for the
    reviewer / rollback path to handle, and the read-side ``current_stage()``
    already falls back to the vertical's first stage at read time without
    mutating the file.
    """
    vert = require_vertical(vertical, project_root)
    path = _state_path(project_root)

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        payload: dict = {}
    else:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VerticalResolutionError(
                f"PIPELINE_STATE.json at {path} is not valid JSON: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise VerticalResolutionError(
                f"PIPELINE_STATE.json at {path} is not a JSON object"
            )

    payload["vertical"] = vert

    # SEED-ONLY, NEVER RESET. Stage authority belongs to the reviewer agent
    # (see docstring). Write an initial stage only when none exists yet — leave
    # any existing stage, even one not in this vertical's order, untouched.
    if not _normalize_stage(payload.get("current_stage")):
        first_stage = _vertical_first_stage(vert, project_root)
        if first_stage:
            payload["current_stage"] = first_stage

    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(rendered, encoding="utf-8")
    os.replace(tmp_path, path)


# --- new-intent vs. reclassification triage --------------------------------


def vertical_reached_own_terminal_stage(project_root: object, vertical: str) -> bool:
    """Whether ``vertical``'s OWN last checklist stage is the raw persisted
    ``current_stage`` in ``research/PIPELINE_STATE.json`` AND that stage's
    ``status`` is ``"done"`` — i.e. a project fully completed under
    ``vertical`` on its own stage list.

    This is the signal :func:`reset_stage_for_new_intent` uses to distinguish
    "the SAME evolving project got reclassified mid-flight" (a stale/foreign
    stage name is real progress and must be PRESERVED — see
    ``persist_vertical``'s seed-only contract) from "a totally different,
    already-finished prior vertical's leftover stage is being inherited by a
    brand-new, unrelated operator intent" (the stage must be RESET). Fail-open:
    any error (unknown vertical, missing/corrupt state, non-dict payload)
    returns ``False`` so callers never reset on ambiguous data.
    """
    try:
        from ..verticals._base import load_vertical, vertical_checklist_stage_order

        order = vertical_checklist_stage_order(
            load_vertical(vertical, project_root=project_root)
        )
    except Exception:  # noqa: BLE001 — never raise on a probe
        return False
    if not order:
        return False
    last_stage = _normalize_stage(order[-1])

    try:
        raw = _state_path(project_root).read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False

    if _normalize_stage(payload.get("current_stage")) != last_stage:
        return False

    stages = payload.get("stages")
    if not isinstance(stages, dict):
        return False
    record = stages.get(last_stage)
    if not isinstance(record, dict):
        # Tolerate a differently-cased key in the stored ``stages`` dict.
        for key, value in stages.items():
            if _normalize_stage(key) == last_stage and isinstance(value, dict):
                record = value
                break
    if not isinstance(record, dict):
        return False
    return str(record.get("status") or "").strip().lower() == "done"


def reset_stage_for_new_intent(
    project_root: object,
    *,
    old_vertical: str | None,
    new_vertical: str,
) -> bool:
    """Reset ``current_stage`` to ``new_vertical``'s first stage when a
    genuinely NEW, operator-issued intent supersedes a DIFFERENT,
    already-finished prior vertical.

    Call this AFTER ``persist_vertical(project_root, new_vertical)`` has
    already run, so the stage machinery (``current_stage`` /
    ``rollback_stage``) resolves against the NEW vertical. ``old_vertical``
    must be the vertical name that was persisted BEFORE that call (e.g. from
    ``resolve_vertical`` or a raw read taken prior to persisting), so this can
    compare against what came before.

    Rationale: ``persist_vertical`` is intentionally seed-only and never
    resets an existing ``current_stage`` — correct for in-project
    reclassification (e.g. research -> speedrun mid-project; see
    ``test_persist_vertical_never_resets_existing_stage``), where a
    stage name foreign to the new vertical is still real progress that must
    be preserved. But when the OLD vertical had already reached ITS OWN
    terminal stage with ``status="done"`` (fully completed on its own stage
    list) and a brand-new intent now assigns a DIFFERENT vertical, that stale
    stage is leftover from an unrelated, closed-out project. If its name
    happens to collide with a stage name in the NEW vertical's order (e.g.
    both call a stage "review"), ``current_stage()`` would silently accept it
    as real progress on the new project — a false stage advance with zero
    underlying evidence. This function detects exactly that case and rolls
    the state back to the new vertical's first stage via
    ``stage_checklists.rollback_stage`` (audited, ``rolled_back_by="manager"``),
    without touching ``persist_vertical``'s own never-reset contract.

    Returns ``True`` iff a reset was actually applied. No-op (returns
    ``False``) when: there is no prior vertical, the vertical is unchanged
    (same evolving project — preserve stage), the prior vertical was not
    actually finished, or the rollback primitive rejects the target stage
    (e.g. the stale stage was never even a member of the new vertical's
    order, in which case ``current_stage()`` already falls back safely on its
    own). Fail-open: any error is treated as "nothing to reset" so a probe or
    rollback hiccup never blocks the Manager's division.
    """
    if not old_vertical or old_vertical == new_vertical:
        return False
    if not vertical_reached_own_terminal_stage(project_root, old_vertical):
        return False

    try:
        from ..verticals._base import load_vertical, vertical_checklist_stage_order

        new_order = vertical_checklist_stage_order(
            load_vertical(new_vertical, project_root=project_root)
        )
    except Exception:  # noqa: BLE001 — never break division on a probe failure
        return False
    if not new_order:
        return False

    try:
        from .stage_checklists import rollback_stage  # late (cycle)

        rollback_stage(
            project_root,
            target_stage=new_order[0],
            reason=(
                f"prior vertical {old_vertical!r} had already reached its own "
                f"terminal stage (done); a genuinely new operator-issued "
                f"intent assigned a different vertical {new_vertical!r} — "
                f"resetting current_stage to its first stage rather than "
                f"silently inheriting the old, unrelated vertical's "
                f"same-named stale stage."
            ),
            rolled_back_by="manager",
        )
    except ValueError:
        log.debug(
            "reset_stage_for_new_intent: rollback rejected for %r -> %r "
            "(stale stage likely not a member of the new vertical's order; "
            "current_stage() already falls back safely)",
            old_vertical, new_vertical, exc_info=True,
        )
        return False
    return True


__all__ = [
    "VERTICALS",
    "VERTICAL_PURPOSES",
    "DEFAULT_VERTICAL",
    "ENV_VERTICAL",
    "VerticalResolutionError",
    "UnknownVerticalError",
    "explicit_builtin_vertical",
    "require_vertical",
    "resolve_vertical",
    "persist_vertical",
    "vertical_reached_own_terminal_stage",
    "reset_stage_for_new_intent",
]
