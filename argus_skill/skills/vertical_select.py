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

The resolved vertical has one authority: the Manager-persisted ``vertical`` in
``research/PIPELINE_STATE.json`` (including a Manager-authored data domain).

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
    "software", "digital_circuit", "digital_circuit_benchmark", "research", "math", "physics", "quant", "speedrun",
    "kernel_engineering", "nanochat", "nanogpt_speedrun", "kernelbench",
    "learning", "ale_last_exam", "fiction_writing", "classical_poetry",
    "modern_poetry", "prose", "literary_editor",
)

#: One-line purpose per built-in vertical, handed to the Manager's vertical
#: decision prompt so the agent can PREFER an existing built-in (which ships
#: expert per-stage reviewer checklists) over authoring a fresh, checklist-less
#: data domain. Keys must stay in sync with ``VERTICALS``.
VERTICAL_PURPOSES: dict[str, str] = {
    "software": "software engineering: repository repairs, features, refactors, "
    "tests, developer tooling, and implementation work",
    "digital_circuit": "digital hardware engineering: Verilog/SystemVerilog RTL, "
    "testbenches, assertions/formal verification, FPGA/ASIC synthesis, timing, "
    "and reproducible sign-off evidence",
    "digital_circuit_benchmark": "single-stage fixed-harness RTL benchmark execution "
    "under digital_circuit: exact public interface closure, RTL, local verification, "
    "pre-score elaboration, and immutable attempt handoff without staged overhead",
    "research": "full multi-stage research-PAPER pipeline (literature review → "
    "experiments → draft → submission); the default when the goal is a written paper",
    "math": "mathematical conjectures, proofs, and open research problems; dynamically "
    "choose background retrieval, examples/counterexamples, computation, natural-language "
    "proof, and Lean formalization as appropriate; not a paper pipeline or a "
    "metric-optimization vertical",
    "physics": "physics tasks on a real physical system; dynamically choose theoretical "
    "derivation, numerical simulation, data analysis, literature synthesis, or experiment "
    "design (or an honest no-go) as appropriate, reporting bounded provenance-tracked "
    "evidence; not a paper pipeline or a metric-optimization vertical",
    "quant": "finance factor-research REPORT — mine/evaluate equity factors "
    "(IC/ICIR, backtest, Sharpe) into a reviewer-certified factor report; not a metric loop",
    "speedrun": "generic single-metric optimize loop on a script/benchmark under a "
    "wall-clock budget (setup → optimize → measure → report); no paper",
    "kernel_engineering": "production GPU-kernel engineering in a real repository "
    "(environment/toolchain audit → correct baseline → profile/optimize → full "
    "validation → upstream-ready report); use for CUDA/Triton/TileLang/CUTLASS/PyTorch "
    "library work and PRs, not fixed SOL-ExecBench competition tasks",
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
    "classical_poetry": "classical CHINESE poetry (近体诗/古体/词) — compose or "
    "prosody-check 律诗/绝句/五言/七言; gates the poem on a reproducible machine "
    "prosody check (押韵/平仄/粘对/孤平/三平尾 via 平水韵) plus live-reviewer 立意/炼字/"
    "反AI. zh only; NOT modern free verse (route that to modern_poetry) and NOT prose",
    "modern_poetry": "modern FREE VERSE / prose poems (zh or en) — compose or revise; "
    "NO 平仄/韵 machine check (free verse is not classical). Gates only DECLARED hard "
    "constraints (language/line-count/banned-words); imagery/lineation/tone are "
    "live-reviewer craft. NOT classical regulated verse and NOT narrative prose",
    "prose": "literary PROSE (抒情/叙事散文/随笔/回忆, zh or en) — compose or revise an "
    "essay/memoir. Machine layer is thin: prose_state structure completeness + declared "
    "hard constraints (language/paragraph-count/banned-words). Concrete observation, the "
    "fact/memory boundary, and paragraph movement are live-reviewer. NOT verse and NOT "
    "plot-driven fiction",
    "literary_editor": "EDIT an existing literary text — rewrite/expand/polish/proofread/"
    "critique. Reuses the Reviewer + revise capability (no new agent). Machine layer is "
    "edit DISCIPLINE (critique doesn't rewrite, proofread doesn't become a rewrite, expand "
    "adds, must-keep segments survive); edit quality and fact-fidelity are live-reviewer. "
    "Requires a source text; NOT from-scratch authoring",
}

#: The safe default vertical when intent is unclear or state is missing.
DEFAULT_VERTICAL: str = "research"

#: Legacy environment name retained for low-level compatibility/introspection.
#: Formal task routing does not consult it; Manager owns vertical classification.
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


def resolve_workflow_mode(project_root: object = ".") -> str:
    """Return the Manager-persisted orchestration mode."""
    try:
        raw = _state_path(project_root).read_text(encoding="utf-8")
    except FileNotFoundError:
        payload: dict = {}
    else:
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


def _is_project_data_domain(value: str | None, project_root: object) -> bool:
    """Whether ``value`` names a project-local DATA domain, not a built-in vertical."""
    if value is None or value in VERTICALS:
        return False
    try:
        from ..verticals._data_domain import data_domain_exists  # late (cycle)

        return data_domain_exists(value, project_root)
    except Exception:  # noqa: BLE001 — resolver must remain fail-open
        return False


def resolve_vertical_if_decided(project_root: object = ".") -> str | None:
    """Return the Manager-decided vertical, or ``None`` without a fallback."""
    return _persisted_vertical(project_root)


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


def persist_vertical(
    project_root: object,
    vertical: str,
    *,
    research_target_level: str | None = None,
    workflow_mode: str | None = None,
    target_venue: str | None = None,
) -> None:
    """Persist the chosen ``vertical`` into ``research/PIPELINE_STATE.json``.

    Validates ``vertical`` against the known built-ins + existing project data
    domains; an unknown name RAISES ``UnknownVerticalError`` (no silent coercion
    to ``research``). A corrupt existing state file RAISES. IO errors PROPAGATE —
    persisting the Manager's decision is load-bearing, not best-effort.
    A target-capable vertical may carry ``research_target_level``; vertical, target,
    and target revision timestamp are then committed by the same atomic replace.

    STAGE AUTHORITY — the harness must NOT control ``current_stage``; only the
    reviewer agent moves it (advance via its verdict, or roll back via
    ``stage_machine.rollback_stage``). So this function SEEDS the vertical's
    first stage only when no stage exists yet (bootstrap of a fresh state
    file); it NEVER overwrites or resets an existing stage. A stale stage left
    by a vertical change is real progress — clobbering it to the first stage is
    an unauthorized rollback that destroys evidence. It is left for the
    reviewer / rollback path to handle, and the read-side ``current_stage()``
    already falls back to the vertical's first stage at read time without
    mutating the file.
    """
    legacy_direct = str(vertical or "").strip().lower() == "direct"
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
        payload["research_target_level"] = normalized_target
        payload["research_target_set_at"] = time.time()
    else:
        from ..verticals._base import load_vertical, vertical_research_target_levels

        if not vertical_research_target_levels(
            load_vertical(vert, project_root=project_root)
        ):
            payload.pop("research_target_level", None)
            payload.pop("research_target_set_at", None)

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
    force_replacement: bool = False,
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
    "resolve_checklist_vertical",
    "resolve_vertical_if_decided",
    "resolve_vertical",
    "resolve_workflow_mode",
    "resolve_evidence_mode",
    "persist_vertical",
    "vertical_reached_own_terminal_stage",
    "reset_stage_for_new_intent",
]
