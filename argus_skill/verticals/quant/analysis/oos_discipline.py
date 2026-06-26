"""Out-of-sample discipline checks read from the search ledger.

The protected item ``analysis.test_set_quarantine`` rules: the test set was
not iteratively peeked at, and any retest is *disclosed and discounted*. The
ledger records every trial's window — so "how many times did factor X get
backtested in the test window?" is a plain count. This module exposes that
count to the reviewer.

A trial counts as "out-of-sample" when its ledger payload has
``is_out_of_sample == True``. Trials are grouped by ``window`` (the ledger
field documents this as "evaluation window label"). Counting per
``(factor_id, window)`` makes peeking visible: a factor with one in-sample
trial and one OOS trial is fine; the same factor with twenty OOS trials
across one window is the data-mining signature.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ..search_ledger import LedgerRow


@dataclass(frozen=True)
class RetestRecord:
    """How many times one factor was backtested in one evaluation window."""

    factor_id: str
    window: str
    is_out_of_sample: bool
    count: int


def retest_counts(rows: Iterable[LedgerRow]) -> tuple[RetestRecord, ...]:
    """Aggregate ``(factor_id, window, oos)`` retest counts from a ledger.

    A row that ran ``K`` factors as a combination contributes to each factor's
    count once — i.e. a single combination-trial is a single touch of each of
    its factors. Sorted descending by ``count`` so the reviewer's eye lands on
    the most-retested factors first; ``factor_id`` ascending breaks ties for
    deterministic output.
    """
    bucket: dict[tuple[str, str, bool], int] = {}
    for row in rows:
        payload: Mapping = row.payload
        factor_ids = payload.get("factor_ids") or ()
        window = str(payload.get("window", ""))
        oos = bool(payload.get("is_out_of_sample", False))
        for fid in factor_ids:
            key = (str(fid), window, oos)
            bucket[key] = bucket.get(key, 0) + 1
    records = tuple(
        RetestRecord(factor_id=fid, window=win, is_out_of_sample=oos, count=cnt)
        for (fid, win, oos), cnt in bucket.items()
    )
    return tuple(sorted(records, key=lambda r: (-r.count, r.factor_id, r.window)))
