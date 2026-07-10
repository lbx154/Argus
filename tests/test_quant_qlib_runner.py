"""Tests for the finance-argus ``qlib_runner`` universe / train-window handling.

``qlib_backtest_run`` accepts ``universe``, ``train_start``, ``train_end`` but
(before this fix) never referenced any of them in its body — every trial
silently ran the same backtest regardless of what universe/train window a
``BacktestSpec`` requested. That's exactly the kind of silent-drop bug the
quant vertical must not tolerate.

Neither ``finance_argus`` nor ``qlib`` is installed in this environment (both
are private/external dependencies), so this module fakes the handful of
symbols ``qlib_backtest_run`` imports lazily (``finance_argus.core.*``,
``finance_argus.integrations.qlib_bridge.*``, ``qlib.backtest``,
``qlib.contrib.evaluate``, ``qlib.contrib.strategy``, ``qlib.data``) via
``sys.modules`` injection, and asserts the fixed behaviour end to end:

* ``universe`` genuinely restricts the scored/traded instrument pool via
  qlib's own ``D.instruments``/``D.list_instruments`` membership API — two
  otherwise-identical calls that only differ by ``universe`` now produce
  different signals (previously byte-identical).
* An empty universe/screen overlap raises rather than silently backtesting an
  empty or wrong pool.
* ``train_start``/``train_end`` are, by design (see the updated docstring in
  ``qlib_runner.py``), *not* consumed by this particular one-shot,
  declared-weight runner — but that is no longer a silent omission: the
  ``FinanceArgusEngine`` now emits an explicit warning disclosing it whenever
  the default (unfitted) qlib runner is used.
"""
from __future__ import annotations

import sys
import types
from typing import Any

import pandas as pd
import pytest

from argus_skill.verticals.quant.backtest import BacktestSpec
from argus_skill.verticals.quant.integrations.finance_argus import qlib_runner
from argus_skill.verticals.quant.integrations.finance_argus.engine import FinanceArgusEngine

# ts_code -> whether it belongs to each fake "universe" (mirrors real qlib
# D.instruments membership tables, just inlined for the test).
_UNIVERSE_MEMBERS = {
    "csi300": ["SH600000", "SH600001"],
    "csi500": ["SZ000001"],
}
_ALL_SCORED_CODES = ["SH600000", "SH600001", "SZ000001", "SH999999"]


class _FakeD:
    """Stand-in for ``qlib.data.D``; records every call for assertions."""

    def __init__(self) -> None:
        self.instruments_calls: list[str] = []
        self.list_instruments_calls: list[tuple[str, str, str, bool]] = []

    def instruments(self, market: str) -> dict[str, str]:
        self.instruments_calls.append(market)
        return {"market": market}

    def list_instruments(
        self,
        instruments: dict[str, str],
        start_time: str | None = None,
        end_time: str | None = None,
        freq: str = "day",
        as_list: bool = False,
    ) -> list[str]:
        market = instruments["market"]
        self.list_instruments_calls.append((market, start_time, end_time, as_list))
        return list(_UNIVERSE_MEMBERS.get(market, []))


class _FakeTopkDropoutStrategy:
    """Records the ``signal`` it was constructed with."""

    last_instance: "_FakeTopkDropoutStrategy | None" = None

    def __init__(
        self,
        signal: pd.Series,
        topk: int,
        n_drop: int,
        only_tradable: bool = True,
        forbid_all_trade_at_limit: bool = True,
    ) -> None:
        self.signal = signal
        self.topk = topk
        self.n_drop = n_drop
        _FakeTopkDropoutStrategy.last_instance = self


class _FakeMarket:
    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg

    def build_market_screen(self, date: str, pure_quant: bool = True, progress_callback=None):
        return None, f"screen-as-of-{date}"


