"""Look-ahead leakage probe scaffold for ``benchmark.no_lookahead``.

Real PIT data hygiene cannot be enforced from inside the harness — it lives
in how the user assembled their feature pipeline. What the harness *can* do
is run a falsification probe: corrupt the future, re-run the engine, and
confirm a metric DERIVED FROM THE SCORE (which a clean engine computes WITHOUT
reading the future) stays **invariant** when information after ``t`` is hidden.
If it moves, the score is reading the future.

This module ships:

* :class:`LeakageProbe` — a Protocol any leakage check can satisfy.
* :class:`NaNFutureLeakageProbe` — a reference implementation: replace the
  forward-return panel with NaN, re-run, and check a score-derived metric
  (``turnover``) is unchanged. A clean score is invariant; a score that secretly
  reads ``forward_returns`` shifts that metric (or makes it NaN) — and is caught.

The probe is *advisory* — it does not certify "no leakage" in the strict
sense, only that the engine survives a basic future-mask. The reviewer's
checklist still requires a written ``benchmark/LEAKAGE_CHECKS.md``; this
probe is one of the artefacts that file cites.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

from .backtest import BacktestEngine, BacktestSpec


@dataclass(frozen=True)
class LeakageReport:
    """Outcome of one probe against one engine.

    ``passed`` is the headline. ``baseline_metric`` and ``masked_metric``
    are recorded so the reviewer can see *how much* the metric changed
    rather than only the verdict. ``rationale`` is human prose for
    ``benchmark/LEAKAGE_CHECKS.md``.
    """

    probe_name: str
    passed: bool
    baseline_metric: float
    masked_metric: float
    rationale: str


@runtime_checkable
class LeakageProbe(Protocol):
    """A falsification check against a backtest engine."""

    name: str

    def check(self, engine: BacktestEngine, spec: BacktestSpec) -> LeakageReport:
        ...


@dataclass
class NaNFutureLeakageProbe:
    """Reference probe: mask the forward returns and require a SCORE-DERIVED
    metric to stay INVARIANT.

    The earlier design compared the *IC* before/after masking — but IC is
    ``Spearman(score, forward_returns)`` by definition, so masking the future
    nulls IC for ANY engine (the correlation TARGET is destroyed, not just the
    score input). That made the probe pass unconditionally — a falsification
    check that could never falsify (a perfect look-ahead score passed too).

    Instead we watch a metric DERIVED FROM THE SCORE that a clean engine computes
    WITHOUT reading forward returns — ``turnover`` (a pure function of the
    score→portfolio map). A clean score is invariant when the future is hidden, so
    the metric does not move; a leaky score (one that reads ``forward_returns``)
    changes, or goes NaN, when the future is masked. PASS = invariant, FAIL = moved.

    Intentionally tied to the toy :mod:`~.reference_engine` (whose ``turnover`` is
    score-only); users with their own engines write their own probe (the Protocol
    is the contract). ``metric_key`` may name any score-derived,
    forward-return-independent metric the engine reports. The fallback ``getattr``
    keeps the probe a no-op rather than crashing on engines without a panel.
    """

    name: str = "nan-future-returns"
    metric_key: str = "turnover"
    tolerance: float = 0.05

    def check(self, engine: BacktestEngine, spec: BacktestSpec) -> LeakageReport:
        baseline = float(engine.run(spec).metrics.get(self.metric_key, float("nan")))
        panel = getattr(engine, "panel", None)
        if panel is None or not hasattr(panel, "forward_returns"):
            return LeakageReport(
                probe_name=self.name,
                passed=False,
                baseline_metric=baseline,
                masked_metric=float("nan"),
                rationale=(
                    "engine does not expose a 'panel.forward_returns' attribute; "
                    "the probe could not run. Implement a domain-specific "
                    "LeakageProbe for production engines."
                ),
            )
        # Mask future returns and re-run, then restore. Holding the original
        # array out of the engine's reach is the whole point — a clean score is
        # unmoved; a score that secretly reads the future shifts.
        original = np.asarray(panel.forward_returns).copy()
        try:
            np.copyto(panel.forward_returns, np.nan)
            masked = float(
                engine.run(spec).metrics.get(self.metric_key, float("nan"))
            )
        finally:
            np.copyto(panel.forward_returns, original)
        # PASS when the score-derived metric is INVARIANT to hiding the future
        # (the score did not read forward returns). FAIL when it moved or went
        # NaN (the score path is reading data it must not — look-ahead).
        passed = (
            not math.isnan(baseline)
            and not math.isnan(masked)
            and abs(masked - baseline) <= self.tolerance
        )
        rationale = (
            f"baseline {self.metric_key}={baseline:.4f}, "
            f"forward-returns-masked {self.metric_key}={masked:.4f}; "
            + (
                "score-derived metric unchanged when the future was hidden → the "
                "score does not read forward returns."
                if passed
                else "score-derived metric MOVED (or went NaN) when the future "
                "was hidden — the score path is reading forward returns (look-ahead)."
            )
        )
        return LeakageReport(
            probe_name=self.name,
            passed=passed,
            baseline_metric=baseline,
            masked_metric=masked,
            rationale=rationale,
        )
