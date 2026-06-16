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

Three sides of the selector live here:

* the **read side** (``resolve_vertical``) is cheap, deterministic, LLM-free,
  and exception-free — it is consulted on every stage transition and gate, so
  it must never raise and must never spend a token.
* the **decide side** (``classify_vertical`` / ``_heuristic_classify``) is
  consulted once at mission bootstrap to pick a vertical from the objective. It
  may optionally use the LLM runner, but always degrades to a keyword
  heuristic — and the heuristic always degrades to ``"research"``.
* the **write side** (``persist_vertical``) writes the resolved vertical into
  the pipeline state and seeds ``current_stage`` to the vertical's first stage.

Precedence for the resolved vertical (read side):

    env ``ARGUS_SKILL_VERTICAL``  >  persisted ``vertical``  >  "research"

Provenance: repurposed from ``pipeline_mode.py`` (mode paper|optimize →
vertical research|speedrun). The prompt builders (planner / reviewer / loop)
and the supervisor are now vertical-native; the only surviving back-compat
shims are ``classify_pipeline_mode`` / ``persist_pipeline_mode``, kept for any
caller still speaking the old paper|optimize vocabulary.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


# --- constants -------------------------------------------------------------

#: Known verticals. ``"research"`` is first and is the canonical default.
VERTICALS: tuple[str, ...] = ("research", "speedrun")

#: The safe default vertical when intent is unclear or state is missing.
DEFAULT_VERTICAL: str = "research"

#: Environment override consulted first by ``resolve_vertical``.
ENV_VERTICAL: str = "ARGUS_SKILL_VERTICAL"

_STATE_RELPATH = ("research", "PIPELINE_STATE.json")


# --- normalization / read side --------------------------------------------


def _strip_needed(value: str) -> str:
    """Drop a trailing ``-needed`` sentinel (main's pre-writer placeholder)."""
    cleaned = value.strip().lower()
    if cleaned.endswith("-needed"):
        cleaned = cleaned[: -len("-needed")]
    return cleaned


def _known_vertical(value: object) -> str | None:
    """Return the normalized vertical name if known, else ``None``.

    Strips whitespace/case and a trailing ``-needed`` sentinel. Returns ``None``
    for non-strings, junk, or any value that is not a recognised vertical so the
    caller can fall through to the next precedence source.
    """
    if not isinstance(value, str):
        return None
    cleaned = _strip_needed(value)
    return cleaned if cleaned in VERTICALS else None


def normalize_vertical(value: object) -> str:
    """Coerce ``value`` to a known vertical, defaulting to ``"research"``."""
    known = _known_vertical(value)
    return known if known is not None else DEFAULT_VERTICAL


def _normalize_stage(stage: object) -> str:
    if not isinstance(stage, str):
        return ""
    return stage.strip().lower()


def _state_path(project_root: object) -> Path:
    return Path(project_root).joinpath(*_STATE_RELPATH)


