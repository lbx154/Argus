"""Statistical helpers for the ``analysis`` stage.

These are the missing pieces the L2 reviewer needs to actually rule on
``analysis.multiple_testing``, ``analysis.independence`` and
``analysis.test_set_quarantine``. Without them the LLM reviewer would have to
"eyeball" honesty, which is fine for prose but not for numbers.

Three pure-numpy modules, no pandas:

* :mod:`.multiple_testing` — deflated Sharpe and Benjamini-Hochberg FDR.
* :mod:`.orthogonality` — pairwise correlation and Gram-Schmidt residual
  variance share, so a candidate factor's *incremental* signal over an
  existing factor set can be quantified.
* :mod:`.oos_discipline` — counts retests of the same factor in the same
  out-of-sample window from a search ledger, so peeking at the test set is
  visible rather than asserted.
"""
from __future__ import annotations

from .multiple_testing import (
    bh_fdr,
    deflated_sharpe_ratio,
    haircut_sharpe,
)
from .oos_discipline import retest_counts
from .orthogonality import (
    correlation_matrix,
    incremental_variance_share,
)

__all__ = [
    "bh_fdr",
    "correlation_matrix",
    "deflated_sharpe_ratio",
    "haircut_sharpe",
    "incremental_variance_share",
    "retest_counts",
]
