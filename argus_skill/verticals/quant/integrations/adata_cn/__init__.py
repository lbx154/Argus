"""adata A-share data integration — real OHLCV panels for the quant vertical.

See :mod:`.loader`. This subpackage is the concrete A-share market binding: it
turns adata k-line fetches into the ``(T, S)`` cross-section arrays the
market-agnostic ``factor_toolkit`` / ``analysis`` layers consume. ``adata`` is
imported lazily inside the loader, so importing this subpackage never requires
adata to be installed or the network to be up.
"""
from __future__ import annotations

from .loader import (
    all_a_codes,
    forward_returns,
    load_ohlcv_panel,
    to_feature_panel,
)

__all__ = [
    "all_a_codes",
    "forward_returns",
    "load_ohlcv_panel",
    "to_feature_panel",
]
