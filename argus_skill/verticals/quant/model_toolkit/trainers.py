"""Numpy-native model trainers — the config→model surface for autonomous selection.

Each trainer is built from a plain config dict (``build_trainer(family, config)``) and
exposes one contract — ``fit(X_tr, y_tr, X_va, y_va)`` / ``predict(X)`` over numpy —
so the selection engine can train ANY family the same way. "Creating a model" is
therefore just emitting a config; no per-model glue.

Three families cover the tabular-cross-section task honestly:

* ``gbdt``   — lightgbm (handles NaN natively; the strong tabular default);
* ``mlp``    — a torch feed-forward net whose depth/width/dropout ARE the config
  (config-level architecture search — the L2 "create a new architecture" form);
* ``linear`` — ridge (a cheap, stable baseline the winner must beat).

MLP/linear cannot eat NaN, so they standardise on train stats then zero-fill;
gbdt keeps NaN. GPU is used for the MLP when available.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

import numpy as np


class ModelTrainer(Protocol):
    """Common surface: fit on train (early-stop on valid), predict a 1-D score."""

    def fit(
        self, X_tr: np.ndarray, y_tr: np.ndarray, X_va: np.ndarray, y_va: np.ndarray
    ) -> "ModelTrainer": ...
    def predict(self, X: np.ndarray) -> np.ndarray: ...


class _Standardizer:
    """Per-column (mean, std) fit on train, NaN-safe; transform then zero-fill."""

    def __init__(self) -> None:
        self.mu: np.ndarray | None = None
        self.sd: np.ndarray | None = None

    def fit(self, X: np.ndarray) -> "_Standardizer":
        self.mu = np.nanmean(X, axis=0)
        sd = np.nanstd(X, axis=0)
        self.sd = np.where(sd > 1e-9, sd, 1.0)
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        z = (X - self.mu) / self.sd
        return np.nan_to_num(z, nan=0.0, posinf=0.0, neginf=0.0).astype("float32")


class GBDTTrainer:
    """lightgbm gradient boosting (NaN-native). Config = lightgbm params + rounds."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = dict(config)
        self.num_boost_round = int(self.config.pop("num_boost_round", 500))
        self.early_stopping = int(self.config.pop("early_stopping_rounds", 50))
        self.booster: Any = None

    def fit(self, X_tr, y_tr, X_va, y_va) -> "GBDTTrainer":
        import lightgbm as lgb

        params = {
            "objective": "regression",
            "metric": "l2",
            "verbosity": -1,
            # LightGBM interprets 0 as "all available cores". On a shared
            # many-core Argus host that lets one small candidate or unit test
            # monopolize the machine. Keep the default bounded; an experiment
            # can still request a different value explicitly in its config.
            "num_threads": min(8, os.cpu_count() or 1),
            **self.config,
        }
        dtr = lgb.Dataset(X_tr, label=y_tr)
        dva = lgb.Dataset(X_va, label=y_va, reference=dtr)
        self.booster = lgb.train(
            params,
            dtr,
            num_boost_round=self.num_boost_round,
            valid_sets=[dva],
            valid_names=["valid"],
            callbacks=[
                lgb.early_stopping(self.early_stopping, verbose=False),
                lgb.log_evaluation(0),
            ],
        )
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.booster.predict(X, num_iteration=self.booster.best_iteration)


