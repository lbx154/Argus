"""argus.judge — the FROZEN, EXTERNAL judge layer. The soul.

The ONLY source of "did we win." No LLM (not Manager, not Reviewer) may decide it.
Enforces: frozen scorer (agent can't touch), anti-cheat (reading the published answer
disqualifies), noise gate (sub-noise delta is a coin flip), hardware-honesty (baseline
re-measured on the SAME hardware). In production ``score`` shells out to the sealed
scorer (cf. self_improving/seal_check.py); here it's a measured-value stub.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from .core import Evidence


@dataclass
class JudgeConfig:
    floor: float                      # frozen fair floor, re-measured on OUR hardware
    noise: float = 0.0015             # sub-noise deltas never count as a win
    n_seed: int = 10
    forbidden: tuple = ()             # published best/optimized answers — reading = DISQUALIFY


class FrozenJudge:
    def __init__(self, cfg: JudgeConfig):
        self.cfg = cfg
        self.floor = cfg.floor

    def anti_cheat_ok(self, refs: list[str]) -> tuple[bool, str]:
        for r in refs:
            for bad in self.cfg.forbidden:
                if bad and bad in r:
                    return False, f"DISQUALIFIED: read the answer ({bad})"
        return True, ""

    def score(self, candidate_metric: float, refs: Optional[list[str]] = None) -> Evidence:
        ok, why = self.anti_cheat_ok(refs or [])
        if not ok:
            return Evidence(passed=False, note=why)
        delta = self.floor - candidate_metric         # >0 means beat the floor
        within_noise = abs(delta) < self.cfg.noise
        passed = (delta > 0) and not within_noise
        note = ("within CI/noise — coin flip, not a win" if within_noise
                else (f"beats floor by {delta:.4f}" if passed else f"misses floor by {-delta:.4f}"))
        ev = Evidence(metric=candidate_metric, ci=self.cfg.noise, n_seed=self.cfg.n_seed,
                      passed=passed, note=note)
        if passed:
            self.floor = min(self.floor, candidate_metric)
        return ev
