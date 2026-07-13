"""Alpha expression DSL — compose factors as formulas, safely evaluated.

The hand-written constructors in :mod:`.price_features` etc. answer "compute
THIS factor"; this module answers "let the agent COMPOSE a novel factor" as a
WorldQuant-style expression, e.g.::

    rank(ts_delta(close, 5)) - rank(ts_std(returns, 20))
    -1 * ts_decay_linear(close / vwap, 10)

It turns argus's ``FactorSpec.expression`` field (previously inert) into a
working factor: :func:`evaluate` parses the string and computes a ``(T, S)``
array over a dict of field arrays, and :func:`expression_feature` wraps it as a
:class:`..builder.FeatureSpec` so an expression drops straight into
:func:`..builder.build_feature_panel`.

**Safety**: the expression is parsed with :mod:`ast` and evaluated by a
whitelist walker — only numeric literals, the declared fields, arithmetic, and
the operators below are allowed. No attribute access, indexing, lambdas,
comprehensions, or arbitrary calls; anything else raises ``ExpressionError``.
There is no ``eval()`` of user text.

Operator vocabulary (semantics follow WorldQuant / the QuantGPT sibling repo's
``expression_parser.py``): cross-sectional ``rank / zscore / scale / demean``;
time-series ``ts_mean / ts_std / ts_sum / ts_min / ts_max / ts_delta / delay /
ts_rank / ts_corr / ts_decay_linear / ts_argmax / ts_argmin / ts_zscore``;
element-wise ``log / abs / sign / sqrt / power / signed_power``. Fields:
``open / high / low / close / volume / amount`` plus derived ``vwap`` (amount /
volume) and ``returns`` (close-to-close).
"""
from __future__ import annotations

import ast
from collections.abc import Callable, Mapping

import numpy as np


class ExpressionError(ValueError):
    """Raised when an expression is malformed or uses a disallowed construct."""


# ── field resolution (base + derived) ───────────────────────────────

def _resolve_field(name: str, fields: Mapping[str, np.ndarray]) -> np.ndarray:
    if name in fields:
        return np.asarray(fields[name], dtype=float)
    if name == "vwap":
        if "amount" in fields and "volume" in fields:
            amt = np.asarray(fields["amount"], dtype=float)
            vol = np.asarray(fields["volume"], dtype=float)
            with np.errstate(divide="ignore", invalid="ignore"):
                return np.where(vol > 0, amt / vol, np.nan)
        return _resolve_field("close", fields)
    if name == "returns":
        c = _resolve_field("close", fields)
        out = np.full_like(c, np.nan)
        with np.errstate(divide="ignore", invalid="ignore"):
            out[1:] = c[1:] / c[:-1] - 1.0
        return out
    raise ExpressionError(f"unknown field {name!r}")


# ── cross-sectional operators (per row / axis=1) ────────────────────

def _rank_row(row: np.ndarray) -> np.ndarray:
    out = np.full(row.shape, np.nan)
    m = ~np.isnan(row)
    n = int(m.sum())
    if n < 2:
        return out
    order = row[m].argsort()
    ranks = np.empty(n, dtype=float)
    ranks[order] = np.arange(n, dtype=float)
    out[m] = ranks / (n - 1)
    return out


def _cs(fn: Callable[[np.ndarray], np.ndarray]) -> Callable[[np.ndarray], np.ndarray]:
    def apply(x: np.ndarray) -> np.ndarray:
        x = np.atleast_2d(np.asarray(x, dtype=float))
        return np.vstack([fn(x[t]) for t in range(x.shape[0])])
    return apply


def _zscore_row(row: np.ndarray) -> np.ndarray:
    m = ~np.isnan(row)
    if m.sum() < 2:
        return np.full(row.shape, np.nan)
    mu, sd = row[m].mean(), row[m].std()
    out = np.full(row.shape, np.nan)
    out[m] = (row[m] - mu) / sd if sd > 0 else 0.0
    return out


