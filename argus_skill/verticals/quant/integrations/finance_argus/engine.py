"""``FinanceArgusEngine`` — the real A-share backtest engine as a ``BacktestEngine``.

Wraps finance-argus' backtest (qlib production engine by default; the
deterministic ``mock_backtest`` for CI) behind the quant-factor
``BacktestEngine`` Protocol so it can be driven by the ``ForcingExecutor`` and
have every trial captured in the search ledger.

Responsibilities of ``run``:

1. Resolve ``spec.window`` -> qlib date ranges via the injected
   :class:`~.windows.WindowSchedule` (``spec.params`` may override).
2. Call the backtest function with the right calling convention (qlib takes
   keyword window/universe args; mock takes only ``factor_names, iteration``).
3. Map the returned dict into ``BacktestResult.metrics`` — only the metrics that
   are *real*, with honest warnings for what's missing or proxied.
4. Stamp provenance (``engine``, ``config_hash``, ``data_snapshot``) and hand the
   picks / combination recipe to an optional :class:`RecommendationSink`.

finance-argus is imported lazily inside ``run`` (only for the qlib path) so that
importing this module — or running the mock path — never drags in pandas/qlib.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from ...backtest import BacktestResult, BacktestSpec
from .provenance import compute_config_hash, resolve_data_snapshot
from .recommendations import RecommendationSink, declared_weights
from .windows import WindowSchedule

BacktestFn = Callable[..., Mapping[str, Any]]
BacktestFnKind = Literal["qlib", "mock"]

# finance-argus dict key -> quant-factor metric key. Only these four are real.
_METRIC_MAP: dict[str, str] = {
    "mean_ic": "ic",
    "sharpe": "sharpe",
    "max_drawdown": "max_drawdown",
    "cumulative_return": "cumulative_return",
}

# Metrics the toy engine emits that the real engine genuinely does not produce.
# We warn rather than fabricate them.
_UNAVAILABLE_METRICS = ("icir", "turnover")


def map_metrics(raw: Mapping[str, Any]) -> tuple[dict[str, float], list[str]]:
    """Map a finance-argus result dict to ``(metrics, warnings)``.

    Emits only the four metrics that are real (``ic, sharpe, max_drawdown,
    cumulative_return``), each coerced to ``float``. Non-float / missing values
    become a warning instead of a bad metric. ``top_n_picks`` and the ``_*``
    bookkeeping keys never leak into ``metrics``. Does **not** synthesise
    ``icir``/``turnover`` — their absence is disclosed as a warning, consistent
    with the domain's anti-placeholder review floor.
    """
    metrics: dict[str, float] = {}
    warnings: list[str] = []
    for src_key, dst_key in _METRIC_MAP.items():
        if src_key not in raw:
            warnings.append(f"engine did not report {src_key!r} (-> {dst_key!r})")
            continue
        value = raw[src_key]
        try:
            metrics[dst_key] = float(value)
        except (TypeError, ValueError):
            warnings.append(f"metric {src_key!r} was non-numeric ({value!r}); omitted")
    for missing in _UNAVAILABLE_METRICS:
        warnings.append(f"engine does not report {missing!r}; downstream {missing} analysis unavailable")
    return metrics, warnings


@dataclass
class FinanceArgusEngine:
    """A :class:`~...backtest.BacktestEngine` backed by finance-argus.

    Attributes
    ----------
    name
        Engine build identifier recorded for provenance.
    schedule
        Window-label -> date-range mapping (fixed in advance).
    universe_default
        qlib universe used when ``spec.universe`` is empty.
    topk
        Number of names held by qlib's ``TopkDropoutStrategy``.
    n_drop
        Names rotated out per rebalance.
    benchmark
        qlib instrument code used as the excess-return reference on the default
        qlib path (must exist in the dump, e.g. ``"SZ000905"``). ``None`` lets
        qlib use its own default index. Ignored when an external ``backtest_fn``
        is supplied.
    backtest_fn / backtest_fn_kind
        The callable that actually runs a backtest. ``None`` (the default) uses
        the benchmark-flexible :func:`~.qlib_runner.qlib_backtest_run`. Pass
        ``mock_backtest`` with ``backtest_fn_kind="mock"`` for CI. The *kind*
        selects the calling convention explicitly (no signature introspection).
    provider_uri
        qlib data dump location, used to resolve ``data_snapshot``.
    recorder
        Optional sink for picks / combination artefacts.
    """

    name: str = "finance-argus-qlib@v1"
    schedule: WindowSchedule = field(default_factory=WindowSchedule)
    universe_default: str = "csi300"
    topk: int = 50
    n_drop: int = 5
    benchmark: str | None = None
    backtest_fn: BacktestFn | None = None
    backtest_fn_kind: BacktestFnKind | None = None
    provider_uri: str | None = None
    data_snapshot_override: str | None = None
    recorder: RecommendationSink | None = None
    _iteration: int = field(default=0, init=False)

    def run(self, spec: BacktestSpec) -> BacktestResult:  # noqa: C901 - linear, readable
        factor_ids = list(spec.factor_ids)
        if not factor_ids:
            raise ValueError("BacktestSpec.factor_ids must be non-empty")

        universe = spec.universe or self.universe_default
        warnings: list[str] = []

        # 1) windows: schedule, with explicit spec.params override.
        window_dates = self._resolve_window(spec, warnings)

        # 2) OOS-labelling consistency check.
        if spec.is_out_of_sample and self.schedule.evaluates_in_sample(spec.window) \
                and not self._has_param_window(spec):
            warnings.append(
                f"is_out_of_sample=True but window {spec.window!r} resolves to the "
                "training slice; OOS-discipline counts may be misleading"
            )

        # 3) provenance. The config hash is computed over the snapshot the
        # *ledger* records (spec.data_snapshot) so the two never diverge. The
        # resolved snapshot is only a human-facing label for the recommendation
        # deliverable; if the caller left it empty we disclose the weaker
        # provenance rather than silently hashing a value the ledger won't show.
        kind = self._kind()
        rec_snapshot = self._data_snapshot(spec, kind)
        if not spec.data_snapshot:
            warnings.append(
                "data_snapshot not set on spec; ledger/config_hash provenance is "
                f"weaker (resolved {rec_snapshot!r} for the recommendation only)"
            )
        config_hash = compute_config_hash(
            engine_name=self.name,
            universe=universe,
            factor_ids=factor_ids,
            window_dates=window_dates,
            data_snapshot=spec.data_snapshot,
            weighting=spec.weighting,
            seed=spec.seed,
        )

        # 4) run the backtest (engine errors propagate; run_backtest records them).
        self._iteration += 1
        raw = self._invoke(kind, factor_ids, universe, window_dates)

        # 5) map metrics + collect engine-side warnings.
        metrics, metric_warnings = map_metrics(raw)
        warnings.extend(metric_warnings)
        if kind == "qlib":
            warnings.append("ic is a sharpe/8 proxy from the qlib path, not a measured cross-sectional IC")
        warnings.append("combination weights recorded are declared, not realised IC weights")

        # 6) surface picks / combination recipe via the optional sink.
        if self.recorder is not None:
            self.recorder.record(
                spec,
                raw,
                config_hash=config_hash,
                data_snapshot=rec_snapshot,
                weights=declared_weights(spec.weighting, factor_ids),
            )

        return BacktestResult(
            run_id=spec.run_id,
            status="ok",
            metrics=metrics,
            engine=self.name,
            config_hash=config_hash,
            warnings=tuple(warnings),
        )

    # -- internals -------------------------------------------------------

    def _kind(self) -> BacktestFnKind:
        if self.backtest_fn_kind is not None:
            return self.backtest_fn_kind
        # Default: a None backtest_fn means the qlib path; an injected fn with
        # no declared kind is assumed to be a mock (the common test case).
        return "qlib" if self.backtest_fn is None else "mock"

    def _resolve_window(self, spec: BacktestSpec, warnings: list[str]) -> tuple[str, str, str, str]:
        if self._has_param_window(spec):
            p = spec.params
            dates = (
                str(p["train_start"]), str(p["train_end"]),
                str(p["test_start"]), str(p["test_end"]),
            )
            warnings.append(f"window dates overridden via spec.params: {dates}")
            return dates
        return self.schedule.resolve(spec.window, is_out_of_sample=spec.is_out_of_sample)

    @staticmethod
    def _has_param_window(spec: BacktestSpec) -> bool:
        keys = ("train_start", "train_end", "test_start", "test_end")
        return all(k in spec.params for k in keys)

    def _data_snapshot(self, spec: BacktestSpec, kind: BacktestFnKind) -> str:
        if spec.data_snapshot:
            return spec.data_snapshot
        if self.data_snapshot_override:
            return self.data_snapshot_override
        if kind == "mock":
            return "mock:v1"
        return resolve_data_snapshot(self.provider_uri)

    def _invoke(
        self,
        kind: BacktestFnKind,
        factor_ids: list[str],
        universe: str,
        window_dates: tuple[str, str, str, str],
    ) -> Mapping[str, Any]:
        train_s, train_e, test_s, test_e = window_dates
        if kind == "mock":
            fn = self.backtest_fn
            if fn is None:  # pragma: no cover - defensive; mock kind needs a fn
                raise ValueError("backtest_fn_kind='mock' requires a backtest_fn")
            return fn(factor_ids, self._iteration)
        # qlib path. Default to the benchmark-flexible runner; an explicit
        # backtest_fn (e.g. finance-argus' own qlib_backtest_for_loop, or a
        # functools.partial binding a benchmark) takes precedence.
        kwargs = dict(
            universe=universe,
            train_start=train_s, train_end=train_e,
            test_start=test_s, test_end=test_e,
            topk=self.topk, n_drop=self.n_drop,
        )
        if self.backtest_fn is None:
            from .qlib_runner import qlib_backtest_run  # lazy: pulls pandas/qlib
            return qlib_backtest_run(
                factor_ids, self._iteration, benchmark=self.benchmark, **kwargs
            )
        return self.backtest_fn(factor_ids, self._iteration, **kwargs)
