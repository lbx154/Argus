"""Vertical selection for the auto-research loop.

The loop runs ONE of several *verticals*, selected by a single ``vertical``
field in ``.argus/PIPELINE_STATE.json``:

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

The resolved vertical has one authority: the Manager-persisted ``vertical`` in
``.argus/PIPELINE_STATE.json`` (including a Manager-authored data domain).

There are NO keyword classifiers and NO fallbacks: an objective is never mapped
to a vertical by matching words, and a missing/corrupt state is never quietly
coerced to ``"research"``. The Manager decides; the harness only validates,
persists, and reads back — loudly.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from ..core.pipeline_state import (
    pipeline_state_path,
    primary_pipeline_state_path,
    read_pipeline_state,
    write_pipeline_state,
)

log = logging.getLogger(__name__)


# --- constants -------------------------------------------------------------

#: Known verticals. ``"research"`` is first and is the canonical default.
#: ``"quant"`` is the finance factor-research vertical — a REPORT peer of
#: ``research`` (it produces a reviewer-certified factor report, not a numeric
#: metric), so it is NOT an optimize vertical and is never routed under speedrun.
#: ``"speedrun"`` is the generic numeric-optimization vertical;
#: ``"kernel_engineering"`` is the production/repository GPU-kernel vertical;
#: the three per-task verticals below are the distinct Recursive "First Steps" tasks,
#: each optimizing its OWN metric (so they are never conflated under speedrun):
#:   nanochat         — Task 1: minimize val_bpb (300s, 1 GPU)
#:   nanogpt_speedrun — Task 2: minimize wall-time to val_loss<=3.28 (8xH100)
#:   kernelbench      — Task 3: maximize SOL score (B200 kernels)
VERTICALS: tuple[str, ...] = (
    "software", "argus_maintenance", "digital_circuit", "digital_circuit_benchmark", "chip_design",
    "research", "medical", "math", "math_synth", "physics", "materials", "quant", "speedrun",
    "kernel_engineering", "nanochat", "nanogpt_speedrun", "kernelbench",
    "learning", "ale_last_exam", "fiction_writing", "classical_poetry",
    "modern_poetry", "prose", "literary_editor",
)

#: One-line purpose per built-in vertical, handed to the Manager's vertical
#: decision prompt so the agent can PREFER an existing built-in (which ships
#: expert per-stage reviewer checklists) over authoring a fresh, checklist-less
#: data domain. Keys must stay in sync with ``VERTICALS``.
VERTICAL_PURPOSES: dict[str, str] = {
    "software": "software engineering: repository repairs, features, tests, tooling, and "
    "ordinary implementation; not specialized hardware/runtime performance research",
    "argus_maintenance": "Argus framework repair and architecture improvement with "
    "independent regression and release checks",
    "digital_circuit": "Verilog/SystemVerilog RTL, testbenches, formal verification, "
    "FPGA/ASIC synthesis, timing, and sign-off",
    "digital_circuit_benchmark": "single-stage fixed-harness RTL benchmark: interface, RTL, "
    "local verification, pre-score elaboration, and attempt handoff",
    "chip_design": "end-to-end digital ASIC/accelerator design from workload and "
    "microarchitecture through RTL, physical implementation, and sign-off",
    "research": "substantial scholarly survey or original research paper: literature, "
    "optional experiments, synthesis, drafting, and review; submission is optional",
    "medical": "biomedical and pharmaceutical evidence execution: target-disease "
    "mechanisms, human genetics, preclinical translation, clinical trials, safety, "
    "failed programs, competitive pipelines, and auditable non-diagnostic decision "
    "dossiers with independent review; not a generic paper pipeline",
    "math": "mathematical conjectures, proofs, and open problems using literature, "
    "computation, natural-language proof, or Lean as needed",
    "math_synth": "math-reasoning data synthesis: maximize pass@4-minus-pass@1 while "
    "the solver, verifier, metric, seeds, and evaluator stay frozen",
    "physics": "theory, simulation, data analysis, literature, or experiment design "
    "for a real physical system with bounded evidence",
    "materials": "materials science and materials processing across atomistic, "
    "microstructure, continuum, CAD/CAE, and experimental scales",
    "quant": "equity factor research (IC/ICIR, backtest, Sharpe) producing a "
    "reviewer-certified report, not a generic metric loop",
    "speedrun": "single-metric script/benchmark optimization under a wall-clock budget: "
    "setup, optimize, measure, report; no paper",
    "kernel_engineering": "accelerator runtime, model inference/serving, communication, "
    "memory movement, and production CUDA/HIP/Triton/TileLang/CUTLASS/PyTorch kernel "
    "performance work in a repository; not a fixed SOL-ExecBench competition",
    "nanochat": "minimize val_bpb on the nanochat train.py (bits-per-byte, ~300s, 1 GPU)",
    "nanogpt_speedrun": "minimize wall-clock time to reach val_loss<=3.28 on modded-nanogpt (8xH100)",
    "kernelbench": "maximize correctness-checked SOL score/speedup for GPU kernels on "
    "B200 SOL-ExecBench/KernelBench",
    "learning": "ingest operator material and create, update, or archive skill/wiki knowledge",
    "ale_last_exam": "Agents' Last Exam long-horizon professional workflow in a real "
    "sandbox with hidden-reference, artifact-first GUI+CLI delivery",
    "fiction_writing": "write or continue original fiction narrative prose while preserving "
    "characters, world, and timeline; not a literature review or research task",
    "classical_poetry": "compose or check classical Chinese 近体诗/古体/词 with reproducible "
    "押韵/平仄 prosody and literary review",
    "modern_poetry": "compose or revise modern free verse/prose poems without classical "
    "prosody checks; enforce only declared hard constraints",
    "prose": "compose or revise literary essays, memoir, or 抒情/叙事散文/随笔; not verse "
    "or plot-driven fiction",
    "literary_editor": "rewrite, expand, polish, proofread, or critique an existing "
    "literary text while preserving edit scope and source facts",
}

#: The safe default vertical when intent is unclear or state is missing.
DEFAULT_VERTICAL: str = "research"

#: Legacy environment name retained for low-level compatibility/introspection.
#: Formal task routing does not consult it; Manager owns vertical classification.
ENV_VERTICAL: str = "ARGUS_SKILL_VERTICAL"

class VerticalResolutionError(RuntimeError):
    """Raised by ``resolve_vertical`` when no vertical can be resolved.

    The Manager DECIDES and PERSISTS the vertical on the initial task; once it
    has, ``.argus/PIPELINE_STATE.json`` names it and this never fires. If it
    DOES fire, a read happened before the decision was persisted, or the state
    is corrupt — a real invariant violation, surfaced loudly instead of silently
    defaulting to ``research``.
    """


class UnknownVerticalError(ValueError):
    """Raised when a value is required to name a known vertical but does not."""



def available_verticals() -> tuple[str, ...]:
    """Built-ins followed by valid installed plugins."""
    from ..verticals._registry import vertical_plugins

    return (*VERTICALS, *(name for name in vertical_plugins() if name not in VERTICALS))


def available_vertical_purposes() -> dict[str, str]:
    from ..verticals._registry import vertical_plugins

    purposes = dict(VERTICAL_PURPOSES)
    purposes.update({
        name: plugin.purpose
        for name, plugin in vertical_plugins().items()
        if name not in purposes
    })
    return purposes


# --- normalization / read side --------------------------------------------


def _strip_needed(value: str) -> str:
    """Drop a trailing ``-needed`` sentinel (main's pre-writer placeholder)."""
    cleaned = value.strip().lower()
    if cleaned.endswith("-needed"):
        cleaned = cleaned[: -len("-needed")]
    # Migration only: ``direct`` used to conflate a capability vertical with an
    # execution topology. Old persisted state now means software + direct mode.
    if cleaned == "direct":
        return "software"
    return cleaned


def _normalize_workflow_mode(value: object) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in {"direct", "staged"} else ""


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
    if cleaned in available_verticals():
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
    """Return a legacy built-in environment hint without applying it.

    This compatibility accessor supports low-level callers that inspect the old
    ``ARGUS_SKILL_VERTICAL`` signal.  It deliberately has no authority over
    ``resolve_vertical`` or Manager dispatch: formal tasks are classified and
    persisted by Manager, and users cannot bypass that decision via environment.
    Project-local data domains are excluded because they are Manager-owned state.
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
            f"(available: {', '.join(available_verticals())}) nor an existing project data domain"
        )
    return known


def _normalize_stage(stage: object) -> str:
    if not isinstance(stage, str):
        return ""
    return stage.strip().lower()


def _state_path(project_root: object) -> Path:
    return pipeline_state_path(project_root)


def _load_state_payload(project_root: object) -> dict:
    """Read Manager-owned pipeline state once with fail-visible corruption."""
    try:
        return read_pipeline_state(project_root)
    except json.JSONDecodeError as exc:
        path = _state_path(project_root)
        raise VerticalResolutionError(
            f"PIPELINE_STATE.json at {path} is not valid JSON: {exc}"
        ) from exc
    except ValueError as exc:
        path = _state_path(project_root)
        raise VerticalResolutionError(
            f"PIPELINE_STATE.json at {path} is not a JSON object"
        ) from exc


def migrate_legacy_manager_state(
    state_root: Path | str,
    legacy_root: Path | str,
) -> bool:
    """Import pre-isolation Manager state once without mutating the workspace.

    Raises when the source payload NAMES a vertical this installation cannot
    resolve: importing it would seat the campaign on a vertical whose stages,
    checklist, and completion hooks do not exist, and every later read would
    fail somewhere less legible than here.

    A payload that names NO vertical is a different situation and is imported
    normally. The workdir copy of ``.argus/PIPELINE_STATE.json`` is not only a
    legacy artifact — it is the live evidence root (every Manager stage call
    passes ``evidence_root=self.execution_workdir``), so a project can hold
    Manager-owned keys there, such as the math vertical's objective mode, before
    the Manager has decided anything. Refusing those bricks the project: this
    runs inside ``build_life_runner``, so raising kills the front-door runner,
    which is reported to the operator as "could not classify … please retry" —
    advice that can never succeed, for a project that is merely undecided.
    """
    target_root = Path(state_root).expanduser()
    source_root = Path(legacy_root).expanduser()
    try:
        if target_root.resolve() == source_root.resolve():
            return False
    except OSError:
        return False
    target = primary_pipeline_state_path(target_root)
    source = _state_path(source_root)
    if target.exists() or not source.is_file():
        return False
    payload = _load_state_payload(source_root)
    if not payload:
        return False

    from ..verticals._data_domain import migrate_data_domains

    migrate_data_domains(source_root, target_root)
    named = payload.get("vertical")
    names_a_vertical = isinstance(named, str) and named.strip() != ""
    if names_a_vertical and _known_vertical(named, target_root) is None:
        raise VerticalResolutionError(
            f"legacy Manager state at {source} names vertical {named!r}, which is "
            f"neither a built-in vertical (available: "
            f"{', '.join(available_verticals())}) nor a project data domain"
        )
    write_pipeline_state(target_root, payload)
    if names_a_vertical:
        # Warm read that proves the imported decision resolves against the new
        # root. Skipped when undecided: `resolve_vertical` would only log its
        # "no Manager vertical resolved" fallback warning for a project that is
        # correctly still waiting for the Manager to choose.
        resolve_vertical(target_root)
    return True


def _persisted_vertical(project_root: object) -> str | None:
    """Return the persisted ``vertical`` from PIPELINE_STATE.json, or ``None``.

    ``None`` only for the legitimate "not decided yet" case: the state file does
    not exist, OR it exists but carries no (known) ``vertical`` key. A present
    but CORRUPT file (bad JSON / non-dict payload) is a real fault of
    Manager-owned state and RAISES ``VerticalResolutionError`` — we do not
    silently treat corruption as "fresh" and fall through to research.
    """
    payload = _load_state_payload(project_root)
    return _known_vertical(payload.get("vertical"), project_root)


def _persisted_domain(project_root: object) -> str | None:
    """Return the optional built-in domain composed with ``research``."""
    payload = _load_state_payload(project_root)
    raw = str(payload.get("domain") or "").strip()
    if not raw:
        return None
    vertical = _known_vertical(payload.get("vertical"), project_root)
    if vertical != "research":
        raise VerticalResolutionError(
            f"PIPELINE_STATE.json at {_state_path(project_root)} sets domain={raw!r} "
            f"for non-research vertical={vertical!r}"
        )
    from ..domains import UnknownDomainError, require_domain

    try:
        return require_domain(raw)
    except UnknownDomainError as exc:
        raise VerticalResolutionError(
            f"PIPELINE_STATE.json at {_state_path(project_root)} names unknown "
            f"domain {raw!r}"
        ) from exc


def resolve_workflow_mode(project_root: object = ".") -> str:
    """Return the Manager-persisted orchestration mode."""
    payload = _load_state_payload(project_root)
    mode = _normalize_workflow_mode(payload.get("workflow_mode"))
    if mode:
        return mode
    # Backward-compatible migration for state written when `direct` was itself
    # a vertical. New writes never persist that value.
    if str(payload.get("vertical") or "").strip().lower() == "direct":
        return "direct"
    return "staged"


def resolve_evidence_mode(project_root: object = ".") -> str:
    """Return direct/staged/proportional evidence policy for agent prompts.

    ``workflow_mode`` is a Manager-owned orchestration axis (direct vs staged).
    ``WORKFLOW_MODE = "proportional"`` on a vertical is an evidence-reuse policy,
    not a request to bypass stage orchestration. A direct Manager decision still
    wins; otherwise the vertical may choose proportional evidence inside its
    staged pipeline.
    """
    orchestration = resolve_workflow_mode(project_root)
    if orchestration == "direct":
        return "direct"
    try:
        from ..verticals._base import load_vertical, vertical_workflow_mode

        vertical = resolve_vertical(project_root)
        mode = vertical_workflow_mode(
            load_vertical(vertical, project_root=project_root)
        )
        return "proportional" if mode == "proportional" else "staged"
    except Exception:  # noqa: BLE001 — evidence policy must not break prompts
        return "staged"


def resolve_vertical_if_decided(project_root: object = ".") -> str | None:
    """Return the Manager-decided vertical, or ``None`` without a fallback."""
    return _persisted_vertical(project_root)


def resolve_domain_if_decided(project_root: object = ".") -> str | None:
    """Return the Manager-decided research domain, or ``None``."""
    return _persisted_domain(project_root)


def resolve_skill_scope(project_root: object = ".") -> str:
    """Return the shared-Skill namespace for the active workflow/domain context."""
    return _persisted_domain(project_root) or _persisted_vertical(project_root) or ""


def resolve_checklist_vertical(project_root: object = ".") -> str | None:
    """Resolve the vertical that owns this project's checklist.

    The Manager-persisted project decision is the only authority.
    """
    return _persisted_vertical(project_root)


def resolve_vertical(project_root: object = ".") -> str:
    """Resolve the active vertical (cheap, deterministic, no LLM).

    The Manager-persisted project vertical is the only authority.

    Formal task entry points require a Manager-persisted decision. The research
    fallback is retained only for low-level library/legacy callers that do not
    dispatch a formal task through Manager.
    """
    decided = resolve_vertical_if_decided(project_root)
    if decided is not None:
        return decided
    log.warning(
        "no Manager vertical resolved for %r; using research only as a low-level "
        "compatibility fallback (formal tasks must classify through Manager)",
        project_root,
    )
    return DEFAULT_VERTICAL


# --- persistence (write side) ---------------------------------------------


def _vertical_first_stage(vertical: str, project_root: object = None) -> str | None:
    """Return the active vertical's first System-(B) checklist stage, if any.

    Late import to avoid a module-load cycle (``_base`` ↔ ``stage_machine``).
    ``project_root`` is threaded so a project-local DATA domain resolves to its
    own first stage. Fail-open: any error yields ``None`` so persistence remains
    available.
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


def persist_vertical(
    project_root: object,
    vertical: str,
    *,
    domain: str | None = None,
    research_target_level: str | None = None,
    research_direction_mode: str | None = None,
    workflow_mode: str | None = None,
    target_venue: str | None = None,
) -> None:
    """Persist the chosen ``vertical`` into ``.argus/PIPELINE_STATE.json``.

    Validates ``vertical`` against the known built-ins + existing project data
    domains; an unknown name RAISES ``UnknownVerticalError`` (no silent coercion
    to ``research``). A corrupt existing state file RAISES. IO errors PROPAGATE —
    persisting the Manager's decision is load-bearing, not best-effort.
    A target-capable vertical may carry ``research_target_level``; vertical, target,
    and target revision timestamp are then committed by the same atomic replace.

    STAGE AUTHORITY — the harness must NOT control ``current_stage``; only the
    reviewer agent moves it (advance via its verdict, or roll back via
    ``stage_machine.rollback_stage``). So this function SEEDS the vertical's
    first stage only when no stage exists yet (initialization of a fresh state
    file); it NEVER overwrites or resets an existing stage. A stale stage left
    by a vertical change is real progress — clobbering it to the first stage is
    an unauthorized rollback that destroys evidence. It is left for the
    reviewer / rollback path to handle, and the read-side ``current_stage()``
    already falls back to the vertical's first stage at read time without
    mutating the file.
    """
    legacy_direct = str(vertical or "").strip().lower() == "direct"
    vert = require_vertical(vertical, project_root)
    payload = _load_state_payload(project_root)

    payload["vertical"] = vert
    if domain is not None:
        from ..domains import require_domain

        if vert != "research":
            raise ValueError(
                f"domain overlays require vertical='research', found {vert!r}"
            )
        payload["domain"] = require_domain(domain)
    else:
        payload.pop("domain", None)
    if target_venue is not None:
        venue = " ".join(str(target_venue).strip().split())[:100]
        if venue:
            payload["target_venue"] = venue
    if workflow_mode is None and legacy_direct:
        workflow_mode = "direct"
    if workflow_mode is not None:
        normalized_mode = _normalize_workflow_mode(workflow_mode)
        if not normalized_mode:
            raise ValueError(f"invalid workflow_mode: {workflow_mode!r}")
        payload["workflow_mode"] = normalized_mode
    if research_target_level is not None:
        from ..core.research_contract import normalize_research_target_level
        from ..verticals._base import (
            load_vertical,
            vertical_research_target_levels,
        )

        supported_levels = vertical_research_target_levels(
            load_vertical(vert, project_root=project_root)
        )
        if not supported_levels:
            raise ValueError(
                f"research_target_level is not supported by vertical {vert!r}"
            )
        normalized_target = normalize_research_target_level(research_target_level)
        if normalized_target is None or normalized_target not in supported_levels:
            raise ValueError(
                f"invalid research target level: {research_target_level!r}"
            )
        previous_target = normalize_research_target_level(
            payload.get("research_target_level")
        )
        payload["research_target_level"] = normalized_target
        # STAMP ONLY ON A REAL CHANGE, for the same reason the stage below is
        # seed-only. This timestamp exists so that raising the bar — say
        # exploratory to publishable — retires certifications earned against
        # the old bar: ``_research_project_done_issue`` walks the journal
        # newest-first and stops at the first mission older than it. Stamping
        # it on every re-persist made that gate unsatisfiable, because callers
        # routinely re-affirm the level they just read. Every certification was
        # older than the next re-stamp, so the Planner was told
        # ``missing_<level>_reviewer_certification`` no matter what it did.
        # Run 8 (s-fed750c2) solved the problem and proved it in Lean in
        # mission 1, then spent missions 2, 3 and 4 certifying it, each one
        # independently reviewed ``done`` and each one rejected.
        if normalized_target != previous_target or not payload.get(
            "research_target_set_at"
        ):
            payload["research_target_set_at"] = time.time()
    else:
        from ..verticals._base import load_vertical, vertical_research_target_levels

        if not vertical_research_target_levels(
            load_vertical(vert, project_root=project_root)
        ):
            payload.pop("research_target_level", None)
            payload.pop("research_target_set_at", None)
    if research_direction_mode is not None:
        from ..core.research_contract import normalize_research_direction_mode

        if vert != "research":
            raise ValueError("research_direction_mode requires vertical='research'")
        normalized_direction = normalize_research_direction_mode(
            research_direction_mode
        )
        if normalized_direction is None:
            raise ValueError(
                f"invalid research direction mode: {research_direction_mode!r}"
            )
        previous_direction = normalize_research_direction_mode(
            payload.get("research_direction_mode")
        )
        if previous_direction == "broad" and normalized_direction == "locked":
            raise ValueError(
                "broad research direction cannot be downgraded to locked"
            )
        payload["research_direction_mode"] = normalized_direction
    elif vert != "research":
        payload.pop("research_direction_mode", None)

    # SEED-ONLY, NEVER RESET. Stage authority belongs to the reviewer agent
    # (see docstring). Write an initial stage only when none exists yet — leave
    # any existing stage, even one not in this vertical's order, untouched.
    if not _normalize_stage(payload.get("current_stage")):
        first_stage = _vertical_first_stage(vert, project_root)
        if first_stage:
            payload["current_stage"] = first_stage

    write_pipeline_state(project_root, payload)


# --- new-intent vs. reclassification triage --------------------------------


def _vertical_completion_record(
    project_root: object,
    vertical: str,
) -> tuple[str, dict[str, Any]] | None:
    """Return the Manager-certified completion stage and its state record."""
    try:
        from ..verticals._base import load_vertical, vertical_checklist_stage_order

        order = [
            _normalize_stage(stage)
            for stage in vertical_checklist_stage_order(
                load_vertical(vertical, project_root=project_root)
            )
        ]
    except Exception:  # noqa: BLE001 — never raise on a probe
        return None
    if not order:
        return None

    try:
        payload = _load_state_payload(project_root)
    except VerticalResolutionError:
        return None

    current = _normalize_stage(payload.get("current_stage"))
    if current not in order:
        return None
    stages = payload.get("stages")
    if not isinstance(stages, dict):
        return None

    def stage_record(stage: str) -> dict[str, Any] | None:
        record = stages.get(stage)
        if isinstance(record, dict):
            return record
        return next(
            (
                value
                for key, value in stages.items()
                if _normalize_stage(key) == stage and isinstance(value, dict)
            ),
            None,
        )

    record = stage_record(current)
    if not isinstance(record, dict):
        return None
    if str(record.get("status") or "").strip().lower() != "done":
        return None
    if current == order[-1]:
        return current, record

    downstream = order[order.index(current) + 1 :]
    if any(
        not isinstance((tail_record := stage_record(stage)), dict)
        or str(tail_record.get("status") or "").strip().lower() != "skipped"
        for stage in downstream
    ):
        return None
    history = payload.get("stage_history")
    if not isinstance(history, list):
        return None
    completion = next(
        (
            entry
            for entry in reversed(history)
            if isinstance(entry, dict)
            and str(entry.get("direction") or "").strip().lower() == "complete"
            and _normalize_stage(entry.get("from_stage")) == current
            and _normalize_stage(entry.get("to_stage")) == current
        ),
        None,
    )
    if completion is None:
        return None
    recorded_skips = completion.get("skipped_stages")
    if not isinstance(recorded_skips, list):
        return None
    if [_normalize_stage(stage) for stage in recorded_skips] != downstream:
        return None
    return current, record


def vertical_reached_own_terminal_stage(project_root: object, vertical: str) -> bool:
    """Whether ``vertical`` has a Manager-certified completed stage.

    The ordinary case is the final stage marked ``done``. A Manager may also
    complete an earlier stage when the remaining stages do not apply; that
    counts only when the same ``complete`` transition explicitly recorded every
    downstream stage as skipped.

    This is the signal :func:`reset_stage_for_new_intent` uses to distinguish
    "the SAME evolving project got reclassified mid-flight" (a stale/foreign
    stage name is real progress and must be PRESERVED — see
    ``persist_vertical``'s seed-only contract) from "a totally different,
    already-finished prior vertical's leftover stage is being inherited by a
    brand-new, unrelated operator intent" (the stage must be RESET). Fail-open:
    any error (unknown vertical, missing/corrupt state, non-dict payload)
    returns ``False`` so callers never reset on ambiguous data.
    """
    return _vertical_completion_record(project_root, vertical) is not None


def vertical_completion_certificate_status(
    project_root: object,
    vertical: str,
) -> dict[str, Any]:
    """Whether terminal ``done`` matches the contract, and if not, what differs.

    Returns ``{"ok": True}`` or a rejection carrying the stage that actually
    holds the record plus both fingerprints. The bool wrapper below is the
    predicate everything decides on; this is what the rejection gets to *say*.
    Fails closed on every error, as the predicate always has.

    Two things this deliberately does NOT prove, stated here because four
    docstrings in this tree once implied otherwise. The fingerprint is a hash of
    the live checklist contract — framework source, no project evidence, no
    goal, no actor, no secret — so anyone able to import
    ``completion_contract_fingerprint`` can compute the expected value. It
    detects a checklist that *moved* since certification; it does not
    authenticate who certified. And ``_vertical_completion_record``'s structural
    audit checks that an early completion is internally consistent, not that it
    was ever legitimate.

    Which is how testbed run 13 read ``{"ok": True}`` with two of math's three
    stages ``skipped`` and the review never done. So early completion is checked
    against the project's workflow mode here as well as at the write side:
    ``direct`` mode is the one arrangement under which stopping before the final
    stage is a real outcome rather than an abandoned pipeline. Run 13 was
    ``staged``.
    """
    completion = _vertical_completion_record(project_root, vertical)
    if completion is None:
        return {"ok": False, "reason": "no certified completion record"}
    completed_stage, record = completion
    detail: dict[str, Any] = {"ok": False, "stage": completed_stage}
    source = str(record.get("completion_contract_source") or "").strip()
    if source:
        detail["source"] = source
    try:
        from ..verticals._base import (
            load_vertical,
            vertical_checklist_stage_order,
            vertical_completion_contract_version,
        )

        module = load_vertical(vertical, project_root=project_root)
        completion_contract_version = vertical_completion_contract_version(module)
        stage_order = [
            _normalize_stage(stage)
            for stage in vertical_checklist_stage_order(module)
        ]
    except Exception:  # noqa: BLE001 — strict completion fails closed
        return {**detail, "reason": "completion contract version unreadable"}
    if stage_order and completed_stage != stage_order[-1]:
        try:
            mode = resolve_workflow_mode(project_root)
        except Exception:  # noqa: BLE001 — an unreadable mode fails closed
            mode = ""
        if mode != "direct":
            skipped = ", ".join(stage_order[stage_order.index(completed_stage) + 1:]) \
                if completed_stage in stage_order else "later stages"
            return {
                **detail,
                "reason": (
                    f"completion is recorded at {completed_stage!r}, not the "
                    f"final stage {stage_order[-1]!r}, and workflow mode "
                    f"{mode or 'unknown'!r} does not permit stopping early. "
                    f"Skipped without certification: {skipped}"
                ),
                "workflow_mode": mode,
                "final_stage": stage_order[-1],
            }
    if completion_contract_version <= 0:
        return {"ok": True}
    try:
        from .stage_machine import completion_contract_fingerprint

        expected = completion_contract_fingerprint(
            Path(str(project_root)),
            completed_stage,
            version=completion_contract_version,
        )
    except Exception:  # noqa: BLE001 — versioned completion fails closed
        return {**detail, "reason": "completion contract could not be recomputed"}
    detail["expected"] = expected
    detail["version"] = completion_contract_version
    try:
        persisted_version = int(record.get("completion_contract_version") or 0)
    except (TypeError, ValueError):
        return {**detail, "reason": "persisted contract version is not a number"}
    persisted = str(record.get("completion_contract_sha256") or "")
    detail["persisted"] = persisted
    detail["persisted_version"] = persisted_version
    if persisted_version != completion_contract_version:
        return {**detail, "reason": "contract version moved since certification"}
    if persisted != expected:
        return {**detail, "reason": "certified checklist differs from the live one"}
    return {"ok": True}


def vertical_has_current_completion_certificate(
    project_root: object,
    vertical: str,
) -> bool:
    """Whether terminal ``done`` matches the vertical's current contract.

    Legacy terminal detection remains available for new-intent reset. Completion
    decisions use this stricter predicate so a versioned checklist change forces
    one fresh Reviewer/Manager certification.
    """
    return bool(
        vertical_completion_certificate_status(project_root, vertical).get("ok")
    )


def reset_stage_for_new_intent(
    project_root: object,
    *,
    old_vertical: str | None,
    new_vertical: str,
    force_replacement: bool = False,
    evidence_root: Path | str | None = None,
) -> bool:
    """Reset ``current_stage`` to ``new_vertical``'s first stage when a
    genuinely NEW, operator-issued intent supersedes an already-finished prior
    run, whether the newly selected vertical is different or the same. A
    Manager-confirmed standing-objective replacement may pass
    ``force_replacement=True`` to reset immediately even when the old pipeline
    was still in progress; ordinary bounded/reclassification calls retain the
    conservative completed-run-only behavior.

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
    list) and a brand-new intent arrives, that terminal stage belongs to the
    closed-out prior run. This is true even when both intents resolve to the
    same vertical: leaving (for example) ``math/review=done`` or
    ``research/submission=done`` in place makes the Planner immediately declare
    the new task complete. For a different vertical, a same-named stage can
    similarly look like false progress. This function detects both cases and
    rolls the state back to the selected vertical's first stage via
    ``stage_machine.rollback_stage`` (audited, ``rolled_back_by="manager"``),
    without touching ``persist_vertical``'s own never-reset contract.

    Returns ``True`` iff a reset was actually applied. Without forced
    replacement, no-op (returns ``False``) when: there is no prior vertical,
    the prior vertical was not actually finished, or the rollback primitive rejects the target stage
    (e.g. the stale stage was never even a member of the new vertical's
    order, in which case ``current_stage()`` already falls back safely on its
    own). Fail-open: any error is treated as "nothing to reset" so a probe or
    rollback hiccup never blocks the Manager's division.
    """
    if not old_vertical and not force_replacement:
        return False
    if (
        not force_replacement
        and not vertical_reached_own_terminal_stage(project_root, old_vertical)
    ):
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
        if force_replacement:
            from .stage_machine import reset_stage_for_replacement_intent

            reset_stage_for_replacement_intent(
                project_root,
                target_stage=new_order[0],
                reason=(
                    "operator replaced the standing Manager objective; resetting "
                    f"the superseded {old_vertical!r} pipeline to the first stage "
                    f"of {new_vertical!r} instead of preserving incompatible progress."
                ),
                reset_by="manager",
                evidence_root=evidence_root,
            )
        else:
            from .stage_machine import rollback_stage  # late (cycle)

            rollback_stage(
                project_root,
                target_stage=new_order[0],
                reason=(
                    f"prior vertical {old_vertical!r} had already reached its own "
                    f"terminal stage (done); a genuinely new operator-issued "
                    f"intent assigned vertical {new_vertical!r} — "
                    f"resetting current_stage to its first stage rather than "
                    f"silently inheriting the completed prior run's stale stage."
                ),
                rolled_back_by="manager",
                evidence_root=evidence_root,
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
    "available_verticals",
    "available_vertical_purposes",
    "DEFAULT_VERTICAL",
    "ENV_VERTICAL",
    "VerticalResolutionError",
    "UnknownVerticalError",
    "explicit_builtin_vertical",
    "require_vertical",
    "resolve_checklist_vertical",
    "resolve_vertical_if_decided",
    "resolve_vertical",
    "resolve_workflow_mode",
    "resolve_evidence_mode",
    "persist_vertical",
    "vertical_has_current_completion_certificate",
    "vertical_reached_own_terminal_stage",
    "reset_stage_for_new_intent",
]