def _scale_row(row: np.ndarray) -> np.ndarray:
    m = ~np.isnan(row)
    out = np.full(row.shape, np.nan)
    denom = np.abs(row[m]).sum()
    out[m] = row[m] / denom if denom > 0 else 0.0
    return out


def _demean_row(row: np.ndarray) -> np.ndarray:
    m = ~np.isnan(row)
    out = np.full(row.shape, np.nan)
    if m.sum():
        out[m] = row[m] - row[m].mean()
    return out


# ── time-series operators (per column / axis=0, window d) ───────────

def _df(x: np.ndarray):
    import pandas as pd

    return pd.DataFrame(np.asarray(x, dtype=float))


def _ts_mean(x, d): return _df(x).rolling(int(d)).mean().to_numpy()
def _ts_std(x, d): return _df(x).rolling(int(d)).std().to_numpy()
def _ts_sum(x, d): return _df(x).rolling(int(d)).sum().to_numpy()
def _ts_min(x, d): return _df(x).rolling(int(d)).min().to_numpy()
def _ts_max(x, d): return _df(x).rolling(int(d)).max().to_numpy()
def _delay(x, d): return _df(x).shift(int(d)).to_numpy()
def _ts_delta(x, d): return (_df(x) - _df(x).shift(int(d))).to_numpy()


def _ts_zscore(x, d):
    df = _df(x)
    mu = df.rolling(int(d)).mean()
    sd = df.rolling(int(d)).std()
    return (df - mu).div(sd.replace(0, np.nan)).to_numpy()


def _ts_corr(x, y, d):
    dx, dy = _df(x), _df(y)
    w = int(d)
    mx, my = dx.rolling(w).mean(), dy.rolling(w).mean()
    cov = (dx * dy).rolling(w).mean() - mx * my
    sx, sy = dx.rolling(w).std(ddof=0), dy.rolling(w).std(ddof=0)
    return cov.div((sx * sy).replace(0, np.nan)).to_numpy()


def _ts_decay_linear(x, d):
    w = int(d)
    weights = np.arange(1, w + 1, dtype=float)
    weights /= weights.sum()
    return _df(x).rolling(w).apply(lambda a: float(np.dot(a, weights)), raw=True).to_numpy()


def _ts_rank(x, d):
    w = int(d)
    return _df(x).rolling(w).apply(
        lambda a: float(a.argsort().argsort()[-1] / (len(a) - 1)) if len(a) > 1 else 0.5,
        raw=True,
    ).to_numpy()


def _ts_argmax(x, d):
    return _df(x).rolling(int(d)).apply(lambda a: float(np.argmax(a)), raw=True).to_numpy()


def _ts_argmin(x, d):
    return _df(x).rolling(int(d)).apply(lambda a: float(np.argmin(a)), raw=True).to_numpy()


# ── element-wise operators ──────────────────────────────────────────

def _log(x): return np.log(np.clip(np.asarray(x, dtype=float), 1e-10, None))
def _abs(x): return np.abs(np.asarray(x, dtype=float))
def _sign(x): return np.sign(np.asarray(x, dtype=float))
def _sqrt(x): return np.sqrt(np.clip(np.asarray(x, dtype=float), 0.0, None))
def _power(x, a): return np.power(np.asarray(x, dtype=float), float(a))
def _signed_power(x, a):
    x = np.asarray(x, dtype=float)
    return np.sign(x) * np.power(np.abs(x), float(a))


