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

    explicit non-default env ``ARGUS_SKILL_VERTICAL``  >  persisted project-local
    DATA domain when env is the safe default ``"research"``  >  persisted
    ``vertical`` in ``research/PIPELINE_STATE.json``  >  RAISE.

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
    "research", "quant", "speedrun",
    "nanochat", "nanogpt_speedrun", "kernelbench",
    "learning",
)

#: One-line purpose per built-in vertical, handed to the Manager's vertical
#: decision prompt so the agent can PREFER an existing built-in (which ships
#: expert per-stage reviewer checklists) over authoring a fresh, checklist-less
#: data domain. Keys must stay in sync with ``VERTICALS``.
VERTICAL_PURPOSES: dict[str, str] = {
    "research": "full multi-stage research-PAPER pipeline (literature review → "
    "experiments → draft → submission); the default when the goal is a written paper",
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

        1. env ``ARGUS_SKILL_VERTICAL`` — only if it names a known vertical
           (a trailing ``-needed`` sentinel is stripped first). Explicit
           non-default env values win, EXCEPT the safe default ``"research"``
           never clobbers a persisted project-local DATA domain.
        2. persisted ``vertical`` in ``research/PIPELINE_STATE.json``.

    FAIL-HARD: if neither yields a known vertical, RAISE
    ``VerticalResolutionError``. There is no silent default-to-``research`` — the
    Manager must have DECIDED and PERSISTED the vertical at mission bootstrap
    before any read. Still deterministic, never spends a token, and (apart from
    the raise) never mutates state.
    """
    env = _known_vertical(os.environ.get(ENV_VERTICAL), project_root)
    persisted = _persisted_vertical(project_root)
    if env is not None:
        if env == DEFAULT_VERTICAL and _is_project_data_domain(persisted, project_root):
            return persisted
        return env
    if persisted is not None:
        return persisted
    raise VerticalResolutionError(
        f"no vertical resolved for {project_root!r}: ARGUS_SKILL_VERTICAL is unset/invalid "
        f"and research/PIPELINE_STATE.json has no known 'vertical'. The Manager must decide "
        f"and persist the vertical at mission bootstrap before this read."
    )


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


__all__ = [
    "VERTICALS",
    "VERTICAL_PURPOSES",
    "DEFAULT_VERTICAL",
    "ENV_VERTICAL",
    "VerticalResolutionError",
    "UnknownVerticalError",
    "require_vertical",
    "resolve_vertical",
    "persist_vertical",
]