class RidgeTrainer:
    """Ridge regression baseline (standardised, zero-filled). Config = {alpha}."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.alpha = float(config.get("alpha", 1.0))
        self.scaler = _Standardizer()
        self.model: Any = None

    def fit(self, X_tr, y_tr, X_va, y_va) -> "RidgeTrainer":
        from sklearn.linear_model import Ridge

        Xs = self.scaler.fit(X_tr).transform(X_tr)
        self.model = Ridge(alpha=self.alpha).fit(Xs, y_tr)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.model.predict(self.scaler.transform(X))


class TorchMLPTrainer:
    """Feed-forward net whose architecture IS the config (L2 arch search).

    Config: ``hidden_dims`` (tuple), ``dropout``, ``lr``, ``weight_decay``,
    ``epochs``, ``batch_size``, ``patience``, ``seed``. Standardises + zero-fills
    inputs, early-stops on validation loss, trains on GPU when available.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        c = dict(config)
        self.hidden_dims = tuple(c.get("hidden_dims", (256, 64)))
        self.dropout = float(c.get("dropout", 0.1))
        self.lr = float(c.get("lr", 1e-3))
        self.weight_decay = float(c.get("weight_decay", 1e-5))
        self.epochs = int(c.get("epochs", 60))
        self.batch_size = int(c.get("batch_size", 8192))
        self.patience = int(c.get("patience", 8))
        self.seed = int(c.get("seed", 0))
        self.scaler = _Standardizer()
        self.net: Any = None
        self._device: Any = None

    def _build(self, n_in: int):
        import torch.nn as nn

        layers: list[Any] = []
        d = n_in
        for h in self.hidden_dims:
            layers += [nn.Linear(d, h), nn.ReLU(), nn.Dropout(self.dropout)]
            d = h
        layers += [nn.Linear(d, 1)]
        return nn.Sequential(*layers)

    def fit(self, X_tr, y_tr, X_va, y_va) -> "TorchMLPTrainer":
        import torch
        from torch import nn, optim

        torch.manual_seed(self.seed)
        self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        Xtr = torch.from_numpy(self.scaler.fit(X_tr).transform(X_tr))
        ytr = torch.from_numpy(y_tr.astype("float32")).view(-1, 1)
        Xva = torch.from_numpy(self.scaler.transform(X_va))
        yva = torch.from_numpy(y_va.astype("float32")).view(-1, 1)
        self.net = self._build(Xtr.shape[1]).to(self._device)
        opt = optim.Adam(self.net.parameters(), lr=self.lr, weight_decay=self.weight_decay)
        lossf = nn.MSELoss()
        Xva_d, yva_d = Xva.to(self._device), yva.to(self._device)
        n = Xtr.shape[0]
        best, best_state, bad = float("inf"), None, 0
        for _ep in range(self.epochs):
            self.net.train()
            perm = torch.randperm(n)
            for i in range(0, n, self.batch_size):
                idx = perm[i : i + self.batch_size]
                xb = Xtr[idx].to(self._device)
                yb = ytr[idx].to(self._device)
                opt.zero_grad()
                loss = lossf(self.net(xb), yb)
                loss.backward()
                opt.step()
            self.net.eval()
            with torch.no_grad():
                vloss = float(lossf(self.net(Xva_d), yva_d))
            if vloss < best - 1e-6:
                best, bad = vloss, 0
                best_state = {k: v.detach().clone() for k, v in self.net.state_dict().items()}
            else:
                bad += 1
                if bad >= self.patience:
                    break
        if best_state is not None:
            self.net.load_state_dict(best_state)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        import torch

        self.net.eval()
        with torch.no_grad():
            xb = torch.from_numpy(self.scaler.transform(X)).to(self._device)
            return self.net(xb).cpu().numpy().reshape(-1)


#: family -> trainer class.
_FAMILIES: dict[str, type] = {
    "gbdt": GBDTTrainer,
    "mlp": TorchMLPTrainer,
    "linear": RidgeTrainer,
}


def build_trainer(family: str, config: dict[str, Any]) -> ModelTrainer:
    """Instantiate a trainer for ``family`` from a plain config dict."""
    cls = _FAMILIES.get(family)
    if cls is None:
        raise ValueError(f"unknown model family {family!r} (have {sorted(_FAMILIES)})")
    return cls(config)  # type: ignore[return-value]


def available_families() -> tuple[str, ...]:
    return tuple(sorted(_FAMILIES))