OPERATORS: dict[str, Callable[..., np.ndarray]] = {
    # cross-sectional
    "rank": _cs(_rank_row),
    "zscore": _cs(_zscore_row),
    "scale": _cs(_scale_row),
    "demean": _cs(_demean_row),
    # time-series
    "ts_mean": _ts_mean, "ts_std": _ts_std, "ts_sum": _ts_sum,
    "ts_min": _ts_min, "ts_max": _ts_max, "ts_delta": _ts_delta,
    "delay": _delay, "ts_rank": _ts_rank, "ts_corr": _ts_corr,
    "ts_decay_linear": _ts_decay_linear, "ts_zscore": _ts_zscore,
    "ts_argmax": _ts_argmax, "ts_argmin": _ts_argmin,
    # element-wise
    "log": _log, "abs": _abs, "sign": _sign, "sqrt": _sqrt,
    "power": _power, "signed_power": _signed_power,
}

#: Common aliases mapped to the canonical operator names.
_ALIASES = {
    "delta": "ts_delta", "correlation": "ts_corr", "stddev": "ts_std",
    "decay_linear": "ts_decay_linear", "ts_shift": "delay", "ts_delay": "delay",
    "sma": "ts_mean",
    # Pandas-style rolling verbs the agent commonly writes; identical semantics
    # to their ts_* canonicals (``_ts_mean`` etc. ARE ``.rolling(d).mean()``).
    # Registering them here means a frozen ``rolling_mean(...)`` expression
    # parses verbatim instead of forcing a hand-rolled feature path.
    "rolling_mean": "ts_mean", "rolling_std": "ts_std", "rolling_sum": "ts_sum",
    "rolling_min": "ts_min", "rolling_max": "ts_max",
}

_BINOPS = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply,
           ast.Div: np.divide, ast.Pow: np.power}


def _eval(node: ast.AST, fields: Mapping[str, np.ndarray]):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        raise ExpressionError(f"disallowed literal {node.value!r}")
    if isinstance(node, ast.Name):
        return _resolve_field(node.id, fields)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _eval(node.operand, fields)
        return np.negative(v) if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        left, right = _eval(node.left, fields), _eval(node.right, fields)
        with np.errstate(divide="ignore", invalid="ignore"):
            return _BINOPS[type(node.op)](left, right)
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.keywords:
            raise ExpressionError("only positional calls to named operators are allowed")
        name = _ALIASES.get(node.func.id, node.func.id)
        op = OPERATORS.get(name)
        if op is None:
            raise ExpressionError(f"unknown operator {node.func.id!r}")
        args = [_eval(a, fields) for a in node.args]
        return op(*args)
    raise ExpressionError(f"disallowed expression node {type(node).__name__}")


def evaluate(expression: str, fields: Mapping[str, np.ndarray]) -> np.ndarray:
    """Evaluate ``expression`` over ``fields`` -> a ``(T, S)`` float array.

    ``fields`` maps field names to ``(T, S)`` arrays (at least ``close``);
    ``vwap`` and ``returns`` are derived on demand. Raises
    :class:`ExpressionError` on a syntax error or any disallowed construct.
    """
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"syntax error: {exc}") from exc
    result = _eval(tree.body, fields)
    return np.atleast_2d(np.asarray(result, dtype=float))


def expression_feature(
    name: str,
    expression: str,
    *,
    direction: float = 1.0,
    description: str = "",
    neutralize: bool = True,
):
    """Wrap an expression as a :class:`..builder.FeatureSpec`.

    The returned FeatureSpec's ``compute`` evaluates ``expression`` against the
    OHLCV panel passed to :func:`..builder.build_feature_panel`, so an alpha
    formula becomes a first-class factor with a stated ``direction`` and
    ``description`` (the economic thesis — required by the review floor).
    """
    from .builder import FeatureSpec

    return FeatureSpec(
        name=name,
        compute=(lambda ohlcv, _e=expression: evaluate(_e, ohlcv)),
        direction=direction,
        transform="rank",
        neutralize=neutralize,
        description=description or f"expression factor: {expression}",
    )


def available_operators() -> tuple[str, ...]:
    """The canonical operator names the DSL accepts (aliases resolve to these)."""
    return tuple(sorted(OPERATORS))
