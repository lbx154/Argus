"""On-disk cache for adata fundamental fetches — fetch CSI500 once, reuse offline.

``adata.stock.finance.get_core_index`` is one network round-trip per code (~1s);
a CSI500 build is ~500 calls. :func:`cached_fetcher` wraps any inner fetcher in a
read-through pickle cache keyed by the (bare) code, so the second run of a
factor/model round needs no network. The cache is the ONLY filesystem write in
the fundamentals path and lives behind an explicit ``cache_dir`` (default under
``~/.cache/argus``, never under the qlib dump).

Pickle (not parquet) keeps this dependency-free and round-trips the adata frame's
dtypes exactly; the per-code frames are tiny (~10^2 rows).
"""
from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from typing import Any

from .fundamentals import Fetcher, _default_fetch, _to_adata_code

DEFAULT_CACHE_DIR = os.path.expanduser("~/.cache/argus/adata_fundamentals")


def cached_fetcher(
    cache_dir: str = DEFAULT_CACHE_DIR, *, inner: Fetcher | None = None
) -> Fetcher:
    """Return a :data:`~.fundamentals.Fetcher` backed by a read-through pickle cache.

    ``code`` is expected already bare (as :func:`load_fundamental_panel` passes it,
    post :func:`_to_adata_code`). On a miss, calls ``inner`` (default the live
    adata fetch), writes ``{cache_dir}/{code}.pkl``, and returns it — an empty
    result is cached too, so a code with no fundamentals is not re-fetched. Inject
    ``inner`` + a temp ``cache_dir`` in tests for a network-free, deterministic seam.
    """
    import pandas as pd

    fetch = inner or _default_fetch
    os.makedirs(cache_dir, exist_ok=True)

    def _fetch(code: str) -> Any:
        path = os.path.join(cache_dir, f"{code}.pkl")
        if os.path.exists(path):
            return pd.read_pickle(path)
        df = fetch(code)
        try:
            (df if df is not None else pd.DataFrame()).to_pickle(path)
        except Exception:  # noqa: BLE001 - caching must never break a fetch
            pass
        return df

    return _fetch


def warm_fundamental_cache(
    codes: Sequence[str],
    cache_dir: str = DEFAULT_CACHE_DIR,
    *,
    fetch: Fetcher | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    """Pre-populate the cache for ``codes`` (one-time ~8 min for CSI500).

    ``codes`` may be qlib-style (``SH600519``) or bare — normalised via
    :func:`_to_adata_code`. Returns ``{"total","have","empty"}`` coverage counts.
    """
    fetch = cached_fetcher(cache_dir, inner=fetch)
    have = empty = 0
    n = len(codes)
    for i, code in enumerate(codes, 1):
        df = fetch(_to_adata_code(code))
        if df is None or len(df) == 0:
            empty += 1
        else:
            have += 1
        if progress:
            progress(i, n)
    return {"total": n, "have": have, "empty": empty}
