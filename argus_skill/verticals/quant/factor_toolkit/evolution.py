"""Evolutionary alpha search over the expression DSL — genuine factor discovery.

Where :mod:`.builder` tests KNOWN factors and :mod:`.expression` lets a human
compose one, this module *discovers* new ones: it mutates and recombines DSL
expressions and keeps the fittest, so the mining loop explores the alpha space
beyond the seeded factor zoo. It is a programmatic genetic search (no LLM in the
loop) — every candidate is a real DSL expression, validated and scored.

Pieces:

* mutation operators as AST transforms — perturb a window, swap an operator or
  field, wrap in a normalisation / non-linearity, negate, or simplify;
* :func:`crossover` — graft a subtree from one expression into another;
* :func:`random_expression` — sample a fresh valid expression (for seeding /
  full regeneration);
* :func:`evolve` — the population loop: evaluate (injected fitness) → keep
  elites → breed → repeat, returning the ranked survivors and the fitness
  trajectory.

Fitness is injected (a ``Callable[[str], float]``); :func:`make_panel_fitness`
wires it to a real backtest so every evaluated expression lands in the search
ledger (the discipline is preserved — the search breadth is auditable).

The mutation strategy taxonomy is adapted from the QuantGPT sibling repo
(quantgpt/mutation_engine.py), but reimplemented as deterministic AST
transforms rather than LLM prompts.
"""
from __future__ import annotations

import ast
import copy
import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np

from .expression import ExpressionError, evaluate

# ── operator / field vocabulary for mutation ────────────────────────

_PRICE_FIELDS = ("open", "high", "low", "close", "vwap")
_VOL_FIELDS = ("volume", "amount")
_ALL_FIELDS = _PRICE_FIELDS + _VOL_FIELDS + ("returns",)
_WINDOWS = (3, 5, 10, 20, 40, 60)

# same-arity operator swaps (canonical DSL names)
_OP_SWAPS: dict[str, tuple[str, ...]] = {
    "ts_mean": ("ts_std", "ts_sum", "ts_decay_linear", "ts_zscore"),
    "ts_std": ("ts_mean", "ts_sum"),
    "ts_sum": ("ts_mean", "ts_std"),
    "ts_decay_linear": ("ts_mean", "ts_sum"),
    "ts_zscore": ("ts_rank", "ts_mean"),
    "ts_rank": ("ts_zscore",),
    "ts_delta": ("delay",),
    "delay": ("ts_delta",),
    "ts_min": ("ts_max", "ts_argmin"),
    "ts_max": ("ts_min", "ts_argmax"),
    "rank": ("zscore", "scale", "demean"),
    "zscore": ("rank", "scale"),
    "scale": ("rank", "zscore"),
    "log": ("sqrt", "abs"),
    "abs": ("sqrt", "sign"),
    "sqrt": ("abs",),
}
_TS_WINDOW_OPS = frozenset({
    "ts_mean", "ts_std", "ts_sum", "ts_min", "ts_max", "ts_delta", "delay",
    "ts_rank", "ts_decay_linear", "ts_zscore", "ts_argmax", "ts_argmin",
})
_NORM_OPS = ("rank", "zscore", "scale")
_NONLINEAR = ("log", "abs", "sign", "sqrt")


# ── AST helpers ─────────────────────────────────────────────────────

def _body(expr: str) -> ast.expr:
    return ast.parse(expr, mode="eval").body


def _text(node: ast.AST) -> str:
    return ast.unparse(node)


def _nodes(tree: ast.AST, predicate: Callable[[ast.AST], bool]) -> list[ast.AST]:
    return [n for n in ast.walk(tree) if predicate(n)]


class _ReplaceNth(ast.NodeTransformer):
    """Replace the ``index``-th node satisfying ``predicate`` with ``repl``."""

    def __init__(self, predicate, index, repl):
        self.predicate, self.index, self.repl = predicate, index, repl
        self._count = 0

    def visit(self, node):
        node = self.generic_visit(node)
        if self.predicate(node):
            hit = self._count == self.index
            self._count += 1
            if hit:
                return self.repl
        return node


