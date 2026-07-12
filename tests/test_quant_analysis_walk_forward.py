"""Tests for the walk-forward split generator (analysis.walk_forward)."""
from __future__ import annotations

import pytest

from argus_skill.verticals.quant.analysis.walk_forward import (
    Fold,
    WalkForwardConfig,
    WalkForwardValidator,
)


def test_config_validation():
    with pytest.raises(ValueError):
        WalkForwardConfig(train_size=5)  # < 10
    with pytest.raises(ValueError):
        WalkForwardConfig(test_size=0)
    with pytest.raises(ValueError):
        WalkForwardConfig(window_type="sideways")  # type: ignore[arg-type]


@pytest.mark.parametrize("window_type", ["rolling", "expanding"])
def test_split_disjoint_with_purge_and_embargo(window_type):
    cfg = WalkForwardConfig(
        train_size=90, test_size=14, step_size=14,
        window_type=window_type, purge_size=2, embargo_size=3,
    )
    v = WalkForwardValidator(cfg)
    folds = list(v.split(400))
    assert folds, "expected at least one fold"
    for f in folds:
        assert isinstance(f, Fold)
        # train and test never overlap
        assert set(f.train_indices.tolist()).isdisjoint(f.test_indices.tolist())
        # embargo gap of at least embargo_size between train end and test start
        assert int(f.test_indices.min()) - int(f.train_indices.max()) >= cfg.embargo_size
        # test window is exactly test_size long, all within range
        assert f.test_indices.size == cfg.test_size
        assert int(f.test_indices.max()) < 400


def test_count_folds_matches_split():
    v = WalkForwardValidator(WalkForwardConfig(train_size=50, test_size=10, step_size=10))
    assert v.count_folds(300) == len(list(v.split(300)))


def test_expanding_train_grows():
    v = WalkForwardValidator(
        WalkForwardConfig(train_size=50, test_size=10, step_size=10, window_type="expanding")
    )
    sizes = [f.train_indices.size for f in v.split(300)]
    assert sizes == sorted(sizes) and sizes[-1] > sizes[0]


def test_rolling_train_size_constant():
    v = WalkForwardValidator(
        WalkForwardConfig(train_size=50, test_size=10, step_size=10, window_type="rolling")
    )
    sizes = {f.train_indices.size for f in v.split(300)}
    assert sizes == {50}


def test_too_few_samples_raises():
    v = WalkForwardValidator(WalkForwardConfig(train_size=90, test_size=14))
    with pytest.raises(ValueError):
        list(v.split(50))


def test_labels_annotate_boundaries():
    v = WalkForwardValidator(WalkForwardConfig(train_size=20, test_size=5, step_size=5))
    labels = [f"d{i}" for i in range(100)]
    fold = next(v.split(100, labels=labels))
    assert fold.labels["train_start"] == "d0"
    assert fold.labels["test_end"].startswith("d")
    with pytest.raises(ValueError):
        list(v.split(100, labels=labels[:50]))
