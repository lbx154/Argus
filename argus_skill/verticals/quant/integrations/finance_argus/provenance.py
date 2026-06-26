"""Provenance helpers: data-snapshot resolution and config hashing.

Pure standard library — imports nothing from ``finance_argus`` and nothing
heavy, so it is safe to import eagerly from the integration ``__init__``.

Two jobs, both in service of the reviewer's reproducibility floor
(``submission.reproducible``, ``analysis.test_set_quarantine``):

* :func:`resolve_data_snapshot` turns the on-disk qlib data dump into a stable
  version string the auditor can quote (and that goes into the config hash), by
  reading ``manifest.json`` + the last calendar day. No qlib import.
* :func:`compute_config_hash` produces a short, order-invariant fingerprint of
  *what was run* so two trials with the same factors/window/data collide and
  two that differ do not. This is what makes the OOS-discipline retest counts
  meaningful.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from pathlib import Path

# The default location the finance-argus qlib bridge dumps to (see
# finance_argus integrations/qlib_bridge/init_helper.resolve_provider_uri).
DEFAULT_QLIB_PROVIDER_URI = "~/.qlib/qlib_data/cn_data_tushare"


def resolve_data_snapshot(provider_uri: str | os.PathLike[str] | None = None) -> str:
    """Return a stable version string for the qlib data dump.

    Reads ``<provider_uri>/manifest.json`` (``universe``, ``calendar_days``)
    and the last line of ``calendars/day.txt`` (the last trading day with
    data). Produces e.g.::

        qlib:cn_data_tushare@cal=2026-06-04;days=1068;universe=all

    Falls back to ``qlib:<name>@unknown`` when the manifest/calendar are absent
    (so a missing dump is labelled honestly rather than crashing). Pure stdlib;
    never imports qlib.
    """
    root = Path(os.path.expanduser(str(provider_uri or DEFAULT_QLIB_PROVIDER_URI)))
    name = root.name or "qlib_data"

    universe = "unknown"
    days: int | None = None
    manifest = root / "manifest.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            universe = str(data.get("universe", universe))
            raw_days = data.get("calendar_days")
            days = int(raw_days) if raw_days is not None else None
        except (ValueError, OSError):
            pass

    last_day = "unknown"
    calendar = root / "calendars" / "day.txt"
    if calendar.is_file():
        try:
            lines = [ln.strip() for ln in calendar.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if lines:
                last_day = lines[-1]
        except OSError:
            pass

    days_part = f";days={days}" if days is not None else ""
    return f"qlib:{name}@cal={last_day}{days_part};universe={universe}"


def compute_config_hash(
    *,
    engine_name: str,
    universe: str,
    factor_ids: Sequence[str],
    window_dates: tuple[str, str, str, str],
    data_snapshot: str,
    weighting: str,
    seed: int | None,
) -> str:
    """A short, deterministic fingerprint of a trial's configuration.

    ``factor_ids`` is sorted so a combination of the same factors hashes the
    same regardless of selection order. ``window_dates`` is the *resolved*
    ``(train_start, train_end, test_start, test_end)`` tuple, so the same
    factors evaluated on different windows get different hashes. The trial's
    ``iteration`` / run_id are deliberately excluded (they are not part of the
    configuration). Returns the first 16 hex chars of a SHA-256, matching the
    toy engine's convention.
    """
    canonical = json.dumps(
        {
            "engine": engine_name,
            "universe": universe,
            "factor_ids": sorted(factor_ids),
            "window_dates": list(window_dates),
            "data_snapshot": data_snapshot,
            "weighting": weighting,
            "seed": seed,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