def _call(name: str, *args: ast.expr) -> ast.Call:
    return ast.Call(func=ast.Name(id=name, ctx=ast.Load()), args=list(args), keywords=[])


# ── mutation operators (each: tree, rng -> new tree | None) ──────────

def _mut_window(tree: ast.expr, rng: np.random.Generator) -> ast.expr | None:
    consts = _nodes(tree, lambda n: isinstance(n, ast.Constant)
                    and isinstance(n.value, int) and not isinstance(n.value, bool))
    if not consts:
        return None
    target = consts[int(rng.integers(len(consts)))]
    target.value = int(rng.choice(_WINDOWS))
    return tree


def _mut_operator(tree: ast.expr, rng: np.random.Generator) -> ast.expr | None:
    calls = _nodes(tree, lambda n: isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Name) and n.func.id in _OP_SWAPS)
    if not calls:
        return None
    target = calls[int(rng.integers(len(calls)))]
    target.func.id = str(rng.choice(_OP_SWAPS[target.func.id]))
    return tree


def _mut_field(tree: ast.expr, rng: np.random.Generator) -> ast.expr | None:
    names = _nodes(tree, lambda n: isinstance(n, ast.Name) and n.id in _ALL_FIELDS)
    if not names:
        return None
    target = names[int(rng.integers(len(names)))]
    group = _PRICE_FIELDS if target.id in _PRICE_FIELDS else _VOL_FIELDS
    target.id = str(rng.choice([f for f in group if f != target.id] or group))
    return tree


def _mut_wrap_norm(tree: ast.expr, rng: np.random.Generator) -> ast.expr:
    return _call(str(rng.choice(_NORM_OPS)), tree)


def _mut_wrap_nonlinear(tree: ast.expr, rng: np.random.Generator) -> ast.expr:
    op = str(rng.choice(_NONLINEAR))
    return _call(op, tree)


def _mut_negate(tree: ast.expr, _rng: np.random.Generator) -> ast.expr:
    return ast.BinOp(left=ast.Constant(value=-1.0), op=ast.Mult(), right=tree)


def _mut_simplify(tree: ast.expr, rng: np.random.Generator) -> ast.expr | None:
    # unwrap a random single-arg Call (reduce nesting)
    calls = _nodes(tree, lambda n: isinstance(n, ast.Call) and len(n.args) >= 1)
    if not calls:
        return None
    idx = int(rng.integers(len(calls)))
    target = calls[idx]
    repl = copy.deepcopy(target.args[0])
    new = _ReplaceNth(lambda n: isinstance(n, ast.Call) and len(n.args) >= 1, idx, repl).visit(
        copy.deepcopy(tree)
    )
    return ast.fix_missing_locations(new)


_MUTATORS = (
    _mut_window, _mut_operator, _mut_field,
    _mut_wrap_norm, _mut_wrap_nonlinear, _mut_negate, _mut_simplify,
)


def mutate(expr: str, rng: np.random.Generator, *, strategy: str | None = None) -> str | None:
    """Return one mutated variant of ``expr`` (or ``None`` if it didn't change).

    ``strategy`` names a specific mutator (``window``/``operator``/``field``/
    ``norm``/``nonlinear``/``negate``/``simplify``); ``None`` picks at random.
    The result is not validated here — the caller validates via the fitness /
    :func:`_is_valid`.
    """
    table = {
        "window": _mut_window, "operator": _mut_operator, "field": _mut_field,
        "norm": _mut_wrap_norm, "nonlinear": _mut_wrap_nonlinear,
        "negate": _mut_negate, "simplify": _mut_simplify,
    }
    fn = table[strategy] if strategy else _MUTATORS[int(rng.integers(len(_MUTATORS)))]
    out = fn(copy.deepcopy(_body(expr)), rng)
    if out is None:
        return None
    text = _text(ast.fix_missing_locations(out))
    return text if text != expr else None


