"""Walk-forward split generator with purging and embargo — market-agnostic.

Standard k-fold cross-validation leaks information in financial time series
(random splits train on the future and predict the past, and adjacent
observations are autocorrelated). Walk-forward validation respects time order:
the train window always precedes the test window, and two guards remove the
residual leakage that time-ordering alone does not:

* **purge** — drop the last ``purge_size`` bars of the training window, so a
  label computed over a forward horizon at the train/test boundary cannot
  overlap the test window.
* **embargo** — skip ``embargo_size`` bars between train and test, so
  autocorrelation right after the boundary does not leak train information into
  the first test bars.

This module is pure index arithmetic on ``n_samples`` — it carries no market,
calendar, or cost assumptions (those live in an ``integrations/<market>/``
package). It produces the index folds; scoring each fold is the caller's job
(see :mod:`..analysis.performance` for the metrics).

Adapted from claude-trading-skills (MIT, © 2026 AGIPro):
walk-forward-validation/scripts/walk_forward.py — the crypto ``compute_sharpe``
default and the SMA-crossover demo are intentionally dropped; only the
market-agnostic splitter is kept.
"""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

WindowType = Literal["rolling", "expanding"]


@dataclass(frozen=True)
class WalkForwardConfig:
    """Configuration for a walk-forward split.

    Attributes
    ----------
    train_size
        Number of bars in the training window (rolling), or the initial
        training size that grows each fold (expanding).
    test_size
        Number of bars in each test window.
    step_size
        Bars to advance the window between folds.
    window_type
        ``"rolling"`` (fixed-size train that slides) or ``"expanding"``
        (train grows from a fixed start).
    purge_size
        Bars dropped from the END of the training window to avoid label
        leakage across the train/test boundary.
    embargo_size
        Bars skipped BETWEEN train and test to avoid autocorrelation leakage.
    """

    train_size: int = 90
    test_size: int = 14
    step_size: int = 14
    window_type: WindowType = "rolling"
    purge_size: int = 0
    embargo_size: int = 0

    def __post_init__(self) -> None:
        if self.train_size < 10:
            raise ValueError("train_size must be >= 10")
        if self.test_size < 1:
            raise ValueError("test_size must be >= 1")
        if self.step_size < 1:
            raise ValueError("step_size must be >= 1")
        if self.purge_size < 0:
            raise ValueError("purge_size must be >= 0")
        if self.embargo_size < 0:
            raise ValueError("embargo_size must be >= 0")
        if self.window_type not in ("rolling", "expanding"):
            raise ValueError("window_type must be 'rolling' or 'expanding'")


@dataclass(frozen=True)
class Fold:
    """One train/test split as integer index arrays.

    ``train_indices`` and ``test_indices`` are disjoint by construction (purge
    + embargo guarantee a gap). ``labels`` optionally carries the string
    boundary labels (e.g. dates) when the caller passed a label sequence to
    :meth:`WalkForwardValidator.split`.
    """

    fold_idx: int
    train_indices: np.ndarray
    test_indices: np.ndarray
    labels: dict[str, str] = field(default_factory=dict)


class WalkForwardValidator:
    """Generate purged, embargoed walk-forward folds over ``n_samples`` bars."""

    def __init__(self, config: WalkForwardConfig) -> None:
        self.config = config

    def split(
        self,
        n_samples: int,
        labels: Sequence[object] | None = None,
    ) -> Iterator[Fold]:
        """Yield :class:`Fold` objects in time order.

        Parameters
        ----------
        n_samples
            Total number of observations available.
        labels
            Optional per-bar labels (e.g. a ``DatetimeIndex`` or list of dates)
            of length ``n_samples`` used only to annotate the fold boundaries;
            it never affects the index math.

        Raises ``ValueError`` if ``n_samples`` is too small to form one fold, or
        if ``labels`` is given with a length other than ``n_samples``.
        """
        cfg = self.config
        if labels is not None and len(labels) != n_samples:
            raise ValueError(
                f"labels length {len(labels)} != n_samples {n_samples}"
            )
        min_required = (
            cfg.train_size + cfg.purge_size + cfg.embargo_size + cfg.test_size
        )
        if n_samples < min_required:
            raise ValueError(f"Need at least {min_required} samples, got {n_samples}")

        fold_idx = 0
        offset = 0
        while True:
            if cfg.window_type == "rolling":
                train_start = offset
                train_end = offset + cfg.train_size
            else:  # expanding: fixed start, growing end
                train_start = 0
                train_end = cfg.train_size + offset

            # Purge trims the training tail; embargo pushes the test start out.
            effective_train_end = train_end - cfg.purge_size
            test_start = train_end + cfg.embargo_size
            test_end = test_start + cfg.test_size
            if test_end > n_samples:
                break

            train_indices = np.arange(train_start, effective_train_end)
            test_indices = np.arange(test_start, test_end)

            fold_labels: dict[str, str] = {}
            if labels is not None:
                fold_labels = {
                    "train_start": str(labels[train_start]),
                    "train_end": str(labels[effective_train_end - 1]),
                    "test_start": str(labels[test_start]),
                    "test_end": str(labels[test_end - 1]),
                }

            yield Fold(
                fold_idx=fold_idx,
                train_indices=train_indices,
                test_indices=test_indices,
                labels=fold_labels,
            )
            fold_idx += 1
            offset += cfg.step_size

    def count_folds(self, n_samples: int) -> int:
        """Number of folds :meth:`split` would yield for ``n_samples`` bars."""
        return sum(1 for _ in self.split(n_samples))
