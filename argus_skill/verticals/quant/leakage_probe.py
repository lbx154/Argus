"""Look-ahead leakage probe scaffold for ``benchmark.no_lookahead``.

Real PIT data hygiene cannot be enforced from inside the harness — it lives
in how the user assembled their feature pipeline. What the harness *can* do
is run a falsification probe: corrupt the future, re-run the engine, and
confirm the IC of a factor whose signal is supposed to come from time
``t`` does **not** drop catastrophically when information after ``t`` is
hidden. If it does, the engine is reading the future.

This module ships:

* :class:`LeakageProbe` — a Protocol any leakage check can satisfy.
* :class:`NaNFutureLeakageProbe` — a reference implementation: replace the
  forward-return panel with NaN starting at horizon ``h`` and verify the
  engine still produces a result without using the masked rows. A naive
  engine that secretly reads ``forward_returns[t+1]`` will either crash or
  produce identical numbers (proving it depends on data it shouldn't).

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


def _materially_different(a: float, b: float, *, tol: float = 0.05) -> bool:
    """``True`` iff |a - b| > ``tol``, treating NaN as 'unmeasurable, fail open'."""
    if math.isnan(a) or math.isnan(b):
        return True
    return abs(a - b) > tol


@dataclass
class NaNFutureLeakageProbe:
    """Reference probe: mask the forward returns, expect the IC to collapse.

    The probe sets ``engine.panel.forward_returns`` to NaN, runs the trial,
    and compares the masked-IC to the baseline IC. A clean engine yields a
    masked IC that is NaN or close to zero (since there is no signal to
    correlate against). A leaky engine that secretly read the future returns
    as part of its score keeps producing the original IC — a clear
    contradiction.

    This implementation is intentionally tied to the toy
    :mod:`~.reference_engine` panel shape; users with their own engines write
    their own probe (the Protocol is the contract). The fallback ``getattr``
    keeps the probe a no-op rather than crashing on engines that don't expose
    a panel.
    """

    name: str = "nan-future-returns"
    metric_key: str = "ic"
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
        # array out of the engine's reach for the duration of the probe is the
        # whole point — a leak shows up as the metric refusing to budge.
        original = np.asarray(panel.forward_returns).copy()
        try:
            np.copyto(panel.forward_returns, np.nan)
            masked = float(
                engine.run(spec).metrics.get(self.metric_key, float("nan"))
            )
        finally:
            np.copyto(panel.forward_returns, original)
        # A clean engine should produce |masked| << |baseline| (no signal left).
        # Pass when the masked IC is materially smaller than the baseline.
        passed = (
            math.isnan(masked)
            or abs(masked) < self.tolerance
            or abs(masked) < 0.5 * abs(baseline)
        )
        rationale = (
            f"baseline {self.metric_key}={baseline:.4f}, "
            f"forward-returns-masked {self.metric_key}={masked:.4f}; "
            + (
                "metric collapsed as expected → no obvious look-ahead in the "
                "score path."
                if passed
                else "metric did NOT collapse when the future was hidden — "
                "engine is reading data it should not have."
            )
        )
        return LeakageReport(
            probe_name=self.name,
            passed=passed,
            baseline_metric=baseline,
            masked_metric=masked,
            rationale=rationale,
        )


def render_leakage_section(reports: tuple[LeakageReport, ...]) -> str:
    """Render a sequence of probe reports as a markdown section.

    The reviewer's ``benchmark/LEAKAGE_CHECKS.md`` is supposed to be a human
    document; this renderer lets the engineer drop the probe outputs in
    verbatim instead of paraphrasing.
    """
    if not reports:
        return "## Leakage probes\n\n_No probes were run._\n"
    lines = ["## Leakage probes\n"]
    for r in reports:
        verdict = "PASS" if r.passed else "FAIL"
        lines.append(
            f"- **{r.probe_name}** — {verdict}. "
            f"baseline={r.baseline_metric:.4f}, masked={r.masked_metric:.4f}. "
            f"{r.rationale}"
        )
    lines.append("")
    return "\n".join(lines)
