"""Window-label -> date-range resolution for the finance-argus engine.

The quant-factor ``BacktestSpec.window`` is a *label* ("train" / "validation" /
"test" / a walk-forward id). The finance-argus ``qlib_backtest_for_loop`` needs
explicit ``train_start/train_end`` + ``test_start/test_end`` dates. This module
holds the fixed schedule that maps one to the other.

Per the reviewer's ``plan.eval_protocol`` floor, the split must be *fixed in
advance* — so the schedule is constructed once and injected into the engine,
not recomputed per trial. The defaults mirror the finance-argus README
(train 2020-01-01..2022-12-31, test 2023-01-01..2024-06-30) and carve the last
six months of the training period as a validation slice so model selection
never touches the quarantined test set.

Pure standard library; no finance_argus / pandas import.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

# (train_start, train_end, test_start, test_end)
DateRange = tuple[str, str, str, str]


@dataclass(frozen=True)
class WindowSchedule:
    """Fixed mapping from window labels to qlib date ranges.

    ``resolve`` always returns the full ``(train_start, train_end, test_start,
    test_end)`` tuple. ``train_start/train_end`` stay at the full training
    period (qlib fits on it); only the *evaluated* slice (``test_start/
    test_end``) changes with the label — which matches how
    ``qlib_backtest_for_loop`` works (it scores at ``test_start`` and backtests
    over ``[test_start, test_end]``).
    """

    train_start: str = "2020-01-01"
    train_end: str = "2022-12-31"
    valid_start: str = "2022-07-01"
    valid_end: str = "2022-12-31"
    test_start: str = "2023-01-01"
    test_end: str = "2024-06-30"
    # Optional walk-forward windows keyed by label -> full DateRange.
    walk_forward: Mapping[str, DateRange] = field(default_factory=dict)

    def resolve(self, window: str, *, is_out_of_sample: bool = False) -> DateRange:
        """Resolve a window label to ``(train_s, train_e, test_s, test_e)``.

        ``"train"`` evaluates in-sample on the training slice; ``"validation"``
        on the held-out validation slice; ``"test"`` on the quarantined test
        slice. A ``walk_forward`` label returns its full registered range. An
        empty/unknown label evaluates on the test slice when
        ``is_out_of_sample`` else on the training slice.
        """
        label = (window or "").strip().lower()
        if label in self.walk_forward:
            return self.walk_forward[label]
        if label == "train":
            return (self.train_start, self.train_end, self.train_start, self.train_end)
        if label in ("validation", "valid"):
            return (self.train_start, self.train_end, self.valid_start, self.valid_end)
        if label == "test":
            return (self.train_start, self.train_end, self.test_start, self.test_end)
        # Unknown / empty label: defer to the oos flag.
        if is_out_of_sample:
            return (self.train_start, self.train_end, self.test_start, self.test_end)
        return (self.train_start, self.train_end, self.train_start, self.train_end)

    def evaluates_in_sample(self, window: str) -> bool:
        """True iff the resolved *evaluated* slice equals the training slice.

        Used by the engine to flag an ``is_out_of_sample=True`` trial whose
        window actually resolves to in-sample data — a labelling inconsistency
        the OOS-discipline analysis must not trust silently.
        """
        _, _, test_s, test_e = self.resolve(window)
        return (test_s, test_e) == (self.train_start, self.train_end)
