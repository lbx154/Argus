"""Sidecar writers for the recommendation / combination artefacts.

``BacktestResult.metrics`` is ``Mapping[str, float]`` — there is no room in the
audited result/ledger schema for list-valued ``top_n_picks`` or a per-factor
weight map. Rather than widen that frozen contract, the realised picks and the
combination recipe are written to the two files the reviewer checklist already
names:

* ``run/COMBINATIONS.json`` — one entry per trial: the weighting method and the
  per-factor weights (the ``run.combinations`` checklist item).
* ``recommendations/<run_id>.json`` — the portfolio output: the top-N picks plus
  the provenance needed to reproduce them.

Both files join back to the hash-chained ledger by ``run_id`` + ``config_hash``,
so nothing about the audit trail is lost by keeping them out of ``metrics``.

Pure standard library; no finance_argus / pandas import. The sink is optional —
disable it (pass ``recommendations_dir=None``) when a run dir isn't wanted.
"""
from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# Avoid a circular import at module load: BacktestSpec is only needed for typing.
from ...backtest import BacktestSpec


def declared_weights(weighting: str, factor_ids: Sequence[str]) -> dict[str, float]:
    """The *declared* combination weights implied by a weighting label.

    ``"single"`` puts all weight on the one factor; ``"equal_weight"`` (and any
    other label, as a safe default) splits evenly. These are the intended
    weights, not the realised IC weights the engine computes internally — see
    the warning the engine emits.
    """
    ids = list(factor_ids)
    if not ids:
        return {}
    if weighting == "single":
        # By convention a single-factor trial weights its first (only) factor.
        return {ids[0]: 1.0}
    share = 1.0 / len(ids)
    return {fid: share for fid in ids}


def _picks_format(raw: Mapping[str, Any]) -> str:
    """qlib path returns qlib codes (``SH600519``); mock returns ts_codes."""
    return "qlib_code" if raw.get("_engine") == "qlib" else "ts_code"


@runtime_checkable
class RecommendationSink(Protocol):
    """Records the non-metric outputs of a trial (picks + combination recipe)."""

    def record(
        self,
        spec: BacktestSpec,
        raw: Mapping[str, Any],
        *,
        config_hash: str,
        data_snapshot: str | None = None,
        weights: Mapping[str, float] | None = None,
    ) -> None:
        ...


@dataclass
class JsonRecommendationSink:
    """Write ``run/COMBINATIONS.json`` and ``recommendations/<run_id>.json``.

    ``root`` is the run directory (typically the same dir whose ``run/``
    subfolder holds ``SEARCH_LEDGER.jsonl``). Writes are last-write-wins per
    ``run_id``; the ``ForcingExecutor`` lock serialises callers so the merge is
    race-free in-process.
    """

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    def record(
        self,
        spec: BacktestSpec,
        raw: Mapping[str, Any],
        *,
        config_hash: str,
        data_snapshot: str | None = None,
        weights: Mapping[str, float] | None = None,
    ) -> None:
        resolved = dict(weights) if weights is not None else declared_weights(
            spec.weighting, spec.factor_ids
        )
        self._append_combination(spec, config_hash, resolved)
        self._write_recommendation(spec, raw, config_hash, data_snapshot)

    # -- run/COMBINATIONS.json -------------------------------------------

    def _append_combination(
        self, spec: BacktestSpec, config_hash: str, weights: Mapping[str, float]
    ) -> None:
        path = self.root / "run" / "COMBINATIONS.json"
        entries = self._load_list(path)
        entry = {
            "run_id": spec.run_id,
            "config_hash": config_hash,
            "weighting": spec.weighting,
            "factor_ids": list(spec.factor_ids),
            "weights": dict(weights),
            "window": spec.window,
            "is_out_of_sample": spec.is_out_of_sample,
        }
        # Last-write-wins per run_id (a re-run of the same trial replaces it).
        entries = [e for e in entries if e.get("run_id") != spec.run_id]
        entries.append(entry)
        self._dump(path, entries)

    # -- recommendations/<run_id>.json -----------------------------------

    def _write_recommendation(
        self,
        spec: BacktestSpec,
        raw: Mapping[str, Any],
        config_hash: str,
        data_snapshot: str | None = None,
    ) -> None:
        picks = list(raw.get("top_n_picks", []) or [])
        payload = {
            "run_id": spec.run_id,
            "config_hash": config_hash,
            "universe": spec.universe,
            "data_snapshot": data_snapshot or spec.data_snapshot,
            "window": spec.window,
            "is_out_of_sample": spec.is_out_of_sample,
            "factor_ids": list(spec.factor_ids),
            "weighting": spec.weighting,
            "picks_format": _picks_format(raw),
            "n_picks": len(picks),
            "top_n_picks": picks,
            "generated_at": time.time(),
        }
        path = self.root / "recommendations" / f"{spec.run_id}.json"
        self._dump(path, payload)

    # -- io helpers ------------------------------------------------------

    @staticmethod
    def _load_list(path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return list(data) if isinstance(data, list) else []
        except (ValueError, OSError):
            return []

    @staticmethod
    def _dump(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