def _persisted_vertical(project_root: object) -> str | None:
    """Return the persisted ``vertical`` from PIPELINE_STATE.json, or ``None``.

    Fail-open: a missing/unreadable/malformed file, a non-dict payload, an
    absent key, or an unrecognised value all yield ``None`` so resolution falls
    through to the default.
    """
    try:
        payload = json.loads(_state_path(project_root).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — fail-open: missing/unreadable/malformed
        return None
    if not isinstance(payload, dict):
        return None
    return _known_vertical(payload.get("vertical"))


def resolve_vertical(project_root: object = ".") -> str:
    """Resolve the active vertical (cheap, deterministic, no LLM).

    Precedence:

        1. env ``ARGUS_SKILL_VERTICAL`` — only if it names a known vertical
           (a trailing ``-needed`` sentinel is stripped first)
        2. persisted ``vertical`` in ``research/PIPELINE_STATE.json``
        3. ``"research"`` (the safe default)

    This is the read side consulted on every stage transition/gate; it never
    raises and never spends a token.
    """
    env = _known_vertical(os.environ.get(ENV_VERTICAL))
    if env is not None:
        return env

    persisted = _persisted_vertical(project_root)
    if persisted is not None:
        return persisted

    return DEFAULT_VERTICAL


# --- classification / decide side -----------------------------------------

_SPEEDRUN_SIGNALS: tuple[str, ...] = (
    "minimize",
    "maximize",
    "lower",
    "reduce",
    "beat",
    "optimize",
    "val_bpb",
    "bpb",
    "score",
    "metric",
    "eval_solution",
    "train.py",
    "benchmark score",
    "loss",
    "accuracy",
    "speedrun",
)

_RESEARCH_SIGNALS: tuple[str, ...] = (
    "paper",
    "emnlp",
    "aaai",
    "acl",
    "submission",
    "write a paper",
    "draft",
    "manuscript",
    "literature",
    "related work",
    "venue",
)


def _heuristic_classify(objective: object) -> str:
    """Keyword/intent heuristic mapping an objective to a vertical.

    Counts SPEEDRUN vs RESEARCH keyword hits. Returns ``"speedrun"`` only when
    speedrun signals clearly dominate (strictly more speedrun hits than research
    hits, and at least one speedrun hit); otherwise ``"research"`` — the safe
    default, since producing a paper subsumes optimize work.
    """
    text = objective.lower() if isinstance(objective, str) else ""
    if not text:
        return "research"
    speedrun_hits = sum(1 for sig in _SPEEDRUN_SIGNALS if sig in text)
    research_hits = sum(1 for sig in _RESEARCH_SIGNALS if sig in text)
    if speedrun_hits >= 1 and speedrun_hits > research_hits:
        return "speedrun"
    return "research"


_LLM_CLASSIFY_PROMPT = (
    "You are routing an automated research loop. Classify the objective "
    "below as EXACTLY one word: RESEARCH or SPEEDRUN.\n\n"
    "Definitions:\n"
    "- RESEARCH: the goal is to produce a research paper or report. This needs "
    "a literature review, written sections (draft/manuscript), and a "
    "submission package for a venue.\n"
    "- SPEEDRUN: the goal is to directly improve code or a model so it beats "
    "a numeric metric (e.g. lower a loss/bpb, raise an accuracy/score) on a "
    "given script or benchmark under a wall-clock budget. No paper, no "
    "literature review, no writing.\n\n"
    "Answer with ONLY the single word RESEARCH or SPEEDRUN, nothing else.\n\n"
    "Objective:\n{objective}\n"
)


def classify_vertical(
    objective: object,
    runner: object = None,
    profile_hint: str | None = None,
) -> str:
    """Decide the vertical for ``objective`` (bootstrap, once).

    Resolution order:

        1. ``profile_hint`` — if it names a known vertical, trust it verbatim
           (an explicit operator/profile choice wins over inference).
        2. ``runner`` LLM classification — if a runner is supplied, ask it to
           label the objective RESEARCH or SPEEDRUN; on ANY error or an
           ambiguous answer, fall back to the heuristic.
        3. ``_heuristic_classify`` — keyword heuristic (when no runner).

    The heuristic itself always degrades to ``"research"``, so the worst case
    is "ran the full pipeline when a lean loop would have done" — a cost
    hazard, never a correctness one.
    """
    hint = _known_vertical(profile_hint)
    if hint is not None:
        return hint

    if runner is None:
        return _heuristic_classify(objective)

    try:
        from ..core.models import RunnerOptions

        prompt = _LLM_CLASSIFY_PROMPT.format(objective=str(objective))
        result = runner.run_exec(
            prompt=prompt,
            options=RunnerOptions(full_auto=True),
            run_label="vertical-classify",
        )
        answer = _parse_llm_vertical(getattr(result, "last_agent_message", "") or "")
        if answer is not None:
            return answer
    except Exception:  # noqa: BLE001 — any failure degrades to the heuristic
        log.warning("LLM vertical classification failed; using heuristic", exc_info=True)

    return _heuristic_classify(objective)


def _parse_llm_vertical(message: str) -> str | None:
    """Extract a clean RESEARCH/SPEEDRUN verdict from an LLM reply.

    Returns the normalized vertical only when the answer is unambiguous (names
    exactly one of the two). Returns ``None`` when the answer is empty, names
    both, or names neither — the caller then falls back to the heuristic.
    """
    if not isinstance(message, str):
        return None
    low = message.lower()
    has_speedrun = "speedrun" in low
    has_research = "research" in low
    if has_speedrun and not has_research:
        return "speedrun"
    if has_research and not has_speedrun:
        return "research"
    return None


# --- persistence (write side) ---------------------------------------------


def _vertical_first_stage(vertical: str) -> str | None:
    """Return the active vertical's first System-(B) checklist stage, if any.

    Late import to avoid a module-load cycle (``_base`` ↔ ``stage_checklists``).
    Fail-open: any error yields ``None`` so persistence never breaks bootstrap.
    """
    try:
        from ..verticals._base import (
            load_vertical,
            vertical_checklist_stage_order,
        )

        order = vertical_checklist_stage_order(load_vertical(vertical))
        return _normalize_stage(order[0]) if order else None
    except Exception:  # noqa: BLE001 — best-effort: never break persistence
        return None


def persist_vertical(project_root: object, vertical: str) -> None:
    """Persist the chosen ``vertical`` into ``research/PIPELINE_STATE.json``.

    Loads the existing state (or ``{}`` if missing/malformed), creates the
    ``research/`` directory if needed, and sets ``vertical`` to the normalized
    name. When ``current_stage`` is unset or is not one of the vertical's own
    stages (e.g. a stale paper stage left by a prior mis-routed run), it is
    reset to the vertical's FIRST stage so the mission starts in the right
    place. Written atomically.

    Fail-open: errors are logged, never raised — persistence is best-effort and
    must not break mission bootstrap.
    """
    try:
        vert = normalize_vertical(vertical)
        path = _state_path(project_root)

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — missing/unreadable/malformed → fresh
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        payload["vertical"] = vert

        first_stage = _vertical_first_stage(vert)
        if first_stage:
            order = []
            try:
                from ..verticals._base import (
                    load_vertical,
                    vertical_checklist_stage_order,
                )

                order = [
                    _normalize_stage(s)
                    for s in vertical_checklist_stage_order(load_vertical(vert))
                ]
            except Exception:  # noqa: BLE001 — best-effort
                order = [first_stage]
            current = payload.get("current_stage")
            current_norm = _normalize_stage(current)
            if current_norm not in order:
                payload["current_stage"] = first_stage

        path.parent.mkdir(parents=True, exist_ok=True)
        rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_text(rendered, encoding="utf-8")
        os.replace(tmp_path, path)
    except Exception:  # noqa: BLE001 — best-effort: log, never raise
        log.warning("failed to persist vertical %r", vertical, exc_info=True)


# --- back-compat shims (delete once all call sites move) -------------------
#
# The prompt builders (planner / reviewer / loop) and the supervisor are now
# vertical-native. These two shims map the old paper|optimize "pipeline mode"
# vocabulary onto the vertical selector for any caller still speaking it.


def classify_pipeline_mode(
    objective: object,
    runner: object = None,
    profile_hint: str | None = None,
) -> str:
    """Back-compat: classify and report in the old paper|optimize vocabulary.

    Translates an old-vocabulary ``profile_hint`` (paper|optimize) into the
    vertical vocabulary, classifies, and maps the result back.
    """
    vhint: str | None = None
    if isinstance(profile_hint, str):
        h = profile_hint.strip().lower()
        if h in ("optimize", "speedrun"):
            vhint = "speedrun"
        elif h in ("paper", "research"):
            vhint = "research"
    vert = classify_vertical(objective, runner=runner, profile_hint=vhint)
    return "optimize" if vert == "speedrun" else "paper"


def persist_pipeline_mode(project_root: object, mode: str) -> None:
    """Back-compat: persist a paper|optimize mode as a research|speedrun vertical."""
    vert = (
        "speedrun"
        if isinstance(mode, str) and mode.strip().lower() == "optimize"
        else "research"
    )
    persist_vertical(project_root, vert)


__all__ = [
    "VERTICALS",
    "DEFAULT_VERTICAL",
    "ENV_VERTICAL",
    "normalize_vertical",
    "resolve_vertical",
    "classify_vertical",
    "persist_vertical",
    "_heuristic_classify",
    # back-compat shims
    "classify_pipeline_mode",
    "persist_pipeline_mode",
]