def crossover(expr_a: str, expr_b: str, rng: np.random.Generator) -> str | None:
    """Graft a random subtree of ``expr_b`` onto a field slot of ``expr_a``."""
    tree_a = copy.deepcopy(_body(expr_a))
    slots = _nodes(tree_a, lambda n: isinstance(n, ast.Name) and n.id in _ALL_FIELDS)
    donors = _nodes(_body(expr_b), lambda n: isinstance(n, (ast.Call, ast.BinOp, ast.Name)))
    if not slots or not donors:
        return None
    i = int(rng.integers(len(slots)))
    donor = copy.deepcopy(donors[int(rng.integers(len(donors)))])
    new = _ReplaceNth(lambda n: isinstance(n, ast.Name) and n.id in _ALL_FIELDS, i, donor).visit(tree_a)
    text = _text(ast.fix_missing_locations(new))
    return text if text not in (expr_a, expr_b) else None


def random_expression(rng: np.random.Generator, *, max_depth: int = 3) -> str:
    """Sample a fresh valid DSL expression (for seeding / full regeneration)."""
    def build(depth: int) -> ast.expr:
        if depth <= 0 or rng.random() < 0.25:
            return ast.Name(id=str(rng.choice(_ALL_FIELDS)), ctx=ast.Load())
        kind = rng.random()
        if kind < 0.4:  # ts op with window
            op = str(rng.choice(sorted(_TS_WINDOW_OPS)))
            return _call(op, build(depth - 1), ast.Constant(value=int(rng.choice(_WINDOWS))))
        if kind < 0.7:  # cross-sectional / nonlinear wrap
            op = str(rng.choice(_NORM_OPS + _NONLINEAR))
            return _call(op, build(depth - 1))
        # binary combine
        op = rng.choice([ast.Add(), ast.Sub(), ast.Mult()])
        return ast.BinOp(left=build(depth - 1), op=op, right=build(depth - 1))

    return _text(ast.fix_missing_locations(ast.Expression(body=build(max_depth)).body))


# ── validation (cheap probe) ────────────────────────────────────────

_PROBE: dict[str, np.ndarray] | None = None


def _probe_fields() -> dict[str, np.ndarray]:
    global _PROBE
    if _PROBE is None:
        rng = np.random.default_rng(12345)
        # rows must exceed the largest window (60) so full-window ops have output
        close = 100 * np.cumprod(1 + rng.normal(0, 0.02, (120, 6)), axis=0)
        _PROBE = {
            "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
            "volume": rng.uniform(1e6, 5e6, (120, 6)),
            "amount": close * rng.uniform(1e6, 5e6, (120, 6)),
        }
    return _PROBE


def _is_valid(expr: str) -> bool:
    """Cheap check that an expression parses, evaluates, and isn't degenerate."""
    try:
        out = evaluate(expr, _probe_fields())
    except (ExpressionError, ValueError, ZeroDivisionError, RecursionError):
        return False
    except Exception:  # noqa: BLE001 - any eval blow-up = invalid candidate
        return False
    finite = np.isfinite(out)
    # need some finite values and not a constant across the cross-section
    return bool(finite.sum() >= out.shape[1]) and float(np.nanstd(np.where(finite, out, np.nan))) > 0


def _slug(expr: str) -> str:
    return "f" + hashlib.sha256(expr.encode()).hexdigest()[:12]


# ── ledger-backed fitness ───────────────────────────────────────────

