"""Saturation detection — pure harness counters (no research judgment).

``analyze`` re-surfaces two facts from the agent's OWN recorded attempts:

  * ``frozen_rounds`` — consecutive attempts since the promoted floor last
    improved (the same number the live altitude block already shows);
  * a DIVERSITY descriptor — how many distinct non-local regime axes the recent
    window of candidates explored (the QD behaviour-space coverage). ``keep-best``
    under a fixed scorer structurally collapses this; a collapsed window is the
    mechanical fingerprint of a frozen basin.

Neither is a research call: ``frozen_rounds`` is arithmetic on the agent's own
promote decisions, and the diversity descriptor counts the agent's own recorded
``strategy_type`` labels (falling back to a proxy over the agent's own attempt
*names* when labels are absent — the same "approximate hint" the operator
already deemed legitimate visibility). The harness asserts the *threshold*
(a process knob: how stale before we change the framing we offer), never the
*verdict* (is the basin dead / where to go next) — that stays with the planner.

Consumes a vertical-provided structured ``facts`` dict so the cross-vertical
harness stays metric-blind (the vertical parses its own ``val_bpb``; the meta
layer only reads the numbers and the agent's strategy labels).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import ModuleType

from .config import REGIME_AXES, MetaConfig, load_meta_config


@dataclass
class SaturationSignal:
    """Counter-based view of the search state (fail-soft, no verdict)."""

    frozen_rounds: int = 0
    diversity_score: int = 0  # distinct non-local regime axes in the window
    coverage: dict[str, int] = field(default_factory=dict)  # axis → count (all)
    untouched_axes: list[str] = field(default_factory=list)
    window_labelled: bool = False  # True if the diversity used real strategy_type
    n_attempts: int = 0
    floor: float = 0.0  # the vertical's promoted floor (a number, not parsed here)
    is_saturated: bool = False

    def as_log_fields(self) -> dict[str, object]:
        return {
            "frozen_rounds": self.frozen_rounds,
            "diversity_score": self.diversity_score,
            "untouched_axes": list(self.untouched_axes),
            "n_attempts": self.n_attempts,
            "is_saturated": self.is_saturated,
        }


_TOKEN_SPLIT = re.compile(r"[_\-]+")


def _name_axis_proxy(name: str) -> str:
    """Cheap proxy bucket from an attempt name's first meaningful token.

    Used ONLY when an attempt has no agent-recorded ``strategy_type`` (legacy
    rounds): it groups by the agent's own naming so a window that recombines the
    same name-token reads as low-diversity, exactly as the live altitude hint
    already does. Never used to label a candidate's regime authoritatively.
    """
    for tok in _TOKEN_SPLIT.split(name.lower()):
        tok = tok.strip()
        if not tok or tok.isdigit() or re.fullmatch(r"a\d+", tok):
            continue
        return tok
    return ""


def _axis_of(attempt: dict) -> tuple[str, bool]:
    """Return ``(axis, labelled)`` for one attempt record.

    ``labelled`` is True when the agent recorded a real ``strategy_type``; else
    we fall back to the name-token proxy and report ``labelled=False`` so the
    caller can flag the diversity score as approximate.
    """
    st = str(attempt.get("strategy_type") or "").strip().lower()
    if st and st not in ("unknown", ""):
        return st, True
    return _name_axis_proxy(str(attempt.get("name") or "")), False


def from_facts(facts: dict, config: MetaConfig | None = None) -> SaturationSignal:
    """Pure: build a ``SaturationSignal`` from a vertical ``facts`` dict.

    ``facts`` shape (all optional, fail-soft): ``{since_improve:int,
    n_attempts:int, attempts:[{name, score, decision, strategy_type}]}``.
    """
    cfg = config or load_meta_config()
    if not isinstance(facts, dict):
        return SaturationSignal()
    attempts = facts.get("attempts") or []
    if not isinstance(attempts, list):
        attempts = []

    try:
        frozen = int(facts.get("since_improve", 0) or 0)
    except (TypeError, ValueError):
        frozen = 0
    n_attempts = int(facts.get("n_attempts", len(attempts)) or len(attempts))
    try:
        floor = float(facts.get("floor", 0.0) or 0.0)
    except (TypeError, ValueError):
        floor = 0.0

    # Coverage over ALL attempts (recorded labels only — never proxy-classify
    # the whole history, that would be the harness inventing research labels).
    coverage: dict[str, int] = {}
    for a in attempts:
        st = str((a or {}).get("strategy_type") or "").strip().lower()
        if st in REGIME_AXES:
            coverage[st] = coverage.get(st, 0) + 1
    untouched = [ax for ax in REGIME_AXES if ax not in coverage]

    # Diversity over the recent window: distinct non-local axes. Prefer the
    # agent's recorded labels; if the window is unlabelled (legacy), use the
    # name-token proxy and flag it.
    window = attempts[-cfg.diversity_window :] if attempts else []
    axes: set[str] = set()
    labelled_any = False
    for a in window:
        axis, labelled = _axis_of(a or {})
        labelled_any = labelled_any or labelled
        if axis and axis != "local":
            axes.add(axis)
    diversity = len(axes)

    # frozen_rounds is the UNAMBIGUOUS saturation fact. The diversity descriptor
    # only REFINES it — and only when it is trustworthy, i.e. when the window
    # carries the agent's OWN regime labels. With unlabelled (legacy) names the
    # proxy over-counts (a fresh adjective per attempt reads as "diverse" even
    # when the regime is identical), so we must NOT let it SUPPRESS a long
    # freeze; we fall back to frozen-only. Once the agent records strategy_type,
    # genuine regime diversity (diversity > floor, labelled) correctly holds the
    # jump back — the agent is already exploring, forcing a jump would be wrong.
    is_saturated = frozen >= cfg.jump_frozen_threshold and (
        diversity <= cfg.diversity_floor or not labelled_any
    )

    return SaturationSignal(
        frozen_rounds=frozen,
        diversity_score=diversity,
        coverage=coverage,
        untouched_axes=untouched,
        window_labelled=labelled_any,
        n_attempts=n_attempts,
        floor=floor,
        is_saturated=is_saturated,
    )


def analyze(
    project_root: object,
    vmod: ModuleType | None = None,
    config: MetaConfig | None = None,
) -> SaturationSignal:
    """Detect saturation for a mission (fail-soft to a non-saturated signal).

    Pulls a structured ``facts`` dict from the vertical's
    ``search_altitude_facts`` hook (metric parsing stays in the vertical), then
    computes the counters. Any failure → an empty, non-saturated signal so the
    planner loop never breaks on detection.
    """
    try:
        from ..verticals._base import vertical_search_altitude_facts

        facts = vertical_search_altitude_facts(vmod, project_root) if vmod else {}
        return from_facts(facts or {}, config)
    except Exception:  # noqa: BLE001 — detection must never break prompt building
        return SaturationSignal()
