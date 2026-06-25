"""Flow controller — the analyze → (exploit | explore | jump) glue.

Wraps the existing planner (does NOT rewrite it). ``decide`` is called while the
planner prompt is being assembled and returns a block to append plus the chosen
mode; ``record_decision`` is called after the planner emits its tasks to persist
the agent-declared forbidden ledger + the decision-log row. Everything is
fail-soft: any error yields ``mode="exploit"`` with an empty block, so the
planner runs exactly as today when the meta layer can't act.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from types import ModuleType

from .config import MetaConfig, load_meta_config
from .ledger import (
    append_decision,
    load_ledger,
    merge_forbidden,
    next_step_id,
    set_jump_pending,
    tick_explore_window,
)
from .meta_prompter import (
    MetaDecision,
    build_meta_block,
    explore_window_block,
    parse_meta_decision,
)
from .saturation import SaturationSignal, analyze


@dataclass
class FlowDecision:
    """What the harness decided to do this planner turn."""

    mode: str = "exploit"  # exploit | explore | jump
    prompt_block: str = ""
    signal: SaturationSignal = field(default_factory=SaturationSignal)
    forbidden_axes: set[str] = field(default_factory=set)


def _strategy_pool(vmod: ModuleType | None, project_root: object) -> str:
    try:
        from ..verticals._base import vertical_strategy_pool

        return vertical_strategy_pool(vmod, project_root) if vmod else ""
    except Exception:  # noqa: BLE001
        return ""


def decide(
    project_root: object,
    vmod: ModuleType | None = None,
    config: MetaConfig | None = None,
) -> FlowDecision:
    """Analyze the search state and choose exploit / explore / jump.

    ``jump`` when the saturation counter trips; ``explore`` as an earlier soft
    nudge when the basin is narrowing but not yet frozen; ``exploit`` otherwise
    (the unchanged planner path).
    """
    cfg = config or load_meta_config()
    try:
        signal = analyze(project_root, vmod, cfg)
        ledger = load_ledger(project_root)
        window = int(getattr(ledger, "explore_window", 0) or 0)
        if window > 0:
            # Post-jump VALLEY-IMMUNITY window: develop the current regime — do
            # NOT convene a new jump (jumping every cycle churns through regimes
            # without ever tuning one), and tell the agent a regressing candidate
            # is expected + must be scored/iterated. The frozen floor stays safe.
            return FlowDecision(
                mode="exploit",
                prompt_block=explore_window_block(window, cfg.explore_window_rounds),
                signal=signal,
                forbidden_axes=ledger.forbidden_axes(),
            )
        if signal.is_saturated:
            mode = "jump"
        elif (
            signal.frozen_rounds >= max(1, cfg.jump_frozen_threshold // 2)
            and signal.diversity_score <= cfg.diversity_floor
        ):
            mode = "explore"
        else:
            mode = "exploit"
        block = build_meta_block(
            signal, ledger, _strategy_pool(vmod, project_root), mode, cfg
        )
        return FlowDecision(
            mode=mode,
            prompt_block=block,
            signal=signal,
            forbidden_axes=ledger.forbidden_axes(),
        )
    except Exception:  # noqa: BLE001 — meta must never break planner prompt building
        return FlowDecision()


def record_decision(
    project_root: object,
    planner_output: str | None,
    flow: FlowDecision,
    config: MetaConfig | None = None,
    *,
    now: float | None = None,
    meta_obj: dict | None = None,
) -> MetaDecision:
    """Persist the agent's meta_decision: forbidden ledger + decision-log row.

    Parses the planner's ``meta_decision`` — preferring the structured
    ``meta_obj`` the planner returns in its schema'd output, falling back to
    scraping ``planner_output`` — merges the AGENT-declared forbidden directions
    into the never-cleared ledger, refreshes the coverage map, and appends one
    row to ``META_LEDGER.jsonl``. Returns the parsed decision (``present=False``
    if the planner emitted none). Fail-soft.
    """
    signal = flow.signal
    try:
        dec = parse_meta_decision(
            planner_output or "",
            forbidden_axes=flow.forbidden_axes,
            require_jump=(flow.mode == "jump"),
            obj=meta_obj,
        )
        # Merge coverage with the just-declared strategy_type so the map advances
        # even before the candidate is scored.
        coverage = dict(signal.coverage)
        if dec.present and dec.strategy_type in coverage:
            coverage[dec.strategy_type] = coverage[dec.strategy_type] + 1
        elif dec.present and dec.strategy_type not in ("local", "unknown"):
            coverage[dec.strategy_type] = coverage.get(dec.strategy_type, 0) + 1

        # Only the AGENT's own declared forbidden directions are persisted; the
        # harness never invents one.
        if dec.present and dec.forbidden:
            merge_forbidden(project_root, dec.forbidden, coverage=coverage, now=now)
        elif coverage != signal.coverage:
            merge_forbidden(project_root, [], coverage=coverage, now=now)

        # A jump turn happened → arm the consume-once context reset so the next
        # engineer session drops the saturated local trajectory (active_line /
        # maturing). Keyed on the harness convening the jump (flow.mode), so the
        # reset fires even if the planner's structured meta_decision was imperfect.
        # A jump ALSO opens the post-jump valley-immunity window (develop the new
        # regime for N rounds); any other cycle DECAYS that window by one.
        if flow.mode == "jump":
            set_jump_pending(project_root, True, now=now)
            tick_explore_window(
                project_root,
                set_to=(config or load_meta_config()).explore_window_rounds,
                now=now,
            )
        else:
            tick_explore_window(project_root, now=now)

        append_decision(
            project_root,
            {
                "step_id": next_step_id(project_root),
                "mode": flow.mode,
                "was_jump": flow.mode == "jump",
                "strategy_type": dec.strategy_type if dec.present else "unknown",
                "performance": float(getattr(signal, "floor", 0.0) or 0.0),
                "diversity_score": signal.diversity_score,
                "decision_valid": dec.valid,
                "violations": dec.violations,
                "ts": now if now is not None else time.time(),
            },
        )
        return dec
    except Exception:  # noqa: BLE001 — recording must never break the loop
        return MetaDecision(present=False)
