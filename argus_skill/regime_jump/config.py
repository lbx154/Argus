"""Process knobs for the regime-jump control layer.

These are SCHEDULING thresholds (how many stale rounds before the harness
convenes a regime-jump turn), NOT research judgments — the harness deciding
*when* to change the framing it offers the agent is domain-agnostic process,
the same posture as a poll interval or a stall timeout. The agent still makes
the actual call (is it saturated? which regime?).

All knobs are overridable via environment variables so the operator can tune
cadence without a code edit / restart of the tuning itself, e.g.
``ARGUS_META_JUMP_FROZEN_THRESHOLD=20``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

#: Canonical regime axes — the diversity descriptor's behaviour space. These are
#: the agent-facing ``strategy_type`` labels (the planner records which axis a
#: candidate explores). ``local`` = a within-regime small tweak (does NOT count
#: as a distinct regime for diversity); ``unknown`` = an unlabelled/legacy
#: attempt. The non-trivial axes mirror the nanochat ``_CATEGORY_AXES`` taxonomy
#: that already lives in the vertical, kept generic here so the regime-jump layer
#: stays
#: cross-vertical.
REGIME_AXES: tuple[str, ...] = (
    "optimizer",
    "architecture",
    "update_mechanics",
    "data",
    "numerics",
)
STRATEGY_TYPES: tuple[str, ...] = REGIME_AXES + ("local", "unknown")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default



@dataclass(frozen=True)
class MetaConfig:
    """Tunable cadence for saturation → jump. Pure process, not research."""

    #: Convene a JUMP turn once the floor has been frozen at least this many
    #: consecutive attempts. The live mission sat at 63 — this would have fired
    #: ~50 rounds earlier.
    jump_frozen_threshold: int = 12
    #: Window (most-recent attempts) over which the diversity descriptor is
    #: measured.
    diversity_window: int = 8
    #: A window with this many or fewer DISTINCT non-local regime axes counts as
    #: low-diversity (the basin has collapsed onto one regime).
    diversity_floor: int = 2
    #: After a regime JUMP, give the new regime this many rounds of "valley
    #: immunity": no new jump convenes, and the agent is told a regressing
    #: candidate is EXPECTED and must be SCORED + iterated (not skipped on the
    #: train-only proxy gate, not restored on round 1) — so a bold regime change
    #: gets the rounds it needs to cross its initial regression valley instead of
    #: being killed on arrival. The frozen floor stays safe throughout.
    explore_window_rounds: int = 3
    #: How many diverse "inspiration" attempts to surface in the jump framing
    #: (AlphaEvolve parent+inspirations; bounded per the EMNLP negative result).
    inspiration_top_k: int = 4

    @classmethod
    def from_env(cls) -> "MetaConfig":
        return cls(
            jump_frozen_threshold=_env_int(
                "ARGUS_META_JUMP_FROZEN_THRESHOLD", cls.jump_frozen_threshold
            ),
            diversity_window=_env_int(
                "ARGUS_META_DIVERSITY_WINDOW", cls.diversity_window
            ),
            diversity_floor=_env_int(
                "ARGUS_META_DIVERSITY_FLOOR", cls.diversity_floor
            ),
            explore_window_rounds=_env_int(
                "ARGUS_META_EXPLORE_WINDOW_ROUNDS", cls.explore_window_rounds
            ),
            inspiration_top_k=_env_int(
                "ARGUS_META_INSPIRATION_TOP_K", cls.inspiration_top_k
            ),
        )


def load_meta_config() -> MetaConfig:
    """Return the env-resolved meta config (fail-soft to defaults)."""
    try:
        return MetaConfig.from_env()
    except Exception:  # noqa: BLE001 — config must never break the loop
        return MetaConfig()
