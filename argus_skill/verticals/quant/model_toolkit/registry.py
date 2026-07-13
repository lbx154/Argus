"""Model search space — the candidate configs Argus selects among / mutates.

A :class:`ModelSpec` is one candidate = ``(family, config)`` plus a stated
rationale and coarse task tags. :func:`default_model_space` is the seed pool
spanning families (L1) and, within the MLP family, several architectures (L2) —
so "pick a model" and "pick an architecture" are the same act of choosing a spec.
The space is deliberately small (a handful) so nested walk-forward stays tractable;
the selection engine can extend/mutate it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModelSpec:
    """One model candidate: a family + a config dict, with a thesis and tags.

    ``config`` is what :func:`.trainers.build_trainer` turns into a live model —
    emitting a new ``ModelSpec`` IS creating a new model/architecture. ``tags``
    (e.g. ``"tabular"``, ``"deep"``, ``"baseline"``, ``"nan_native"``) let the
    task-conditional prior rank specs without hard-coding names.
    """

    name: str
    family: str
    config: dict[str, Any]
    description: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


def default_model_space() -> list[ModelSpec]:
    """Seed pool: GBDT (2) + MLP architectures (3) + a linear baseline."""
    return [
        ModelSpec(
            name="gbdt_shallow", family="gbdt", tags=("tabular", "nan_native", "baseline"),
            config={"num_leaves": 31, "learning_rate": 0.05, "num_boost_round": 500,
                    "feature_fraction": 0.7, "bagging_fraction": 0.7, "bagging_freq": 5,
                    "min_data_in_leaf": 200, "lambda_l1": 1.0, "lambda_l2": 1.0},
            description="Shallow GBDT — strong low-variance tabular default.",
        ),
        ModelSpec(
            name="gbdt_deep", family="gbdt", tags=("tabular", "nan_native"),
            config={"num_leaves": 127, "learning_rate": 0.03, "num_boost_round": 800,
                    "feature_fraction": 0.6, "bagging_fraction": 0.7, "bagging_freq": 5,
                    "min_data_in_leaf": 100, "lambda_l1": 2.0, "lambda_l2": 2.0},
            description="Deeper GBDT — more capacity, more overfitting risk.",
        ),
        ModelSpec(
            name="mlp_small", family="mlp", tags=("deep", "needs_data"),
            config={"hidden_dims": (128, 32), "dropout": 0.1, "lr": 1e-3,
                    "weight_decay": 1e-5, "epochs": 60, "batch_size": 8192, "patience": 8},
            description="Small MLP — a compact non-linear alternative to trees.",
        ),
        ModelSpec(
            name="mlp_medium", family="mlp", tags=("deep", "needs_data"),
            config={"hidden_dims": (256, 64), "dropout": 0.2, "lr": 1e-3,
                    "weight_decay": 1e-5, "epochs": 60, "batch_size": 8192, "patience": 8},
            description="Medium MLP — more capacity; higher variance.",
        ),
        ModelSpec(
            name="mlp_deep", family="mlp", tags=("deep", "needs_data", "high_capacity"),
            config={"hidden_dims": (512, 128, 32), "dropout": 0.3, "lr": 7e-4,
                    "weight_decay": 3e-5, "epochs": 80, "batch_size": 8192, "patience": 10},
            description="Deep MLP — needs the most data; easiest to overfit.",
        ),
        ModelSpec(
            name="ridge", family="linear", tags=("baseline", "cheap", "robust"),
            config={"alpha": 10.0},
            description="Ridge — cheap linear baseline the winner must beat.",
        ),
    ]