class _FakeQuantFactorModel:
    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self.definitions: tuple[Any, ...] = ()

    def score_cross_section(self, screen: Any) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "ts_code": _ALL_SCORED_CODES,
                "quant_score": [0.5, 0.3, 0.9, 0.1],
            }
        )


class _FakeFactorPool:
    @staticmethod
    def with_builtins() -> "_FakeFactorPool":
        return _FakeFactorPool()

    def definitions(self, names):
        return [types.SimpleNamespace(name=n) for n in names]


def _fake_qlib_backtest(**kwargs):
    report = pd.DataFrame({"return": [0.001, 0.002, -0.0005]})
    return {"1day": (report, None)}, None


def _fake_risk_analysis(series):
    class _Result:
        @staticmethod
        def to_dict():
            return {
                "risk": {
                    "information_ratio": 1.5,
                    "max_drawdown": -0.12,
                    "annualized_return": 0.08,
                }
            }

    return _Result()


def _make_module(name: str, **attrs: Any) -> types.ModuleType:
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def _install_fake_finance_argus_and_qlib(monkeypatch: pytest.MonkeyPatch) -> _FakeD:
    """Inject fake ``finance_argus``/``qlib`` modules; returns the fake ``D``."""
    fake_d = _FakeD()

    fa = _make_module("finance_argus")
    fa_core = _make_module("finance_argus.core")
    fa_core_config = _make_module("finance_argus.core.config", load_config=lambda: object())
    fa_core_data = _make_module("finance_argus.core.data", TinyshareMarketData=_FakeMarket)
    fa_core_factor_pool = _make_module(
        "finance_argus.core.factor_pool", FactorPool=_FakeFactorPool
    )
    fa_core_quant = _make_module(
        "finance_argus.core.quant", QuantFactorModel=_FakeQuantFactorModel
    )
    fa_integrations = _make_module("finance_argus.integrations")
    fa_qlib_bridge = _make_module("finance_argus.integrations.qlib_bridge")
    fa_qlib_bridge_init_helper = _make_module(
        "finance_argus.integrations.qlib_bridge.init_helper",
        init_qlib_bridge=lambda: None,
    )
    fa_qlib_bridge_universe = _make_module(
        "finance_argus.integrations.qlib_bridge.universe",
        ts_to_qlib_code=lambda code: code,
    )
    fa.core = fa_core
    fa_core.config = fa_core_config
    fa_core.data = fa_core_data
    fa_core.factor_pool = fa_core_factor_pool
    fa_core.quant = fa_core_quant
    fa.integrations = fa_integrations
    fa_integrations.qlib_bridge = fa_qlib_bridge
    fa_qlib_bridge.init_helper = fa_qlib_bridge_init_helper
    fa_qlib_bridge.universe = fa_qlib_bridge_universe

    qlib = _make_module("qlib")
    qlib_backtest_mod = _make_module("qlib.backtest", backtest=_fake_qlib_backtest)
    qlib_contrib = _make_module("qlib.contrib")
    qlib_contrib_evaluate = _make_module(
        "qlib.contrib.evaluate", risk_analysis=_fake_risk_analysis
    )
    qlib_contrib_strategy = _make_module(
        "qlib.contrib.strategy", TopkDropoutStrategy=_FakeTopkDropoutStrategy
    )
    qlib_data = _make_module("qlib.data", D=fake_d)
    qlib.backtest = qlib_backtest_mod
    qlib.contrib = qlib_contrib
    qlib_contrib.evaluate = qlib_contrib_evaluate
    qlib_contrib.strategy = qlib_contrib_strategy
    qlib.data = qlib_data

    fake_modules = {
        "finance_argus": fa,
        "finance_argus.core": fa_core,
        "finance_argus.core.config": fa_core_config,
        "finance_argus.core.data": fa_core_data,
        "finance_argus.core.factor_pool": fa_core_factor_pool,
        "finance_argus.core.quant": fa_core_quant,
        "finance_argus.integrations": fa_integrations,
        "finance_argus.integrations.qlib_bridge": fa_qlib_bridge,
        "finance_argus.integrations.qlib_bridge.init_helper": fa_qlib_bridge_init_helper,
        "finance_argus.integrations.qlib_bridge.universe": fa_qlib_bridge_universe,
        "qlib": qlib,
        "qlib.backtest": qlib_backtest_mod,
        "qlib.contrib": qlib_contrib,
        "qlib.contrib.evaluate": qlib_contrib_evaluate,
        "qlib.contrib.strategy": qlib_contrib_strategy,
        "qlib.data": qlib_data,
    }
    for name, mod in fake_modules.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return fake_d