def make_panel_fitness(
    ohlcv: dict[str, np.ndarray],
    forward_returns: np.ndarray,
    *,
    ledger: object | None = None,
    metric: str = "abs_ic",
) -> Callable[[str], float]:
    """A fitness ``Callable[[str], float]`` that backtests an expression.

    Each expression is built into a one-factor panel over ``ohlcv`` and scored
    by the toy engine's cross-sectional IC (``metric="abs_ic"`` for magnitude,
    ``"ic"`` for signed). If ``ledger`` is given, every evaluation is submitted
    through a :class:`~..executor.ForcingExecutor` so the search breadth lands
    in the search ledger. An invalid / degenerate expression scores ``-inf``.
    """
    from ..backtest import BacktestSpec
    from ..executor import ForcingExecutor
    from ..reference_engine import ToyBacktestEngine
    from .builder import build_feature_panel
    from .expression import expression_feature

    def fitness(expr: str) -> float:
        try:
            feat = expression_feature(_slug(expr), expr, direction=1.0,
                                      description=f"evolved: {expr}")
            panel, registry = build_feature_panel(ohlcv, forward_returns, features=[feat])
        except (ExpressionError, ValueError):
            return float("-inf")
        except Exception:  # noqa: BLE001
            return float("-inf")
        engine = ToyBacktestEngine(panel=panel, registry=registry)
        spec = BacktestSpec(run_id=f"evo-{_slug(expr)}", factor_ids=list(registry.factor_ids()),
                            weighting="single", window="search")
        if ledger is not None:
            res, _row = ForcingExecutor(engine=engine, ledger=ledger).submit(spec)
        else:
            res = engine.run(spec)
        ic = res.metrics.get("ic", float("nan"))
        if not np.isfinite(ic):
            return float("-inf")
        return float(abs(ic)) if metric == "abs_ic" else float(ic)

    return fitness


# ── the evolutionary loop ───────────────────────────────────────────

@dataclass
class EvolutionResult:
    """Outcome of :func:`evolve`.

    ``best`` is the final population ranked by fitness (expr, score); ``history``
    is the best fitness at each generation; ``evaluated`` is the number of
    distinct expressions scored (= distinct search-ledger trials).
    """

    best: list[tuple[str, float]]
    history: list[float] = field(default_factory=list)
    evaluated: int = 0


def evolve(
    seeds: Sequence[str],
    fitness_fn: Callable[[str], float],
    *,
    generations: int = 5,
    population: int = 20,
    elite: int = 5,
    mutation_rate: float = 0.7,
    seed: int = 0,
    max_evals: int | None = None,
) -> EvolutionResult:
    """Evolve a population of DSL expressions toward higher fitness.

    Seeds the population with ``seeds`` (valid ones) plus random expressions,
    then each generation: score, keep the top ``elite``, breed the rest by
    mutation (prob ``mutation_rate``) or crossover, and repeat for
    ``generations`` rounds. Fitness is cached so each distinct expression is
    scored once. ``max_evals`` caps the total distinct evaluations (budget).

    Returns the ranked final population and the per-generation best-fitness
    trajectory. Pair with :func:`make_panel_fitness` (ledger-backed) so the
    whole search is auditable in the search ledger.
    """
    rng = np.random.default_rng(seed)
    pop: list[str] = list(dict.fromkeys(s for s in seeds if _is_valid(s)))
    guard = 0
    while len(pop) < population and guard < population * 40:
        guard += 1
        cand = random_expression(rng)
        if _is_valid(cand) and cand not in pop:
            pop.append(cand)

    cache: dict[str, float] = {}

    def score(e: str) -> float:
        if e not in cache:
            if max_evals is not None and len(cache) >= max_evals:
                return float("-inf")
            cache[e] = fitness_fn(e)
        return cache[e]

    history: list[float] = []
    for _gen in range(generations):
        ranked = sorted(pop, key=score, reverse=True)
        history.append(score(ranked[0]))
        elites = ranked[:elite]
        children: list[str] = list(elites)  # elitism: carry the best forward
        guard = 0
        while len(children) < population and guard < population * 30:
            guard += 1
            if rng.random() < mutation_rate or len(elites) < 2:
                child = mutate(elites[int(rng.integers(len(elites)))], rng)
            else:
                a = elites[int(rng.integers(len(elites)))]
                b = elites[int(rng.integers(len(elites)))]
                child = crossover(a, b, rng)
            if child and child not in children and _is_valid(child):
                children.append(child)
        pop = children
        if max_evals is not None and len(cache) >= max_evals:
            break

    final = sorted(((e, score(e)) for e in pop), key=lambda kv: kv[1], reverse=True)
    return EvolutionResult(best=final, history=history, evaluated=len(cache))