def _signal_instruments(strategy: _FakeTopkDropoutStrategy) -> set[str]:
    return set(strategy.signal.index.get_level_values("instrument"))


def test_universe_restricts_signal_to_membership(monkeypatch: pytest.MonkeyPatch) -> None:
    """`universe="csi300"` must only score/trade csi300 members, not every code."""
    fake_d = _install_fake_finance_argus_and_qlib(monkeypatch)

    result = qlib_runner.qlib_backtest_run(
        ["factor_a"], 1, universe="csi300", test_start="2023-01-01", test_end="2023-01-10"
    )

    assert fake_d.instruments_calls == ["csi300"]
    assert fake_d.list_instruments_calls == [("csi300", "2023-01-01", "2023-01-10", True)]

    strategy = _FakeTopkDropoutStrategy.last_instance
    assert strategy is not None
    assert _signal_instruments(strategy) == {"SH600000", "SH600001"}
    # SZ000001 / SH999999 were scored but are outside csi300 -> must be dropped.
    assert "SZ000001" not in _signal_instruments(strategy)
    assert "SH999999" not in _signal_instruments(strategy)

    assert result["_universe"] == "csi300"
    assert set(result["top_n_picks"]).issubset({"SH600000", "SH600001"})


def test_different_universe_changes_the_backtest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two specs differing only by `universe` must no longer be byte-identical."""
    _install_fake_finance_argus_and_qlib(monkeypatch)
    qlib_runner.qlib_backtest_run(["factor_a"], 1, universe="csi300")
    signal_300 = _signal_instruments(_FakeTopkDropoutStrategy.last_instance)

    _install_fake_finance_argus_and_qlib(monkeypatch)
    qlib_runner.qlib_backtest_run(["factor_a"], 1, universe="csi500")
    signal_500 = _signal_instruments(_FakeTopkDropoutStrategy.last_instance)

    assert signal_300 == {"SH600000", "SH600001"}
    assert signal_500 == {"SZ000001"}
    assert signal_300 != signal_500  # was previously always identical (bug)


def test_universe_with_no_overlap_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_finance_argus_and_qlib(monkeypatch)
    with pytest.raises(ValueError, match="matched none of the scored instruments"):
        qlib_runner.qlib_backtest_run(["factor_a"], 1, universe="csi_unknown")


def test_engine_discloses_default_runner_does_not_fit_train_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default (backtest_fn=None) qlib path must honestly disclose that
    train_start/train_end don't shape its computation, instead of silently
    accepting-and-dropping them."""
    _install_fake_finance_argus_and_qlib(monkeypatch)

    engine = FinanceArgusEngine()  # default: backtest_fn=None -> qlib_backtest_run
    spec = BacktestSpec(
        run_id="r1",
        factor_ids=["factor_a"],
        weighting="single",
        window="test",
        is_out_of_sample=True,
        universe="csi300",
        data_snapshot="snap:v1",
    )

    result = engine.run(spec)

    assert result.status == "ok"
    assert any(
        "does not fit on train_start/train_end" in w for w in result.warnings
    ), result.warnings
    # Regression guard: the pre-existing declared-weights disclosure must survive.
    assert any("declared, not realised IC weights" in w for w in result.warnings)
    assert result.metrics["sharpe"] == pytest.approx(1.5)
